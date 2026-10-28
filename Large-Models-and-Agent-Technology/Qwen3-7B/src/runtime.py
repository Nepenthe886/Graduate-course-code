"""准备 llama.cpp 运行时：自动下载（多连接并行，绕开单连接限速）或从本地 zip 解压。"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import math
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import requests

from .config import load_config
from .paths import lock_caches_into_project, resolve

API = "https://api.github.com/repos/ggml-org/llama.cpp/releases"
CHUNK = 8 * 1024 * 1024  # 8 MiB per range request

MANUAL_HINT = """
手动下载（浏览器/加速器）后放到 {dest}，再执行 --from-zip：
  1) https://github.com/ggml-org/llama.cpp/releases/download/{tag}/llama-{tag}-bin-win-cuda-{cuda}.zip
  2) https://github.com/ggml-org/llama.cpp/releases/download/{tag}/cudart-llama-bin-win-cuda-{cuda}.zip
"""


def _json(url: str) -> object:
    with requests.get(url, timeout=60, headers={"User-Agent": "qwen3-local"}) as resp:
        resp.raise_for_status()
        return resp.json()


def fetch_releases(proxy: str, tag: str) -> list[dict]:
    base = f"{proxy.rstrip('/')}/{API}" if proxy else API
    if tag == "latest":
        data = _json(base + "?per_page=10")
        return data if isinstance(data, list) else [data]
    return [_json(base + f"/tags/{tag}")]


def pick_assets(release: dict, cuda_tag: str) -> tuple[str, str, list[str]]:
    names = [a["name"] for a in release.get("assets", []) if a["name"].endswith(".zip")]
    main = cudart = ""
    for asset in release.get("assets", []):
        name = asset["name"]
        if not name.endswith(".zip"):
            continue
        if name.startswith("llama-") and cuda_tag in name:
            main = asset["browser_download_url"]
        elif name.startswith("cudart") and cuda_tag in name:
            cudart = asset["browser_download_url"]
    return main, cudart, names


def _probe_size(real_url: str) -> int:
    req = urllib.request.Request(real_url, headers={"Range": "bytes=0-0",
                                                   "User-Agent": "qwen3-local"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_range = resp.headers.get("Content-Range", "")
        if "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        length = resp.headers.get("Content-Length")
        return int(length) if length else 0


def download_parallel(url: str, target: Path, proxy: str, workers: int = 8,
                      retries: int = 4) -> Path:
    """多连接分块下载（每块独立文件，可续传），最后合并。"""
    real = f"{proxy.rstrip('/')}/{url}" if proxy else url
    target.parent.mkdir(parents=True, exist_ok=True)
    size = _probe_size(real)
    if size <= 0:
        raise RuntimeError("无法获取文件大小（服务器不支持 Range？）")

    chunk_dir = target.with_suffix(target.suffix + ".chunks")
    chunk_dir.mkdir(parents=True, exist_ok=True)
    total_chunks = math.ceil(size / CHUNK)
    print(f"[dl] {target.name}: {size / 2**20:.1f} MiB，{total_chunks} 块，{workers} 并发")

    def fetch(index: int) -> int:
        out = chunk_dir / f"{index:05d}"
        start = index * CHUNK
        end = min(start + CHUNK, size) - 1
        need = end - start + 1
        if out.exists() and out.stat().st_size == need:
            return need
        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(
                    real,
                    headers={"Range": f"bytes={start}-{end}",
                             "User-Agent": "qwen3-local"},
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    if resp.status not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = resp.read()
                if len(data) != need:
                    raise RuntimeError(f"块大小不符 {len(data)} != {need}")
                out.write_bytes(data)
                return need
            except Exception as exc:  # noqa: BLE001
                if attempt == retries:
                    print(f"\n[FAIL] 块 {index} 重试 {retries} 次仍失败: {exc}")
                    raise
                time.sleep(2 * attempt)
        return 0

    done_chunks = 0
    done_bytes = 0
    started = time.time()
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(fetch, range(total_chunks)):
            done_chunks += 1
            done_bytes += CHUNK
            elapsed = max(time.time() - started, 0.001)
            print(f"\r  {done_chunks}/{total_chunks} 块  "
                  f"{min(done_bytes, size) / 2**20:.1f} MiB  "
                  f"{done_bytes / 1024 / elapsed:.0f} KB/s", end="")
    print()

    # 合并
    tmp = target.with_suffix(target.suffix + ".merging")
    with tmp.open("wb") as out:
        for index in range(total_chunks):
            out.write((chunk_dir / f"{index:05d}").read_bytes())
    tmp.replace(target)
    for part in chunk_dir.glob("*"):
        part.unlink(missing_ok=True)
    chunk_dir.rmdir()
    print(f"[ok] {target.name} 合并完成 {target.stat().st_size / 2**20:.1f} MiB")
    return target


def unzip_into(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


def classify_zips(zips: list[Path]) -> tuple[list[Path], list[Path]]:
    main, cudart = [], []
    for path in zips:
        low = path.name.lower()
        if low.startswith("cudart"):
            cudart.append(path)
        elif low.startswith("llama-") or "bin-win" in low:
            main.append(path)
    return main, cudart


def install_from_zips(zips: list[Path], dest: Path) -> int:
    main, cudart = classify_zips(zips)
    if not main:
        print(f"[FAIL] 没找到主程序包（llama-*-bin-win-cuda-*.zip），现有：")
        for z in zips:
            print("   -", z.name)
        return 2
    for zip_path in main + cudart:
        print(f"[unzip] {zip_path.name} -> {dest}")
        unzip_into(zip_path, dest)
    exe = dest / "llama-server.exe"
    print(f"[check] llama-server.exe 存在: {exe.exists()}")
    return 0 if exe.exists() else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="准备 llama.cpp 运行时")
    ap.add_argument("--config", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--cuda", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--from-zip", action="store_true")
    ap.add_argument("--zip", action="append", default=None)
    ap.add_argument("--parallel", type=int, default=8, help="并行连接数（默认 8）")
    args = ap.parse_args(argv)

    lock_caches_into_project()
    cfg = load_config(args.config)
    dest = resolve(cfg.runtime_dir)
    downloads = resolve(".cache") / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    exe = dest / "llama-server.exe"

    print(f"[info] runtime dir = {dest}")
    if exe.exists() and not args.force and not (args.from_zip or args.zip):
        print(f"[skip] 已存在 {exe.name}")
        return 0

    if args.from_zip or args.zip:
        zips = [Path(p) for p in args.zip] if args.zip else sorted(downloads.glob("*.zip"))
        zips = [z for z in zips if z.exists()]
        if not zips:
            print(f"[FAIL] {downloads} 里没有 zip")
            return 2
        return install_from_zips(zips, dest)

    tag = args.tag or cfg.llama_release_tag
    cuda_tag = args.cuda or cfg.cuda_tag
    print(f"[info] 查询 release（proxy={cfg.github_proxy or '直连'}）...")
    try:
        releases = fetch_releases(cfg.github_proxy, tag)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 获取 release 失败: {exc}")
        print(MANUAL_HINT.format(dest=downloads, tag="b11933", cuda="13.4"))
        return 2

    release = next((r for r in releases if r.get("assets")), None)
    if release is None:
        print("[FAIL] 没有可用 release")
        return 2
    print(f"[info] release = {release.get('tag_name')}")
    bin_url, cudart_url, names = pick_assets(release, cuda_tag)
    if args.list:
        for name in names:
            print("   ", name)
        return 0
    if not bin_url:
        print(f"[FAIL] 没找到 {cuda_tag} 主包")
        return 2

    zips: list[Path] = []
    for url in [u for u in (bin_url, cudart_url) if u]:
        target = downloads / Path(url).name
        if target.exists() and target.stat().st_size > 0:
            print(f"[skip] 已下载 {target.name}")
            zips.append(target)
            continue
        try:
            zips.append(download_parallel(url, target, cfg.github_proxy, workers=args.parallel))
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 下载 {target.name} 失败: {exc}")
            print(MANUAL_HINT.format(dest=downloads, tag=release.get("tag_name"), cuda="13.4"))
            return 4
    return install_from_zips(zips, dest)


if __name__ == "__main__":
    sys.exit(main())
