"""路径解析：一切相对路径都以本工程根目录为基准。"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = PROJECT_ROOT / "configs"
MAPS_DIR = PROJECT_ROOT / "maps"
LOGS_DIR = PROJECT_ROOT / "logs"
OUT_DIR = PROJECT_ROOT / "out"

DEFAULT_CONFIG = CONFIG_DIR / "default.json"


def resolve(path_like: str | os.PathLike[str]) -> Path:
    """相对路径 -> 相对 PROJECT_ROOT；绝对路径原样返回。"""
    path = Path(str(path_like)).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def ensure_dirs() -> None:
    for directory in (MAPS_DIR, LOGS_DIR, OUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def candidate_dirs(config: dict) -> list[Path]:
    """地图搜索目录：map_dir + external_map_dirs（均相对工程根）。"""
    dirs: list[Path] = []
    for key in ("map_dir", *(config.get("external_map_dirs") or [])):
        if not key:
            continue
        if key in config:
            dirs.append(resolve(config[key]))
        else:
            dirs.append(resolve(key))
    seen: set[Path] = set()
    unique: list[Path] = []
    for directory in dirs:
        if directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def find_map(name_or_path: str, config: dict) -> Path:
    """按相对路径或地图名查找地图文件。"""
    direct = resolve(name_or_path)
    if direct.is_file():
        return direct
    if direct.with_suffix(".json").is_file():
        return direct.with_suffix(".json")

    needle = name_or_path.strip().lower()
    for directory in candidate_dirs(config):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if needle in path.name.lower():
                return path
            try:
                import json

                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if needle == str(data.get("name", "")).strip().lower():
                return path
            if needle == str(data.get("id", "")).strip().lower():
                return path
    raise FileNotFoundError(
        f"找不到地图 {name_or_path!r}；已搜索目录：" + ", ".join(str(d) for d in candidate_dirs(config))
    )


def list_maps(config: dict) -> list[tuple[Path, str, str]]:
    """列出所有可用地图：(路径, 名称, id)。"""
    import json

    result: list[tuple[Path, str, str]] = []
    seen: set[Path] = set()
    for directory in candidate_dirs(config):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path in seen:
                continue
            seen.add(path)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            result.append((path, str(data.get("name", path.stem)), str(data.get("id", ""))))
    return result
