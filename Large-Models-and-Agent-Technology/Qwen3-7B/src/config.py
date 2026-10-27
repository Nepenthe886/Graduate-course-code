"""配置加载：configs/default.json -> 环境变量 -> 命令行，逐层覆盖。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .paths import DEFAULT_CONFIG, resolve


@dataclass
class AppConfig:
    # 模型
    model_dir: str = "models/Qwen3-8B"
    hf_repo: str = "Qwen/Qwen3-8B"
    hf_method: str = "hf"                 # hf | modelscope
    hf_endpoint: str = "https://hf-mirror.com"
    hf_disable_xet: bool = True
    quant: str = "4bit"                   # 4bit | 8bit | bf16 | fp16
    device: str = "cuda"                  # cuda | cpu
    # 服务
    host: str = "127.0.0.1"
    port: int = 8010
    api_key: str = ""
    # 生成
    max_new_tokens: int = 1024
    temperature: float = 0.7
    top_p: float = 0.8
    enable_thinking: bool = False
    system_prompt: str = "You are a helpful assistant."

    # ---------- 派生属性 ----------
    @property
    def model_path(self) -> Path:
        return resolve(self.model_dir)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_ENV_MAP = {
    "QWEN3_MODEL_DIR": ("model_dir", str),
    "QWEN3_HF_REPO": ("hf_repo", str),
    "QWEN3_HF_METHOD": ("hf_method", str),
    "QWEN3_HF_ENDPOINT": ("hf_endpoint", str),
    "QWEN3_QUANT": ("quant", str),
    "QWEN3_DEVICE": ("device", str),
    "QWEN3_HOST": ("host", str),
    "QWEN3_PORT": ("port", int),
    "QWEN3_API_KEY": ("api_key", str),
    "QWEN3_MAX_NEW_TOKENS": ("max_new_tokens", int),
}


def _coerce(raw: str, caster):
    if caster is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return caster(raw)


def load_config(config_path: str | None = None) -> AppConfig:
    """加载顺序：默认值 -> json 文件 -> 环境变量。"""
    path = resolve(config_path) if config_path else DEFAULT_CONFIG
    data: dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    elif config_path:
        raise FileNotFoundError(f"配置文件不存在: {path}")

    valid = {f.name for f in fields(AppConfig)}
    unknown = set(data) - valid
    if unknown:
        raise ValueError(f"配置里有未知字段: {sorted(unknown)}")

    cfg = AppConfig(**{k: v for k, v in data.items() if k in valid})

    for env_key, (attr, caster) in _ENV_MAP.items():
        raw = os.getenv(env_key)
        if raw:
            setattr(cfg, attr, _coerce(raw, caster))

    return cfg


def apply_overrides(cfg: AppConfig, **overrides: Any) -> AppConfig:
    for key, value in overrides.items():
        if value is not None and hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg
