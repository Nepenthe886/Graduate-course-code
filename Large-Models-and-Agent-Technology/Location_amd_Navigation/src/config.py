"""配置与地图选择。"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import DEFAULT_CONFIG, find_map, list_maps, resolve


@dataclass
class NavConfig:
    map_dir: str = "maps"
    external_map_dirs: list[str] = field(default_factory=list)
    scenario_dirs: list[str] = field(default_factory=list)
    default_map: str | None = None
    clearance: float = 45.0
    grid_step: float = 10.0
    arrival_tolerance: float = 5.0
    z_default: float = 59.0
    max_detour_attempts: int = 3
    uav_api_base: str = "http://127.0.0.1:8000"
    frame: dict[str, Any] = field(default_factory=dict)
    # ---- 规划维度 ----
    planning_dim: str = "2d"
    vertical_margin: float = 5.0
    max_altitude: float = 120.0
    altitude_step: float = 10.0
    vertical_penalty: float = 1.5
    z_min: float = 5.0
    # ---- 轨迹平滑 ----
    smoothing: bool = True
    smooth_iterations: int = 2
    max_waypoints: int = 40
    max_turn_deg: float = 60.0
    coverage_swath: float = 40.0
    grid_max_expansions: int = 4000
    plan_time_budget: float = 1.5


def load_config(path: str | Path | None = None) -> NavConfig:
    config_path = resolve(path) if path else DEFAULT_CONFIG
    data: dict[str, Any] = {}
    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    known = set(NavConfig.__dataclass_fields__)
    return NavConfig(**{k: v for k, v in data.items() if k in known})


def select_map(config: NavConfig, args: argparse.Namespace | None = None) -> Path:
    if args is not None:
        if getattr(args, "map", None):
            return resolve(args.map)
        if getattr(args, "map_name", None):
            return find_map(args.map_name, config.__dict__)
    if config.default_map:
        return find_map(config.default_map, config.__dict__)
    maps = list_maps(config.__dict__)
    if not maps:
        raise FileNotFoundError("没有可用地图，先把地图放进 maps/ 或配置 external_map_dirs")
    return maps[0][0]


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="配置文件（相对工程根）")
    parser.add_argument("--map", default=None, help="地图文件路径（相对工程根）")
    parser.add_argument("--map-name", default=None, help="地图名称，自动搜索")
