"""实践二 3.3 流式调用：stream（llama.cpp 版，对应课件 ollama 流式示例）

课件原始代码（ollama）：
    from ollama import chat
    stream = chat(
        model="qwen3.5:0.8b",
        messages=[{"role": "user", "content": "写一首关于秋天的短诗"}],
        stream=True,
    )
    for chunk in stream:
        print(chunk["message"]["content"], end="", flush=True)

本文件的等价写法（llama.cpp / OpenAI 兼容接口）：
    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:8010/v1", api_key="sk-local")
    stream = client.chat.completions.create(
        model="Qwen3-8B",
        messages=[{"role": "user", "content": "写一首关于秋天的短诗"}],
        stream=True,
    )
    for chunk in stream:
        text = chunk.choices[0].delta.content
        if text:
            print(text, end="", flush=True)

同步 vs 流式：
    同步（stream=False）：一次性返回全文，代码简单，但首字要等整段生成完；
    流式（stream=True） ：逐字返回，首字延迟低（本脚本会打印 TTFT），交互体验好。

前置：先启动本地模型
    cd /d E:\\Code\\Graduate-course-code\\Large-Models-and-Agent-Technology\\Qwen3-7B
    scripts\\run_model.cmd            :: 默认 127.0.0.1:8010

用法：
    py -3.12 stream_chat_llama.py
    py -3.12 stream_chat_llama.py --question "写一首关于秋天的短诗"
    py -3.12 stream_chat_llama.py --compare      # 同步 vs 流式 对比首字延迟
    py -3.12 stream_chat_llama.py --base-url http://127.0.0.1:8012/v1
"""
from __future__ import annotations

import argparse
import sys
import time

DEFAULT_BASE_URL = "http://127.0.0.1:8010/v1"
DEFAULT_MODEL = "Qwen3-8B"
DEFAULT_QUESTION = "写一首关于秋天的短诗"


def resolve_model(client, fallback: str) -> str:
    try:
        models = client.models.list()
        if models.data:
            return models.data[0].id
    except Exception:  # noqa: BLE001
        pass
    return fallback


def stream_once(client, model: str, question: str) -> dict:
    """流式调用：逐字打印，并统计首字延迟（TTFT）与总耗时。"""
    messages = [{"role": "user", "content": question}]
    print(f"user: {question}")
    print("assistant: ", end="", flush=True)

    t0 = time.perf_counter()
    ttft = None
    chunks = 0
    chars = 0
    parts: list[str] = []

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.8,
        max_tokens=512,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if not text:
            continue
        if ttft is None:
            ttft = time.perf_counter() - t0
        chunks += 1
        chars += len(text)
        parts.append(text)
        print(text, end="", flush=True)

    total = time.perf_counter() - t0
    print()
    print("-" * 60)
    print(f"首字延迟 TTFT: {ttft * 1000:.0f} ms" if ttft is not None else "首字延迟 TTFT: -")
    print(f"总耗时: {total:.2f} s | 分片数: {chunks} | 字符数: {chars} | "
          f"平均速度: {chars / total:.1f} 字/秒")
    return {"text": "".join(parts), "ttft": ttft or 0.0, "total": total, "chars": chars}


def sync_once(client, model: str, question: str) -> dict:
    """同步调用：一次性返回全文，用于对比首字延迟。"""
    messages = [{"role": "user", "content": question}]
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.8,
        max_tokens=512,
        stream=False,
    )
    total = time.perf_counter() - t0
    text = response.choices[0].message.content or ""
    return {"text": text, "first_visible": total, "total": total, "chars": len(text)}


def main() -> int:
    ap = argparse.ArgumentParser(description="llama.cpp 流式调用示例（对应 ollama stream 示例）")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--question", default=DEFAULT_QUESTION)
    ap.add_argument("--compare", action="store_true", help="额外跑一次同步调用做对比")
    args = ap.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("[错误] 缺少 openai 依赖，请先执行: py -3.12 -m pip install openai")
        return 1

    client = OpenAI(base_url=args.base_url, api_key="sk-local")
    try:
        model = resolve_model(client, args.model)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 无法连接模型服务 {args.base_url}")
        print(f"       原因: {exc}")
        print("       请先在 Qwen3-7B 目录运行: scripts\\run_model.cmd")
        return 2

    print(f"服务地址: {args.base_url}")
    print(f"模型标识: {model}")
    print("=" * 60)
    print("【流式调用 stream=True】")

    stream_result = stream_once(client, model, args.question)

    if args.compare:
        print("\n" + "=" * 60)
        print("【同步调用 stream=False（对比）】")
        sync_result = sync_once(client, model, args.question)
        print(f"assistant: {sync_result['text'][:80]}…")
        print("-" * 60)
        print(f"同步：首字可见要等 {sync_result['first_visible'] * 1000:.0f} ms，"
              f"总耗时 {sync_result['total']:.2f} s")
        print(f"流式：首字仅 {stream_result['ttft'] * 1000:.0f} ms，"
              f"总耗时 {stream_result['total']:.2f} s")
        print(f"→ 首字延迟降低约 {1000 * (sync_result['first_visible'] - stream_result['ttft']):.0f} ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())
