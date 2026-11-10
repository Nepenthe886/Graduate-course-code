"""行为树模板（L2）：把任务 spec 组合成行为树节点，业务编排只在这里。

设计原则（与用户约定一致）：
  L3 动作库 = 官方 11 个命令的薄封装（src/actions.py），无编排
  L3 传感器 = 只读状态（src/sensors.py）
  L2 编排   = 本文件：覆盖/编队/定向位移/多目标等全部由节点组合表达

用法：
    tree, steps = build_mission(world, spec)      # spec["actions"] = [{name, params}, ...]
    status = tree.tick({"api": api, "drone_id": drone_id, "results": []})
"""
from __future__ import annotations

import math

from . import actions, coverage, sensors
from .bt import ActionLeaf, FailLeaf, Node, Sequence, Status, WaitLeaf


def _find_target(world, name: str):
    if world is None:
        return None
    wanted = str(name or "").strip().lower()
    for target in getattr(world, "targets", []):
        if wanted in (str(target.name).lower(), str(target.id).lower()):
            return target
    return None


def _densify(points: list[tuple[float, float]], step: float = 15.0) -> list[tuple[float, float]]:
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


def _point_blocked(world, point) -> bool:
    for obstacle in getattr(world, "obstacles", []) or []:
        try:
            if obstacle.contains(point):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _coverage_waypoints(world, target_name: str, spacing: float, altitude: float) -> list[dict]:
    target = _find_target(world, target_name)
    if target is None:
        return []
    vertices = getattr(target, "vertices", None) or []
    if str(getattr(target, "type", "")).lower() == "polygon" and len(vertices) >= 3:
        polygon = [(float(v["x"]), float(v["y"])) for v in vertices]
        points = coverage.serpentine_polygon(polygon, spacing)
    else:
        center = getattr(target, "center", (0.0, 0.0))
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        points = coverage.serpentine_route((float(center[0]), float(center[1])), radius, spacing)
    points = _densify(points, 20.0)
    projected = []
    for point in points:
        free = world.free_point_near(point, clearance=5.0, max_radius=80.0)
        if free is not None:
            projected.append(free)
    points = projected
    return [{"x": round(p[0], 2), "y": round(p[1], 2), "z": altitude} for p in points]


def _segment_blocker(world, a, b, margin: float = 0.2):
    """检查单段是否与障碍相交，返回阻挡障碍名（通过返回 None）。"""
    from .planner import Planner

    if math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])) < 1e-6:
        return None
    check = Planner(world, clearance=margin).segment((float(a[0]), float(a[1])),
                                                     (float(b[0]), float(b[1])),
                                                     clearance=margin)
    return None if check.clear else (check.blocker or "unknown")


def _repair_route(world, start, points, margin: float = 0.2):
    """对穿障航段插入一个绕角点（在目标点周围找可通过的中间点），失败返回 None。"""
    from .planner import Planner

    planner = Planner(world, clearance=margin)
    repaired: list[dict] = []
    previous = (float(start[0]), float(start[1]))
    for point in points:
        target = (float(point[0]), float(point[1]))
        if _segment_blocker(world, previous, target, margin) is None:
            repaired.append({"x": target[0], "y": target[1]})
            previous = target
            continue
        inserted = False
        for anchor in (previous, target):
            for radius in (1.0, 1.5, 2.0, 3.0, 4.0):
                for degree in range(0, 360, 30):
                    angle = math.radians(degree)
                    via = (anchor[0] + radius * math.cos(angle),
                           anchor[1] + radius * math.sin(angle))
                    try:
                        if not planner.point_clear(via, margin):
                            continue
                    except Exception:  # noqa: BLE001
                        continue
                    if _segment_blocker(world, previous, via, margin) is not None:
                        continue
                    if _segment_blocker(world, via, target, margin) is not None:
                        continue
                    repaired.append({"x": round(via[0], 2), "y": round(via[1], 2)})
                    repaired.append({"x": target[0], "y": target[1]})
                    previous = target
                    inserted = True
                    break
                if inserted:
                    break
            if inserted:
                break
        if not inserted:
            return None
    return repaired


def _route_blocker(world, start, points, margin: float = 0.2):
    """逐段检查折线是否与障碍相交（0 余量语义，贴近服务器），返回首个阻挡障碍名。"""
    from .planner import Planner

    planner = Planner(world, clearance=margin)
    previous = (float(start[0]), float(start[1]))
    for point in points:
        px, py = float(point[0]), float(point[1])
        if math.hypot(px - previous[0], py - previous[1]) < 1e-6:
            previous = (px, py)
            continue
        check = planner.segment(previous, (px, py), clearance=margin)
        if not check.clear:
            return check.blocker or "unknown"
        previous = (px, py)
    return None


def _move_to_smart(api, world, drone_id: str, x: float, y: float,
                   z: float | None = None, clearance: float = 1.5) -> dict:
    """先自检：直线可通过才发 move_to；否则规划并自检路线后再发，避免服务器拒绝。"""
    if world is None:
        return actions.run_atomic(api, drone_id, "move_to", {"x": x, "y": y, "z": z})
    try:
        pose = sensors.read_pose(api, drone_id)
        start = (float(pose["x"]), float(pose["y"]))
        blocker = _route_blocker(world, start, [(x, y)])
    except Exception:  # noqa: BLE001
        blocker = None
    if blocker is None:
        result = actions.run_atomic(api, drone_id, "move_to", {"x": x, "y": y, "z": z})
        if result.get("ok"):
            return result
    try:
        from .planner import Planner

        pose = sensors.read_pose(api, drone_id)
        altitude = float(z if z is not None else (pose.get("z") or 20.0))
        start = (float(pose["x"]), float(pose["y"]))
        blocked_by = None
        planner = Planner(world, clearance=min(clearance, 4.0))
        attempt_clearances = (min(clearance, 6.0), min(clearance * 2.0, 6.0))
        for attempt_clearance in attempt_clearances:
            route = planner.plan(start, (float(x), float(y)),
                                 clearance=attempt_clearance, altitude=altitude)
            route_points = [[round(float(p[0]), 2), round(float(p[1]), 2), round(float(p[2]), 2)]
                            for p in (route.waypoints_xyz or [])]
            if not route_points:
                route_points = [[round(float(start[0]), 2), round(float(start[1]), 2),
                                 round(float(altitude), 2)]]
            first_point = route_points[0]
            differs = math.hypot(first_point[0] - start[0],
                                 first_point[1] - start[1]) > 0.5
            selected = route_points if differs else route_points[1:]
            waypoints = [{"x": p[0], "y": p[1], "z": p[2]} for p in selected]
            if not waypoints:
                waypoints = [{"x": float(x), "y": float(y), "z": altitude}]
            raw = [(p["x"], p["y"]) for p in waypoints]
            blocked_by = _route_blocker(world, start, raw)
            if blocked_by is not None:
                repaired = _repair_route(world, start, raw)
                if repaired is None:
                    continue
                waypoints = [{"x": p["x"], "y": p["y"], "z": altitude} for p in repaired]
                blocked_by = _route_blocker(world, start,
                                            [(p["x"], p["y"]) for p in waypoints])
                if blocked_by is not None:
                    continue
            return actions.run_atomic(api, drone_id, "move_along_path",
                                      {"waypoints": waypoints})
        if blocked_by is not None:
            return {"ok": False, "error": "路径自检未通过（%s），已放弃发送" % blocked_by}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "路径自检/规划失败: %s" % str(exc)[:120]}
    return {"ok": False, "error": "无法生成通过自检的路径"}


class InspectTarget(Node):
    """巡检目标：飞到目标半径内 -> 对准目标中心 -> 拍照。

    用于 target_is_reached_by_drone + target_in_photo_taken_by_drone 检查。
    """

    def __init__(self, target_name: str, z: float | None = None, tolerance: float = 8.0,
                 drone_id: str | None = None, clearance: float = 1.5):
        self.target_name = str(target_name)
        self.z = z
        self.tolerance = float(tolerance)
        self.drone_id = drone_id
        self.clearance = float(clearance)
        self.name = "inspect_target"

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        world = blackboard.get("world")
        drone_id = self.drone_id or blackboard.get("drone_id")
        if api is None or not drone_id:
            return Status.FAILURE
        target = _find_target(world, self.target_name)
        if target is None:
            blackboard.setdefault("results", []).append(
                {"ok": False, "name": "inspect_target", "error": "找不到目标 %s" % self.target_name})
            return Status.FAILURE

        center = getattr(target, "center", (0.0, 0.0))
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        perceived = 50.0
        for drone_meta in (getattr(world, "drones", []) or []):
            if str(drone_meta.get("id")) == str(drone_id) \
                    or str(drone_meta.get("name", "")).lower() == str(drone_id).lower():
                perceived = float(drone_meta.get("perceived_radius") or 50.0)
                break
        pose = sensors.read_pose(api, drone_id)
        dx = float(center[0]) - pose["x"]
        dy = float(center[1]) - pose["y"]
        distance = math.hypot(dx, dy)
        # 接近点：目标半径 60% 与感知半径 60% 取小（否则照片不计入 target_in_photo 检查）
        approach = min(max(2.0, radius * 0.6), max(5.0, perceived * 0.6))
        if distance > approach:
            ratio = (distance - approach) / distance
            point = (pose["x"] + dx * ratio, pose["y"] + dy * ratio)
        else:
            point = (pose["x"], pose["y"])
        z = self.z if self.z is not None else pose["z"]
        moved = _move_to_smart(api, world, drone_id, point[0], point[1], z, self.clearance)
        if not moved.get("ok"):
            blackboard.setdefault("results", []).append(
                {"ok": False, "name": "inspect_target", "target": self.target_name,
                 "error": moved.get("error")})
            return Status.FAILURE

        pose = sensors.read_pose(api, drone_id)
        dx = float(center[0]) - pose["x"]
        dy = float(center[1]) - pose["y"]
        heading = math.degrees(math.atan2(dx, dy)) % 360.0
        rotated = actions.run_atomic(api, drone_id, "rotate", {"heading": round(heading, 1)})
        shot = actions.run_atomic(api, drone_id, "take_photo", {})
        ok = bool(shot.get("ok"))
        blackboard.setdefault("results", []).append(
            {"ok": ok, "name": "inspect_target", "target": self.target_name,
             "heading": round(heading, 1), "rotate_ok": bool(rotated.get("ok")),
             "error": shot.get("error")})
        return Status.SUCCESS if ok else Status.FAILURE


class NavigateToPose(Node):
    """Nav2 风格 NavigateToPose：输入目标位姿，内部完成规划/避障/到达校验/恢复。

    参数：x, y, z(可选), heading(可选), tolerance(默认 2m), drone_id(可选)
    恢复行为：清空净空重规划 -> 侧移 -> 重试一次。
    """

    def __init__(self, x: float, y: float, z: float | None = None,
                 heading: float | None = None, tolerance: float = 2.0,
                 drone_id: str | None = None, clearance: float = 1.5,
                 timeout: float = 60.0):
        self.x = float(x)
        self.y = float(y)
        self.z = z
        self.heading = None if heading is None else float(heading)
        self.tolerance = float(tolerance)
        self.drone_id = drone_id
        self.clearance = float(clearance)
        self.timeout = float(timeout)
        self.name = "navigate_to_pose"

    def _wait_arrival(self, api, drone_id: str) -> bool:
        import time as _time

        started = _time.time()
        while _time.time() - started < self.timeout:
            pose = sensors.read_pose(api, drone_id)
            if math.hypot(pose["x"] - self.x, pose["y"] - self.y) <= self.tolerance:
                return True
            _time.sleep(1.0)
        return False

    def _backoff(self, api, world, drone_id: str) -> None:
        """被服务器拒绝时，先朝远离最近障碍的方向退 1.5~2.0 m，再让上层重试。"""
        try:
            pose = sensors.read_pose(api, drone_id)
            start = (float(pose["x"]), float(pose["y"]))
            obstacles = getattr(world, "obstacles", []) or []
            if not obstacles:
                return
            nearest = min(obstacles, key=lambda o: math.hypot(start[0] - o.center[0],
                                                              start[1] - o.center[1]))
            dx = start[0] - float(nearest.center[0])
            dy = start[1] - float(nearest.center[1])
            norm = math.hypot(dx, dy) or 1.0
            for step in (2.0, 1.5):
                target = (start[0] + dx / norm * step, start[1] + dy / norm * step)
                try:
                    if world.clearance_to_obstacles(target) < 0.8:
                        continue
                except Exception:  # noqa: BLE001
                    continue
                moved = actions.run_atomic(api, drone_id, "move_to",
                                           {"x": target[0], "y": target[1],
                                            "z": pose.get("z")})
                if moved.get("ok"):
                    return
        except Exception:  # noqa: BLE001
            return

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        drone_id = self.drone_id or blackboard.get("drone_id")
        if api is None or not drone_id:
            return Status.FAILURE
        world = blackboard.get("world")
        if world is not None:
            for obstacle in getattr(world, "obstacles", []):
                try:
                    if obstacle.contains((self.x, self.y)):
                        blackboard.setdefault("results", []).append(
                            {"ok": False, "name": "navigate_to_pose", "goal": [self.x, self.y],
                             "error": "目标点位于障碍 %s 内部，物理不可达（地图不匹配）" % obstacle.name})
                        return Status.FAILURE
                except Exception:  # noqa: BLE001
                    continue
        result = {"ok": False, "name": "navigate_to_pose", "goal": [self.x, self.y]}
        attempts = (self.clearance, min(self.clearance * 2.0, 3.5),
                    min(self.clearance * 3.0, 4.0))
        for attempt, clearance in enumerate(attempts, 1):
            move = _move_to_smart(api, world, drone_id, self.x, self.y, self.z, clearance)
            if move.get("ok") and self._wait_arrival(api, drone_id):
                result["ok"] = True
                result["attempts"] = attempt
                break
            error = str(move.get("error") or "")
            result["error"] = error or result.get("error")
            if world is not None and ("block" in error.lower() or "intersect" in error.lower()):
                self._backoff(api, world, drone_id)
        if result["ok"] and self.heading is not None:
            rotated = actions.run_atomic(api, drone_id, "rotate", {"heading": self.heading})
            if not rotated.get("ok"):
                result["ok"] = False
                result["error"] = rotated.get("error")
                result["heading_failed"] = True
        blackboard.setdefault("results", []).append(result)
        return Status.SUCCESS if result["ok"] else Status.FAILURE


class ApproachDrone(Node):
    """飞到另一架无人机附近、进入指定最大距离内（编队/靠拢任务）。"""

    def __init__(self, target_drone_id: str | None = None, max_distance: float = 15.0,
                 z: float | None = None, tolerance: float = 2.0,
                 drone_id: str | None = None, clearance: float = 1.5,
                 timeout: float = 60.0):
        self.target_drone_id = target_drone_id
        self.max_distance = float(max_distance)
        self.z = z
        self.tolerance = float(tolerance)
        self.drone_id = drone_id
        self.clearance = float(clearance)
        self.timeout = float(timeout)
        self.name = "approach_drone"

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        drone_id = self.drone_id or blackboard.get("drone_id")
        if api is None or not drone_id or not self.target_drone_id:
            return Status.FAILURE
        world = blackboard.get("world")
        try:
            me = sensors.read_pose(api, drone_id)
            target = sensors.read_pose(api, self.target_drone_id)
        except Exception:  # noqa: BLE001
            return Status.FAILURE
        distance = math.hypot(me["x"] - target["x"], me["y"] - target["y"])
        result = {"ok": False, "name": "approach_drone", "drone_id": drone_id,
                  "target_drone_id": self.target_drone_id,
                  "distance": round(distance, 2)}
        if distance <= self.max_distance - 0.5:
            result.update(ok=True, moved=False)
            blackboard.setdefault("results", []).append(result)
            return Status.SUCCESS
        keep = max(0.0, self.max_distance - 2.0)
        ux = (me["x"] - target["x"]) / distance if distance > 1e-6 else 1.0
        uy = (me["y"] - target["y"]) / distance if distance > 1e-6 else 0.0
        z = self.z if self.z is not None else me.get("z")
        move = _move_to_smart(api, world, drone_id,
                              target["x"] + ux * keep, target["y"] + uy * keep,
                              z, self.clearance)
        if move.get("ok"):
            import time as _time

            started = _time.time()
            while _time.time() - started < self.timeout:
                pose = sensors.read_pose(api, drone_id)
                now = math.hypot(pose["x"] - target["x"], pose["y"] - target["y"])
                if now <= self.max_distance:
                    result.update(ok=True, moved=True, final_distance=round(now, 2))
                    break
                _time.sleep(1.0)
        result["error"] = None if result["ok"] else (move.get("error") or "未进入指定距离")
        blackboard.setdefault("results", []).append(result)
        return Status.SUCCESS if result["ok"] else Status.FAILURE


class MoveToNode(Node):
    """飞到指定点：直线优先，受阻改绕障规划。"""

    def __init__(self, x: float, y: float, z: float | None = None,
                 drone_id: str | None = None, clearance: float = 1.5):
        self.x = float(x)
        self.y = float(y)
        self.z = z
        self.drone_id = drone_id
        self.clearance = float(clearance)
        self.name = "move_to"

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        drone_id = self.drone_id or blackboard.get("drone_id")
        if api is None or not drone_id:
            return Status.FAILURE
        result = _move_to_smart(api, blackboard.get("world"), drone_id,
                                self.x, self.y, self.z, self.clearance)
        blackboard.setdefault("results", []).append(
            {"ok": result.get("ok"), "name": "move_to", "goal": [self.x, self.y],
             "error": result.get("error")})
        return Status.SUCCESS if result.get("ok") else Status.FAILURE


class TakeOffNode(Node):
    """起飞节点：已悬停时降级为 change_altitude（官方会拒绝重复 take_off）。"""

    def __init__(self, altitude: float, drone_id: str | None = None):
        self.altitude = float(altitude)
        self.drone_id = drone_id
        self.name = "take_off"

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        drone_id = self.drone_id or blackboard.get("drone_id")
        if api is None or not drone_id:
            return Status.FAILURE
        pose = sensors.read_pose(api, drone_id)
        if str(pose.get("status")).lower() == "hovering":
            result = actions.run_atomic(api, drone_id, "change_altitude",
                                        {"altitude": self.altitude})
        else:
            result = actions.run_atomic(api, drone_id, "take_off",
                                        {"altitude": self.altitude})
        blackboard.setdefault("results", []).append(
            {"ok": result["ok"], "name": "take_off", "altitude": self.altitude,
             "error": result.get("error")})
        return Status.SUCCESS if result["ok"] else Status.FAILURE


class MoveDirection(Node):
    """沿 heading 累计移动 distance（0=北/+y，90=东/+x）；受阻侧移重试。"""

    def __init__(self, heading: float, distance: float, z: float | None = None,
                 tolerance: float = 5.0, max_attempts: int = 6, side_step: float = 20.0):
        self.heading = float(heading)
        self.distance = float(distance)
        self.z = z
        self.tolerance = float(tolerance)
        self.max_attempts = int(max_attempts)
        self.side_step = float(side_step)
        self.name = "move_direction"

    def tick(self, blackboard: dict) -> Status:
        api = blackboard.get("api")
        drone_id = blackboard.get("drone_id")
        if api is None or not drone_id:
            return Status.FAILURE
        moved = 0.0
        attempts = 0
        last_error = None
        while moved < self.distance - self.tolerance and attempts < self.max_attempts:
            attempts += 1
            start = sensors.read_pose(api, drone_id)
            remaining = max(1.0, self.distance - moved)
            rad = math.radians(self.heading)
            target = {"x": start["x"] + math.sin(rad) * remaining,
                      "y": start["y"] + math.cos(rad) * remaining}
            if self.z is not None:
                target["z"] = self.z
            result = actions.run_atomic(api, drone_id, "move_to", target)
            if not result["ok"]:
                # 受阻：先尝试规划绕障，再退化为侧移
                result = _move_to_smart(api, blackboard.get("world"), drone_id,
                                        target["x"], target["y"], target.get("z"),
                                        float(blackboard.get("clearance") or 1.5))
            if not result["ok"]:
                last_error = result.get("error")
                side = math.radians(self.heading + 90.0)
                offset = self.side_step * attempts
                side_target = {"x": start["x"] + math.sin(side) * offset,
                               "y": start["y"] + math.cos(side) * offset}
                if self.z is not None:
                    side_target["z"] = self.z
                actions.run_atomic(api, drone_id, "move_to", side_target)
                continue
            end = sensors.read_pose(api, drone_id)
            moved += sensors.directed_progress(start, end, self.heading)
        ok = moved >= self.distance - self.tolerance
        blackboard.setdefault("results", []).append(
            {"ok": ok, "name": "move_direction", "heading": self.heading,
             "distance": self.distance, "moved": round(moved, 2),
             "error": None if ok else (last_error or "方向位移不足")})
        return Status.SUCCESS if ok else Status.FAILURE


def _normalize(raw) -> tuple[str, dict]:
    if isinstance(raw, str):
        return raw, {}
    name = str(raw.get("name") or raw.get("kind") or "")
    params = raw.get("params")
    if params is None:
        params = {k: v for k, v in raw.items() if k not in ("name", "kind", "params")}
    return name, dict(params or {})


def build_mission(world, spec: dict, altitude: float | None = None):
    """spec -> (行为树根节点, 原子步骤列表)。"""
    altitude = float(spec.get("altitude") or altitude or 32.0)
    clearance = float(spec.get("clearance") or 1.5)
    nodes: list[Node] = []
    steps: list[dict] = []

    def resolve_drone(value):
        """把 "Drone 2" 这类名字解析成真实 id；已是 id 则原样返回。"""
        if isinstance(value, dict):
            value = value.get("drone_id") or value.get("id") or value.get("name") or ""
        text = str(value or "")
        if not text or world is None:
            return text
        drone = world.drone_by_name(text)
        if isinstance(drone, dict) and drone.get("id"):
            return str(drone["id"])
        return text

    def add(kind: str, params: dict) -> None:
        params = dict(params)
        if params.get("drone_id"):
            params["drone_id"] = resolve_drone(params["drone_id"])
        if params.get("target_drone_id"):
            params["target_drone_id"] = resolve_drone(params["target_drone_id"])
        if kind == "take_off":
            nodes.append(TakeOffNode(float(params.get("altitude") or altitude),
                                     params.get("drone_id")))
        elif kind == "inspect_target":
            nodes.append(InspectTarget(str(params.get("target") or ""), params.get("z"),
                                       float(params.get("tolerance", 8.0)),
                                       params.get("drone_id"), clearance))
        elif kind == "approach_drone":
            nodes.append(ApproachDrone(params.get("target_drone_id")
                                       or params.get("target_drone"),
                                       float(params.get("max_distance", 15.0)),
                                       params.get("z"),
                                       float(params.get("tolerance", 2.0)),
                                       params.get("drone_id"), clearance))
        elif kind in ("move_to", "navigate_to_pose"):
            nodes.append(NavigateToPose(float(params["x"]), float(params["y"]),
                                        params.get("z"), params.get("heading"),
                                        float(params.get("tolerance", 2.0)),
                                        params.get("drone_id"), clearance))
        else:
            nodes.append(ActionLeaf(kind, params))
        steps.append({"kind": kind, "params": dict(params)})

    def fail(message: str) -> None:
        nodes.append(FailLeaf(message))
        steps.append({"kind": "fail", "params": {"error": message}})

    def add_direction(heading: float, distance: float, params: dict, default_drone=None) -> None:
        drone_id = resolve_drone(params.get("drone_id") or default_drone)
        node: Node = MoveDirection(heading, distance, z=params.get("z", altitude),
                                   tolerance=float(params.get("tolerance", 5.0)))
        if drone_id:
            node = _PerDrone(node, str(drone_id))
        nodes.append(node)
        leaf_params = {"drone_id": drone_id} if drone_id else {}
        steps.append({"kind": "move_direction",
                      "params": {"heading": float(heading), "distance": float(distance),
                                 "z": params.get("z", altitude), **leaf_params}})

    for raw in (spec.get("actions") or []):
        name, params = _normalize(raw)

        if name == "coordinated_sweep":
            drones = [resolve_drone(d) for d in (params.get("drones") or [])] or [None]
            waypoints = _coverage_waypoints(world, params.get("target"), 24.0, altitude)
            if not waypoints:
                fail("目标 %s 无法生成覆盖航线" % params.get("target"))
                continue
            for index, drone in enumerate(drones):
                group = waypoints[index::len(drones)]
                if not group:
                    continue
                add("move_along_path", {"waypoints": group, "drone_id": drone})
                add("take_photo", {"drone_id": drone})
        elif name == "coverage_search":
            waypoints = _coverage_waypoints(world, params.get("target"),
                                            float(params.get("spacing") or 30.0), altitude)
            if not waypoints:
                fail("目标 %s 无法生成覆盖航线" % params.get("target"))
                continue
            photos = int(params.get("photos") or 0)
            interval = max(1, len(waypoints) // photos) if photos else 0
            for index, point in enumerate(waypoints, 1):
                add("navigate_to_pose", {"x": point["x"], "y": point["y"], "z": point["z"],
                                         "tolerance": 10.0})
                if interval and index % interval == 0:
                    add("take_photo", {})
        elif name == "visit_targets":
            for target_name in (params.get("targets") or []):
                target = _find_target(world, target_name)
                if target is None:
                    fail("找不到目标 %s" % target_name)
                    continue
                center = getattr(target, "center", (0.0, 0.0))
                add("move_to", {"x": float(center[0]), "y": float(center[1]), "z": altitude})
        elif name == "patrol":
            points = [{"x": float(p["x"]), "y": float(p["y"]),
                       "z": float(p.get("z", altitude))} for p in (params.get("points") or [])]
            if params.get("closed") and points:
                points = points + [dict(points[0])]
            if points:
                add("move_along_path", {"waypoints": points})
        elif name in ("formation", "formation_hold"):
            center = params.get("center") or [0.0, 0.0]
            drones = params.get("drones") or []
            kind = str(params.get("kind") or "line").lower()
            gap = float(params.get("gap") or 40.0)
            for index, drone_id in enumerate(drones):
                if kind == "triangle":
                    offsets = [(0.0, 0.0), (-gap, gap * 0.9), (gap, gap * 0.9)]
                    dx, dy = offsets[index % len(offsets)]
                else:
                    dx, dy = (index - (len(drones) - 1) / 2.0) * gap, 0.0
                add("move_to", {"x": float(center[0]) + dx, "y": float(center[1]) + dy,
                                "z": altitude, "drone_id": drone_id})
            if name == "formation_hold":
                for drone_id in drones:
                    add("hover", {"drone_id": drone_id})
        elif name == "track_target":
            target = _find_target(world, params.get("target"))
            if target is None:
                fail("找不到目标 %s" % params.get("target"))
                continue
            center = getattr(target, "center", (0.0, 0.0))
            add("move_towards", {"x": float(center[0]), "y": float(center[1]), "z": altitude})
        elif name == "move_direction":
            add_direction(params.get("heading", 0.0), params.get("distance", 0.0), params)
        elif name == "wait":
            seconds = float(params.get("seconds") or 5.0)
            nodes.append(WaitLeaf(seconds))
            steps.append({"kind": "wait", "params": {"seconds": seconds}})
        elif name == "inspect_target":
            add(name, params)
        elif name == "approach_drone":
            add(name, params)
        elif name in ("move_to", "navigate_to_pose"):
            add(name, params)
        elif name in actions.ACTION_LIBRARY:
            add(name, params)
        elif name:
            fail("未知动作: %s" % name)

    if spec.get("ensure_takeoff", True) and steps and steps[0]["kind"] != "take_off":
        nodes.insert(0, TakeOffNode(altitude, None))
        steps.insert(0, {"kind": "take_off", "params": {"altitude": altitude}})

    return Sequence(*nodes, name=str(spec.get("name") or "mission")), steps


class _PerDrone(Node):
    """给子树绑定固定 drone_id（多机任务用）。"""

    def __init__(self, inner: Node, drone_id: str):
        self.inner = inner
        self.drone_id = drone_id
        self.name = getattr(inner, "name", "node")

    def tick(self, blackboard: dict) -> Status:
        child_board = dict(blackboard)
        child_board["drone_id"] = self.drone_id
        child_board["results"] = blackboard.setdefault("results", [])
        return self.inner.tick(child_board)

    def reset(self) -> None:
        self.inner.reset()
