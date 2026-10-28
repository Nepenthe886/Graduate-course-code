"""行为树任务执行器：按 plan_mission 展开的步骤逐点执行（一个点一个点完成）。

用法：
  py -3.12 -m src.mission_run --map <地图.json> --drone 612145fe --altitude 32 \
      --task "fly to (62,80,32), then (152,159,32), finally (915,914,32) and land"

  py -3.12 -m src.mission_run --spec mission.json
  py -3.12 -m src.mission_run ... --dry-run     # 只打印步骤不执行
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import coverage
from . import geometry
from .config import load_config
from .frames import Pose
from .mission import plan_mission
from .planner import Planner
from .worldmap import WorldMap

NAV_LOGS = Path(__file__).resolve().parent.parent / "logs"

COORD_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
)


def parse_task_waypoints(task_text: str) -> list[dict]:
    points = []
    for match in COORD_RE.finditer(task_text or ""):
        points.append({"x": float(match.group(1)), "y": float(match.group(2)),
                       "z": float(match.group(3))})
    return points


class UavApi:
    def __init__(self, base: str = "http://127.0.0.1:8000", timeout: float = 15.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(text) if text.strip().startswith(("{", "[")) else text)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def drone(self, drone_id: str) -> dict:
        _, data = self._request("GET", "/drones/" + drone_id)
        return data if isinstance(data, dict) else {}

    def command(self, drone_id: str, command: str, *, params: dict | None = None,
                body: dict | None = None):
        if params:
            query = "&".join("{}={}".format(k, v) for k, v in params.items())
            return self._request("POST", "/drones/{}/command/{}?{}".format(drone_id, command, query), body)
        return self._request("POST", "/drones/{}/command/{}".format(drone_id, command), body)

    def wait_arrival(self, drone_id: str, goal: tuple[float, float], tolerance: float = 30.0,
                     timeout: float = 60.0) -> bool:
        started = time.time()
        while time.time() - started < timeout:
            position = self.drone(drone_id).get("position") or {}
            x = float(position.get("x", 0.0))
            y = float(position.get("y", 0.0))
            if ((x - goal[0]) ** 2 + (y - goal[1]) ** 2) ** 0.5 <= tolerance:
                return True
            time.sleep(1.5)
        return False


COVERAGE_TARGET_RE = re.compile(r"(polygon|circle|ellipse)\s*target\s*(\d+)", re.I)
PHOTO_RE = re.compile(r"(\d+)\s*(?:photos?|images?)", re.I)
THRESHOLD_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*%")
DEFAULT_COVERAGE_SWATH = 40.0


def _coverage_waypoints(world, task_text: str, altitude: float):
    """从任务文本识别覆盖目标，生成蛇形覆盖航点。返回 (waypoints, meta)。"""
    match = COVERAGE_TARGET_RE.search(task_text or "")
    if not match or world is None:
        return [], {}
    kind = match.group(1).lower()
    name = "%s Target %s" % (kind.capitalize(), match.group(2))
    target = None
    for candidate in getattr(world, "targets", []):
        if str(candidate.name).lower() == name.lower():
            target = candidate
            break
    if target is None:
        return [], {"coverage_error": "地图里没有目标 %s" % name}

    swath = DEFAULT_COVERAGE_SWATH
    spacing = max(8.0, swath * 0.9)
    vertices = getattr(target, "vertices", None) or []
    if str(getattr(target, "type", "")).lower() == "polygon" and len(vertices) >= 3:
        polygon = [(float(v["x"]), float(v["y"])) for v in vertices]
        points = coverage.serpentine_polygon(polygon, spacing)
        lanes = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
        estimate = coverage.estimate_coverage_polygon(polygon, lanes, swath)
    else:
        center = getattr(target, "center", (0.0, 0.0))
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        points = coverage.serpentine_route((float(center[0]), float(center[1])), radius, spacing)
        lanes = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
        estimate = coverage.estimate_coverage((float(center[0]), float(center[1])),
                                             radius, lanes, swath)
    if not points:
        return [], {"coverage_error": "目标 %s 无法生成覆盖航线" % name}
    waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": altitude} for p in points]
    threshold_match = THRESHOLD_RE.search(task_text or "")
    threshold = float(threshold_match.group(1)) / 100.0 if threshold_match else 0.9
    return waypoints, {"coverage_target": name, "coverage_threshold": threshold,
                       "coverage_spacing": spacing, "coverage_swath": swath,
                       "estimated_coverage": round(estimate, 4)}


def build_spec_from_task(task_text: str, altitude: float, land: bool = True,
                         world=None) -> dict:
    points = parse_task_waypoints(task_text)
    if not points:
        waypoints, meta = _coverage_waypoints(world, task_text, altitude)
        if waypoints:
            photos_match = PHOTO_RE.search(task_text or "")
            photos = int(photos_match.group(1)) if photos_match else 0
            spec = {"altitude": altitude, "waypoints": waypoints, "land_at_end": land,
                    "photo_count": photos, "task_text": task_text}
            spec.update(meta)
            return spec
        if meta.get("coverage_error"):
            return {"altitude": altitude, "waypoints": [], "land_at_end": land,
                    "error": meta["coverage_error"]}
    return {"altitude": altitude,
            "waypoints": [{"x": p["x"], "y": p["y"], "z": p["z"]} for p in points],
            "land_at_end": land}


def run_mission(map_path: str, drone_id: str, spec: dict,
                api_base: str = "http://127.0.0.1:8000", dry_run: bool = False,
                config_path: str | None = None) -> int:
    config = load_config(config_path)
    world = WorldMap.load(map_path)
    api = UavApi(api_base)

    drone = api.drone(drone_id)
    position = drone.get("position") or {}
    start = Pose(float(position.get("x", 0.0)), float(position.get("y", 0.0)),
                 float(position.get("z", 0.0)), float(drone.get("heading") or 0.0))
    plan = plan_mission(spec, world, start, Planner(world, clearance=config.clearance))

    print("[mission] 状态={} 航点={} 航段={}".format(
        plan["status"], plan["stats"]["waypoints"], plan["stats"]["legs"]))
    for index, step in enumerate(plan["steps"], 1):
        if step["kind"] == "move_along_path":
            extra = " 覆盖={}".format(step.get("cover")) if step.get("cover") else ""
            print("  {:2d}. move_along_path 航点={} 长度={}{}".format(
                index, len(step["waypoints"]), step.get("length", 0), extra))
        else:
            print("  {:2d}. {}".format(index, step["kind"]))
    if plan["status"] != "success":
        print("[mission] 规划失败:", plan.get("error"))
        return 2
    if dry_run:
        print("[mission] --dry-run，不执行")
        return 0

    altitude = float(spec.get("altitude", 32.0))
    print("[exec] 起飞到 {} m".format(altitude))
    api.command(drone_id, "take_off", params={"altitude": altitude})
    time.sleep(3)

    total_legs = 0
    for index, step in enumerate(plan["steps"], 1):
        kind = step["kind"]
        if kind == "take_off":
            continue
        if kind == "move_along_path":
            goal = step.get("goal")
            waypoints = step["waypoints"]
            if not waypoints:
                continue
            total_legs += 1
            label = step.get("label") or ""
            print("[exec] {:2d} 第 {} 段 -> {} {}（{} 航点）".format(
                index, total_legs, goal, label, len(waypoints)))
            status, body = api.command(drone_id, "move_along_path",
                                       body={"waypoints": waypoints, "allow_partial_move": False})
            if isinstance(body, dict) and body.get("status") == "error":
                print("     ! 被拒绝:", str(body.get("message"))[:120])
                continue
            if goal:
                ok = api.wait_arrival(drone_id, (goal[0], goal[1]))
                print("     到达" if ok else "     ! 超时未到达")
        elif kind == "take_photo":
            print("[exec] {:2d} 拍照".format(index))
            api.command(drone_id, "take_photo")
        elif kind == "hover":
            duration = float(step.get("duration", 5.0))
            print("[exec] {:2d} 悬停 {:.0f}s".format(index, duration))
            api.command(drone_id, "hover", params={"duration": duration})
        elif kind == "broadcast":
            print("[exec] {:2d} 广播".format(index))
            api.command(drone_id, "broadcast", body={"content": step.get("content", "")})
        elif kind == "land":
            print("[exec] {:2d} 降落".format(index))
            api.command(drone_id, "land")
    print("[mission] 完成，共执行 {} 段".format(total_legs))
    return 0



def execute_actions(map_path: str, drone_id: str, spec: dict,
                    api_base: str = "http://127.0.0.1:8000",
                    config_path: str | None = None) -> dict:
    """按动作库执行任务（行为树逐步 tick），支持全部原子/复合动作。"""
    from .actions import build_action_steps
    from .bt import Status, build_tree_from_steps

    world = WorldMap.load(map_path)
    api = UavApi(api_base)
    altitude = float(spec.get("altitude") or 32.0)
    steps = build_action_steps(world, spec)
    if not steps:
        return {"ok": False, "mode": "actions", "steps": [], "executed": [],
                "failures": [{"error": "没有可执行动作"}]}
    if spec.get("ensure_takeoff", True) and steps[0].get("kind") != "take_off":
        steps.insert(0, {"kind": "take_off", "params": {"altitude": altitude}})
    tree = build_tree_from_steps(steps, name=str(spec.get("name") or "actions"))
    blackboard = {"api": api, "drone_id": drone_id, "results": []}
    status = tree.tick(blackboard)
    executed = blackboard.get("results") or []
    failures = [item for item in executed if not item.get("ok")]
    if spec.get("land_at_end") and steps[-1].get("kind") != "land":
        api.command(drone_id, "land")
        executed.append({"ok": True, "name": "land"})
    return {"ok": status == Status.SUCCESS and not failures, "mode": "actions",
            "steps": steps, "executed": executed, "failures": failures}


def execute_mission(map_path: str, drone_id: str, spec: dict | str,
                    api_base: str = "http://127.0.0.1:8000",
                    config_path: str | None = None) -> dict:
    """L2 行为树执行入口：**逐段** JIT 规划 + 执行，返回结构化结果。

    关键：不再一次性规划全部航段（那会长时间静默且可能超时），
    而是 每个航点：先规划这一段 -> 立即飞 -> 验证到达 -> 记录，再下一个。
    """
    if isinstance(spec, dict) and spec.get("actions"):
        return execute_actions(map_path, drone_id, spec, api_base, config_path)

    from .planner import Planner as _Planner

    config = load_config(config_path)
    world = WorldMap.load(map_path)
    api = UavApi(api_base)

    if isinstance(spec, str):
        spec = build_spec_from_task(spec, 32.0)
    waypoints = spec.get("waypoints") or []
    altitude = float(spec.get("altitude", 32.0))
    land_at_end = bool(spec.get("land_at_end", True))

    result = {
        "ok": bool(waypoints),
        "planned": {"waypoints": len(waypoints), "legs": 0, "mode": "per_leg_jit"},
        "executed": [],
        "failures": [],
    }
    if not waypoints:
        result["ok"] = False
        return result

    planner = _Planner(
        world,
        clearance=config.clearance,
        grid_step=config.grid_step,
        planning_dim=getattr(config, "planning_dim", "2d"),
        altitude=altitude,
        vertical_margin=getattr(config, "vertical_margin", 5.0),
        max_altitude=getattr(config, "max_altitude", 120.0),
        altitude_step=getattr(config, "altitude_step", 10.0),
        grid_max_expansions=getattr(config, "grid_max_expansions", 4000),
        plan_time_budget=getattr(config, "plan_time_budget", 1.5),
    )

    drone = api.drone(drone_id)
    position = drone.get("position") or {}
    current = (float(position.get("x", 0.0)), float(position.get("y", 0.0)))

    result["take_off"] = api.command(drone_id, "take_off", params={"altitude": altitude})[1]
    time.sleep(2.0)

    mission_log = NAV_LOGS / "mission_run.log"
    try:
        mission_log.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _log(message: str) -> None:
        try:
            with mission_log.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except Exception:
            pass

    budget = float(spec.get("time_budget", 900.0))
    mission_started = time.time()
    photo_total = int(spec.get("photo_count") or 0)
    photo_every = max(1, len(waypoints) // photo_total) if photo_total else 0
    photos_taken = 0

    def _take_photo() -> bool:
        nonlocal photos_taken
        status, body = api.command(drone_id, "take_photo")
        ok = isinstance(body, dict) and body.get("status") != "error"
        if ok:
            photos_taken += 1
        result["executed"].append({"action": "take_photo", "ok": ok,
                                   "error": None if ok else str(body)[:100]})
        _log(f"[photo] taken={photos_taken}/{photo_total} ok={ok}")
        return ok
    # 执行容差：优先 spec，其次无人机自己的 task_radius（Easy 5 = 20m），最后兜底 20m
    default_tolerance = float(drone.get("task_radius") or 20.0)
    tolerance = float(spec.get("arrival_tolerance", default_tolerance))

    def _resolve_target(waypoint):
        """航点若绑定了目标（target/label 名字），取出该目标。"""
        hint = str(waypoint.get("target") or waypoint.get("label") or "").strip().lower()
        if not hint:
            return None
        for target in getattr(world, "targets", []):
            if hint in (str(target.name).lower(), str(target.id).lower()):
                return target
        return None

    def _target_reached(target, x: float, y: float) -> bool:
        """官方口径：圆目标看 radius，多边形目标看 vertices 围成的区域。"""
        kind = str(getattr(target, "type", "") or "").lower()
        vertices = getattr(target, "vertices", None)
        if kind == "polygon" and vertices:
            polygon = [(float(v["x"]), float(v["y"])) for v in vertices]
            return geometry.point_in_polygon((x, y), polygon)
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        center = getattr(target, "center", (0.0, 0.0))
        return ((x - float(center[0])) ** 2 + (y - float(center[1])) ** 2) ** 0.5 <= radius

    for index, waypoint in enumerate(waypoints, 1):
        if time.time() - mission_started > budget:
            result["failures"].append({"leg": index, "error": "任务总时长超预算，提前结束"})
            result["ok"] = False
            _log(f"[budget] stop at leg {index}")
            break
        goal = (float(waypoint["x"]), float(waypoint["y"]))
        z = float(waypoint.get("z", altitude))
        # 画布外的点服务端允许飞（已实测），默认不拦截；需要夹边界时传 clamp_out_of_bounds
        if spec.get("clamp_out_of_bounds", False):
            width = float(getattr(world, "width", 0.0) or 0.0)
            height = float(getattr(world, "height", 0.0) or 0.0)
            goal = (min(max(goal[0], 0.0), width), min(max(goal[1], 0.0), height))
        t0 = time.time()
        try:
            route = planner.plan(current, goal, altitude=z)
        except Exception as exc:  # noqa: BLE001
            record = {"leg": index, "goal": list(goal), "plan_ms": int((time.time() - t0) * 1000),
                      "error": f"规划失败: {str(exc)[:80]}"}
            result["executed"].append(record)
            result["failures"].append(record)
            result["ok"] = False
            continue
        plan_ms = int((time.time() - t0) * 1000)
        leg_waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)}
                         for p in (route.waypoints_xyz[1:] or [(goal[0], goal[1], z)])]
        status, body = api.command(drone_id, "move_along_path",
                                   body={"waypoints": leg_waypoints, "allow_partial_move": False})
        error = None
        if isinstance(body, dict) and body.get("status") == "error":
            error = str(body.get("message"))[:120]
        arrived = False
        if not error:
            arrived = api.wait_arrival(drone_id, goal, tolerance=tolerance)
        if not error and not arrived:
            # 闭环补飞一次：小容差下把无人机再拉向目标点，短等 25s 后按实际距离判定
            api.command(drone_id, "move_to", body={"x": goal[0], "y": goal[1], "z": z})
            arrived = api.wait_arrival(drone_id, goal, tolerance=tolerance, timeout=25.0)
        position = api.drone(drone_id).get("position") or {}
        px, py = float(position.get("x", 0.0)), float(position.get("y", 0.0))
        distance = ((px - goal[0]) ** 2 + (py - goal[1]) ** 2) ** 0.5
        target = _resolve_target(waypoint)
        target_hit = _target_reached(target, px, py) if target is not None else None
        record = {"leg": index, "goal": list(goal), "waypoints": len(leg_waypoints),
                  "plan_ms": plan_ms, "arrived": arrived, "distance_m": round(distance, 1),
                  "target": getattr(target, "name", None) if target is not None else None,
                  "target_reached": target_hit, "error": error}
        _log(f"[leg {index:02d}] goal={goal} distance={distance:.1f}m arrived={arrived} "
             f"target={record['target']} target_reached={target_hit} "
             f"plan={plan_ms}ms error={error}")
        result["executed"].append(record)
        if error or not arrived:
            result["failures"].append(record)
            result["ok"] = False
        else:
            current = goal
        if photo_every and photos_taken < photo_total and index % photo_every == 0:
            _take_photo()

    while photos_taken < photo_total:
        if not _take_photo():
            break

    result["planned"]["legs"] = len(result["executed"])
    result["photos_taken"] = photos_taken
    if photo_total:
        result["photos_required"] = photo_total
        if photos_taken < photo_total:
            result["ok"] = False
            result["failures"].append({"error": "拍照数量不足 %d/%d" % (photos_taken, photo_total)})
    if spec.get("coverage_target"):
        result["coverage"] = {"target": spec.get("coverage_target"),
                              "estimated": spec.get("estimated_coverage"),
                              "threshold": spec.get("coverage_threshold"),
                              "spacing": spec.get("coverage_spacing")}
    if land_at_end:
        api.command(drone_id, "land")
        result["executed"].append({"action": "land"})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="行为树任务执行器（逐点完成）")
    parser.add_argument("--map", default=None, help="地图 JSON")
    parser.add_argument("--drone", required=True, help="无人机 id")
    parser.add_argument("--task", default=None, help="任务文本（解析其中 (x,y,z)）")
    parser.add_argument("--spec", default=None, help="spec JSON 文件")
    parser.add_argument("--altitude", type=float, default=32.0)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    elif args.task:
        spec = build_spec_from_task(args.task, args.altitude)
    else:
        print("需要 --task 或 --spec")
        return 2
    if not args.map:
        print("需要 --map")
        return 2
    return run_mission(args.map, args.drone, spec, args.api, args.dry_run, args.config)


if __name__ == "__main__":
    sys.exit(main())
