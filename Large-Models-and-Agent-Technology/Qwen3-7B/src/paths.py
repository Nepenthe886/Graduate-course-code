"""项目路径 + 把所有缓存/临时目录强制锁在项目内（防止写 C 盘）。"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = PROJECT_ROOT / "configs"
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
MODELS_DIR = PROJECT_ROOT / "models"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"

DEFAULT_CONFIG = CONFIG_DIR / "default.json"


def resolve(path_like: str | os.PathLike[str]) -> Path:
    """相对路径一律相对项目根解析。"""
    path = Path(path_like).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def ensure_dirs() -> None:
    for directory in (
        MODELS_DIR,
        RUNTIME_DIR,
        LOGS_DIR,
        DATA_DIR,
        CACHE_DIR / "pip",
        CACHE_DIR / "temp",
        CACHE_DIR / "modelscope",
        CACHE_DIR / "hf",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def lock_caches_into_project() -> None:
    """把所有常见缓存/临时目录指向项目内，避免任何下载或临时文件落到 C 盘。"""
    ensure_dirs()
    env = {
        "TMP": CACHE_DIR / "temp",
        "TEMP": CACHE_DIR / "temp",
        "PIP_CACHE_DIR": CACHE_DIR / "pip",
        "HF_HOME": CACHE_DIR / "hf",
        "MODELSCOPE_CACHE": CACHE_DIR / "modelscope",
        "XDG_CACHE_HOME": CACHE_DIR,
        "HF_HUB_DISABLE_XET": "1",
        "PYTHONPYCACHEPREFIX": CACHE_DIR / "pycache",
    }
    for key, value in env.items():
        os.environ[key] = str(value) if not isinstance(value, str) else value
    (CACHE_DIR / "pycache").mkdir(parents=True, exist_ok=True)
