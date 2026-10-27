"""命令行对话客户端（走 HTTP，不加载模型，可远程调用）。

用法：
    .venv\\Scripts\\python.exe -m src.cli --prompt "介绍一下 MoE"
    .venv\\Scripts\\python.exe -m src.cli            # 进入交互模式
"""
from __future__ import annotations

import argparse
import json
import sys

import requests


def _post(base: str, payload: dict, stream: bool, timeout: int = 600) -> str:
    url = base.rstrip("/") + "/v1/chat/completions"
    if not stream:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    out = []
    with requests.post(url, json=payload, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            body = raw[6:]
            if body == "[DONE]":
                break
            chunk = json.loads(body)
            piece = chunk["choices"][0].get("delta", {}).get("content")
            if piece:
                sys.stdout.write(piece)
                sys.stdout.flush()
                out.append(piece)
    print()
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Qwen3 命令行聊天")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--model", default="Qwen3-8B")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--thinking", action="store_true", help="启用 Qwen3 思考模式")
    args = ap.parse_args(argv)

    base = f"http://{args.host}:{args.port}"
    try:
        requests.get(base + "/health", timeout=5).raise_for_status()
    except Exception:
        print(f"[FAIL] 连不上服务 {base}，先启动：scripts\\run_server.cmd")
        return 1

    history: list[dict] = []
    if args.prompt:
        history.append({"role": "user", "content": args.prompt})
        payload = {"model": args.model, "messages": history, "stream": args.stream,
                   "max_tokens": args.max_tokens, "enable_thinking": args.thinking}
        text = _post(base, payload, args.stream)
        if not args.stream:
            print(text)
        return 0

    print(f"[chat] {base}  (输入 exit 退出)")
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"exit", "quit"}:
            break
        if not line:
            continue
        history.append({"role": "user", "content": line})
        payload = {"model": args.model, "messages": history, "stream": args.stream,
                   "max_tokens": args.max_tokens, "enable_thinking": args.thinking}
        text = _post(base, payload, args.stream)
        if not args.stream:
            print(f"AI> {text}")
        history.append({"role": "assistant", "content": text})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
