"""定位：世界系/机体系位姿管理，支持静态初始化、UAV API 同步、航位推算。"""
from __future__ import annotations

import math
from typing import Callable

from .frames import Pose, bearing_deg, distance_xy, pose_from_drone, relative_bearing_deg


class Localizer:
    """维护"我在哪"。

    pose_provider: 无参回调 -> Pose 或 None（例如从 UAV API 拉取最新位姿）。
    """

    def __init__(self, pose: Pose | None = None, pose_provider: Callable[[], Pose | None] | None = None):
        self._pose = pose or Pose()
        self._provider = pose_provider

    # ---------- 状态 ----------
    @property
    def pose(self) -> Pose:
        self.sync()
        return self._pose

    def set_pose(self, pose: Pose) -> None:
        self._pose = pose

    def sync(self) -> Pose:
        if self._provider is not None:
            fresh = self._provider()
            if fresh is not None:
                self._pose = fresh
        return self._pose

    # ---------- 航位推算（没有 API 时估算） ----------
    def dead_reckon(self, forward: float = 0.0, left: float = 0.0, climb: float = 0.0,
                    turn_deg: float = 0.0) -> Pose:
        pose = self._pose
        yaw = math.radians(pose.yaw_deg + turn_deg)
        dx = forward * math.cos(yaw) - left * math.sin(yaw)
        dy = forward * math.sin(yaw) + left * math.cos(yaw)
        self._pose = Pose(pose.x + dx, pose.y + dy, pose.z + climb, pose.yaw_deg + turn_deg)
        return self._pose

    # ---------- 相对量 ----------
    def distance_to(self, point: tuple[float, float]) -> float:
        return distance_xy(self.pose.xy, point)

    def bearing_to(self, point: tuple[float, float]) -> float:
        return bearing_deg(self.pose.xy, point)

    def relative_bearing(self, point: tuple[float, float]) -> float:
        return relative_bearing_deg(self.pose, point)

    def describe(self, target: tuple[float, float] | None = None) -> dict:
        data = {
            "pose": {"x": round(self.pose.x, 2), "y": round(self.pose.y, 2),
                     "z": round(self.pose.z, 2), "yaw_deg": round(self.pose.yaw_deg, 2)},
        }
        if target is not None:
            data["target"] = {
                "bearing_deg": round(self.bearing_to(target), 2),
                "relative_bearing_deg": round(self.relative_bearing(target), 2),
                "distance_m": round(self.distance_to(target), 2),
            }
        return data


def localizer_from_world(world, drone_name: str, pose_provider: Callable[[], Pose | None] | None = None) -> Localizer:
    pose = world.drone_pose(drone_name) or Pose()
    return Localizer(pose=pose, pose_provider=pose_provider)
