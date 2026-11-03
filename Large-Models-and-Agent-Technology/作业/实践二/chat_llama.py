"""实践二：本地大模型对话调用（llama.cpp 版，对应课件中 ollama chat 示例）

课件原始代码（ollama）：
    from ollama import chat
    response = chat(
        model="qwen3.5:0.8b",
        messages=[
            {"role": "system", "content": "你是一个简洁的助手"},
            {"role": "user",   "content": "什么是大语言模型？"},
        ],
    )
    print(response.message.content)

本文件的等价写法（llama.cpp / llama-server，OpenAI 兼容接口）：
    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:8010/v1", api_key="sk-local")
    response = client.chat.completions.create(
        model="Qwen3-8B",
        messages=[...],
    )
    print(response.choices[0].message.content)

区别对照：
    ollama                          →  llama.cpp
    ------------------------------------------------
    ollama.chat(...)                →  /v1/chat/completions（OpenAI 兼容）
    model="qwen3.5:0.8b"            →  model="Qwen3-8B"（或模型文件路径）
    messages=[{role, content}]      →  完全相同的 role/content 结构
    response.message.content        →  response.choices[0].message.content
    一次返回完整结果                 →  stream=False 时同样返回完整结果

前置：先启动本地模型服务（只启动模型，不涉及代理/UAV）
    cd /d E:\\Code\\Graduate-course-code\\Large-Models-and-Agent-Technology\\Qwen3-7B
    scripts\\run_model.cmd            :: 默认 127.0.0.1:8010

用法：
    py -3.12 chat_llama.py
    py -3.12 chat_llama.py --question "什么是智能体？"
    py -3.12 chat_llama.py --stream          # 流式输出（对应 stream=True）
    py -3.12 chat_llama.py --base-url http://127.0.0.1:8012/v1
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_BASE_URL = "http://127.0.0.1:8010/v1"   # run_model.cmd 的默认端口
DEFAULT_MODEL = "Qwen3-8B"                       # llama-server 忽略具体名称，填任意标识即可
SYSTEM_PROMPT = "你是一个简洁的助手"
DEFAULT_QUESTION = "什么是大语言模型？"


def resolve_model(client, fallback: str) -> str:
    """向 /v1/models 查询实际模型名（llama.cpp 返回的是 GGUF 文件路径）。"""
    try:
        models = client.models.list()
        if models.data:
            return models.data[0].id
    except Exception:  # noqa: BLE001
        pass
    return fallback


def build_messages(system: str, question: str) -> list[dict]:
    """messages 采用角色—内容结构：system / user / assistant。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def chat_once(client, model: str, messages: list[dict]) -> None:
    """非流式：一次调用返回完整结果（对应课件中的调用方式）。"""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        max_tokens=512,
        stream=False,
    )
    print("=" * 60)
    print("模型回复：")
    print(response.choices[0].message.content)
    print("-" * 60)
    print(f"finish_reason: {response.choices[0].finish_reason}")
    if response.usage:
        print(f"usage: prompt={response.usage.prompt_tokens} "
              f"completion={response.usage.completion_tokens} "
              f"total={response.usage.total_tokens}")


def chat_stream(client, model: str, messages: list[dict]) -> None:
    """流式：逐段打印（对应 stream=True），适合长回答。"""
    print("=" * 60)
    print("模型回复（流式）：")
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
        max_tokens=512,
        stream=True,
    )
    full = []
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            full.append(text)
            print(text, end="", flush=True)
    print()
    print("-" * 60)
    print(f"总字数: {len(''.join(full))}")


def main() -> int:
    ap = argparse.ArgumentParser(description="llama.cpp 本地模型对话示例（对应 ollama chat 示例）")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI 兼容接口地址")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="模型标识（llama.cpp 不校验，可填任意）")
    ap.add_argument("--question", default=DEFAULT_QUESTION, help="用户问题")
    ap.add_argument("--system", default=SYSTEM_PROMPT, help="system 提示词")
    ap.add_argument("--stream", action="store_true", help="流式输出")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("[错误] 缺少 openai 依赖，请先执行: py -3.12 -m pip install openai")
        return 1

    client = OpenAI(base_url=args.base_url, api_key="sk-local")  # llama.cpp 不校验 key
    try:
        model = resolve_model(client, args.model)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 无法连接模型服务 {args.base_url}")
        print(f"       原因: {exc}")
        print("       请先在 Qwen3-7B 目录运行: scripts\\run_model.cmd")
        return 2

    messages = build_messages(args.system, args.question)

    print(f"服务地址: {args.base_url}")
    print(f"模型标识: {model}")
    print(f"system  : {args.system}")
    print(f"user    : {args.question}")

    if args.stream:
        chat_stream(client, model, messages)
    else:
        chat_once(client, model, messages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
