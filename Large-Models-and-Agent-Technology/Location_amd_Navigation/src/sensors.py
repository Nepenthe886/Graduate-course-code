"""传感器/条件（L3 薄层）：只读状态，不做动作、不做编排。

行为树的条件节点与"定向位移"等模板通过这里读取真实世界状态。
"""
from __future__ import annotations

import math


def read_pose(api, drone_id: str) -> dict:
    """读取无人机当前位姿 {x, y, z, heading, status}。"""
    data = api.drone(drone_id) or {}
    position = data.get("position") or {}
    return {
        "x": float(position.get("x", 0.0)),
        "y": float(position.get("y", 0.0)),
        "z": float(position.get("z", 0.0)),
        "heading": float(data.get("heading") or 0.0),
        "status": str(data.get("status") or ""),
    }


def target_pose(api, target_id: str) -> dict:
    """读取目标当前位置（移动目标每次 tick 重新读取）。"""
    getter = getattr(api, "target", None)
    if getter is None or not target_id:
        return {}
    data = getter(target_id) or {}
    position = data.get("position") or {}
    if not position:
        return {}
    return {
        "x": float(position.get("x", 0.0)),
        "y": float(position.get("y", 0.0)),
        "z": float(position.get("z", 0.0)),
        "radius": float(data.get("radius") or 0.0),
    }


def distance_to(api, drone_id: str, goal) -> float:
    pose = read_pose(api, drone_id)
    return math.hypot(pose["x"] - float(goal[0]), pose["y"] - float(goal[1]))


def directed_progress(pose_from: dict, pose_to: dict, heading: float) -> float:
    """沿 heading 方向的有向位移（heading 0=北/+y，90=东/+x）。"""
    rad = math.radians(float(heading))
    dx = pose_to["x"] - pose_from["x"]
    dy = pose_to["y"] - pose_from["y"]
    return dx * math.sin(rad) + dy * math.cos(rad)


def is_hovering(pose: dict, tolerance: float = 0.5) -> bool:
    return str(pose.get("status") or "").lower() == "hovering" and pose.get("z", 0.0) > tolerance
