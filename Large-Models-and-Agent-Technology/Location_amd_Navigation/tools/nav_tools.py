"""把导航模块包装成 agent 可调用的工具（10 个）。

工具参数约定与 agent4drone 一致：带 `input_json` 字符串参数。
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from src.config import NavConfig, load_config
from src.coverage import estimate_coverage, serpentine_route
from src.localization import localizer_from_world
from src.navigation import Navigation
from src.paths import resolve
from src.planner import Planner
from src.worldmap import WorldMap


def _compact_result(result: Any) -> dict:
    """压缩执行结果，避免几百步明细撑爆模型上下文。"""
    if not isinstance(result, dict):
        return {"result": str(result)[:500]}
    compact = {key: result.get(key) for key in
               ("ok", "mode", "planned", "photos_taken", "photos_required", "coverage", "take_off")
               if key in result}
    executed = result.get("executed") or []
    compact["steps"] = len(executed)
    compact["sample"] = executed[:3]
    failures = result.get("failures") or []
    compact["failures"] = failures[:5]
    if len(failures) > 5:
        compact["failures_total"] = len(failures)
    return compact


def _xyz_item(item: Any, default_z: float = 20.0) -> tuple[float, float, float]:
    """兼容 dict({x,y,z}) 与列表([x,y,z]) 两种坐标写法。"""
    if isinstance(item, dict):
        return (float(item.get("x", 0.0)), float(item.get("y", 0.0)),
                float(item.get("z", default_z)))
    values = list(item) if isinstance(item, (list, tuple)) else []
    x = float(values[0]) if len(values) > 0 else 0.0
    y = float(values[1]) if len(values) > 1 else 0.0
    z = float(values[2]) if len(values) > 2 else float(default_z)
    return (x, y, z)


def _payload_from_list(items: list) -> dict:
    """把模型直接传来的数组归一化成 dict：航点列表 / 动作列表。"""
    if not items:
        return {}
    if all(isinstance(item, dict) for item in items):
        if all("x" in item and "y" in item for item in items):
            return {"waypoints": items}
        if all(("name" in item or "kind" in item) for item in items):
            return {"actions": items}
    return {"items": items}


def _payload(input_json: Any) -> dict:
    if input_json is None:
        return {}
    if isinstance(input_json, dict):
        return input_json
    if isinstance(input_json, (list, tuple)):
        return _payload_from_list(list(input_json))
    if isinstance(input_json, str):
        text = input_json.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return _payload_from_list(value)
        return {"value": value}
    raise TypeError(f"无法解析参数: {input_json!r}")


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return (float(value.get("x", 0.0)), float(value.get("y", 0.0)))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    raise TypeError(f"坐标格式不对: {value!r}")


class NavToolkit:
    def __init__(self, map_path: str | None = None, config: NavConfig | None = None,
                 drone_name: str | None = None, pose_provider=None):
        self.config = config or load_config()
        self.map_path = resolve(map_path) if map_path else resolve(self.config.default_map)
        self.world = WorldMap.load(self.map_path)
        self.planner = Planner(
            self.world,
            clearance=self.config.clearance,
            grid_step=self.config.grid_step,
            planning_dim=getattr(self.config, "planning_dim", "2d"),
            altitude=self.config.z_default,
            vertical_margin=getattr(self.config, "vertical_margin", 5.0),
            max_altitude=getattr(self.config, "max_altitude", 120.0),
            altitude_step=getattr(self.config, "altitude_step", 10.0),
            vertical_penalty=getattr(self.config, "vertical_penalty", 1.5),
            z_min=getattr(self.config, "z_min", 5.0),
            smoothing=getattr(self.config, "smoothing", True),
            smooth_iterations=getattr(self.config, "smooth_iterations", 2),
            max_waypoints=getattr(self.config, "max_waypoints", 40),
            max_turn_deg=getattr(self.config, "max_turn_deg", 60.0),
        )
        self.drone_name = drone_name or (self.world.drones[0].get("name") if self.world.drones else "Drone 1")
        self.localizer = localizer_from_world(self.world, self.drone_name, pose_provider)
        self.navigation = Navigation(self.world, self.localizer, self.planner,
                                     arrival_tolerance=self.config.arrival_tolerance,
                                     z_default=self.config.z_default)

    # ---------- 实时位姿同步 ----------
    def _live_pose(self, drone_ref: Any = None):
        """按 drone_id/名字从 UAV API 拉实时位姿（失败返回 None）。"""
        import urllib.request

        base = str(getattr(self.config, "uav_api_base", "http://127.0.0.1:8000")).rstrip("/")
        needle = str(drone_ref or self.drone_name or "").strip()
        if not needle:
            return None
        try:
            if len(needle) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in needle):
                url = base + "/drones/" + needle
                data = json.loads(urllib.request.urlopen(url, timeout=3).read().decode())
                drone = data if isinstance(data, dict) else {}
            else:
                drones = json.loads(urllib.request.urlopen(base + "/drones", timeout=3).read().decode())
                drone = {}
                for item in (drones or []):
                    if str(item.get("name", "")).strip().lower() == needle.lower() \
                            or str(item.get("id", "")).lower() == needle.lower():
                        drone = item
                        break
            position = drone.get("position") or {}
            if "x" not in position:
                return None
            from src.frames import Pose

            pose = Pose(float(position.get("x", 0.0)), float(position.get("y", 0.0)),
                        float(position.get("z", 0.0)), float(drone.get("heading") or 0.0))
            self.localizer.set_pose(pose)
            return pose
        except Exception:  # noqa: BLE001
            return None

    def _best_drone(self):
        """返回电量最高的无人机（失败返回 None）。"""
        base = str(getattr(self.config, "uav_api_base", "http://127.0.0.1:8000")).rstrip("/")
        try:
            drones = json.loads(urllib.request.urlopen(base + "/drones", timeout=3).read().decode())
        except Exception:  # noqa: BLE001
            return None
        best, best_battery = None, -1.0
        for drone in (drones or []):
            battery = float(drone.get("battery_level") or 0.0)
            if battery > best_battery:
                best, best_battery = drone, battery
        return best

    def _sync_pose(self, data: dict | None = None) -> None:
        payload = data or {}
        self._live_pose(payload.get("drone_id") or payload.get("drone") or payload.get("drone_name"))

    # ---------- 1 地图信息 ----------
    def nav_map_info(self, input_json: Any = None) -> dict:
        return {
            "map": self.world.name,
            "map_file": str(self.world.source),
            "world": {"width": self.world.width, "height": self.world.height},
            "obstacle_count": len(self.world.obstacles),
            "obstacles": [
                {"name": o.name, "type": o.type, "center": list(o.center), "height": o.height,
                 "radius": o.radius, "width": o.width, "length": o.length}
                for o in self.world.obstacles
            ],
            "target_count": len(self.world.targets),
            "targets": [{"name": t.name, "center": list(t.center), "radius": t.radius}
                        for t in self.world.targets],
            "drone": self.drone_name,
            "pose": self.localizer.describe()["pose"],
            "planning_dim": self.planner.planning_dim,
        }

    # ---------- 2 附近障碍 ----------
    def nav_nearby_obstacles(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        origin = _point([data["x"], data["y"]]) if ("x" in data and "y" in data) else self.localizer.pose.xy
        radius = float(data.get("radius", 150.0))
        found = []
        for obstacle, distance in self.world.obstacles_near(origin, radius):
            footprint = obstacle.footprint()
            found.append({
                "name": obstacle.name, "type": obstacle.type,
                "center": [round(v, 2) for v in obstacle.center],
                "distance_m": round(distance, 2),
                "height": obstacle.height,
                "footprint_bbox": {
                    "min": [round(min(p[0] for p in footprint), 2), round(min(p[1] for p in footprint), 2)],
                    "max": [round(max(p[0] for p in footprint), 2), round(max(p[1] for p in footprint), 2)],
                },
            })
        return {"origin": [round(origin[0], 2), round(origin[1], 2)], "radius_m": radius, "obstacles": found}

    # ---------- 3 线段检查 ----------
    def nav_check_line(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        start = _point(data["start"]) if data.get("start") else self.localizer.pose.xy
        goal = _point(data["goal"])
        check = self.planner.segment(start, goal)
        return {"start": [round(v, 2) for v in start], "goal": [round(v, 2) for v in goal], **check.as_dict()}

    # ---------- 4 规划绕障路线 ----------
    def nav_plan_route(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        start = _point(data["start"]) if data.get("start") else self.localizer.pose.xy
        goal = _point(data["goal"])
        clearance = float(data["clearance"]) if data.get("clearance") is not None else None
        z = data.get("z", data.get("altitude"))
        altitude = float(z) if z is not None else None
        route = self.planner.plan(start, goal, clearance, altitude=altitude)
        return route.as_dict()

    # ---------- 5 到目标名的航点 ----------
    def nav_route_to_target(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        name = data.get("target")
        if not name:
            return {"error": "缺少 target 参数"}
        target = self.world.target_by_name(str(name))
        if target is None:
            return {"error": f"地图里没有目标 {name!r}",
                    "available": [t.name for t in self.world.targets]}
        altitude = float(data.get("z", self.config.z_default))
        route = self.planner.plan(self.localizer.pose.xy, target.center, altitude=altitude)
        if route.waypoints_xyz:
            waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)}
                         for p in route.waypoints_xyz[1:]]
        else:
            waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(altitude, 2)}
                         for p in route.waypoints]
        return {
            "target": {"name": target.name, "center": list(target.center), "radius": target.radius},
            "route": route.as_dict(),
            "move_along_path_waypoints": waypoints,
        }

    # ---------- 6 位姿 ----------
    def nav_pose(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        target = self.world.target_by_name(str(data["target"])) if data.get("target") else None
        return self.localizer.describe(target.center if target else None)

    # ---------- 7 到达验证 ----------
    def nav_verify_arrival(self, input_json: Any = None) -> dict:
        data = _payload(input_json)
        self._sync_pose(data)
        goal = _point(data["goal"])
        tolerance = float(data["tolerance"]) if data.get("tolerance") is not None else None
        return self.navigation.verify(goal, tolerance)

    # ---------- 11 任务级规划（行为树展开）----------
    def nav_mission_plan(self, input_json: Any = None) -> dict:
        """把"目标点 + 途径点 + 每点任务"用行为树展开成可执行步骤序列。

        input_json = {
          "altitude": 22,
          "clearance": 45,
          "waypoints": [
            {"x": 225, "y": 448, "label": "Polygon Target 2",
             "tasks": [{"kind": "cover", "target_name": "Polygon Target 2"},
                       {"kind": "photo"}]},
            {"x": 500, "y": 400, "tasks": [{"kind": "hover", "duration": 5}]}
          ],
          "land_at_end": true
        }
        返回 steps（take_off / move_along_path / hover / take_photo / land ...）。
        """
        from src.mission import plan_mission

        data = _payload(input_json)
        self._sync_pose(data)
        if not data.get("waypoints"):
            return {"error": "缺少 waypoints"}
        start = self.localizer.pose
        return plan_mission(data, self.world, start, self.planner)

    # ---------- 12 交给行为树执行（L1 唯一入口）----------
    def nav_execute_mission(self, input_json: Any = None) -> dict:
        """L1->L2 入口：把整条任务交给行为树执行（逐点推进，不需要模型规划航点）。

        input_json = {
          "task_text": "...",              // 也可直接用 "waypoints": [{x,y,z}, ...]
          "drone_id": "612145fe",
          "altitude": 32,
          "waypoints": [{"x": 62, "y": 80, "z": 32}, ...],   // 可选
          "land_at_end": true
        }
        """
        from src.mission_run import execute_mission

        data = _payload(input_json)
        self._sync_pose(data)
        drone_values = [str(item) for item in (data.get("drones") or [])]
        if not drone_values and data.get("drone_id"):
            drone_values.append(str(data["drone_id"]))
        task_text = str(data.get("task_text") or "")
        if not drone_values or re.search(r"any available drone", task_text, re.I):
            best = self._best_drone()
            if isinstance(best, dict) and best.get("id"):
                drone_values = [str(best["id"])]
        if not drone_values:
            return {"error": "需要 drone_id 或 drones 列表"}

        def _looks_like_id(value: str) -> bool:
            return len(value) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in value)

        resolved_ids: list[str] = []
        for value in drone_values:
            if _looks_like_id(value):
                resolved_ids.append(value)
                continue
            drone = self.world.drone_by_name(value)
            if isinstance(drone, dict) and drone.get("id"):
                resolved_ids.append(str(drone["id"]))
            elif self.world.drones:
                resolved_ids.append(str((self.world.drones[0] or {}).get("id") or value))
            else:
                resolved_ids.append(value)
        drone_id = resolved_ids[0]
        if data.get("actions"):
            spec = {"actions": data["actions"],
                    "altitude": float(data.get("altitude", self.config.z_default)),
                    "land_at_end": bool(data.get("land_at_end", True))}
        elif data.get("task_text"):
            from src.mission_run import build_spec_from_task
            spec = build_spec_from_task(str(data["task_text"]),
                                        float(data.get("altitude", self.config.z_default)),
                                        bool(data.get("land_at_end", True)),
                                        world=self.world)
            if not spec.get("actions") and not spec.get("waypoints") and data.get("waypoints"):
                spec = {"altitude": float(data.get("altitude", self.config.z_default)),
                        "waypoints": data["waypoints"],
                        "land_at_end": bool(data.get("land_at_end", True))}
        elif data.get("waypoints"):
            alt_default = float(data.get("altitude", self.config.z_default))
            spec = {"altitude": alt_default,
                    "waypoints": [dict(zip(("x", "y", "z"), _xyz_item(item, alt_default)))
                                  for item in data["waypoints"]],
                    "land_at_end": bool(data.get("land_at_end", True))}
        else:
            return {"error": "需要 task_text 或 waypoints"}
        if spec.get("error"):
            return {"error": spec["error"]}
        if not spec.get("actions") and not spec.get("waypoints"):
            return {"error": "任务里没有可解析的航点，也不是可识别的覆盖/编队任务"}
        if spec.get("actions"):
            # 动作规格内部已按 drone_id 指定每架机，执行一次即可
            return _compact_result(execute_mission(str(self.map_path), drone_id, spec,
                                                   self.config.uav_api_base))
        results = [execute_mission(str(self.map_path), did, spec, self.config.uav_api_base)
                   for did in resolved_ids]
        if len(results) == 1:
            return _compact_result(results[0])
        return {"ok": all(bool(r.get("ok")) for r in results),
                "drones": resolved_ids,
                "results": [_compact_result(r) for r in results]}

    # ---------- 8 覆盖航线 ----------
    def nav_coverage_route(self, input_json: Any = None) -> dict:
        """圆形区域蛇形覆盖航线 + 覆盖率估算（用于"覆盖率≥95%"类任务）。"""
        data = _payload(input_json)
        self._sync_pose(data)
        center = None
        radius = float(data.get("radius") or 0.0)
        if data.get("target_name"):
            target = self.world.target_by_name(str(data["target_name"]))
            if target is None:
                return {"error": f"地图里没有目标 {data['target_name']!r}",
                        "available": [t.name for t in self.world.targets]}
            center = target.center
            radius = radius or float(target.radius)
        if center is None:
            raw = data.get("center") or {}
            center = (float(raw.get("x", 0.0)), float(raw.get("y", 0.0)))
        z = float(data.get("z", self.config.z_default))
        target_obj = None
        if data.get("target_name"):
            target_obj = self.world.target_by_name(str(data["target_name"]))
        if target_obj is not None and str(getattr(target_obj, "type", "")).lower() != "circle" \
                and getattr(target_obj, "vertices", None):
            from src.coverage import estimate_coverage_polygon, serpentine_polygon

            footprint = target_obj.footprint()
            xs = [p[0] for p in footprint]
            ys = [p[1] for p in footprint]
            min_dim = max(1.0, min(max(xs) - min(xs), max(ys) - min(ys)))
            spacing = float(data.get("spacing") or max(8.0, float(getattr(self.config, "coverage_swath", 40.0)) * 0.9))
            points = serpentine_polygon(footprint, spacing)
            lanes = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
            coverage = estimate_coverage_polygon(footprint, lanes, spacing)
            radius = 0.0
        else:
            if radius <= 1.0:
                return {"error": "缺少 radius（目标半径过小或无目标）"}
            spacing = float(data.get("spacing") or max(8.0, float(getattr(self.config, "coverage_swath", 40.0)) * 0.9))
            points = serpentine_route(center, radius, spacing)
            lanes = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
            coverage = estimate_coverage(center, radius, lanes, spacing)
        waypoints: list[dict] = []
        current = self.localizer.pose.xy
        for point in points:
            route = self.planner.plan(current, point, altitude=z)
            for p in (route.waypoints_xyz or [(q[0], q[1], z) for q in route.waypoints]):
                waypoints.append({"x": round(p[0], 2), "y": round(p[1], 2), "z": round(z, 2)})
            current = point
        return {
            "center": [round(center[0], 2), round(center[1], 2)],
            "radius": radius, "spacing": spacing,
            "estimated_coverage": round(coverage, 4),
            "waypoint_count": len(waypoints),
            "waypoints_sample": waypoints[:5],
            "waypoints_truncated": max(0, len(waypoints) - 5),
            "hint": "执行请用 nav_execute_mission(task_text=...)，不要自己传完整航点",
        }

    # ---------- 9 坐标串 -> 一条航线 ----------
    def nav_points_route(self, input_json: Any = None) -> dict:
        """把一串坐标合成一条绕障航线（长任务用，一次 move_along_path 发完）。"""
        data = _payload(input_json)
        self._sync_pose(data)
        raw_points = data.get("points") or []
        if not raw_points:
            return {"error": "缺少 points"}
        clearance = data.get("clearance")
        waypoints: list[dict] = []
        current = self.localizer.pose.xy
        total = 0.0
        for item in raw_points:
            x, y, z = _xyz_item(item, self.config.z_default)
            point = (x, y)
            route = self.planner.plan(current, point, clearance, altitude=z)
            for p in (route.waypoints_xyz or [(q[0], q[1], z) for q in route.waypoints]):
                waypoints.append({"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)})
            total += route.length_3d or route.length
            current = point
        return {"waypoint_count": len(waypoints), "total_length": round(total, 2),
                "waypoints_sample": waypoints[:5],
                "hint": "执行请用 nav_execute_mission(task_text=...)，不要自己传完整航点"}

    # ---------- 10 多机任务 ----------
    def nav_multi_drone_route(self, input_json: Any = None) -> dict:
        """按无人机分别生成航点（多机任务用，避免只飞一架）。"""
        data = _payload(input_json)
        self._sync_pose(data)
        assignments = data.get("assignments") or []
        if not assignments:
            return {"error": "缺少 assignments"}
        result = []
        for item in assignments:
            drone = item.get("drone") or item.get("drone_id") or ""
            z = float(item.get("z", self.config.z_default))
            goal = None
            if item.get("target_name"):
                target = self.world.target_by_name(str(item["target_name"]))
                if target is None:
                    result.append({"drone": drone, "error": f"没有目标 {item['target_name']!r}"})
                    continue
                goal = target.center
            elif item.get("goal"):
                goal = (float(item["goal"].get("x", 0.0)), float(item["goal"].get("y", 0.0)))
            if goal is None:
                result.append({"drone": drone, "error": "缺少 target_name 或 goal"})
                continue
            pose = self.world.drone_pose(drone) if drone else None
            start = pose.xy if pose is not None else self.localizer.pose.xy
            route = self.planner.plan(start, goal, altitude=z)
            waypoints = route.waypoints_xyz or [(p[0], p[1], z) for p in route.waypoints]
            result.append({
                "drone": drone,
                "goal": [round(goal[0], 2), round(goal[1], 2)],
                "length": round(route.length_3d or route.length, 2),
                "move_along_path_waypoints": [
                    {"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)} for p in waypoints
                ],
            })
        return {"assignments": result}


NAV_TOOL_SPECS: list[tuple[str, str, str]] = [
    ("nav_map_info", "查看当前地图概况（障碍物/目标清单、世界边界）。", "nav_map_info"),
    ("nav_nearby_obstacles", "查询附近障碍物，参数 {\"x\":..,\"y\":..,\"radius\":..}（x/y 可省略=当前位置）。", "nav_nearby_obstacles"),
    ("nav_check_line", "检查两点之间能否直线通过，参数 {\"start\":{...},\"goal\":{...}}。", "nav_check_line"),
    ("nav_plan_route", "规划绕开障碍的航点，参数 {\"goal\":{...},\"clearance\":45,\"z\":22}。", "nav_plan_route"),
    ("nav_route_to_target", "按目标名给出 move_along_path 航点，参数 {\"target\":\"...\",\"z\":22}。", "nav_route_to_target"),
    ("nav_pose", "当前位置与到目标的方位/距离，参数 {\"target\":\"...\"}（可省略）。", "nav_pose"),
    ("nav_verify_arrival", "验证是否到达目标点，参数 {\"goal\":{...},\"tolerance\":5}。", "nav_verify_arrival"),
    ("nav_coverage_route", "【覆盖率任务必用】圆形区域蛇形覆盖航线+覆盖率估算，参数 {\"target_name\":\"...\",\"z\":22,\"spacing\":20}。", "nav_coverage_route"),
    ("nav_points_route", "【长航点任务必用】把一串坐标合成一条绕障航线，参数 {\"points\":[...]}，一次 move_along_path 发完。", "nav_points_route"),
    ("nav_execute_mission", "【长任务唯一入口】把任务交给行为树执行（多机/编队/定向位移/覆盖/逐点避障）。参数 {\"task_text\":\"...\",\"drones\":[\"Drone 1\",\"Drone 2\"],\"altitude\":19}；也可直接传 {\"actions\":[{name,params},...]} 或 {\"drone_id\":\"...\",\"waypoints\":[...]}。", "nav_execute_mission"),
    ("nav_mission_plan", "【多段任务必用】用行为树把目标点/途径点/每点任务展开成可执行步骤，参数 {\"waypoints\":[...]}。", "nav_mission_plan"),
    ("nav_multi_drone_route", "【多机任务必用】按无人机分别生成航点，参数 {\"assignments\":[{\"drone\":\"Drone 1\",\"target_name\":\"...\"}]}。", "nav_multi_drone_route"),
]


def create_nav_tools(map_path: str | None = None, config: NavConfig | None = None,
                     drone_name: str | None = None, pose_provider=None):
    toolkit = NavToolkit(map_path=map_path, config=config, drone_name=drone_name,
                         pose_provider=pose_provider)
    try:
        from langchain_core.tools import StructuredTool
    except Exception:
        return {name: getattr(toolkit, name) for name, _desc, _attr in NAV_TOOL_SPECS}
    tools = []
    for name, description, attr in NAV_TOOL_SPECS:
        fn = getattr(toolkit, attr)
        tools.append(
            StructuredTool.from_function(
                func=lambda input_json=None, _fn=fn: json.dumps(_fn(input_json), ensure_ascii=False),
                name=name,
                description=description + " 参数为 input_json 字符串。",
            )
        )
    return tools
