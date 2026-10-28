"""OpenAI 兼容客户端（自测/对话），不加载模型。"""
from __future__ import annotations

import argparse
import json
import sys

import requests


def chat(base: str, model: str, messages: list[dict], stream: bool = False,
         max_tokens: int = 512, extra: dict | None = None) -> str:
    url = base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": 0.3, "stream": stream}
    if extra:
        payload.update(extra)
    if not stream:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    pieces: list[str] = []
    with requests.post(url, json=payload, timeout=300, stream=True) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            body = raw[6:]
            if body == "[DONE]":
                break
            piece = json.loads(body)["choices"][0].get("delta", {}).get("content")
            if piece:
                sys.stdout.write(piece)
                sys.stdout.flush()
                pieces.append(piece)
    print()
    return "".join(pieces)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Qwen3 本地服务客户端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--model", default="Qwen3-8B")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=256)
    args = ap.parse_args(argv)

    base = f"http://{args.host}:{args.port}/v1"
    try:
        requests.get(f"http://{args.host}:{args.port}/health", timeout=5).raise_for_status()
    except Exception:
        print(f"[FAIL] 连不上 {base}，先启动 scripts\\run_server.cmd")
        return 1

    if args.prompt:
        text = chat(base, args.model, [{"role": "user", "content": args.prompt}],
                    args.stream, args.max_tokens)
        if not args.stream:
            print(text)
        return 0

    history: list[dict] = []
    print(f"[chat] {base}  (exit 退出)")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in {"exit", "quit"}:
            break
        if not line:
            continue
        history.append({"role": "user", "content": line})
        reply = chat(base, args.model, history, args.stream, args.max_tokens)
        if not args.stream:
            print(f"ai> {reply}")
        history.append({"role": "assistant", "content": reply})
    return 0


if __name__ == "__main__":
    sys.exit(main())
