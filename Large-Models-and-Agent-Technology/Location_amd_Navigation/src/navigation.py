"""导航：把"去哪"翻译成"怎么飞"（含 2D 绕障 / 3D 飞越），并做到达验证/重规划。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .frames import Pose, distance_xy, relative_bearing_deg
from .localization import Localizer
from .planner import Planner, Route
from .worldmap import WorldMap


@dataclass
class NavStep:
    kind: str          # turn | climb | goto | hover | photo
    x: float | None = None
    y: float | None = None
    z: float | None = None
    yaw_deg: float | None = None
    note: str = ""

    def as_dict(self) -> dict:
        data = {"kind": self.kind, "note": self.note}
        for key in ("x", "y", "z", "yaw_deg"):
            value = getattr(self, key)
            if value is not None:
                data[key] = round(value, 2)
        return data


class Navigation:
    def __init__(self, world: WorldMap, localizer: Localizer, planner: Planner | None = None,
                 arrival_tolerance: float = 5.0, z_default: float = 59.0):
        self.world = world
        self.localizer = localizer
        self.planner = planner or Planner(world)
        self.arrival_tolerance = arrival_tolerance
        self.z_default = z_default

    @property
    def pose(self) -> Pose:
        return self.localizer.pose

    # ---------- 规划 ----------
    def plan_to(self, goal: tuple[float, float], clearance: float | None = None,
                z: float | None = None) -> Route:
        altitude = self.z_default if z is None else float(z)
        return self.planner.plan(self.pose.xy, goal, clearance, altitude=altitude)

    def plan_to_target(self, target_name: str, clearance: float | None = None,
                       z: float | None = None) -> Route:
        target = self.world.target_by_name(target_name)
        if target is None:
            raise KeyError(f"地图里没有目标 {target_name!r}")
        return self.plan_to(target.center, clearance, z)

    # ---------- 动作序列 ----------
    def steps_to(self, goal: tuple[float, float], z: float | None = None,
                 clearance: float | None = None) -> list[NavStep]:
        altitude = self.z_default if z is None else float(z)
        route = self.plan_to(goal, clearance, altitude)
        steps: list[NavStep] = []
        pose = self.pose
        first = route.waypoints[0] if route.waypoints else goal
        steps.append(NavStep("turn", yaw_deg=relative_bearing_deg(pose, first),
                             note="align heading to first waypoint"))

        current_z = pose.z
        for index, waypoint in enumerate(route.waypoints, start=1):
            target_z = altitude
            if route.waypoints_xyz and index - 1 < len(route.waypoints_xyz):
                target_z = route.waypoints_xyz[index - 1][2] if index - 1 else route.waypoints_xyz[0][2]
            if abs(target_z - current_z) > 1.0:
                steps.append(NavStep("climb", z=target_z, note="vertical transition"))
                current_z = target_z
            note = f"leg {index}/{len(route.waypoints)}"
            if index == len(route.waypoints):
                note += " (goal)"
            steps.append(NavStep("goto", x=waypoint[0], y=waypoint[1], z=target_z, note=note))

        if route.waypoints_xyz and len(route.waypoints_xyz) > len(route.waypoints):
            final_z = route.waypoints_xyz[-1][2]
            if abs(final_z - current_z) > 1.0:
                steps.append(NavStep("climb", z=final_z, note="descend to target altitude"))
        steps.append(NavStep("hover", z=current_z, note="hold position and verify arrival"))
        return steps

    def waypoints_to(self, goal: tuple[float, float], z: float | None = None,
                     clearance: float | None = None) -> list[dict]:
        """给 UAV move_along_path 用的航点列表（3D 时含垂直转场点）。"""
        altitude = self.z_default if z is None else float(z)
        route = self.plan_to(goal, clearance, altitude)
        if route.waypoints_xyz:
            return [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)}
                    for p in route.waypoints_xyz[1:]]
        return [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(altitude, 2)}
                for p in route.waypoints]

    # ---------- 重规划 / 验证 ----------
    def detour(self, blocked_at: tuple[float, float], goal: tuple[float, float],
               clearance: float | None = None, z: float | None = None) -> Route:
        return self.plan_to(goal, clearance, z)

    def arrived(self, goal: tuple[float, float], tolerance: float | None = None) -> bool:
        limit = self.arrival_tolerance if tolerance is None else tolerance
        return distance_xy(self.pose.xy, goal) <= limit

    def verify(self, goal: tuple[float, float], tolerance: float | None = None) -> dict:
        limit = self.arrival_tolerance if tolerance is None else tolerance
        distance = distance_xy(self.pose.xy, goal)
        return {
            "arrived": distance <= limit,
            "distance_m": round(distance, 2),
            "tolerance_m": limit,
            "pose": {"x": round(self.pose.x, 2), "y": round(self.pose.y, 2), "z": round(self.pose.z, 2)},
        }

    def nearby_obstacles(self, radius: float = 100.0) -> list[dict]:
        result = []
        for obstacle, distance in self.world.obstacles_near(self.pose.xy, radius):
            result.append({
                "name": obstacle.name,
                "type": obstacle.type,
                "center": list(obstacle.center),
                "distance_m": round(distance, 2),
                "height_m": obstacle.height,
            })
        return result
