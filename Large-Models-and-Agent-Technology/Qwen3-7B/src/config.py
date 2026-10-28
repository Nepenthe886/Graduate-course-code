"""配置加载：configs/*.json -> 环境变量 -> 命令行覆盖。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import DEFAULT_CONFIG, resolve


@dataclass
class AppConfig:
    model_repo: str = "Qwen/Qwen3-8B-GGUF"
    model_file: str = "Qwen3-8B-Q4_K_M.gguf"
    model_dir: str = "models"
    runtime_dir: str = "runtime/llama"
    host: str = "127.0.0.1"
    port: int = 8010
    context_size: int = 8192
    gpu_layers: int = 99
    threads: int = 8
    batch_size: int = 1024
    ubatch_size: int = 256
    jinja: bool = True
    enable_thinking: bool = False
    modelscope_endpoint: str = "https://www.modelscope.cn"
    github_proxy: str = "https://gh-proxy.com"
    llama_release_tag: str = "latest"
    cuda_tag: str = "win-cuda-13.4-x64"

    @property
    def model_path(self) -> Path:
        return resolve(self.model_dir) / self.model_file

    @property
    def runtime_path(self) -> Path:
        return resolve(self.runtime_dir)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ENV_MAP = {
    "QWEN3_MODEL_REPO": ("model_repo", str),
    "QWEN3_MODEL_FILE": ("model_file", str),
    "QWEN3_PORT": ("port", int),
    "QWEN3_HOST": ("host", str),
    "QWEN3_CTX": ("context_size", int),
    "QWEN3_NGL": ("gpu_layers", int),
    "QWEN3_THREADS": ("threads", int),
}


def load_config(config_path: str | None = None) -> AppConfig:
    path = resolve(config_path) if config_path else DEFAULT_CONFIG
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    elif config_path:
        raise FileNotFoundError(f"配置文件不存在: {path}")

    valid = {f.name for f in fields(AppConfig)}
    data = {k: v for k, v in data.items() if k in valid}
    cfg = AppConfig(**data)

    for env_key, (attr, caster) in _ENV_MAP.items():
        raw = os.getenv(env_key)
        if raw:
            setattr(cfg, attr, caster(raw))
    return cfg


def apply_overrides(cfg: AppConfig, **overrides: Any) -> AppConfig:
    for key, value in overrides.items():
        if value is not None and hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg
