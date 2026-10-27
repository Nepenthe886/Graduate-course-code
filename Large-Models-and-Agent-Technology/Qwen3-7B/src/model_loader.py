"""模型加载：量化、tokenizer、chat template（含 tools 支持）。"""
from __future__ import annotations

import json
import time
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

from .config import AppConfig


def pick_dtype() -> torch.dtype:
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float16


def _require_model(cfg: AppConfig) -> str:
    path = cfg.model_path
    if not (path / "config.json").exists():
        raise FileNotFoundError(
            f"模型不存在: {path}\n"
            f"请先运行: .venv\\Scripts\\python.exe -m src.download --mirror"
        )
    return str(path)


def load_tokenizer(cfg: AppConfig):
    tokenizer = AutoTokenizer.from_pretrained(_require_model(cfg), trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    return tokenizer


def load_model(cfg: AppConfig, tokenizer=None):
    path = _require_model(cfg)
    tokenizer = tokenizer or load_tokenizer(cfg)
    dtype = pick_dtype()
    kwargs: dict[str, Any] = {"trust_remote_code": True, "low_cpu_mem_usage": True}

    if cfg.quant == "4bit":
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
        )
        kwargs["device_map"] = "auto" if cfg.device == "cuda" else {"": "cpu"}
    elif cfg.quant == "8bit":
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        kwargs["device_map"] = "auto" if cfg.device == "cuda" else {"": "cpu"}
    else:
        kwargs["device_map"] = "auto" if cfg.device == "cuda" else {"": "cpu"}
        target = torch.bfloat16 if cfg.quant == "bf16" else torch.float16
        try:
            kwargs["dtype"] = target
        except TypeError:  # 兼容旧版 transformers
            kwargs["torch_dtype"] = target

    started = time.time()
    print(f"[load] {path}  quant={cfg.quant}  device={cfg.device}", flush=True)
    model = AutoModelForCausalLM.from_pretrained(path, **kwargs)
    model.eval()
    print(f"[load] done in {time.time() - started:.1f}s", flush=True)

    if cfg.device == "cuda" and torch.cuda.is_available():
        used = torch.cuda.memory_allocated() / 2**30
        total = torch.cuda.get_device_properties(0).total_memory / 2**30
        print(f"[load] VRAM allocated: {used:.2f} GiB / {total:.1f} GiB", flush=True)

    return model, tokenizer


def model_id(cfg: AppConfig) -> str:
    return cfg.model_path.name


def normalize_tools(tools: list[dict] | None) -> list[dict] | None:
    """把 OpenAI tools 规范成 Qwen 模板能吃的结构（保留 name/description/parameters）。"""
    if not tools:
        return None
    out: list[dict] = []
    for item in tools:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict):
            continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": params,
                },
            }
        )
    return out or None


def to_template_messages(messages: list[dict], system_prompt: str | None = None) -> list[dict]:
    """OpenAI messages -> chat template messages（补 system、规范化 content/tool_calls）。"""
    msgs: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        item: dict[str, Any] = {"role": role, "content": content}
        if m.get("tool_calls"):
            item["tool_calls"] = m["tool_calls"]
        if role == "tool" and m.get("tool_call_id"):
            item["tool_call_id"] = m["tool_call_id"]
        if m.get("name"):
            item["name"] = m["name"]
        msgs.append(item)

    if system_prompt and not any(x["role"] == "system" for x in msgs):
        msgs.insert(0, {"role": "system", "content": system_prompt})
    return msgs


def _fallback_prompt_with_tools(tokenizer, msgs: list[dict], tools: list[dict]) -> str:
    """旧版 transformers 不支持 tools= 参数时：把工具说明塞进 system prompt。"""
    tool_block = (
        "# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
        "You are provided with function signatures within <tools></tools> XML tags:\n<tools>\n"
        + "\n".join(json.dumps(t, ensure_ascii=False) for t in tools)
        + "\n</tools>\n\nFor each function call, return a json object with function name "
        'and arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n'
        '{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>\n'
    )
    msgs = [dict(x) for x in msgs]
    if msgs and msgs[0]["role"] == "system":
        msgs[0]["content"] = tool_block + "\n" + msgs[0]["content"]
    else:
        msgs.insert(0, {"role": "system", "content": tool_block})
    try:
        return tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )
    except TypeError:
        return tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )


def build_prompt(
    tokenizer,
    messages: list[dict],
    enable_thinking: bool,
    system_prompt: str | None = None,
    tools: list[dict] | None = None,
) -> str:
    """渲染 prompt：支持 tools（走 Qwen3 官方模板的 tools 分支）。"""
    msgs = to_template_messages(messages, system_prompt)
    tools = normalize_tools(tools)
    if tools:
        try:
            return tokenizer.apply_chat_template(
                msgs,
                tokenize=False,
                add_generation_prompt=True,
                enable_thinking=enable_thinking,
                tools=tools,
            )
        except TypeError:
            return _fallback_prompt_with_tools(tokenizer, msgs, tools)
    try:
        return tokenizer.apply_chat_template(
            msgs,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
        )
    except TypeError:
        return tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )
