"""坐标系与位姿。

约定（可在 configs/default.json 的 frame 里改注释）：
  世界系 W：右手系，x=东，y=北，z=上，单位米
  机体系 B：x=机头前向，y=机体左向，z=上
  yaw：度，0 指向世界 x 轴，逆时针为正
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw_deg: float = 0.0

    @property
    def xy(self) -> tuple[float, float]:
        return (self.x, self.y)

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def yaw_rad(self) -> float:
        return math.radians(self.yaw_deg)


def wrap_deg(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def distance_xy(a: tuple[float, float] | Pose, b: tuple[float, float] | Pose) -> float:
    ax, ay = (a.xy if isinstance(a, Pose) else a)
    bx, by = (b.xy if isinstance(b, Pose) else b)
    return math.hypot(bx - ax, by - ay)


def distance_3d(a: Pose, b: Pose) -> float:
    return math.sqrt((b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2)


def bearing_deg(origin: tuple[float, float] | Pose, target: tuple[float, float] | Pose) -> float:
    """从 origin 指向 target 的方位角（世界系，度）。"""
    ox, oy = (origin.xy if isinstance(origin, Pose) else origin)
    tx, ty = (target.xy if isinstance(target, Pose) else target)
    return wrap_deg(math.degrees(math.atan2(ty - oy, tx - ox)))


def relative_bearing_deg(pose: Pose, target: tuple[float, float]) -> float:
    """目标相对机头的偏航（度，右正左负）。"""
    return wrap_deg(bearing_deg(pose.xy, target) - pose.yaw_deg)


def world_to_body(pose: Pose, point: tuple[float, float, float]) -> tuple[float, float, float]:
    dx = point[0] - pose.x
    dy = point[1] - pose.y
    cos_yaw = math.cos(-pose.yaw_rad)
    sin_yaw = math.sin(-pose.yaw_rad)
    bx = dx * cos_yaw - dy * sin_yaw
    by = dx * sin_yaw + dy * cos_yaw
    return (bx, by, point[2] - pose.z)


def body_to_world(pose: Pose, point: tuple[float, float, float]) -> tuple[float, float, float]:
    bx, by, bz = point
    cos_yaw = math.cos(pose.yaw_rad)
    sin_yaw = math.sin(pose.yaw_rad)
    wx = pose.x + bx * cos_yaw - by * sin_yaw
    wy = pose.y + bx * sin_yaw + by * cos_yaw
    return (wx, wy, pose.z + bz)


def pose_from_drone(drone: dict) -> Pose:
    """从 UAV session/API 的 drone 字典取位姿。"""
    pos = drone.get("position") or {}
    return Pose(
        x=float(pos.get("x", 0.0)),
        y=float(pos.get("y", 0.0)),
        z=float(pos.get("z", 0.0)),
        yaw_deg=float(drone.get("heading", 0.0) or 0.0),
    )
