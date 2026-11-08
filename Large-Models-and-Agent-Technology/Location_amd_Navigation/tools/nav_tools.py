"""把导航模块包装成 agent 可调用的工具（10 个）。

工具参数约定与 agent4drone 一致：带 `input_json` 字符串参数。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
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
        self._pose_provider = pose_provider
        self.drone_name = drone_name
        self.session_id: str | None = None
        self.session_name = ""
        self._last_refresh = 0.0
        self.map_path = resolve(map_path) if map_path else resolve(self.config.default_map)
        source = "file"
        # 初始化时若 8000 可用，按会话名匹配作业场景文件
        try:
            info = self._current_session(timeout=5)
        except Exception:  # noqa: BLE001
            info = None
        matched = self._scenario_for(info) if info else None
        if matched is not None:
            self.map_path, source = matched, "scenario"
            self.session_id = str((info or {}).get("id") or "") or None
            self.session_name = str((info or {}).get("name") or "")
        self._setup_runtime(self.map_path, source)

    # ---------- 地图与会话 ----------
    def _current_session(self, timeout: float = 1.5) -> dict:
        import urllib.request

        base = str(getattr(self.config, "uav_api_base", "http://127.0.0.1:8000")).rstrip("/")
        with urllib.request.urlopen(base + "/sessions/current", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", "ignore"))
        return data if isinstance(data, dict) else {}

    def _scenario_for(self, info: dict) -> Path | None:
        """仅当当前地图是默认回退地图时，才按会话名换成场景文件。"""
        name = str((info or {}).get("name") or "").strip()
        if not name or not getattr(self.config, "scenario_dirs", None):
            return None
        default_path = resolve(self.config.default_map) if self.config.default_map else None
        is_fallback = default_path is not None and Path(self.map_path) == Path(default_path)
        if not is_fallback:
            return None
        dirs = [resolve(d) for d in self.config.scenario_dirs]
        return WorldMap.find_scenario(name, dirs)

    def _setup_runtime(self, path, source: str) -> None:
        self.map_path = Path(path)
        self.map_source = source
        self.world = WorldMap.load(self.map_path)
        self._map_signature = (str(self.map_path), str(getattr(self.world, "id", "")))
        if not self.drone_name:
            self.drone_name = self.world.drones[0].get("name") if self.world.drones else "Drone 1"
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
        self.localizer = localizer_from_world(self.world, self.drone_name, self._pose_provider)
        self.navigation = Navigation(self.world, self.localizer, self.planner,
                                     arrival_tolerance=self.config.arrival_tolerance,
                                     z_default=self.config.z_default)

    def _refresh_session(self) -> None:
        """运行期自动换图：8000 恢复或会话切换后，按会话名重新匹配场景文件。"""
        import time

        now = time.time()
        if now - self._last_refresh < 5.0:
            return
        self._last_refresh = now
        try:
            info = self._current_session(timeout=1.5)
        except Exception:  # noqa: BLE001
            return
        if not info:
            return
        sid = str(info.get("id") or "")
        if sid and sid == self.session_id:
            return
        self.session_id = sid or self.session_id
        self.session_name = str(info.get("name") or "")
        matched = self._scenario_for(info)
        if matched is not None:
            self._setup_runtime(matched, "scenario")

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
        self._refresh_session()
        self._live_pose(payload.get("drone_id") or payload.get("drone") or payload.get("drone_name"))

    # ---------- 1 地图信息 ----------
    def nav_map_info(self, input_json: Any = None) -> dict:
        self._refresh_session()
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
                alt_default = float(data.get("altitude", self.config.z_default))
                normalized = [dict(zip(("x", "y", "z"), _xyz_item(item, alt_default)))
                              for item in data["waypoints"]]
                for idx, point in enumerate(normalized, start=1):
                    if self.world.clearance_to_obstacles((point["x"], point["y"])) <= 0.0:
                        near = self.world.free_point_near((point["x"], point["y"]), 2.0, 120.0)
                        return {"ok": False,
                                "error": f"第 {idx} 个航点 ({point['x']:.1f},{point['y']:.1f}) 位于障碍物内部，任务不可行",
                                "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None}
                spec = {"altitude": alt_default,
                        "waypoints": normalized,
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
        if center is not None and self.world.clearance_to_obstacles(center) <= 0.0:
            near = self.world.free_point_near(center, 2.0, 120.0)
            return {"ok": False,
                    "error": f"目标中心 ({center[0]:.1f},{center[1]:.1f}) 位于障碍物内部，覆盖率任务不可行",
                    "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None,
                    "hint": "该目标被障碍物包围，无法进入其范围完成覆盖"}
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
        if data.get("execute"):
            from src import mission_run as mr

            drone_ref = data.get("drone_id") or self.drone_name
            drone = self.world.drone_by_name(str(drone_ref))
            drone_id = str((drone or {}).get("id") or drone_ref)
            try:
                photos = max(1, int(data.get("photos") or 1))
            except (TypeError, ValueError):
                photos = 1
            actions = [{"kind": "take_off", "params": {"drone_id": drone_id, "altitude": z}}]
            actions += [{"kind": "move_to", "params": {"drone_id": drone_id,
                                                       "x": round(pt[0], 2), "y": round(pt[1], 2),
                                                       "z": z, "tolerance": 5.0}}
                        for pt in points]
            actions += [{"kind": "take_photo", "params": {"drone_id": drone_id}}] * photos
            actions.append({"kind": "hover", "params": {"drone_id": drone_id}})
            result = mr.execute_actions(self.map_path, drone_id,
                                        {"actions": actions, "altitude": z},
                                        api_base=str(self.config.uav_api_base))
            return {"ok": bool(result.get("ok")), "mode": "coverage_exec",
                    "estimated_coverage": round(coverage, 4),
                    "waypoint_count": len(points), "photos": photos,
                    "steps": len(result.get("executed") or []),
                    "failures": result.get("failures") or []}
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

    # ---------- 13 官方模板组合动作 ----------
    def nav_template_action(self, input_json: Any = None) -> dict:
        """按官方 10 类任务模板执行组合动作（L1 一次调用完成整条任务）。

        input_json = {"template": "corridor_detour",
                      "params": {"drone_id": "Bravo", "start": [x, y], "end": [x, y],
                                 "altitude": 28.2, "clearance": 59, "photos": 2}}
        """
        from src.template_actions import TEMPLATES, build_template_spec, execute_template

        data = _payload(input_json)
        self._sync_pose(data)
        template = str(data.get("template") or data.get("template_id") or "").strip()
        if not template:
            return {"error": "需要 template", "templates": list(TEMPLATES)}
        params = dict(data.get("params") or {})
        for key in ("drone_id", "altitude", "clearance", "message", "photos"):
            if data.get(key) is not None and key not in params:
                params[key] = data[key]
        drone_ref = params.get("drone_id") or data.get("drone_id")
        if not drone_ref:
            best = self._best_drone()
            drone_ref = (best or {}).get("id") if isinstance(best, dict) else best
        drone = self.world.drone_by_name(str(drone_ref)) if drone_ref else None
        drone_id = str((drone or {}).get("id") or drone_ref or "")
        if drone is None:
            looks_like_id = len(drone_id) == 8 and all(c in "0123456789abcdefABCDEF" for c in drone_id)
            if not looks_like_id:
                return {"ok": False, "error": f"无法识别无人机 {drone_ref!r}",
                        "available_drones": [d.get("name") for d in self.world.drones]}
        if not drone_id:
            return {"error": "无法确定无人机"}
        params["drone_id"] = drone_id
        # 编队：先归一化参数（兼容 drones=["Bravo","Charlie"] + positions=[{drone,position}]）
        if template == "line_formation":
            raw = params.get("drones") or []
            positions = params.get("positions") or []
            pos_map: dict[str, tuple[float, float]] = {}
            aligned: list[tuple[float, float] | None] = []
            for item in positions:
                if not isinstance(item, dict):
                    aligned.append(None)
                    continue
                pos = item.get("position") if isinstance(item.get("position"), dict) else item
                xy = None
                if isinstance(pos, dict) and "x" in pos and "y" in pos:
                    xy = (float(pos["x"]), float(pos["y"]))
                aligned.append(xy)
                if xy is not None:
                    key = str(item.get("drone") or item.get("drone_id") or "")
                    if key:
                        pos_map[key] = xy
            normalized: list[dict] = []
            for idx, item in enumerate(raw):
                if isinstance(item, dict):
                    normalized.append(dict(item))
                    continue
                key = str(item)
                xy = pos_map.get(key) or (aligned[idx] if idx < len(aligned) else None)
                if xy is not None:
                    normalized.append({"drone_id": key, "x": xy[0], "y": xy[1]})
            if normalized:
                params["drones"] = normalized
        # 编队：若某架机的悬停点在障碍内，保持与首机距离不变、绕首机旋转到自由位置
        if template == "line_formation" and isinstance(params.get("drones"), list) and len(params["drones"]) >= 2:
            import math

            drones = [dict(item) if isinstance(item, dict) else item for item in params["drones"]]
            try:
                bx, by = _point(drones[0])
            except Exception:  # noqa: BLE001
                bx = by = None
            if bx is not None:
                for item in drones[1:]:
                    try:
                        x, y = _point(item)
                    except Exception:  # noqa: BLE001
                        continue
                    if self.world.clearance_to_obstacles((x, y)) > 0.0:
                        continue
                    dist = math.hypot(x - bx, y - by)
                    relocated = None
                    for angle in (90, -90, 45, -45, 135, -135, 180):
                        rad = math.radians(angle)
                        cand = (bx + math.cos(rad) * dist, by + math.sin(rad) * dist)
                        if self.world.clearance_to_obstacles(cand) > 2.0:
                            relocated = cand
                            break
                    if relocated is None:
                        return {"ok": False,
                                "error": f"编队点 ({x:.1f},{y:.1f}) 在障碍内，且找不到保持 {dist:.0f} m 间距的自由位置"}
                    if isinstance(item, dict):
                        item["x"], item["y"] = round(relocated[0], 2), round(relocated[1], 2)
                        for key in ("position", "target_position"):
                            if isinstance(item.get(key), dict):
                                item[key] = {"x": item["x"], "y": item["y"]}
            params["drones"] = drones
        # 覆盖率模板：允许传目标名，自动解析中心与半径
        if template == "target_coverage" and params.get("target") and not params.get("center"):
            coverage_target = self.world.target_by_name(str(params["target"]))
            if coverage_target is None:
                return {"ok": False, "error": f"地图里没有目标 {params['target']!r}",
                        "targets": [t.name for t in self.world.targets]}
            cx, cy = coverage_target.center
            radius = float(getattr(coverage_target, "radius", 0.0) or 0.0)
            if radius <= 1.0 and getattr(coverage_target, "vertices", None):
                footprint = coverage_target.footprint()
                xs = [pt[0] for pt in footprint]
                ys = [pt[1] for pt in footprint]
                radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2.0
            params["center"] = {"x": cx, "y": cy}
            params["radius"] = max(radius, 5.0)
            if self.world.clearance_to_obstacles((cx, cy)) <= 0.0:
                near = self.world.free_point_near((cx, cy), 2.0, 120.0)
                return {"ok": False,
                        "error": f"目标中心 ({cx:.1f},{cy:.1f}) 位于障碍物内部，覆盖率任务不可行",
                        "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None}
        # 拍照模板：允许传目标名，自动解析坐标；目标在障碍内时直接报不可达
        if template == "target_photo" and params.get("target") and "x" not in params:
            target = self.world.target_by_name(str(params["target"]))
            if target is None:
                return {"ok": False, "error": f"地图里没有目标 {params['target']!r}",
                        "targets": [t.name for t in self.world.targets]}
            params["x"], params["y"] = float(target.center[0]), float(target.center[1])
        # 起终点预检：若落在障碍物内，直接给出明确失败与最近自由点，避免模型反复重试
        for key in ("start", "end", "target_point"):
            if key in params and params[key] is not None:
                try:
                    x, y = _point(params[key])
                except Exception:  # noqa: BLE001
                    continue
                if self.world.clearance_to_obstacles((x, y)) <= 0.0:
                    near = self.world.free_point_near((x, y), 2.0, 120.0)
                    return {"ok": False,
                            "error": f"模板参数 {key}=({x:.1f},{y:.1f}) 位于障碍物内部，物理不可达"
                                     f"（可能是当前会话地图与默认地图不一致）",
                            "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None,
                            "hint": "请确认当前会话地图，或改用 nearest_free_point 作为起终点"}
        if template == "target_photo" and "x" in params and "y" in params:
            x, y = float(params["x"]), float(params["y"])
            if self.world.clearance_to_obstacles((x, y)) <= 0.0:
                near = self.world.free_point_near((x, y), 2.0, 80.0)
                return {"ok": False,
                        "error": f"目标 ({x:.1f},{y:.1f}) 位于障碍物内部，无法到达该目标",
                        "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None,
                        "hint": "该目标在当前场景中不可达，请换一个可达目标"}
        if params.get("corners") or params.get("waypoints") or params.get("points"):
            for idx, corner in enumerate(params.get("corners") or params.get("waypoints") or params.get("points"), start=1):
                try:
                    x, y = _point(corner)
                except Exception:  # noqa: BLE001
                    continue
                if self.world.clearance_to_obstacles((x, y)) <= 0.0:
                    near = self.world.free_point_near((x, y), 2.0, 80.0)
                    return {"ok": False,
                            "error": f"第 {idx} 个角点 ({x:.1f},{y:.1f}) 位于障碍物内部，该任务不可行",
                            "nearest_free_point": [round(near[0], 2), round(near[1], 2)] if near else None,
                            "hint": "任务坐标落入障碍物，建议换任务实例或使用最近自由点"}
        spec = build_template_spec(template, params)
        if spec.get("error"):
            return {"error": spec["error"], "templates": list(TEMPLATES)}
        result = execute_template(str(self.map_path), drone_id, template, params,
                                  self.config.uav_api_base)
        return _compact_result(result)


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
    ("nav_template_action", "【模板任务必用】按官方 10 类任务模板一次执行组合动作，参数 {\"template\":\"corridor_detour\",\"params\":{...}}；template 可选: altitude_stage / grid_survey / target_photo / line_formation / perimeter_patrol / relay_message / corridor_detour / battery_cycle / target_coverage / emergency_land。", "nav_template_action"),
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

        def _call(input_json=None, _fn=fn):
            try:
                result = _fn(input_json)
            except Exception as exc:  # noqa: BLE001
                result = {"error": f"{type(exc).__name__}: {exc}"}
            if not isinstance(result, dict):
                result = {"items": result}
            return json.dumps(result, ensure_ascii=False)

        tools.append(
            StructuredTool.from_function(
                func=_call,
                name=name,
                description=description + " 参数为 input_json 字符串。",
            )
        )
    return tools
