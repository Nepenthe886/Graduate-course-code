"""下载 Qwen3 权重（HF / hf-mirror / ModelScope），默认关闭 Xet。"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .config import load_config
from .paths import ensure_dirs, resolve, setup_env_vars

IGNORE = ["*.pth", "*.gguf", "*.bin", "original/*", "*.msgpack", "*.h5", "coreml/*", "onnx/*"]


def _preflight(url: str, timeout: int = 10) -> tuple[bool, str]:
    """用 GET(stream) 做连通性探测；HEAD 常被镜像拒绝，故不用 HEAD。"""
    try:
        import requests

        started = time.time()
        resp = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
        resp.close()
        return True, f"{url} -> HTTP {resp.status_code} ({time.time() - started:.1f}s)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{url} -> {type(exc).__name__}: {str(exc)[:140]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下载 Qwen3 权重")
    ap.add_argument("--config", default=None)
    ap.add_argument("--method", choices=["hf", "modelscope"], default=None)
    ap.add_argument("--mirror", action="store_true", help="使用 https://hf-mirror.com")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--xet", action="store_true", help="启用 Xet（默认关闭）")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--out", default=None, help="默认取配置里的 model_dir（相对项目根）")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    ensure_dirs()
    setup_env_vars()

    repo = args.repo or cfg.hf_repo
    out = resolve(args.out or cfg.model_dir)
    method = args.method or cfg.hf_method
    endpoint = args.endpoint or ("https://hf-mirror.com" if args.mirror else cfg.hf_endpoint)

    if not args.xet and cfg.hf_disable_xet:
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint

    print(f"[info] repo   = {repo}")
    print(f"[info] target = {out}  (相对项目根: {cfg.model_dir})")
    print(f"[info] method = {method}")
    print(f"[info] HF_ENDPOINT = {os.environ.get('HF_ENDPOINT', '(默认)')}")
    print(f"[info] HF_HUB_DISABLE_XET = {os.environ.get('HF_HUB_DISABLE_XET', '(未设置)')}")
    print(f"[info] MODELSCOPE_CACHE = {os.environ.get('MODELSCOPE_CACHE', '(未设置)')}")

    if method == "hf":
        ok, msg = _preflight(os.environ.get("HF_ENDPOINT", "https://huggingface.co"))
        print(f"[net ] {msg}")
        if not ok:
            print("[WARN] 预检失败（镜像可能拒绝探测或超时），仍尝试下载；"
                  "若失败请改用 --method modelscope")
        from huggingface_hub import snapshot_download

        path = snapshot_download(repo_id=repo, local_dir=str(out),
                                 ignore_patterns=IGNORE, max_workers=8)
    else:
        try:
            from modelscope import snapshot_download as ms_download
        except ImportError:
            print("[FAIL] 未安装 modelscope：")
            print("       .venv\\Scripts\\python.exe -m pip install modelscope "
                  "-i https://mirrors.ustc.edu.cn/pypi/simple")
            return 2
        path = ms_download(repo, local_dir=str(out))

    cfg_file = Path(path) / "config.json"
    if not cfg_file.exists():
        print("[warn] 缺少 config.json，下载可能不完整，可重复执行续传")
        return 1

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    total = sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file()) / 2**30
    print(f"[ok] {path}")
    print(f"[ok] arch={data.get('architectures')} layers={data.get('num_hidden_layers')} "
          f"size={total:.2f} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
