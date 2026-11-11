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
import threading
import time
import urllib.error
import urllib.parse
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

_MISSION_TASK_LOCK = threading.Lock()
_ACTIVE_TASK = {"event": None, "count": 0}


def begin_mission_task(wait_seconds: float = 20.0):
    """开始新任务：取消旧任务并等待其退出；返回本任务的取消事件。"""
    with _MISSION_TASK_LOCK:
        previous = _ACTIVE_TASK.get("event")
        event = threading.Event()
        _ACTIVE_TASK["event"] = event
    if previous is not None:
        previous.set()
    deadline = time.time() + float(wait_seconds)
    while time.time() < deadline:
        with _MISSION_TASK_LOCK:
            if int(_ACTIVE_TASK.get("count") or 0) <= 0:
                break
        time.sleep(0.2)
    with _MISSION_TASK_LOCK:
        _ACTIVE_TASK["count"] = int(_ACTIVE_TASK.get("count") or 0) + 1
    return event


def end_mission_task(event) -> None:
    """结束任务：标记事件并释放互斥计数。"""
    with _MISSION_TASK_LOCK:
        if event is not None:
            event.set()
        _ACTIVE_TASK["count"] = max(0, int(_ACTIVE_TASK.get("count") or 0) - 1)
        if _ACTIVE_TASK.get("event") is event:
            _ACTIVE_TASK["event"] = None


def mission_cancelled(event) -> bool:
    return bool(event is not None and event.is_set())

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

    @staticmethod
    def _ref(drone_id) -> str:
        value = drone_id
        if isinstance(value, dict):
            value = value.get("drone_id") or value.get("id") or value.get("name") or ""
        return urllib.parse.quote(str(value or "").strip(), safe="")

    def drone(self, drone_id: str) -> dict:
        _, data = self._request("GET", "/drones/" + self._ref(drone_id))
        return data if isinstance(data, dict) else {}

    def target(self, target_id: str) -> dict:
        _, data = self._request("GET", "/targets/" + self._ref(target_id))
        return data if isinstance(data, dict) else {}

    def command(self, drone_id: str, command: str, *, params: dict | None = None,
                body: dict | None = None):
        ref = self._ref(drone_id)
        if params:
            query = urllib.parse.urlencode({key: value for key, value in params.items()})
            return self._request("POST", "/drones/{}/command/{}?{}".format(ref, command, query), body)
        return self._request("POST", "/drones/{}/command/{}".format(ref, command), body)

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


def _parse_altitude(text: str, default: float) -> float:
    match = re.search(r"take\s+off[^.\n]{0,40}?to\s+(\d+(?:\.\d+)?)\s*m", text or "", re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"(?:at|altitude\s+of)\s+(\d+(?:\.\d+)?)\s*(?:m\b|meters?)",
                      text or "", re.I)
    return float(match.group(1)) if match else default


def _formation_spec(task_text: str, altitude: float, world=None):
    """解析“多机编队就位 + 保持 + 定向位移”任务。无法识别返回 None。"""
    text = task_text or ""
    if "formation" not in text.lower():
        return None
    directions = [(DIRECTION_HEADING[m.group(1).lower()], float(m.group(2)))
                  for m in DIRECTION_RE.finditer(text)]
    # 去掉“整组重复描述”（例如括号里再次列出 north 74m、east 79m）
    if len(directions) >= 4 and len(directions) % 2 == 0:
        half = len(directions) // 2
        if directions[:half] == directions[half:]:
            directions = directions[:half]
    deduped: list[tuple[float, float]] = []
    for item in directions:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    directions = deduped
    alt = _parse_altitude(text, altitude)

    prefix = text.split("take off", 1)[0] if "take off" in text.lower() else text
    drone_names: list[str] = []
    if world is not None:
        for match in NAME_NUM_RE.finditer(prefix):
            token = "%s %s" % (match.group(1).strip(), match.group(2))
            classified = _classify_named(world, token)
            if classified is None or classified[0] != "drone":
                continue
            if classified[1] not in drone_names:
                drone_names.append(classified[1])
    if not drone_names and world is not None:
        drone_names = _named_drones_in_text(world, prefix)
    if not drone_names:
        for match in DRONE_NAME_RE.finditer(prefix):
            name = "Drone %s" % match.group(1)
            if name not in drone_names:
                drone_names.append(name)
    if not drone_names:
        return None

    positions = parse_task_waypoints(text)
    # 编队点优先按“Drone X ... at (x, y, z)”名字-坐标配对，避免把目标中心当成编队点
    pair_re = re.compile(
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+\d{1,4})\s*"
        r"(?:positions?|flies?|flys?|goes?|moves?)?\s*(?:at|to)\s*\(([^)]*)\)",
        re.I)
    paired: dict[str, dict] = {}
    for match in pair_re.finditer(text):
        raw_name = match.group(1).strip()
        if world is not None:
            classified = _classify_named(world, raw_name)
            if classified is None or classified[0] != "drone":
                continue
            drone = classified[1]
        else:
            drone = "Drone %s" % raw_name.split()[-1]
        values = [value for value in re.split(r"[,\s]+", match.group(2).strip()) if value]
        if len(values) < 2:
            continue
        try:
            paired[drone] = {"x": float(values[0]), "y": float(values[1]),
                             "z": float(values[2]) if len(values) > 2 else alt}
        except ValueError:
            continue
    # 目标中心坐标（around target at position (...)）不能当作编队点
    center_points: set[tuple[float, float]] = set()
    center_re = re.compile(r"target\s+(?:at\s+)?(?:position\s+)?\(([^)]*)\)", re.I)
    for match in center_re.finditer(text):
        values = [value for value in re.split(r"[,\s]+", match.group(1).strip()) if value]
        if len(values) >= 2:
            try:
                center_points.add((round(float(values[0]), 2), round(float(values[1]), 2)))
            except ValueError:
                continue
    used = {(round(p["x"], 2), round(p["y"], 2)) for p in paired.values()}
    pool = [p for p in positions
            if (round(float(p["x"]), 2), round(float(p["y"]), 2)) not in used
            and (round(float(p["x"]), 2), round(float(p["y"]), 2)) not in center_points]
    ordered: list[dict | None] = []
    for drone in drone_names:
        if drone in paired:
            ordered.append(paired[drone])
        elif pool:
            ordered.append(pool.pop(0))
        else:
            ordered.append(None)
    missing_count = sum(1 for point in ordered if point is None)
    if missing_count or len(ordered) != len(drone_names):
        return {"error": "编队解析失败：无人机 %d 架，但只找到 %d 个匹配的编队坐标；"
                          "请检查文本中每架机的位置是否写清"
                          % (len(drone_names), len(drone_names) - missing_count),
                "altitude": alt, "land_at_end": False}
    approach_target = None
    approach_distance = None
    approach_match = re.search(
        r"move\s+(?:to\s+)?(?:within\s+|at\s+most\s+|closer\s+(?:than\s+)?)*"
        r"(\d+(?:\.\d+)?)\s*(?:m\b|meters?)[^.]*?\b(?:to|of|from|near)\s+"
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+\d{1,4})", text, re.I)
    if approach_match:
        approach_distance = float(approach_match.group(1))
        raw_target = approach_match.group(2).strip()
        approach_target = None
        if world is not None:
            classified = _classify_named(world, raw_target)
            if classified is not None and classified[0] == "drone":
                approach_target = classified[1]
        if approach_target is None:
            approach_target = "Drone %s" % raw_target.split()[-1]

    actions: list[dict] = []
    for drone in drone_names:
        actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
    for drone, point in zip(drone_names, ordered):
        if point is None:
            continue
        actions.append({"name": "move_to", "params": {"x": point["x"], "y": point["y"],
                                                      "z": alt, "drone_id": drone}})
    if approach_target and len(drone_names) >= 2:
        moving = next((d for d in drone_names if d.lower() != approach_target.lower()), None)
        if moving:
            actions.append({"name": "approach_drone",
                            "params": {"drone_id": moving,
                                       "target_drone_id": approach_target,
                                       "max_distance": approach_distance,
                                       "z": alt}})
    for heading, distance in directions:
        for drone in drone_names:
            actions.append({"name": "move_direction",
                            "params": {"heading": heading, "distance": distance,
                                       "z": alt, "tolerance": 1.0, "drone_id": drone}})
    for drone in drone_names:
        actions.append({"name": "hover", "params": {"drone_id": drone}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "formation": {"drones": drone_names,
                          "positions": ordered,
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
    alt = _parse_altitude(text, altitude)
    hover_seconds = _hover_seconds(text)

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


def _named_position_spec(task_text: str, world, altitude: float, land: bool):
    """多机各自前往文本中指定的坐标点（guard point / position / waypoint 等）。"""
    if world is None:
        return None
    text = task_text or ""
    if not re.search(r"position|deploy|guard|station|place|assign|patrol|search|move",
                     text, re.I):
        return None
    mentions: list[tuple[str, int, int]] = []
    for match in NAME_NUM_RE.finditer(text):
        token = "%s %s" % (match.group(1).strip(), match.group(2))
        classified = _classify_named(world, token)
        if classified is None or classified[0] != "drone":
            continue
        name = classified[1]
        if any(item[0] == name for item in mentions):
            continue
        mentions.append((name, match.start(), match.end()))
    for name in _named_drones_in_text(world, text):
        if any(item[0] == name for item in mentions):
            continue
        position = text.lower().find(name.lower())
        mentions.append((name, max(0, position), max(0, position) + len(name)))
    if len(mentions) < 2:
        return None
    pairs_by_name: dict[str, tuple[float, float, float]] = {}
    for coord in re.finditer(r"\(([^)]*)\)", text):
        values = [value for value in re.split(r"[,\s]+", coord.group(1).strip()) if value]
        if len(values) < 2:
            continue
        try:
            x = float(values[0])
            y = float(values[1])
            z = float(values[2]) if len(values) > 2 else altitude
        except ValueError:
            continue
        prefix = text[max(0, coord.start() - 90):coord.start()]
        nearest = None
        nearest_pos = -1
        for match in NAME_NUM_RE.finditer(prefix):
            token = "%s %s" % (match.group(1).strip(), match.group(2))
            classified = _classify_named(world, token)
            if classified is not None and classified[0] == "drone":
                if match.start() >= nearest_pos:
                    nearest_pos = match.start()
                    nearest = classified[1]
        for name in _named_drones_in_text(world, prefix):
            position = prefix.lower().rfind(name.lower())
            if position > nearest_pos:
                nearest_pos = position
                nearest = name
        if nearest is None or nearest in pairs_by_name:
            continue
        pairs_by_name[nearest] = (x, y, z)
    pairs = [(name, x, y, z) for name, (x, y, z) in pairs_by_name.items()]
    if len(pairs) < 2:
        return None
    hover = bool(re.search(r"hover|maintain|monitor|search|patrol|observe", text, re.I))
    photo = bool(re.search(r"photo|photograph|capture|image", text, re.I))
    alt = _parse_altitude(text, altitude)
    actions: list[dict] = []
    for name, _x, _y, _z in pairs:
        actions.append({"name": "take_off",
                        "params": {"altitude": alt, "drone_id": name}})
    for name, x, y, z in pairs:
        actions.append({"name": "move_to",
                        "params": {"x": round(x, 2), "y": round(y, 2), "z": z,
                                   "drone_id": name}})
        if hover or photo:
            actions.append({"name": "hover", "params": {"drone_id": name}})
        if photo:
            actions.append({"name": "take_photo", "params": {"drone_id": name}})
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        for name, _x, _y, _z in pairs:
            actions.append({"name": "land", "params": {"drone_id": name}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "named_positions": [{"drone": name, "x": x, "y": y, "z": z}
                                for name, x, y, z in pairs],
            "task_text": text}


def _misc_spec(task_text: str, world, altitude: float, land: bool):
    """关键词驱动的其它模板：巡检/拍照文档/通信中继/悬停观察/旋转测试。"""
    text = task_text or ""
    lower = text.lower()
    alt = _parse_altitude(text, altitude)
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
        hover_seconds = _hover_seconds(text, default=5.0)
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
        duration = _hover_seconds(text, default=5.0)
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
    alt = _parse_altitude(text, altitude)
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
            fleet_drones = _named_drones_in_text(world, text)
            actions: list[dict] = []
            if fleet_drones:
                for drone in fleet_drones:
                    actions.append({"name": "take_off",
                                    "params": {"altitude": alt, "drone_id": drone}})
                for index, target in enumerate(targets):
                    drone = fleet_drones[index % len(fleet_drones)]
                    actions.append({"name": "navigate_to_pose",
                                    "params": {"x": float(target.center[0]),
                                               "y": float(target.center[1]), "z": alt,
                                               "drone_id": drone,
                                               "tolerance": max(5.0, min(
                                                   float(getattr(target, "radius", 0.0) or 0.0) * 0.5,
                                                   15.0))}})
            else:
                actions.append({"name": "take_off", "params": {"altitude": alt}})
                for target in targets:
                    actions.append({"name": "navigate_to_pose",
                                    "params": {"x": float(target.center[0]),
                                               "y": float(target.center[1]), "z": alt,
                                               "tolerance": max(5.0, min(
                                                   float(getattr(target, "radius", 0.0) or 0.0) * 0.5,
                                                   15.0))}})
            return {"actions": actions, "altitude": alt, "land_at_end": land,
                    "task_text": text}

    return None


ROUTE_HEADER_RE = re.compile(r"([A-Za-z][A-Za-z0-9 _-]*?)\s+route\s*:", re.I)


def _dual_route_spec(task_text: str, world, altitude: float, land: bool) -> dict | None:
    """解析“Drone A route: … Drone B route: …”式双机任务，生成逐机动作。"""
    headers = list(ROUTE_HEADER_RE.finditer(task_text or ""))
    if len(headers) < 2:
        return None
    actions: list[dict] = []
    for index, match in enumerate(headers):
        drone = re.sub(r"(?i)^(and|then|finally|the)\s+", "", match.group(1).strip())
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(task_text)
        segment = task_text[start:end]
        points = parse_task_waypoints(segment)
        if not points:
            continue
        actions.append({"kind": "take_off",
                        "params": {"drone_id": drone, "altitude": altitude}})
        for point in points:
            actions.append({"kind": "move_to",
                            "params": {"drone_id": drone, "x": point["x"], "y": point["y"],
                                       "z": point["z"]}})
        wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", segment, re.I))
        if wants_land or land:
            actions.append({"kind": "land", "params": {"drone_id": drone}})
    if not actions:
        return None
    return {"actions": actions, "altitude": altitude, "land_at_end": False,
            "task_text": task_text, "multi_drone": True}


def _hover_seconds(text: str, default: float = 5.0) -> float:
    """解析“hover … 11 seconds”式要求；允许 hover 与数字之间还有其他描述。"""
    match = re.search(r"hover[^.\n]{0,80}?(\d+(?:\.\d+)?)\s*(?:s\b|seconds?|sec\b)",
                      text or "", re.I)
    return float(match.group(1)) if match else default


NAMED_TARGET_RE = re.compile(
    r"\btargets?\b[^A-Za-z0-9]{0,3}([A-Za-z][A-Za-z0-9 _-]*?\b\d{1,3}\b)", re.I)


def _resolve_target_phrase(world, phrase: str):
    """把短语解析成地图里的目标（精确优先，其次包含匹配）。"""
    phrase = " ".join((phrase or "").split()).strip(" .,:;")
    if not phrase:
        return None
    direct = world.target_by_name(phrase)
    if direct is not None:
        return direct
    lowered = phrase.lower()
    for target in getattr(world, "targets", []) or []:
        name = str(getattr(target, "name", "")).strip().lower()
        if not name:
            continue
        if name == lowered or lowered in name or name in lowered:
            return target
    return None


def _named_targets_in_text(world, text: str):
    """收集文本中所有可识别的具名目标（支持有编号/无编号名称，按出现顺序去重）。"""
    if world is None:
        return []
    source = text or ""
    found: list[tuple[int, object]] = []
    seen: set[str] = set()

    def add(position: int, target) -> None:
        key = str(getattr(target, "id", "") or getattr(target, "name", ""))
        if key in seen:
            return
        seen.add(key)
        found.append((position, target))

    for match in NAME_NUM_RE.finditer(source):
        token = "%s %s" % (match.group(1).strip(), match.group(2))
        classified = _classify_named(world, token)
        if classified is None or classified[0] != "target":
            continue
        target = world.target_by_name(classified[1])
        if target is not None:
            add(match.start(), target)
    for keyword in re.finditer(r"\btargets?\b", source, re.I):
        tail = source[keyword.end():]
        stop = len(tail)
        for separator in (".", ";", "。", "；", "\n"):
            index = tail.find(separator)
            if index != -1:
                stop = min(stop, index)
        clause = tail[:stop]
        clause = re.sub(r"^\s*(?:are|is|be|will\s+be|:)\s*", "", clause, flags=re.I)
        offset = keyword.end()
        for part in re.split(r",|\band\b|/|&", clause):
            phrase = re.sub(r"^\s*(?:the\s+|a\s+|an\s+)?", "", part.strip(), flags=re.I)
            phrase = re.sub(
                r"\b(?:photographs?|captures?|inspects?|observes?|visits?|reaches?|"
                r"flies\s+to|fly\s+to|goes\s+to|go\s+to)\b", " ", phrase, flags=re.I)
            target = _resolve_target_phrase(world, phrase)
            if target is not None:
                add(offset, target)
            offset += len(part) + 1
    for target in getattr(world, "targets", []) or []:
        name = str(getattr(target, "name", "")).strip()
        if not name:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                             re.I)
        match = pattern.search(source)
        if match is not None:
            add(match.start(), target)
    found.sort(key=lambda item: item[0])
    return [target for _position, target in found]


def _named_drones_in_text(world, text: str):
    """按出现顺序返回文本中提到的、地图可识别的无人机名（支持无编号命名）。"""
    if world is None:
        return []
    source = text or ""
    target_spans: list[tuple[int, int]] = []
    for target in getattr(world, "targets", []) or []:
        target_name = str(getattr(target, "name", "")).strip()
        if not target_name:
            continue
        target_pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(target_name) + r"(?![A-Za-z0-9])", re.I)
        for match in target_pattern.finditer(source):
            target_spans.append((match.start(), match.end()))
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for drone in getattr(world, "drones", []) or []:
        name = str(drone.get("name") or "").strip()
        if not name:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                             re.I)
        chosen = None
        for match in pattern.finditer(source):
            if any(start <= match.start() and match.end() <= end
                   for start, end in target_spans):
                continue
            chosen = match
            break
        if chosen is None:
            continue
        key = str(drone.get("id") or name)
        if key in seen:
            continue
        seen.add(key)
        found.append((chosen.start(), name))
    found.sort(key=lambda item: item[0])
    return [name for _position, name in found]


def _is_moving_target(target) -> bool:
    """目标是否会移动（移动 courier 等）。"""
    return str(getattr(target, "type", "")).lower() == "moving" \
        or bool(getattr(target, "movement_mode", None))


def _target_spec(task_text: str, world, altitude: float, land: bool):
    """按目标名（如 target Bay 036）生成“起飞→逐个到目标→悬停→拍照→降落”动作。"""
    if world is None:
        return None
    text = task_text or ""
    drone_mentions = {match.group(0).lower() for match in
                      re.finditer(r"\b(?:drone|uav|yard|cargo)\s+\d{1,4}\b", text, re.I)}
    named_drones = _named_drones_in_text(world, text)
    if len(drone_mentions) > 1 or len(named_drones) > 1:
        return None
    if re.search(r"inspect|visit|in sequence|sequential|multi-target|at each target|"
                 r"sweep|coverage|search pattern|serpentine|\bgrid\b", text, re.I):
        return None
    photo = bool(re.search(r"photo|photograph|capture|image", text, re.I))
    if not photo and not re.search(r"navigate|fly|go to|head to|reach", text, re.I):
        return None
    targets: list[tuple[str, object]] = [
        (str(getattr(found, "name", "")), found)
        for found in _named_targets_in_text(world, text)]
    if not targets:
        return None
    hover_seconds = _hover_seconds(text, default=5.0)
    actions = [{"name": "take_off", "params": {"altitude": altitude}}]
    names: list[str] = []
    for candidate, target in targets:
        name = str(getattr(target, "name", candidate))
        if _is_moving_target(target):
            names.append(name)
            actions.append({"name": "track_target",
                            "params": {"target": name,
                                       "target_id": str(getattr(target, "id", "")),
                                       "z": altitude, "timeout": 90.0}})
            if photo:
                actions.append({"name": "hover", "params": {"duration": hover_seconds}})
                actions.append({"name": "take_photo", "params": {}})
            continue
        center = getattr(target, "center", (0.0, 0.0))
        x, y = float(center[0]), float(center[1])
        try:
            clearance = world.clearance_to_obstacles((x, y))
        except Exception:  # noqa: BLE001
            clearance = 99.0
        if clearance <= 0.0:
            free = world.free_point_near((x, y), 2.0, 60.0)
            if free is None:
                continue
            x, y = float(free[0]), float(free[1])
        names.append(name)
        actions.append({"name": "navigate_to_pose",
                        "params": {"x": round(x, 2), "y": round(y, 2), "z": altitude,
                                   "tolerance": 2.0}})
        if photo:
            actions.append({"name": "hover", "params": {"duration": hover_seconds}})
            actions.append({"name": "take_photo", "params": {}})
    if not names:
        return {"error": "文本中的目标都不可达（位于障碍物内部且附近无自由点）",
                "altitude": altitude, "land_at_end": False}
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        actions.append({"name": "land", "params": {}})
    return {"actions": actions, "altitude": altitude, "land_at_end": False,
            "targets": names, "photo_count": len(names) if photo else 0,
            "task_text": task_text}


NAME_NUM_RE = re.compile(r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+(\d{1,4})\b")


def _classify_named(world, token: str):
    """把 “Yard 8 / Fixed Target 1” 分类成 drone 或 target；支持前缀冗余（如 Drone Yard 8）。"""
    words = token.split()
    for start in range(0, max(1, len(words) - 1)):
        candidate = " ".join(words[start:])
        drone = world.drone_by_name(candidate)
        if isinstance(drone, dict) and drone.get("id"):
            return "drone", candidate, drone
        target = world.target_by_name(candidate)
        if target is not None:
            return "target", candidate, target
    return None


def _text_segments(text: str):
    """按逗号/分号/句号/换行切分文本，返回 (start_offset, segment)。"""
    segments = []
    previous = 0
    for separator in re.finditer(r"[,;.。；\n]", text or ""):
        segments.append((previous, (text or "")[previous:separator.start()]))
        previous = separator.end()
    segments.append((previous, (text or "")[previous:]))
    return segments


def _assignment_spec(task_text: str, world, altitude: float, land: bool):
    """通用任务分配：解析“某机→某目标（可含交换/第二阶段）”，生成按机分组动作。"""
    if world is None:
        return None
    text = task_text or ""
    if not re.search(r"find|swap|assign|exchange|goes? to|navigate|visit|reach|search|"
                     r"photograph|capture|inspect|observe", text, re.I):
        return None
    alt = _parse_altitude(text, altitude)
    swap_pos = None
    for marker in (r"\bswap\b", r"after the first discovery", r"afterwards",
                   r"then exchange", r"after (?:those|the|these) targets? (?:are|is) found",
                   r"after finding", r"第二阶段"):
        found = re.search(marker, text, re.I)
        if found:
            swap_pos = found.start()
            break
    assignments: list[tuple[int, str, str]] = []
    last_drone = None
    last_target = None
    for segment_start, segment in _text_segments(text):
        drones_in_clause: list[str] = []
        targets_in_clause: list[tuple[str, int]] = []
        for match in NAME_NUM_RE.finditer(segment):
            token = "%s %s" % (match.group(1).strip(), match.group(2))
            classified = _classify_named(world, token)
            if classified is None or classified[0] != "drone":
                continue
            name = classified[1]
            if name not in drones_in_clause:
                drones_in_clause.append(name)
        for name in _named_drones_in_text(world, segment):
            if name not in drones_in_clause:
                drones_in_clause.append(name)
        drones_in_clause.sort(key=lambda item: segment.lower().find(item.lower()))
        targets_in_clause = [(str(getattr(target, "name", "")), segment_start)
                             for target in _named_targets_in_text(world, segment)]
        if not targets_in_clause and last_target and drones_in_clause and re.search(
                r"same target|that target|the target|same destination|同一目标|该目标",
                segment, re.I):
            targets_in_clause = [(last_target, segment_start)]
        for index, (target_name, position) in enumerate(targets_in_clause):
            if index < len(drones_in_clause):
                drone = drones_in_clause[index]
            elif drones_in_clause:
                drone = drones_in_clause[-1]
            else:
                drone = last_drone
            if not drone:
                continue
            phase = 2 if (swap_pos is not None and position >= swap_pos) else 1
            assignments.append((phase, drone, target_name))
        if targets_in_clause:
            last_target = targets_in_clause[-1][0]
        if drones_in_clause:
            last_drone = drones_in_clause[-1]
    if len(assignments) < 2:
        return None
    drones: list[str] = []
    for _, drone, _ in assignments:
        if drone not in drones:
            drones.append(drone)
    if len(drones) < 2:
        return None
    photo = bool(re.search(r"photo|photograph|capture|image", text, re.I))
    hover_seconds = _hover_seconds(text, default=5.0)
    actions: list[dict] = []
    for drone in drones:
        actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
    for phase in (1, 2):
        for assign_phase, drone, target_name in assignments:
            if assign_phase != phase:
                continue
            target = world.target_by_name(target_name)
            if target is None:
                continue
            center = getattr(target, "center", (0.0, 0.0))
            x, y = float(center[0]), float(center[1])
            try:
                clearance = world.clearance_to_obstacles((x, y))
            except Exception:  # noqa: BLE001
                clearance = 99.0
            if clearance <= 0.0:
                free = world.free_point_near((x, y), 2.0, 60.0)
                if free is None:
                    continue
                x, y = float(free[0]), float(free[1])
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": round(x, 2), "y": round(y, 2), "z": alt,
                                       "tolerance": 2.0, "drone_id": drone}})
            if photo:
                actions.append({"name": "hover",
                                "params": {"duration": hover_seconds, "drone_id": drone}})
                actions.append({"name": "take_photo", "params": {"drone_id": drone}})
    if len([item for item in actions if item.get("name") == "navigate_to_pose"]) < 2:
        return None
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        for drone in drones:
            actions.append({"name": "land", "params": {"drone_id": drone}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "assignment": True, "drones": drones, "task_text": task_text}
def _shared_target_spec(task_text: str, world, altitude: float, land: bool):
    """多机共同前往同一（或更少）目标：每架机都飞到每个目标。"""
    if world is None:
        return None
    text = task_text or ""
    drones: list[str] = []
    for match in NAME_NUM_RE.finditer(text):
        token = "%s %s" % (match.group(1).strip(), match.group(2))
        classified = _classify_named(world, token)
        if classified is None or classified[0] != "drone":
            continue
        name = classified[1]
        if name not in drones:
            drones.append(name)
    for name in _named_drones_in_text(world, text):
        if name not in drones:
            drones.append(name)
    drones.sort(key=lambda item: text.lower().find(item.lower()))
    targets = [str(getattr(target, "name", ""))
               for target in _named_targets_in_text(world, text)]
    if len(drones) < 2 or not targets:
        return None
    shared_hint = bool(re.search(
        r"same target|both drones|all drones|jointly|identical target|共同目标|同一目标|都要|均需",
        text, re.I))
    if not (shared_hint or len(targets) < len(drones)):
        return None
    alt = _parse_altitude(text, altitude)
    photo = bool(re.search(r"photo|photograph|capture|image", text, re.I))
    hover_seconds = _hover_seconds(text, default=5.0)
    actions: list[dict] = []
    for drone in drones:
        actions.append({"name": "take_off", "params": {"altitude": alt, "drone_id": drone}})
    for name in targets:
        target = world.target_by_name(name)
        if target is None:
            continue
        center = getattr(target, "center", (0.0, 0.0))
        x, y = float(center[0]), float(center[1])
        try:
            clearance = world.clearance_to_obstacles((x, y))
        except Exception:  # noqa: BLE001
            clearance = 99.0
        if clearance <= 0.0:
            free = world.free_point_near((x, y), 2.0, 60.0)
            if free is None:
                continue
            x, y = float(free[0]), float(free[1])
        for drone in drones:
            actions.append({"name": "navigate_to_pose",
                            "params": {"x": round(x, 2), "y": round(y, 2), "z": alt,
                                       "tolerance": 2.0, "drone_id": drone}})
            if photo:
                actions.append({"name": "hover",
                                "params": {"duration": hover_seconds, "drone_id": drone}})
                actions.append({"name": "take_photo", "params": {"drone_id": drone}})
    if len([item for item in actions if item.get("name") == "navigate_to_pose"]) < 2:
        return None
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        for drone in drones:
            actions.append({"name": "land", "params": {"drone_id": drone}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "drones": drones, "targets": targets, "task_text": task_text}


def _order_named_targets(world, targets, drone_name: str = ""):
    """按最近邻 + 2-opt 优化具名目标访问顺序（起点可取无人机当前位置）。"""
    points = [(float(t.center[0]), float(t.center[1])) for t in targets]
    start = None
    if drone_name:
        drone = world.drone_by_name(drone_name)
        if isinstance(drone, dict):
            position = drone.get("position") or {}
            if position.get("x") is not None:
                start = (float(position["x"]), float(position["y"]))
    remaining = set(range(len(points)))
    order: list[int] = []
    current = start
    while remaining:
        if current is None:
            nxt = min(remaining)
        else:
            nxt = min(remaining,
                      key=lambda i: (points[i][0] - current[0]) ** 2 +
                      (points[i][1] - current[1]) ** 2)
        order.append(nxt)
        remaining.remove(nxt)
        current = points[nxt]

    def length(sequence):
        total = 0.0
        previous = start
        for index in sequence:
            if previous is not None:
                total += math.dist(previous, points[index])
            previous = points[index]
        return total

    improved = True
    while improved and len(order) > 2:
        improved = False
        best = length(order)
        for i in range(len(order) - 1):
            for j in range(i + 2, len(order)):
                candidate = (order[:i + 1] + list(reversed(order[i + 1:j + 1]))
                             + order[j + 1:])
                candidate_length = length(candidate)
                if candidate_length + 1e-9 < best:
                    order, best, improved = candidate, candidate_length, True
                    break
            if improved:
                break
    return [targets[index] for index in order]


def _multi_named_target_spec(task_text: str, world, altitude: float, land: bool):
    """单机（或未点名）依次访问多个具名目标：导航 → 悬停 → 拍照，可选路线优化。"""
    if world is None:
        return None
    text = task_text or ""
    targets = _named_targets_in_text(world, text)
    if len(targets) < 2:
        return None
    drones: list[str] = []
    for match in NAME_NUM_RE.finditer(text):
        token = "%s %s" % (match.group(1).strip(), match.group(2))
        classified = _classify_named(world, token)
        if classified is None or classified[0] != "drone":
            continue
        if classified[1] not in drones:
            drones.append(classified[1])
    for name in _named_drones_in_text(world, text):
        if name not in drones:
            drones.append(name)
    mentioned = {match.group(0).lower() for match in
                 re.finditer(r"\b(?:drone|uav|yard|cargo)\s+\d{1,4}\b", text, re.I)}
    if len(drones) > 1 or len(mentioned) > 1:
        return None
    if re.search(r"\b(?:fleet|drones|each drone|every drone|all drones)\b", text, re.I):
        return None
    optimize = bool(re.search(r"optimal|minimi[sz]e|shortest|best route|optimize|route",
                              text, re.I))
    ordered = (_order_named_targets(world, targets, drones[0] if drones else "")
               if optimize else list(targets))
    alt = _parse_altitude(text, altitude)
    photo = bool(re.search(r"photo|photograph|capture|image", text, re.I))
    wants_hover = bool(re.search(r"hover|observ|inspect", text, re.I))
    hover_seconds = _hover_seconds(text, default=5.0)
    actions: list[dict] = []
    takeoff = {"altitude": alt}
    if drones:
        takeoff["drone_id"] = drones[0]
    actions.append({"name": "take_off", "params": takeoff})
    for target in ordered:
        if _is_moving_target(target):
            track_params = {"target": str(getattr(target, "name", "")),
                            "target_id": str(getattr(target, "id", "")),
                            "z": alt, "timeout": 90.0}
            if drones:
                track_params["drone_id"] = drones[0]
            actions.append({"name": "track_target", "params": track_params})
            if photo:
                hover = {"duration": hover_seconds}
                shot = {}
                if drones:
                    hover["drone_id"] = drones[0]
                    shot["drone_id"] = drones[0]
                actions.append({"name": "hover", "params": hover})
                actions.append({"name": "take_photo", "params": shot})
            continue
        center = getattr(target, "center", (0.0, 0.0))
        x, y = float(center[0]), float(center[1])
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        try:
            clearance = world.clearance_to_obstacles((x, y))
        except Exception:  # noqa: BLE001
            clearance = 99.0
        if clearance <= 0.0:
            free = world.free_point_near((x, y), 2.0, 60.0)
            if free is None:
                continue
            x, y = float(free[0]), float(free[1])
        tolerance = max(2.0, min(radius * 0.9, 15.0)) if radius > 2 else 3.0
        params = {"x": round(x, 2), "y": round(y, 2), "z": alt, "tolerance": tolerance}
        if drones:
            params["drone_id"] = drones[0]
        actions.append({"name": "navigate_to_pose", "params": params})
        if wants_hover or photo:
            hover = {"duration": hover_seconds}
            if drones:
                hover["drone_id"] = drones[0]
            actions.append({"name": "hover", "params": hover})
        if photo:
            shot = {}
            if drones:
                shot["drone_id"] = drones[0]
            actions.append({"name": "take_photo", "params": shot})
    if len([item for item in actions
            if item.get("name") in ("navigate_to_pose", "track_target")]) < 2:
        return None
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        actions.append({"name": "land",
                        "params": {"drone_id": drones[0]} if drones else {}})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "targets": [str(getattr(target, "name", "")) for target in ordered],
            "optimized": optimize, "task_text": text}


def _targets_reach_spec(task_text: str, world, altitude: float, land: bool):
    """通用“到达目标列表”解析：多目标分布给无人机；带进度阈值时继续安排额外目标。"""
    if world is None:
        return None
    text = task_text or ""
    if not re.search(r"progress|assigned targets|assign them|required targets|session task|"
                     r"all targets|task radius|targets within", text, re.I):
        return None
    target_names: list[str] = []
    seen: set[str] = set()
    drone_names: list[str] = []
    for match in NAME_NUM_RE.finditer(text):
        token = "%s %s" % (match.group(1).strip(), match.group(2))
        classified = _classify_named(world, token)
        if classified is None:
            continue
        kind, name, _ = classified
        if kind == "target" and name not in seen:
            seen.add(name)
            target_names.append(name)
        elif kind == "drone" and name not in drone_names:
            drone_names.append(name)
    all_targets = [str(getattr(target, "name", "")) for target in
                   (getattr(world, "targets", []) or [])]
    wants_all_targets = bool(re.search(r"all targets|task radius|targets within", text, re.I))
    if len(target_names) < 2:
        if not (wants_all_targets and all_targets):
            return None
        target_names = list(all_targets)
        seen = set(target_names)
    if not drone_names:
        candidates = [drone for drone in (getattr(world, "drones", []) or [])
                      if isinstance(drone, dict)]
        candidates.sort(key=lambda drone: float(drone.get("battery_level") or 0.0),
                        reverse=True)
        idle = [drone for drone in candidates
                if str(drone.get("status") or "").lower() in ("idle", "landed", "ground")]
        selected = idle or candidates
        drone_names = [str(drone.get("name") or "") for drone in selected[:6]
                       if drone.get("name")]
    alt = _parse_altitude(text, altitude)
    progress_match = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:the\s+)?session\s+task\s+progress|"
        r"progress[^.%\n]{0,40}?(\d+(?:\.\d+)?)\s*%", text, re.I)
    threshold = None
    if progress_match:
        threshold = float(progress_match.group(1) or progress_match.group(2))
    wants_continue = threshold is not None and bool(
        re.search(r"continue|additional|until|do not exceed|not exceed|exceed", text, re.I))
    planned = list(target_names)
    if wants_continue and all_targets and threshold is not None:
        extras = [name for name in all_targets if name not in seen]
        while extras and (len(planned) / float(len(all_targets)) * 100.0) <= threshold:
            planned.append(extras.pop(0))
    actions: list[dict] = []
    for drone in (drone_names or [""]):
        params = {"altitude": alt}
        if drone:
            params["drone_id"] = drone
        actions.append({"name": "take_off", "params": params})
    for index, name in enumerate(planned):
        target = world.target_by_name(name)
        if target is None:
            continue
        if _is_moving_target(target):
            track_params = {"target": name,
                            "target_id": str(getattr(target, "id", "")),
                            "z": alt, "timeout": 90.0}
            if drone_names:
                track_params["drone_id"] = drone_names[index % len(drone_names)]
            actions.append({"name": "track_target", "params": track_params})
            continue
        center = getattr(target, "center", (0.0, 0.0))
        x, y = float(center[0]), float(center[1])
        try:
            clearance = world.clearance_to_obstacles((x, y))
        except Exception:  # noqa: BLE001
            clearance = 99.0
        if clearance <= 0.0:
            free = world.free_point_near((x, y), 2.0, 60.0)
            if free is None:
                continue
            x, y = float(free[0]), float(free[1])
        params = {"x": round(x, 2), "y": round(y, 2), "z": alt, "tolerance": 2.0}
        if drone_names:
            params["drone_id"] = drone_names[index % len(drone_names)]
        actions.append({"name": "navigate_to_pose", "params": params})
    if len([item for item in actions
            if item.get("name") in ("navigate_to_pose", "track_target")]) < 2:
        return None
    wants_land = bool(re.search(r"\blands?\b|\blanded\b|touch\s*down", text, re.I)) or land
    if wants_land:
        for drone in (drone_names or [""]):
            params = {}
            if drone:
                params["drone_id"] = drone
            actions.append({"name": "land", "params": params})
    return {"actions": actions, "altitude": alt, "land_at_end": False,
            "targets": planned, "progress_threshold": threshold,
            "drones": drone_names, "task_text": task_text}


def _build_spec_base(task_text: str, altitude: float, land: bool = True,
                         world=None) -> dict:
    # “Avoid obstacles X, Y”只是说明；导航动作已自动避障，解析前忽略该从句
    task_text = re.sub(r"(?i)\bavoid\s+obstacles?\b[^.\n]*[.\n]?", " ", task_text or "")
    dual = _dual_route_spec(task_text, world, altitude, land)
    if dual is not None:
        return dual
    formation = _formation_spec(task_text, altitude, world)
    if formation is not None:
        return formation
    assignment = _assignment_spec(task_text, world, altitude, land)
    if assignment is not None:
        return assignment
    shared_target = _shared_target_spec(task_text, world, altitude, land)
    if shared_target is not None:
        return shared_target
    targets_reach = _targets_reach_spec(task_text, world, altitude, land)
    if targets_reach is not None:
        return targets_reach
    multi_named = _multi_named_target_spec(task_text, world, altitude, land)
    if multi_named is not None:
        return multi_named
    multitarget = _multitarget_spec(task_text, world, altitude, land)
    if multitarget is not None:
        return multitarget
    fleet = _fleet_spec(task_text, world, altitude, land)
    if fleet is not None:
        return fleet
    named_position = _named_position_spec(task_text, world, altitude, land)
    if named_position is not None:
        return named_position
    misc = _misc_spec(task_text, world, altitude, land)
    if misc is not None:
        return misc
    target_spec = _target_spec(task_text, world, altitude, land)
    if target_spec is not None:
        return target_spec
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


def _parse_directions(text: str) -> list[tuple[float, float]]:
    directions = [(DIRECTION_HEADING[m.group(1).lower()], float(m.group(2)))
                  for m in DIRECTION_RE.finditer(text or "")]
    if len(directions) >= 4 and len(directions) % 2 == 0:
        half = len(directions) // 2
        if directions[:half] == directions[half:]:
            directions = directions[:half]
    result: list[tuple[float, float]] = []
    for item in directions:
        if not result or result[-1] != item:
            result.append(item)
    return result


def _augment_directions(spec, task_text: str, altitude: float):
    """若文本要求定向移动而 spec 没有，就为涉及的所有无人机追加 move_direction。"""
    if not isinstance(spec, dict) or spec.get("error"):
        return spec
    actions = spec.get("actions")
    if not actions:
        return spec
    if any(str(item.get("name") or item.get("kind")) == "move_direction" for item in actions):
        return spec
    directions = _parse_directions(task_text)
    if not directions:
        return spec
    drones: list[str] = []
    for action in actions:
        drone = str((action.get("params") or {}).get("drone_id") or "")
        if drone and drone not in drones:
            drones.append(drone)
    extra: list[dict] = []
    for heading, distance in directions:
        for drone in (drones or [""]):
            params = {"heading": heading, "distance": distance, "z": altitude,
                      "tolerance": 1.0}
            if drone:
                params["drone_id"] = drone
            extra.append({"name": "move_direction", "params": params})
    new_actions = [dict(action) for action in actions]
    insert_at = len(new_actions)
    for index, action in enumerate(new_actions):
        if str(action.get("name") or action.get("kind")) == "land":
            insert_at = index
            break
    new_actions[insert_at:insert_at] = extra
    result = dict(spec)
    result["actions"] = new_actions
    result["directional_moves"] = [
        {"heading": heading, "distance": distance} for heading, distance in directions]
    return result


def build_spec_from_task(task_text: str, altitude: float, land: bool = True,
                         world=None) -> dict:
    spec = _build_spec_base(task_text, altitude, land, world)
    try:
        spec = _augment_directions(spec, task_text or "", altitude)
    except Exception:  # noqa: BLE001
        pass
    return spec


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
                    config_path: str | None = None,
                    cancel_event=None) -> dict:
    """按行为树模板执行任务（L2 编排 + L3 原子动作）。"""
    from .bt import Status
    from .bt_defs import build_mission

    world = WorldMap.load(map_path)
    api = UavApi(api_base)
    config = load_config(config_path)
    altitude = float(spec.get("altitude") or 32.0)
    if not spec.get("clearance"):
        spec = dict(spec)
        spec["clearance"] = float(config.clearance)
    tree, steps = build_mission(world, spec, altitude)
    if not steps:
        return {"ok": False, "mode": "actions", "steps": [], "executed": [],
                "failures": [{"error": "没有可执行动作"}]}
    blackboard = {"api": api, "drone_id": drone_id, "results": [],
                  "world": world, "clearance": float(spec.get("clearance") or config.clearance),
                  "cancel_event": cancel_event,
                  "deadline": time.time() + float(spec.get("time_budget") or 300.0)}
    status = tree.tick(blackboard)
    executed = blackboard.get("results") or []
    failures = [item for item in executed if not item.get("ok")]
    failed = status != Status.SUCCESS or bool(failures)
    if failed and spec.get("land_on_failure", True):
        drone_ids: list[str] = []
        for step in steps:
            candidate = str((step.get("params") or {}).get("drone_id") or "")
            if candidate and candidate not in drone_ids:
                drone_ids.append(candidate)
        if not drone_ids:
            drone_ids = [drone_id]
        for candidate in drone_ids:
            try:
                api.command(candidate, "land")
                executed.append({"ok": True, "name": "land", "drone_id": candidate,
                                 "cleanup": True})
            except Exception:  # noqa: BLE001
                continue
    if spec.get("land_at_end") and steps[-1].get("kind") != "land":
        api.command(drone_id, "land")
        executed.append({"ok": True, "name": "land"})
    unreachable: list[dict] = []
    for step in steps:
        if step.get("kind") not in ("move_to", "navigate_to_pose"):
            continue
        params = step.get("params") or {}
        if "x" not in params:
            continue
        try:
            point = (float(params["x"]), float(params["y"]))
            if world.clearance_to_obstacles(point) > 0.0:
                continue
        except Exception:  # noqa: BLE001
            continue
        obstacle_name = None
        for obstacle in getattr(world, "obstacles", []) or []:
            try:
                if obstacle.contains(point):
                    obstacle_name = getattr(obstacle, "name", None)
                    break
            except Exception:  # noqa: BLE001
                continue
        unreachable.append({"goal": [round(point[0], 2), round(point[1], 2)],
                            "obstacle": obstacle_name,
                            "reason": "航点位于障碍物内部，无法到达"})
    result = {"ok": status == Status.SUCCESS and not failures and not unreachable,
              "mode": "actions", "steps": steps, "executed": executed, "failures": failures}
    if unreachable:
        result["unreachable_waypoints"] = unreachable
        result["hint"] = "存在不可达航点（位于障碍物内部），请在最终回答中明确列出"
    return result


def _send_waypoints_from_route(route_points, current, z: float) -> list[dict]:
    """从规划路线生成发送航点：规划起点被 clamp 过时，把它作为第一个航点。"""
    points = [[round(float(p[0]), 2), round(float(p[1]), 2), round(float(p[2]), 2)]
              for p in (route_points or [])]
    if not points:
        return []
    first = points[0]
    if math.hypot(first[0] - float(current[0]), first[1] - float(current[1])) > 0.5:
        selected = points
    else:
        selected = points[1:]
    return [{"x": p[0], "y": p[1], "z": p[2]} for p in selected]


def execute_mission(map_path: str, drone_id: str, spec: dict | str,
                    api_base: str = "http://127.0.0.1:8000",
                    config_path: str | None = None,
                    cancel_event=None) -> dict:
    """L2 行为树执行入口：**逐段** JIT 规划 + 执行，返回结构化结果。

    关键：不再一次性规划全部航段（那会长时间静默且可能超时），
    而是 每个航点：先规划这一段 -> 立即飞 -> 验证到达 -> 记录，再下一个。
    """
    if isinstance(spec, dict) and spec.get("actions"):
        return execute_actions(map_path, drone_id, spec, api_base, config_path,
                               cancel_event=cancel_event)

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
    adjusted = list(waypoints)
    problems = []
    unreachable: list[dict] = []
    skip_legs: set[int] = set()
    try:
        for index, waypoint in enumerate(waypoints, 1):
            point = (float(waypoint["x"]), float(waypoint["y"]))
            z = float(waypoint.get("z", altitude))
            try:
                clearance = world.clearance_to_obstacles(point)
            except Exception:  # noqa: BLE001
                clearance = 99.0
            if clearance > 0.0:
                continue
            obstacle_name = None
            for obstacle in getattr(world, "obstacles", []) or []:
                try:
                    if obstacle.contains(point):
                        obstacle_name = getattr(obstacle, "name", None)
                        break
                except Exception:  # noqa: BLE001
                    continue
            record = {"leg": index,
                      "goal": [round(point[0], 2), round(point[1], 2), round(z, 2)],
                      "obstacle": obstacle_name,
                      "reason": "航点位于障碍物内部，无法到达"}
            try:
                free = world.free_point_near(point, 2.0, 40.0)
            except Exception:  # noqa: BLE001
                free = None
            if free is not None:
                record["nearest_free"] = [round(free[0], 2), round(free[1], 2)]
            unreachable.append(record)
            problems.append({"leg": index, "goal": [round(point[0], 2), round(point[1], 2)],
                             "error": "航点在障碍物内部（%s），已跳过该航点"
                                      % (obstacle_name or "障碍")})
            skip_legs.add(index)
    except Exception:  # noqa: BLE001
        unreachable = []
        problems = []
        skip_legs = set()
    if problems:
        result["ok"] = False
        result["failures"].extend(problems)
    if unreachable:
        result["unreachable_waypoints"] = unreachable
        result["skipped_legs"] = sorted(skip_legs)
        result["hint"] = "存在不可达航点（位于障碍物内部），已跳过，请在最终回答中明确列出"
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

    budget = float(spec.get("time_budget", 300.0))
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
    # 执行容差：优先 spec，其次统一收紧到 2m（检查容差通常是 3m）
    default_tolerance = float(spec.get("arrival_tolerance") or 2.0)
    tolerance = default_tolerance

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
        if mission_cancelled(cancel_event):
            result["failures"].append({"leg": index, "error": "任务已被新任务取消"})
            result["ok"] = False
            result["terminated_reason"] = "cancelled"
            try:
                api.command(drone_id, "land")
            except Exception:  # noqa: BLE001
                pass
            _log(f"[cancel] stop at leg {index}")
            break
        if index in skip_legs:
            _log(f"[skip] leg {index}: waypoint inside obstacle")
            continue
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
        leg_waypoints = _send_waypoints_from_route(route.waypoints_xyz, current, z)
        if not leg_waypoints:
            leg_waypoints = [{"x": round(goal[0], 2), "y": round(goal[1], 2), "z": round(z, 2)}]
        try:
            from .bt_defs import _repair_route, _route_blocker

            raw_leg = [(p["x"], p["y"]) for p in leg_waypoints]
            blocker = _route_blocker(world, current, raw_leg)
            if blocker is not None:
                repaired = _repair_route(world, current, raw_leg)
                if repaired is None:
                    record = {"leg": index, "goal": list(goal), "plan_ms": plan_ms,
                              "error": "路径自检未通过（%s），已放弃发送" % blocker}
                    result["executed"].append(record)
                    result["failures"].append(record)
                    result["ok"] = False
                    continue
                leg_waypoints = [{"x": p["x"], "y": p["y"], "z": z} for p in repaired]
                blocker = _route_blocker(world, current,
                                         [(p["x"], p["y"]) for p in leg_waypoints])
                if blocker is not None:
                    record = {"leg": index, "goal": list(goal), "plan_ms": plan_ms,
                              "error": "修复后仍穿障（%s），已放弃发送" % blocker}
                    result["executed"].append(record)
                    result["failures"].append(record)
                    result["ok"] = False
                    continue
        except Exception:  # noqa: BLE001
            pass
        status, body = api.command(drone_id, "move_along_path",
                                   body={"waypoints": leg_waypoints, "allow_partial_move": False})
        error = None
        if isinstance(body, dict) and body.get("status") == "error":
            error = str(body.get("message"))[:120]
        if error and ("blocked" in error.lower() or "intersect" in error.lower()):
            # 被服务端拒绝：用更大净空重新规划后重试一次
            try:
                retry_route = planner.plan(current, goal,
                                           clearance=min(max(config.clearance * 2.0, 0.3), 3.0),
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
