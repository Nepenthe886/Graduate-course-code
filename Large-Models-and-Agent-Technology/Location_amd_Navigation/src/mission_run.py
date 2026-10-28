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
import math
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


def _densify(points: list[tuple[float, float]], step: float = 15.0) -> list[tuple[float, float]]:
    """沿线插值加密航点（官方覆盖按记录位置统计，需要密集航点）。"""
    if not points:
        return []
    result = [points[0]]
    for index in range(1, len(points)):
        ax, ay = points[index - 1]
        bx, by = points[index]
        distance = math.hypot(bx - ax, by - ay)
        if distance <= step:
            result.append(points[index])
            continue
        count = int(distance // step)
        for k in range(1, count + 1):
            ratio = k * step / distance
            result.append((ax + (bx - ax) * ratio, ay + (by - ay) * ratio))
        result.append(points[index])
    return result


def _point_in_obstacle(world, point) -> bool:
    for obstacle in getattr(world, "obstacles", []) or []:
        try:
            if obstacle.contains(point):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


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
    spacing = max(8.0, swath * 0.75)
    vertices = getattr(target, "vertices", None) or []
    if str(getattr(target, "type", "")).lower() == "polygon" and len(vertices) >= 3:
        polygon = [(float(v["x"]), float(v["y"])) for v in vertices]
        points = coverage.serpentine_polygon(polygon, spacing)
        points = _densify(points, 20.0)
        lanes = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
        estimate = coverage.estimate_coverage_polygon(polygon, lanes, swath)
    else:
        center = getattr(target, "center", (0.0, 0.0))
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        points = coverage.serpentine_route((float(center[0]), float(center[1])), radius, spacing)
        points = _densify(points, 20.0)
        lanes = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
        estimate = coverage.estimate_coverage((float(center[0]), float(center[1])),
                                             radius, lanes, swath)
    if not points:
        return [], {"coverage_error": "目标 %s 无法生成覆盖航线" % name}
    projected = []
    for point in points:
        free = world.free_point_near(point, clearance=5.0, max_radius=80.0)
        if free is not None:
            projected.append(free)
    points = projected
    if not points:
        return [], {"coverage_error": "目标 %s 的覆盖航点全部被障碍物占据" % name}
    waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": altitude} for p in points]
    threshold_match = THRESHOLD_RE.search(task_text or "")
    threshold = float(threshold_match.group(1)) / 100.0 if threshold_match else 0.9
    return waypoints, {"coverage_target": name, "coverage_threshold": threshold,
                       "coverage_spacing": spacing, "coverage_swath": swath,
                       "estimated_coverage": round(estimate, 4)}


DRONE_NAME_RE = re.compile(r"Drone\s+(\d+)", re.I)
DIRECTION_RE = re.compile(r"(north|south|east|west)\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*m", re.I)
DIRECTION_HEADING = {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}


def _formation_spec(task_text: str, altitude: float):
    """解析“多机编队就位 + 保持 + 定向位移”任务。无法识别返回 None。"""
    text = task_text or ""
    if "formation" not in text.lower():
        return None
    directions = [(DIRECTION_HEADING[m.group(1).lower()], float(m.group(2)))
                  for m in DIRECTION_RE.finditer(text)]
    if not directions:
        return None
    alt_match = re.search(r"take\s+off[^.]*?to\s+(\d+(?:\.\d+)?)\s*m", text, re.I)
    alt = float(alt_match.group(1)) if alt_match else altitude

    prefix = text.split("take off", 1)[0] if "take off" in text.lower() else text
    drone_names = []
    for match in DRONE_NAME_RE.finditer(prefix):
        name = "Drone %s" % match.group(1)
        if name not in drone_names:
            drone_names.append(name)
    if not drone_names:
        return None

    positions = parse_task_waypoints(text)
    if len(positions) < 1:
        return None

    actions: list[dict] = []
    for drone in drone_names:
        actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
    for index, drone in enumerate(drone_names):
        if index >= len(positions):
            break
        point = positions[index]
        actions.append({"name": "move_to", "params": {"x": point["x"], "y": point["y"],
                                                      "z": alt, "drone_id": drone}})
    for heading, distance in directions:
        for drone in drone_names:
            actions.append({"name": "move_direction",
                            "params": {"heading": heading, "distance": distance,
                                       "z": alt, "drone_id": drone}})
    for drone in drone_names:
        actions.append({"name": "hover", "params": {"drone_id": drone}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "formation": {"drones": drone_names,
                          "positions": positions[:len(drone_names)],
                          "directions": directions}, "task_text": text}


TARGET_NAME_RE = re.compile(r"(polygon|circle|ellipse)\s+target\s*(\d+)", re.I)


def _multitarget_spec(task_text: str, world, altitude: float, land: bool):
    """顺序多目标访问：每目标 导航 -> 悬停 -> 拍照。无法识别返回 None。"""
    text = task_text or ""
    names = []
    for match in TARGET_NAME_RE.finditer(text):
        name = "%s Target %s" % (match.group(1).capitalize(), match.group(2))
        if name not in names:
            names.append(name)
    if len(names) < 2 or world is None:
        return None
    if not re.search(r"visit|in sequence|sequential|multi-target|at each target", text, re.I):
        return None
    alt_match = re.search(r"take\s+off[^.]*?to\s+(\d+(?:\.\d+)?)\s*m", text, re.I)
    alt = float(alt_match.group(1)) if alt_match else altitude
    hover_match = re.search(r"hover(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*s", text, re.I)
    hover_seconds = float(hover_match.group(1)) if hover_match else 5.0

    actions: list[dict] = []
    visits = []
    for name in names:
        target = None
        for candidate in world.targets:
            if str(candidate.name).lower() == name.lower():
                target = candidate
                break
        if target is None:
            continue
        center = target.center
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        tolerance = max(5.0, min(radius * 0.5, 15.0)) if radius > 2 else 12.0
        visits.append({"target": name, "center": [float(center[0]), float(center[1])],
                       "radius": radius})
        actions.append({"name": "navigate_to_pose",
                        "params": {"x": float(center[0]), "y": float(center[1]),
                                   "z": alt, "tolerance": tolerance}})
        actions.append({"name": "hover", "params": {"duration": hover_seconds}})
        actions.append({"name": "take_photo", "params": {}})
    if not visits:
        return None
    return {"actions": actions, "altitude": alt, "land_at_end": land,
            "visits": visits, "photo_count": len(visits), "task_text": text}


def _misc_spec(task_text: str, world, altitude: float, land: bool):
    """关键词驱动的其它模板：巡检/拍照文档/通信中继/悬停观察/旋转测试。"""
    text = task_text or ""
    lower = text.lower()
    alt_match = re.search(r"take\s+off[^.]*?to\s+(\d+(?:\.\d+)?)\s*m", text, re.I)
    alt = float(alt_match.group(1)) if alt_match else altitude
    points = parse_task_waypoints(text)
    names: list[str] = []
    for match in TARGET_NAME_RE.finditer(text):
        name = "%s Target %s" % (match.group(1).capitalize(), match.group(2))
        if name not in names:
            names.append(name)

    # 1) 目标巡检（含单个目标；文本可能是 "navigate to target X ... take a photo"）
    if names and re.search(r"inspect|navigate\s+to\s+target|observe\s+target|take\s+photos?", lower):
        actions = [{"name": "inspect_target", "params": {"target": name}} for name in names]
        return {"actions": actions, "altitude": alt, "land_at_end": land,
                "photo_count": len(names), "task_text": text}

    # 2) 拍照文档（每个航点：可选悬停 + 一张照片）
    if points and re.search(r"photo|photograph|image|camera", lower):
        hover_match = re.search(r"hover(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(?:s\b|seconds?)",
                                text, re.I)
        hover_seconds = float(hover_match.group(1)) if hover_match else 0.0
        actions = []
        for point in points:
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": point["x"], "y": point["y"],
                                       "z": point.get("z") or alt}})
            if hover_seconds > 0 or "hover" in lower:
                actions.append({"name": "hover", "params": {"duration": hover_seconds or 5.0}})
            actions.append({"name": "take_photo", "params": {}})
        return {"actions": actions, "altitude": alt, "land_at_end": land,
                "photo_count": len(points), "task_text": text}

    # 3) 通信中继（到位广播）
    if points and re.search(r"relay|broadcast|message", lower):
        quoted = re.search(r'message[:\s]*["\u201c]([^"\u201d]{2,120})["\u201d]', text, re.I)
        content = quoted.group(1) if quoted else "relay online"
        actions = []
        for point in points:
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": point["x"], "y": point["y"],
                                       "z": point.get("z") or alt}})
            actions.append({"name": "broadcast", "params": {"content": content}})
        return {"actions": actions, "altitude": alt, "land_at_end": land, "task_text": text}

    # 4) 悬停观察（到点后按指定时长悬停）
    if points and re.search(r"hover", lower):
        duration_match = re.search(r"hover(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*(?:s\b|seconds?)",
                                   text, re.I)
        duration = float(duration_match.group(1)) if duration_match else 5.0
        actions = []
        for point in points:
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": point["x"], "y": point["y"],
                                       "z": point.get("z") or alt}})
            actions.append({"name": "hover", "params": {"duration": duration}})
        return {"actions": actions, "altitude": alt, "land_at_end": land, "task_text": text}

    # 5) 旋转测试
    if re.search(r"rotat|heading", lower):
        heading_match = re.search(r"heading\s*(\d+(?:\.\d+)?)\s*(?:degrees|deg|°)", text, re.I)
        if heading_match:
            actions = [{"name": "rotate", "params": {"heading": float(heading_match.group(1))}},
                       {"name": "hover", "params": {}}]
            return {"actions": actions, "altitude": alt, "land_at_end": land, "task_text": text}

    return None


TARGET_ANY_RE = re.compile(
    r"((?:polygon|circle|ellipse|fixed|waypoint)\s+target\s*\d+(?:\s*\d+)?)", re.I)


def _world_targets_in_text(world, text: str) -> list:
    """按文本出现顺序返回地图里被提到的目标对象。"""
    if world is None:
        return []
    found = []
    for target in getattr(world, "targets", []):
        name = str(getattr(target, "name", ""))
        if name and re.search(re.escape(name), text, re.I):
            pos = re.search(re.escape(name), text, re.I).start()
            found.append((pos, target))
    return [t for _, t in sorted(found, key=lambda item: item[0])]


def _fleet_spec(task_text: str, world, altitude: float, land: bool):
    """多机模板：目标分配/编队扫掠/守护点/协同搜索/按机拍照。"""
    text = task_text or ""
    lower = text.lower()
    alt_match = re.search(r"take\s+off[^.]*?to\s+(\d+(?:\.\d+)?)\s*m", text, re.I)
    alt = float(alt_match.group(1)) if alt_match else altitude
    drone_names = []
    for match in DRONE_NAME_RE.finditer(text):
        name = "Drone %s" % match.group(1)
        if name not in drone_names:
            drone_names.append(name)

    # 1) 守护点部署：Drone X at guard point (x, y, z)
    guards = re.findall(r"(Drone\s+\d+)[^()]{0,30}?\(([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\)",
                        text, re.I)
    if guards and re.search(r"guard point|perimeter|deploy", lower):
        actions = []
        for drone, gx, gy, gz in guards:
            actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": float(gx), "y": float(gy), "z": float(gz),
                                       "drone_id": drone}})
            actions.append({"name": "hover", "params": {"drone_id": drone}})
            actions.append({"name": "take_photo", "params": {"drone_id": drone}})
        return {"actions": actions, "altitude": alt, "land_at_end": False,
                "photo_count": len(guards), "task_text": text}

    # 2) 每机拍照巡检：Drone 5 photographs targets A and B / Drone 5 surveys target X
    assignment_re = re.compile(
        r"(Drone\s+\d+)\s+(?:photographs?|surveys?|inspects?|visits?)\s+(?:targets?\s+)?"
        r"(.+?)(?=(?:Drone\s+\d+\s+(?:photographs?|surveys?|inspects?|visits?))|$)",
        re.I | re.S)
    matches = assignment_re.findall(text)
    if matches and re.search(r"photograph|survey|inspect", lower):
        actions = []
        photos = 0
        for drone, tail in matches:
            names = [m.group(0) for m in TARGET_ANY_RE.finditer(tail)]
            if not names:
                targets = _world_targets_in_text(world, tail)
                names = [str(t.name) for t in targets]
            if not names:
                continue
            actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
            for name in names:
                actions.append({"name": "inspect_target",
                                "params": {"target": name, "drone_id": drone}})
                photos += 1
        if actions:
            return {"actions": actions, "altitude": alt, "land_at_end": land,
                    "photo_count": photos, "task_text": text}

    # 3) 编队扫掠：Drone 4 flies to target X + 定向位移
    pair_re = re.compile(
        r"(Drone\s+\d+)\s+(?:flies|fly|moves|navigates|will fly)\s+to\s+target\s+"
        r"((?:polygon|circle|ellipse|fixed|waypoint)\s+target\s*\d+)", re.I)
    pairs = pair_re.findall(text)
    directions = [(DIRECTION_HEADING[m.group(1).lower()], float(m.group(2)))
                  for m in DIRECTION_RE.finditer(text)]
    if pairs and directions:
        actions = []
        for drone in drone_names:
            actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
        for drone, target_name in pairs:
            target = None
            for candidate in getattr(world, "targets", []) if world else []:
                if str(candidate.name).lower() == str(target_name).strip().lower():
                    target = candidate
                    break
            if target is None:
                continue
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": float(target.center[0]), "y": float(target.center[1]),
                                       "z": alt, "drone_id": drone}})
        for heading, distance in directions:
            for drone in drone_names:
                actions.append({"name": "move_direction",
                                "params": {"heading": heading, "distance": distance,
                                           "z": alt, "drone_id": drone}})
        return {"actions": actions, "altitude": alt, "land_at_end": False, "task_text": text}

    # 4) 协同搜索扫掠：N 机并行覆盖同一目标（蛇形航线交错分配）
    sweep_match = re.search(r"coordinated search sweep of target area (.+?)(?:\.|$)", text, re.I)
    if sweep_match and re.search(r"sweep|parallel formation", lower):
        target = _world_targets_in_text(world, sweep_match.group(1))
        if target:
            actions = []
            for drone in drone_names:
                actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
            actions.append({"name": "coordinated_sweep",
                            "params": {"target": str(target[0].name), "drones": drone_names}})
            return {"actions": actions, "altitude": alt, "land_at_end": False, "task_text": text}

    # 5) 目标分配（take off necessary drones ... reach required targets ...）
    if re.search(r"assign|required targets|task progress", lower):
        targets = _world_targets_in_text(world, text)
        if targets:
            actions = [{"name": "take_off", "params": {"altitude": alt}}]
            for target in targets:
                actions.append({"name": "navigate_to_pose",
                                "params": {"x": float(target.center[0]),
                                           "y": float(target.center[1]), "z": alt,
                                           "tolerance": max(5.0, min(
                                               float(getattr(target, "radius", 0.0) or 0.0) * 0.5, 15.0))}})
            return {"actions": actions, "altitude": alt, "land_at_end": land,
                    "task_text": text}

    return None


def build_spec_from_task(task_text: str, altitude: float, land: bool = True,
                         world=None) -> dict:
    formation = _formation_spec(task_text, altitude)
    if formation is not None:
        return formation
    multitarget = _multitarget_spec(task_text, world, altitude, land)
    if multitarget is not None:
        return multitarget
    fleet = _fleet_spec(task_text, world, altitude, land)
    if fleet is not None:
        return fleet
    misc = _misc_spec(task_text, world, altitude, land)
    if misc is not None:
        return misc
    # 多目标覆盖搜索：Any Drone / Two-Target Area Coverage Search
    coverage_names: list[str] = []
    for match in COVERAGE_TARGET_RE.finditer(task_text or ""):
        name = "%s Target %s" % (match.group(1).capitalize(), match.group(2))
        if name not in coverage_names:
            coverage_names.append(name)
    if len(coverage_names) >= 2 and re.search(r"coverage|area search", task_text or "", re.I):
        actions = [{"name": "coverage_search",
                    "params": {"target": name, "photos": 1}} for name in coverage_names]
        return {"actions": actions, "altitude": altitude, "land_at_end": land,
                "photo_count": len(coverage_names), "task_text": task_text}

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

    if isinstance(spec, dict) and spec.get("actions"):
        from .bt_defs import build_mission

        tree, steps = build_mission(world, spec, float(spec.get("altitude") or 32.0))
        print("[mission] actions 模式，共 {} 步".format(len(steps)))
        for index, step in enumerate(steps, 1):
            print("  {:2d}. {} {}".format(index, step["kind"],
                                          {k: v for k, v in step["params"].items()
                                           if k in ("drone_id", "x", "y", "z", "heading", "distance")}))
        if dry_run:
            print("[mission] --dry-run，不执行")
            return 0
        result = execute_actions(map_path, drone_id, spec, api_base, config_path)
        print("[mission] ok={} steps={} failures={}".format(
            result["ok"], len(result.get("steps") or []), len(result.get("failures") or [])))
        for item in result.get("executed") or []:
            print("   -", item)
        return 0 if result["ok"] else 2

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
    """按行为树模板执行任务（L2 编排 + L3 原子动作）。"""
    from .bt import Status
    from .bt_defs import build_mission

    world = WorldMap.load(map_path)
    api = UavApi(api_base)
    config = load_config(config_path)
    altitude = float(spec.get("altitude") or 32.0)
    tree, steps = build_mission(world, spec, altitude)
    if not steps:
        return {"ok": False, "mode": "actions", "steps": [], "executed": [],
                "failures": [{"error": "没有可执行动作"}]}
    blackboard = {"api": api, "drone_id": drone_id, "results": [],
                  "world": world, "clearance": float(spec.get("clearance") or config.clearance)}
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

    # 电量预检：按总航程估算（0.006%/m + 起飞 0.5%），不足则提前失败并建议换机
    try:
        total_length = 0.0
        previous = None
        for waypoint in waypoints:
            point = (float(waypoint["x"]), float(waypoint["y"]))
            if previous is not None:
                total_length += math.hypot(point[0] - previous[0], point[1] - previous[1])
            previous = point
        drone_info = api.drone(drone_id) or {}
        battery = float(drone_info.get("battery_level") or 100.0)
        required = total_length * 0.006 + 0.5
        if required > max(0.0, battery - 2.0):
            result["ok"] = False
            result["mode"] = "battery_preflight"
            result["failures"] = [{
                "error": "电量不足：预计需要 %.1f%%（航程 %.0f m），当前 %.1f%%" % (required, total_length, battery),
                "suggestion": "换电量更高的无人机，或重置会话恢复电量",
            }]
            return result
    except Exception:  # noqa: BLE001
        pass

    # 预检：障碍内航点投影到最近自由点后继续执行（不整单中止）
    adjusted = []
    problems = []
    try:
        probe = _Planner(world, clearance=config.clearance, grid_step=config.grid_step)
        for index, waypoint in enumerate(waypoints, 1):
            point = (float(waypoint["x"]), float(waypoint["y"]))
            z = float(waypoint.get("z", altitude))
            # 仅当航点真正落在障碍内部（净空≈0）才需要调整；离障碍很近但仍在外面的点可以飞
            clearance = world.clearance_to_obstacles(point)
            if clearance > 1.0:
                adjusted.append(waypoint)
                continue
            free = world.free_point_near(point, 2.0, 40.0)
            if free is None:
                problems.append({"leg": index, "goal": list(point),
                                 "error": "航点在障碍物内且无法投影到自由点"})
                adjusted.append(waypoint)
                continue
            moved = math.hypot(free[0] - point[0], free[1] - point[1])
            adjusted.append({"x": round(free[0], 2), "y": round(free[1], 2), "z": z})
            if moved > 3.0:
                problems.append({"leg": index, "goal": list(point),
                                 "projected": [round(free[0], 2), round(free[1], 2)],
                                 "error": "航点在障碍物内，已投影 %.1f m（原位置仍不可达）" % moved})
    except Exception:  # noqa: BLE001
        adjusted = list(waypoints)
        problems = []
    if problems:
        result["ok"] = False
        result["failures"].extend(problems)
        result["projected_count"] = len(problems)
    waypoints = adjusted

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
        # 每段都从无人机真实位置开始规划（避免用旧目标点导致直线穿障）
        live = api.drone(drone_id).get("position") or {}
        if "x" in live and "y" in live:
            current = (float(live["x"]), float(live["y"]))
        leg_clearance = None
        try:
            goal_clearance = world.clearance_to_obstacles(goal)
            if goal_clearance < config.clearance * 0.5:
                leg_clearance = max(2.0, goal_clearance * 0.6)
        except Exception:  # noqa: BLE001
            leg_clearance = None
        try:
            route = planner.plan(current, goal, clearance=leg_clearance, altitude=z)
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
        if error and ("blocked" in error.lower() or "intersect" in error.lower()):
            # 被服务端拒绝：用更大净空重新规划后重试一次
            try:
                retry_route = planner.plan(current, goal, clearance=min(config.clearance * 1.6, 90.0),
                                           altitude=z)
                retry_waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)}
                                   for p in (retry_route.waypoints_xyz[1:]
                                             or [(goal[0], goal[1], z)])]
                status, body = api.command(drone_id, "move_along_path",
                                           body={"waypoints": retry_waypoints,
                                                 "allow_partial_move": False})
                if isinstance(body, dict) and body.get("status") != "error":
                    error = None
                    leg_waypoints = retry_waypoints
            except Exception:  # noqa: BLE001
                pass
        if error and "insufficient battery" in error.lower():
            record = {"leg": index, "goal": list(goal), "error": error,
                      "suggestion": "电量不足，任务提前终止；请换机或重置会话恢复电量"}
            result["executed"].append(record)
            result["failures"].append(record)
            result["ok"] = False
            result["terminated_reason"] = "insufficient_battery"
            _log(f"[battery] 终止于第 {index} 段：{error}")
            break
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
        if not args.map:
            print("需要 --map")
            return 2
        spec = build_spec_from_task(args.task, args.altitude, world=WorldMap.load(args.map))
    else:
        print("需要 --task 或 --spec")
        return 2
    if not args.map:
        print("需要 --map")
        return 2
    return run_mission(args.map, args.drone, spec, args.api, args.dry_run, args.config)


if __name__ == "__main__":
    sys.exit(main())
