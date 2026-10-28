"""检查 git 是否会跟踪大文件（防止把模型/二进制推到 GitHub）。"""
from __future__ import annotations

import subprocess
import sys

LIMIT_MB = 20
BIG_PATTERNS = (".gguf", ".exe", ".dll", ".zip", ".safetensors", ".bin")


def main() -> int:
    try:
        root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    except Exception:
        print("[FAIL] 当前目录不在 git 仓库内")
        return 1
    print(f"[info] repo root = {root}")

    files = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    files = [f for f in files if f]
    print(f"[info] 已跟踪文件 {len(files)} 个")

    problems = []
    for rel in files:
        path = __import__("pathlib").Path(root) / rel
        if not path.exists():
            continue
        size_mb = path.stat().st_size / 2**20
        if size_mb > LIMIT_MB or rel.lower().endswith(BIG_PATTERNS):
            problems.append((rel, size_mb))

    if problems:
        print("[WARN] 以下文件已被 git 跟踪，建议 git rm --cached 并加 .gitignore：")
        for rel, size_mb in problems[:20]:
            print(f"   {size_mb:8.1f} MB  {rel}")
        return 2

    print("[ok] 没有超过 20 MB 或疑似二进制的大文件被跟踪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
