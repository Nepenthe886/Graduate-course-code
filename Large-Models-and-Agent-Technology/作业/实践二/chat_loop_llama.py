"""实践二 3.4 完整实践：命令行聊天（llama.cpp 版，对应课件 chat_loop.py）

课件原始代码（ollama）：
    # chat_loop.py — 多轮对话核心逻辑
    history = []
    while True:
        q = input("\\n你: ")
        if q in ("exit", "quit"):
            break
        history.append({"role": "user", "content": q})
        stream = chat(model=MODEL, messages=history, stream=True)
        print("助手: ", end="")
        ans = ""
        for chunk in stream:
            t = chunk["message"]["content"]
            ans += t
            print(t, end="", flush=True)
        history.append({"role": "assistant", "content": ans})

本文件的等价写法（llama.cpp / OpenAI 兼容接口）：
    history = []
    while True:
        q = input("\\n你: ")
        if q.lower() in ("exit", "quit"):
            break
        history.append({"role": "user", "content": q})
        stream = client.chat.completions.create(
            model=MODEL, messages=history, stream=True)
        print("助手: ", end="")
        ans = ""
        for chunk in stream:
            t = chunk.choices[0].delta.content or ""
            ans += t
            print(t, end="", flush=True)
        history.append({"role": "assistant", "content": ans})

多轮对话关键：把历史消息累积传入 messages，模型才具备"记忆"；
流式输出时一边打印一边拼接完整回复，回填到 history。

前置：先启动本地模型（只启动模型，不涉及代理/UAV）
    cd /d E:\\Code\\Graduate-course-code\\Large-Models-and-Agent-Technology\\Qwen3-7B
    scripts\\run_model.cmd            :: 默认 127.0.0.1:8010

用法：
    py -3.12 chat_loop_llama.py
    py -3.12 chat_loop_llama.py --system "你是一个简洁的助手"
    py -3.12 chat_loop_llama.py --base-url http://127.0.0.1:8012/v1

交互命令：
    直接输入内容 = 发消息；exit/quit = 退出；/clear = 清空历史；/history = 查看轮数
"""
from __future__ import annotations

import argparse
import sys
import time

DEFAULT_BASE_URL = "http://127.0.0.1:8010/v1"
DEFAULT_MODEL = "Qwen3-8B"


def resolve_model(client, fallback: str) -> str:
    try:
        models = client.models.list()
        if models.data:
            return models.data[0].id
    except Exception:  # noqa: BLE001
        pass
    return fallback


def stream_reply(client, model: str, history: list[dict], max_tokens: int) -> str:
    """流式请求，边打印边拼接，返回完整回复（用于回填 history）。"""
    stream = client.chat.completions.create(
        model=model,
        messages=history,
        temperature=0.7,
        max_tokens=max_tokens,
        stream=True,
    )
    print("助手: ", end="", flush=True)
    parts: list[str] = []
    for chunk in stream:
        if not chunk.choices:
            continue
        text = getattr(chunk.choices[0].delta, "content", None)
        if not text:
            continue
        parts.append(text)
        print(text, end="", flush=True)
    print()
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="llama.cpp 命令行多轮聊天（对应课件 chat_loop.py）")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--system", default="", help="可选的 system 提示词")
    ap.add_argument("--max-tokens", type=int, default=512)
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

    history: list[dict] = []
    if args.system:
        history.append({"role": "system", "content": args.system})

    print("=" * 60)
    print("命令行多轮聊天（llama.cpp）")
    print(f"服务: {args.base_url} | 模型: {model}")
    print("输入内容开始对话；exit / quit 退出；/clear 清空历史；/history 查看轮数")
    print("=" * 60)

    turns = 0
    while True:
        try:
            q = input("\n你: ")
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break
        q = q.strip()
        if not q:
            continue
        if q.lower() in ("exit", "quit", "/exit", "/quit"):
            print("[退出] 再见")
            break
        if q.lower() == "/clear":
            history = [{"role": "system", "content": args.system}] if args.system else []
            turns = 0
            print("[已清空对话历史]")
            continue
        if q.lower() == "/history":
            print(f"[当前历史 {len(history)} 条消息，已完成 {turns} 轮对话]")
            continue

        history.append({"role": "user", "content": q})   # 累积历史
        t0 = time.perf_counter()
        answer = stream_reply(client, model, history, args.max_tokens)
        dt = time.perf_counter() - t0
        history.append({"role": "assistant", "content": answer})  # 回填历史
        turns += 1
        print(f"[本轮 {dt:.2f}s，历史 {len(history)} 条消息]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
