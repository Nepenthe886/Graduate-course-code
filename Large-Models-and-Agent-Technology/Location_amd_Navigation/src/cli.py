"""命令行入口：列出地图 / 查看地图 / 规划路线 / 检查线段 / 显示位姿。"""
from __future__ import annotations

import argparse
import json
import sys

from .config import add_common_args, load_config, select_map
from .localization import localizer_from_world
from .navigation import Navigation
from .paths import ensure_dirs, list_maps
from .planner import Planner
from .worldmap import WorldMap


def _parse_point(text: str) -> tuple[float, float]:
    parts = text.replace(" ", "").split(",")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError("坐标格式应为 x,y")
    return (float(parts[0]), float(parts[1]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="定位与导航模块 CLI")
    add_common_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("maps", help="列出可用地图")

    p_show = sub.add_parser("show", help="显示地图摘要")
    add_common_args(p_show)

    p_plan = sub.add_parser("plan", help="规划一条绕障路线")
    add_common_args(p_plan)
    p_plan.add_argument("--start", type=_parse_point, required=True, help="起点 x,y")
    p_plan.add_argument("--goal", type=_parse_point, required=True, help="终点 x,y")
    p_plan.add_argument("--clearance", type=float, default=None, help="安全净空（米）")

    p_check = sub.add_parser("check", help="检查一条线段是否可直线通过")
    add_common_args(p_check)
    p_check.add_argument("--start", type=_parse_point, required=True)
    p_check.add_argument("--goal", type=_parse_point, required=True)
    p_check.add_argument("--clearance", type=float, default=None)

    p_route = sub.add_parser("route", help="规划到某个目标名并输出动作序列")
    add_common_args(p_route)
    p_route.add_argument("--target", required=True, help="目标名，如 Circle Target 1")
    p_route.add_argument("--drone", default=None, help="无人机名，用于取起点")
    p_route.add_argument("--z", type=float, default=None, help="飞行高度")
    p_route.add_argument("--clearance", type=float, default=None)

    p_pose = sub.add_parser("pose", help="显示无人机位姿")
    add_common_args(p_pose)
    p_pose.add_argument("--drone", required=True)
    p_pose.add_argument("--target", default=None, help="可选：同时显示到目标的方位/距离")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "maps":
        rows = list_maps(config.__dict__)
        print(f"可用地图 {len(rows)} 个：")
        for path, name, map_id in rows:
            print(f"  [{map_id}] {name}")
            print(f"        {path}")
        return 0

    map_path = select_map(config, args)
    world = WorldMap.load(map_path)
    planner = Planner(world, clearance=args.clearance or config.clearance, grid_step=config.grid_step)
    output: dict = {"map_file": str(map_path)}

    if args.command == "show":
        output.update(world.summary())

    elif args.command == "check":
        check = planner.segment(args.start, args.goal, args.clearance)
        output.update({"start": list(args.start), "goal": list(args.goal), **check.as_dict()})

    elif args.command == "plan":
        route = planner.plan(args.start, args.goal, args.clearance)
        output.update(route.as_dict())

    elif args.command == "route":
        drone = args.drone or (world.drones[0].get("name") if world.drones else "Drone 1")
        localizer = localizer_from_world(world, drone)
        navigation = Navigation(world, localizer, planner,
                                arrival_tolerance=config.arrival_tolerance, z_default=config.z_default)
        route = navigation.plan_to_target(args.target, args.clearance)
        steps = navigation.steps_to(route.goal, z=args.z, clearance=args.clearance)
        output.update({
            "drone": drone,
            "start_pose": localizer.describe()["pose"],
            "target": args.target,
            "route": route.as_dict(),
            "steps": [step.as_dict() for step in steps],
        })

    elif args.command == "pose":
        localizer = localizer_from_world(world, args.drone)
        target = world.target_by_name(args.target) if args.target else None
        output.update({"drone": args.drone, **localizer.describe(target.center if target else None)})

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
