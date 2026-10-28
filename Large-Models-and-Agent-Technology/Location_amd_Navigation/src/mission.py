"""任务规划（行为树 + 导航模块分工）。

分层：
  任务级（本文件）：目标点、途径点、每个点的任务（悬停/拍照/覆盖/降落）
                   -> 用行为树展开成有序步骤；失败时可重试/换更大净空重规划
  导航级（planner/navigation）：只在"点到点"时被调用，做绕障/3D/平滑

对外：
  plan_mission(spec, world, start_pose) -> dict   # 纯规划，不执行
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .bt import Action, Condition, Retry, Selector, Sequence, Status
from .coverage import estimate_coverage, estimate_coverage_polygon, serpentine_polygon, serpentine_route
from .frames import Pose
from .planner import Planner
from .worldmap import WorldMap


@dataclass
class WaypointTask:
    kind: str                    # hover | photo | cover | message
    params: dict = field(default_factory=dict)


@dataclass
class MissionSpec:
    altitude: float = 22.0
    waypoints: list[dict] = field(default_factory=list)   # [{x,y,label?,tasks:[{kind,...}]}]
    land_at_end: bool = True
    clearance: float | None = None
    coverage_swath: float = 40.0


def parse_spec(data: dict) -> MissionSpec:
    waypoints = []
    for item in data.get("waypoints") or []:
        waypoint = {
            "x": float(item.get("x", 0.0)),
            "y": float(item.get("y", 0.0)),
            "label": str(item.get("label") or ""),
            "z": float(item.get("z", data.get("altitude", 22.0))),
            "tasks": [WaypointTask(kind=str(t.get("kind")), params={k: v for k, v in t.items() if k != "kind"})
                      for t in (item.get("tasks") or [])],
        }
        waypoints.append(waypoint)
    return MissionSpec(
        altitude=float(data.get("altitude", 22.0)),
        waypoints=waypoints,
        land_at_end=bool(data.get("land_at_end", True)),
        clearance=float(data["clearance"]) if data.get("clearance") is not None else None,
        coverage_swath=float(data.get("coverage_swath", 40.0)),
    )


def _cover_points(world: WorldMap, target_name: str, spacing: float | None) -> tuple[list[tuple[float, float]], float]:
    target = world.target_by_name(target_name) if target_name else None
    if target is None:
        return [], 0.0
    footprint = target.footprint()
    if str(getattr(target, "type", "")).lower() != "circle" and len(footprint) >= 3:
        xs = [p[0] for p in footprint]
        ys = [p[1] for p in footprint]
        min_dim = max(1.0, min(max(xs) - min(xs), max(ys) - min(ys)))
        lane = spacing or max(8.0, min_dim * 0.2)
        points = serpentine_polygon(footprint, lane)
        lanes = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
        return points, estimate_coverage_polygon(footprint, lanes, lane)
    radius = float(getattr(target, "radius", 0.0) or 0.0)
    lane = spacing or max(10.0, radius * 0.35)
    points = serpentine_route(target.center, radius, lane)
    lanes = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
    return points, estimate_coverage(target.center, radius, lanes, lane)


def plan_mission(spec: MissionSpec | dict, world: WorldMap, start: Pose,
                 planner: Planner | None = None) -> dict:
    """用行为树展开任务 -> 可执行步骤序列（不调用 UAV API）。"""
    if isinstance(spec, dict):
        spec = parse_spec(spec)
    planner = planner or Planner(world)
    base_clearance = spec.clearance if spec.clearance is not None else planner.clearance

    steps: list[dict] = []
    blackboard = {
        "world": world,
        "planner": planner,
        "pose": Pose(start.x, start.y, start.z, start.yaw_deg),
        "spec": spec,
        "steps": steps,
        "clearance": base_clearance,
        "stats": {"legs": 0, "distance": 0.0, "covered": []},
    }

    def add_take_off(bb: dict) -> bool:
        bb["pose"].z = spec.altitude
        bb["steps"].append({"kind": "take_off", "altitude": spec.altitude,
                            "note": "起飞到任务高度"})
        return True

    def make_leg(index: int, waypoint: dict):
        def plan_leg(bb: dict) -> bool | Status:
            planner_local: Planner = bb["planner"]
            pose: Pose = bb["pose"]
            clearance = bb["clearance"]
            goal = (waypoint["x"], waypoint["y"])
            try:
                route = planner_local.plan(pose.xy, goal, clearance, altitude=waypoint.get("z", spec.altitude))
            except Exception as exc:  # noqa: BLE001
                bb["last_error"] = str(exc)
                return Status.FAILURE
            bb["route"] = route
            return True

        def emit_leg(bb: dict) -> bool:
            route = bb["route"]
            waypoints = route.waypoints_xyz or [(p[0], p[1], waypoint.get("z", spec.altitude))
                                                for p in route.waypoints]
            bb["steps"].append({
                "kind": "move_along_path",
                "leg": index,
                "label": waypoint.get("label", ""),
                "goal": [round(waypoint["x"], 2), round(waypoint["y"], 2)],
                "waypoints": [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)}
                              for p in waypoints[1:]],
                "detoured": route.detoured,
                "avoided": route.avoided,
                "length": round(route.length_3d or route.length, 2),
            })
            bb["stats"]["legs"] += 1
            bb["stats"]["distance"] += float(route.length_3d or route.length)
            pose: Pose = bb["pose"]
            pose.x, pose.y = waypoint["x"], waypoint["y"]
            pose.z = waypoint.get("z", spec.altitude)
            return True

        def emit_tasks(bb: dict) -> bool:
            pose: Pose = bb["pose"]
            for task in waypoint.get("tasks") or []:
                if task.kind == "hover":
                    duration = float(task.params.get("duration", 5.0))
                    bb["steps"].append({"kind": "hover", "duration": duration,
                                        "note": f"在 {waypoint.get('label') or '航点'} 悬停 {duration:.0f}s"})
                elif task.kind == "photo":
                    bb["steps"].append({"kind": "take_photo", "note": "拍照"})
                elif task.kind == "cover":
                    target_name = str(task.params.get("target_name") or "")
                    spacing = task.params.get("spacing")
                    lane = float(spacing) if spacing else max(8.0, spec.coverage_swath * 0.9)
                    points, coverage = _cover_points(world, target_name, lane)
                    if not points:
                        return Status.FAILURE
                    bb["steps"].append({
                        "kind": "move_along_path",
                        "cover": target_name,
                        "waypoints": [{"x": round(p[0], 2), "y": round(p[1], 2),
                                       "z": round(spec.altitude, 2)} for p in points],
                        "estimated_coverage": round(coverage, 4),
                        "note": f"覆盖扫描 {target_name}（估算 {coverage * 100:.1f}%）",
                    })
                    bb["stats"]["covered"].append({"target": target_name,
                                                   "coverage": round(coverage, 4)})
                elif task.kind == "message":
                    bb["steps"].append({"kind": "broadcast",
                                        "content": str(task.params.get("content", "mission progress")),
                                        "note": "广播消息"})
            return True

        return Sequence(
            Selector(
                Retry(Action(plan_leg, name=f"plan_leg_{index}"), attempts=2),
                Action(lambda bb: False, name="plan_failed"),
            ),
            Action(emit_leg, name=f"emit_leg_{index}"),
            Action(emit_tasks, name=f"tasks_{index}"),
            name=f"waypoint_{index}",
        )

    def add_land(bb: dict) -> bool:
        if spec.land_at_end:
            bb["steps"].append({"kind": "land", "note": "任务结束降落"})
        return True

    tree = Sequence(
        Action(add_take_off, name="take_off"),
        *[make_leg(i + 1, wp) for i, wp in enumerate(spec.waypoints)],
        Action(add_land, name="land"),
        name="mission",
    )

    status = tree.tick(blackboard)
    result = {
        "status": status.value,
        "altitude": spec.altitude,
        "steps": steps,
        "stats": {
            "waypoints": len(spec.waypoints),
            "legs": blackboard["stats"]["legs"],
            "distance": round(blackboard["stats"]["distance"], 2),
            "covered": blackboard["stats"]["covered"],
        },
        "error": blackboard.get("last_error"),
    }
    return result
