"""项目路径与目录管理 —— 所有路径都相对项目根解析，便于整包搬运。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根 = 本文件的上两级（src/paths.py -> src -> 根）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = PROJECT_ROOT / "configs"
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
MODELS_DIR = PROJECT_ROOT / "models"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = PROJECT_ROOT / ".cache"
HF_CACHE_DIR = CACHE_DIR / "hf"
MODELSCOPE_CACHE_DIR = CACHE_DIR / "modelscope"

DEFAULT_CONFIG = CONFIG_DIR / "default.json"


def resolve(path_like: str | os.PathLike[str]) -> Path:
    """相对路径一律相对项目根解析（与当前工作目录无关）。"""
    path = Path(path_like).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def ensure_dirs() -> None:
    for directory in (MODELS_DIR, DATA_DIR, LOGS_DIR, HF_CACHE_DIR, MODELSCOPE_CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def setup_env_vars() -> None:
    """把下载缓存固定到项目内，避免污染 C 盘。"""
    os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    os.environ.setdefault("MODELSCOPE_CACHE", str(MODELSCOPE_CACHE_DIR))
