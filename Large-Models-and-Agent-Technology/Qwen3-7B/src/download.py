"""下载 GGUF 模型（ModelScope 直链，支持 Range 断点续传），全部落在项目内。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from .config import load_config
from .paths import lock_caches_into_project, resolve

CHUNK = 4 * 1024 * 1024


def build_url(endpoint: str, repo: str, filename: str) -> str:
    """ModelScope 的 resolve 直链（实测比 api/v1 重定向快 4 倍以上）。"""
    return f"{endpoint.rstrip('/')}/models/{repo}/resolve/master/{filename}"


def download_with_resume(url: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}

    with requests.get(url, headers=headers, stream=True, timeout=60, allow_redirects=True) as resp:
        if have and resp.status_code == 200:
            print("[warn] 服务器不支持断点续传，从头下载")
            have = 0
            part.unlink(missing_ok=True)
        elif have:
            print(f"[resume] 从 {have / 2**20:.1f} MiB 继续")

        total = resp.headers.get("Content-Length")
        total_bytes = int(total) + have if total and total.isdigit() else None
        mode = "ab" if have else "wb"
        with part.open(mode) as handle:
            done = have
            for chunk in resp.iter_content(CHUNK):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if total_bytes:
                    pct = done * 100 / total_bytes
                    print(f"\r  {done / 2**20:8.1f} / {total_bytes / 2**20:.1f} MiB  ({pct:5.1f}%)", end="")
                else:
                    print(f"\r  {done / 2**20:8.1f} MiB", end="")
    print()
    part.replace(target)
    return target.stat().st_size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下载 GGUF 模型到项目 models/ 目录")
    ap.add_argument("--config", default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--file", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    lock_caches_into_project()
    cfg = load_config(args.config)
    repo = args.repo or cfg.model_repo
    filename = args.file or cfg.model_file
    target = resolve(cfg.model_dir) / filename

    print(f"[info] repo   = {repo}")
    print(f"[info] file   = {filename}")
    print(f"[info] target = {target}")

    if target.exists() and not args.force:
        print(f"[skip] 已存在（{target.stat().st_size / 2**30:.2f} GiB），加 --force 可重下")
        return 0

    url = build_url(cfg.modelscope_endpoint, repo, filename)
    print(f"[info] url    = {url}")
    try:
        size = download_with_resume(url, target)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 下载失败: {exc}")
        print("       可手动用浏览器/魔搭下载，然后放到上面的 target 路径")
        return 1

    print(f"[ok] 完成 {target.name}（{size / 2**30:.2f} GiB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
