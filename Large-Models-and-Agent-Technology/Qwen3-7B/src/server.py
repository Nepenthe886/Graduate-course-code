"""启动 llama-server（相对路径、端口预检、参数由配置决定）。"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys

from .config import apply_overrides, load_config
from .paths import PROJECT_ROOT, lock_caches_into_project, resolve


def port_in_use(host: str, port: int) -> bool:
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def build_command(cfg) -> list[str]:
    exe = resolve(cfg.runtime_dir) / "llama-server.exe"
    model = cfg.model_path
    cmd = [
        str(exe),
        "-m", str(model),
        "--host", cfg.host,
        "--port", str(cfg.port),
        "-c", str(cfg.context_size),
        "-ngl", str(cfg.gpu_layers),
        "-t", str(cfg.threads),
        "-b", str(cfg.batch_size),
        "-ub", str(cfg.ubatch_size),
        "--metrics",
    ]
    if cfg.jinja:
        cmd.append("--jinja")
    # Qwen3 默认会先"思考"，对 agent 场景既慢又容易把 content 占成空
    cmd.extend(["--reasoning", "on" if cfg.enable_thinking else "off"])
    return cmd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="启动本地 Qwen3（llama.cpp）服务")
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--ctx", type=int, default=None)
    ap.add_argument("--ngl", type=int, default=None)
    args = ap.parse_args(argv)

    lock_caches_into_project()
    cfg = apply_overrides(load_config(args.config), port=args.port, host=args.host,
                          context_size=args.ctx, gpu_layers=args.ngl)

    exe = resolve(cfg.runtime_dir) / "llama-server.exe"
    if not exe.exists():
        print(f"[FAIL] 找不到 {exe}")
        print("       先运行: scripts\\setup_runtime.cmd")
        return 1
    if not cfg.model_path.exists():
        print(f"[FAIL] 找不到模型 {cfg.model_path}")
        print("       先运行: scripts\\download_model.cmd")
        return 1
    if port_in_use(cfg.host, cfg.port):
        print(f"[FAIL] 端口 {cfg.host}:{cfg.port} 已被占用（未启动）")
        print(f"       换端口:  scripts\\run_server.cmd --port {cfg.port + 1}")
        return 4

    cmd = build_command(cfg)
    print("[cmd]", " ".join(cmd))
    print(f"[serve] OpenAI base_url = {cfg.base_url}")
    print(f"[serve] 模型 = {cfg.model_file}  ctx = {cfg.context_size}  ngl = {cfg.gpu_layers}")
    try:
        return subprocess.call(cmd, cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
