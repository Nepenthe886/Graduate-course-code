"""FastAPI OpenAI 兼容服务（支持 function calling / tool_calls）。

端点：
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions      (stream 可选，支持 tools)

启动：
    .venv\\Scripts\\python.exe -m src.server.app
    .venv\\Scripts\\python.exe -m src.server.app --port 8011 --quant 8bit
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from typing import Any

import torch
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from transformers import TextIteratorStreamer

from ..config import AppConfig, apply_overrides, load_config
from ..model_loader import build_prompt, load_model, load_tokenizer, model_id

STATE: dict[str, Any] = {}
LOCK = threading.Lock()

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
LOOSE_TOOL_CALL_RE = re.compile(
    r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*(\{.*?\}|"(?:[^"\\]|\\.)*")\s*\}', re.S
)


class Message(BaseModel):
    role: str
    content: Any = ""
    name: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    enable_thinking: bool | None = None
    stop: list[str] | None = None
    # ---- function calling ----
    tools: list[dict] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None


def _new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:20]


def parse_tool_calls(text: str) -> tuple[str, list[dict]]:
    """从模型输出里解析 Qwen3 的 <tool_call>{...}</tool_call>，返回(剩余文本, tool_calls)。"""
    calls: list[dict] = []

    for match in TOOL_CALL_RE.finditer(text):
        raw = match.group(1)
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = obj.get("name") or (obj.get("function") or {}).get("name")
        if not name:
            continue
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters", {})
        args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        calls.append(
            {
                "id": _new_call_id(),
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )

    if not calls:
        # 容错：模型偶尔漏掉 <tool_call> 包裹
        for match in LOOSE_TOOL_CALL_RE.finditer(text):
            snippet = match.group(0)
            try:
                obj = json.loads(snippet)
            except json.JSONDecodeError:
                continue
            args = obj.get("arguments")
            args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            calls.append(
                {
                    "id": _new_call_id(),
                    "type": "function",
                    "function": {"name": obj.get("name", ""), "arguments": args_str},
                }
            )

    cleaned = TOOL_CALL_RE.sub("", text)
    if calls:
        cleaned = LOOSE_TOOL_CALL_RE.sub("", cleaned)
    return cleaned.strip(), calls


def create_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="Qwen3 local OpenAI-compatible server", version="2.0")

    def check_auth(authorization: str | None) -> None:
        if cfg.api_key and (authorization or "").removeprefix("Bearer ").strip() != cfg.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": STATE.get("model_id"),
            "quant": cfg.quant,
            "cuda": torch.cuda.is_available(),
            "tools": True,
        }

    @app.get("/v1/models")
    def models() -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": STATE.get("model_id", "qwen3-local"),
                    "object": "model",
                    "created": 0,
                    "owned_by": "local",
                }
            ],
        }

    def _prepare(req: ChatRequest):
        tokenizer, model = STATE["tokenizer"], STATE["model"]
        thinking = cfg.enable_thinking if req.enable_thinking is None else req.enable_thinking
        tools = None
        if req.tools and req.tool_choice != "none":
            tools = req.tools
        prompt = build_prompt(
            tokenizer,
            [m.model_dump() for m in req.messages],
            thinking,
            cfg.system_prompt,
            tools,
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        temperature = cfg.temperature if req.temperature is None else req.temperature
        top_p = cfg.top_p if req.top_p is None else req.top_p
        do_sample = temperature is not None and temperature > 0
        gen_kwargs = {
            "input_ids": inputs["input_ids"],
            "attention_mask": inputs["attention_mask"],
            "max_new_tokens": req.max_tokens or cfg.max_new_tokens,
            "do_sample": do_sample,
            "temperature": max(temperature, 1e-5) if do_sample else None,
            "top_p": top_p if do_sample else None,
            "pad_token_id": tokenizer.pad_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        }
        return prompt, inputs["input_ids"].shape[1], gen_kwargs, tools

    @app.post("/v1/chat/completions")
    def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
        check_auth(authorization)
        tokenizer, model = STATE["tokenizer"], STATE["model"]
        _prompt, prompt_len, gen_kwargs, tools = _prepare(req)
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        # 有 tools：非流式生成后按 OpenAI 协议回 tool_calls（流式则合成 SSE）
        if tools:
            with LOCK, torch.inference_mode():
                out = model.generate(**gen_kwargs)
            new_tokens = out[0][prompt_len:]
            raw = tokenizer.decode(new_tokens, skip_special_tokens=True)
            clean, calls = parse_tool_calls(raw)
            if calls:
                print(
                    "[tool] %d call(s): %s"
                    % (len(calls), [c["function"]["name"] for c in calls]),
                    flush=True,
                )

            if not req.stream:
                message: dict[str, Any] = {"role": "assistant", "content": clean or None}
                if calls:
                    message["tool_calls"] = calls
                return {
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": STATE["model_id"],
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "tool_calls" if calls else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_len,
                        "completion_tokens": int(new_tokens.shape[0]),
                        "total_tokens": prompt_len + int(new_tokens.shape[0]),
                    },
                }

            def sse_tools():
                head = {
                    "id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": STATE["model_id"],
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                 "finish_reason": None}],
                }
                yield "data: " + json.dumps(head, ensure_ascii=False) + "\n\n"
                if clean:
                    ch = {"id": cid, "object": "chat.completion.chunk", "created": created,
                          "model": STATE["model_id"],
                          "choices": [{"index": 0, "delta": {"content": clean},
                                       "finish_reason": None}]}
                    yield "data: " + json.dumps(ch, ensure_ascii=False) + "\n\n"
                if calls:
                    ch = {"id": cid, "object": "chat.completion.chunk", "created": created,
                          "model": STATE["model_id"],
                          "choices": [{"index": 0,
                                       "delta": {"tool_calls": [
                                           {"index": i, **call} for i, call in enumerate(calls)
                                       ]},
                                       "finish_reason": None}]}
                    yield "data: " + json.dumps(ch, ensure_ascii=False) + "\n\n"
                tail = {"id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": STATE["model_id"],
                        "choices": [{"index": 0, "delta": {},
                                     "finish_reason": "tool_calls" if calls else "stop"}]}
                yield "data: " + json.dumps(tail, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(sse_tools(), media_type="text/event-stream")

        # 无 tools：保持原来的流式/非流式行为
        if not req.stream:
            with LOCK, torch.inference_mode():
                out = model.generate(**gen_kwargs)
            new_tokens = out[0][prompt_len:]
            text = tokenizer.decode(new_tokens, skip_special_tokens=True)
            return {
                "id": cid, "object": "chat.completion", "created": created,
                "model": STATE["model_id"],
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_len,
                          "completion_tokens": int(new_tokens.shape[0]),
                          "total_tokens": prompt_len + int(new_tokens.shape[0])},
            }

        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
        gen_kwargs["streamer"] = streamer

        def worker() -> None:
            with LOCK, torch.inference_mode():
                model.generate(**gen_kwargs)

        threading.Thread(target=worker, daemon=True).start()

        def sse():
            head = {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": STATE["model_id"],
                    "choices": [{"index": 0, "delta": {"role": "assistant"},
                                 "finish_reason": None}]}
            yield "data: " + json.dumps(head, ensure_ascii=False) + "\n\n"
            for piece in streamer:
                if not piece:
                    continue
                ch = {"id": cid, "object": "chat.completion.chunk", "created": created,
                      "model": STATE["model_id"],
                      "choices": [{"index": 0, "delta": {"content": piece},
                                   "finish_reason": None}]}
                yield "data: " + json.dumps(ch, ensure_ascii=False) + "\n\n"
            tail = {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": STATE["model_id"],
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield "data: " + json.dumps(tail, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Qwen3 OpenAI 兼容服务（支持 tool calls）")
    ap.add_argument("--config", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--quant", default=None, choices=["4bit", "8bit", "bf16", "fp16"])
    ap.add_argument("--device", default=None, choices=["cuda", "cpu"])
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--max-new-tokens", type=int, default=None)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args(argv)

    cfg = apply_overrides(
        load_config(args.config),
        model_dir=args.model, quant=args.quant, device=args.device,
        host=args.host, port=args.port, max_new_tokens=args.max_new_tokens,
        api_key=args.api_key,
    )

    # ---- 端口预检：在加载模型（约 18 s / 5.7 GB 显存）之前失败，避免白加载 ----
    # 注意：Windows 默认允许多个 socket bind 同一端口（未设 SO_EXCLUSIVEADDRUSE），
    # 所以这里用「能否连上」来判断端口是否真的被监听。
    import socket

    probe_host = "127.0.0.1" if cfg.host in ("", "0.0.0.0", "::") else cfg.host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
        _probe.settimeout(0.5)
        if _probe.connect_ex((probe_host, cfg.port)) == 0:
            print(f"[FAIL] 端口 {cfg.host}:{cfg.port} 已被占用，模型未加载。", flush=True)
            print(f"       换端口:  --port {cfg.port + 1}", flush=True)
            print(f'       查占用:  netstat -ano | findstr ":{cfg.port}"', flush=True)
            return 4

    tokenizer = load_tokenizer(cfg)
    model, tokenizer = load_model(cfg, tokenizer)
    STATE.update(model=model, tokenizer=tokenizer, model_id=model_id(cfg))

    app = create_app(cfg)
    import uvicorn

    print(f"[serve] http://{cfg.host}:{cfg.port}/v1   (OpenAI base_url, tools=on)", flush=True)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
