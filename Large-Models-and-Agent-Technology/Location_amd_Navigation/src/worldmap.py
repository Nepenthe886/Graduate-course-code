"""地图模型：从 MultiUAV-Plat session JSON 读取世界、障碍、目标、无人机。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from . import geometry
from .frames import Pose, pose_from_drone


@dataclass
class Obstacle:
    id: str
    name: str
    type: str
    center: tuple[float, float]
    height: float = 0.0
    radius: float | None = None
    width: float | None = None
    length: float | None = None
    vertices: list[dict] = field(default_factory=list)

    def footprint(self, segments: int = 24) -> list[tuple[float, float]]:
        """二维占用廓形（绝对坐标）。"""
        if self.type == "polygon" and self.vertices:
            return [(float(v["x"]), float(v["y"])) for v in self.vertices]
        if self.type == "circle" and self.radius:
            return geometry.circle_to_polygon(self.center, float(self.radius), segments)
        if self.type == "ellipse" and self.width and self.length:
            return geometry.ellipse_to_polygon(self.center, float(self.width), float(self.length), segments)
        # 兜底：把未知类型当作半径 10 m 的圆
        return geometry.circle_to_polygon(self.center, 10.0, segments)

    def contains(self, point: tuple[float, float]) -> bool:
        return geometry.point_in_polygon(point, self.footprint())

    def distance_to_point(self, point: tuple[float, float]) -> float:
        return geometry.point_segment_distance(point, point, point) if False else _point_polygon_distance(point, self.footprint())


def _point_polygon_distance(point: tuple[float, float], polygon: list[tuple[float, float]]) -> float:
    if geometry.point_in_polygon(point, polygon):
        return 0.0
    best = float("inf")
    n = len(polygon)
    for i in range(n):
        best = min(best, geometry.point_segment_distance(point, polygon[i], polygon[(i + 1) % n]))
    return best


@dataclass
class Target:
    id: str
    name: str
    center: tuple[float, float]
    radius: float = 1.0
    is_reached: bool = False
    type: str = "circle"
    width: float | None = None
    length: float | None = None
    vertices: list[dict] = field(default_factory=list)

    def footprint(self, segments: int = 24) -> list[tuple[float, float]]:
        """二维廓形（绝对坐标）：多边形用顶点，圆/椭圆按参数离散。"""
        if self.type == "polygon" and self.vertices:
            return [(float(v["x"]), float(v["y"])) for v in self.vertices]
        if self.type == "ellipse" and self.width and self.length:
            return geometry.ellipse_to_polygon(self.center, float(self.width), float(self.length), segments)
        return geometry.circle_to_polygon(self.center, max(float(self.radius or 1.0), 1.0), segments)

    def approx_area(self) -> float:
        polygon = self.footprint()
        area = 0.0
        n = len(polygon)
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0


@dataclass
class WorldMap:
    source: Path
    id: str = ""
    name: str = ""
    width: float = 1000.0
    height: float = 1000.0
    obstacles: list[Obstacle] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    drones: list[dict] = field(default_factory=list)

    # ---------- 几何查询 ----------
    def clearance_to_obstacles(self, point: tuple[float, float]) -> float:
        """点到所有障碍物的最小距离（0 表示在障碍内）。"""
        best = float("inf")
        for obstacle in self.obstacles:
            try:
                footprint = obstacle.footprint()
            except Exception:  # noqa: BLE001
                continue
            if geometry.point_in_polygon(point, footprint):
                return 0.0
            for index in range(len(footprint)):
                distance = geometry.point_segment_distance(
                    point, footprint[index], footprint[(index + 1) % len(footprint)])
                if distance < best:
                    best = distance
        return best if best != float("inf") else 1e9

    def free_point_near(self, point: tuple[float, float], clearance: float = 5.0,
                        max_radius: float = 80.0):
        """把落在障碍内（或离障碍过近）的点投影到最近的可飞点；找不到返回 None。"""
        if self.clearance_to_obstacles(point) >= clearance:
            return (float(point[0]), float(point[1]))
        import math as _math

        radius = 2.0
        while radius <= max_radius:
            for step in range(16):
                angle = 2.0 * _math.pi * step / 16.0
                candidate = (float(point[0]) + radius * _math.cos(angle),
                             float(point[1]) + radius * _math.sin(angle))
                if not (0.0 <= candidate[0] <= self.width and 0.0 <= candidate[1] <= self.height):
                    continue
                if self.clearance_to_obstacles(candidate) >= clearance:
                    return candidate
            radius += 2.0
        return None

    # ---------- 加载 ----------
    @classmethod
    def find_scenario(cls, session_name: str, dirs: list) -> Path | None:
        """按会话名在场景目录中匹配 JSON（忽略大小写、空格与下划线）。"""
        def norm(text: str) -> str:
            return "".join(ch for ch in str(text).lower() if ch.isalnum())

        needle = norm(session_name or "")
        if not needle:
            return None
        for directory in dirs:
            base = Path(directory)
            if not base.is_dir():
                continue
            for path in sorted(base.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if norm(str(data.get("name") or "")) == needle:
                    return path
        return None

    @classmethod
    def load(cls, path: str | Path) -> "WorldMap":
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        obstacles: list[Obstacle] = []
        for item in data.get("obstacles") or []:
            pos = item.get("position") or {}
            obstacles.append(
                Obstacle(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    type=str(item.get("type", "circle")),
                    center=(float(pos.get("x", 0.0)), float(pos.get("y", 0.0))),
                    height=float(item.get("height") or 0.0),
                    radius=item.get("radius"),
                    width=item.get("width"),
                    length=item.get("length"),
                    vertices=item.get("vertices") or [],
                )
            )
        targets: list[Target] = []
        for item in data.get("targets") or []:
            pos = item.get("position") or {}
            targets.append(
                Target(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    center=(float(pos.get("x", 0.0)), float(pos.get("y", 0.0))),
                    radius=float(item.get("radius") or 1.0),
                    is_reached=bool(item.get("is_reached", False)),
                    type=str(item.get("type", "circle")),
                    width=item.get("width"),
                    length=item.get("length"),
                    vertices=item.get("vertices") or [],
                )
            )
        return cls(
            source=source,
            id=str(data.get("id", "")),
            name=str(data.get("name", source.stem)),
            width=float(data.get("canvas_width") or 1000.0),
            height=float(data.get("canvas_height") or 1000.0),
            obstacles=obstacles,
            targets=targets,
            drones=list(data.get("drones") or []),
        )

    # ---------- 查询 ----------
    def drone_by_name(self, name: str) -> dict | None:
        low = name.strip().lower()
        for drone in self.drones:
            if str(drone.get("name", "")).strip().lower() == low:
                return drone
        for drone in self.drones:
            if low in str(drone.get("name", "")).strip().lower():
                return drone
        return None

    def drone_pose(self, name: str) -> Pose | None:
        drone = self.drone_by_name(name)
        return pose_from_drone(drone) if drone else None

    def target_by_name(self, name: str) -> Target | None:
        low = name.strip().lower()
        for target in self.targets:
            if target.name.strip().lower() == low:
                return target
        for target in self.targets:
            if low in target.name.strip().lower():
                return target
        return None

    def obstacle_by_name(self, name: str) -> Obstacle | None:
        low = name.strip().lower()
        for obstacle in self.obstacles:
            if obstacle.name.strip().lower() == low:
                return obstacle
        for obstacle in self.obstacles:
            if low in obstacle.name.strip().lower():
                return obstacle
        return None

    def obstacles_near(self, point: tuple[float, float], radius: float) -> list[tuple[Obstacle, float]]:
        result = []
        for obstacle in self.obstacles:
            dist = _point_polygon_distance(point, obstacle.footprint())
            if dist <= radius:
                result.append((obstacle, dist))
        result.sort(key=lambda item: item[1])
        return result

    def in_bounds(self, point: tuple[float, float], margin: float = 0.0) -> bool:
        x, y = point
        return (margin <= x <= self.width - margin) and (margin <= y <= self.height - margin)

    def clamp_to_bounds(self, point: tuple[float, float], margin: float = 0.0) -> tuple[float, float]:
        x = min(max(point[0], margin), self.width - margin)
        y = min(max(point[1], margin), self.height - margin)
        return (x, y)

    def summary(self) -> dict:
        return {
            "map": self.name,
            "id": self.id,
            "source": str(self.source),
            "world": {"width": self.width, "height": self.height},
            "obstacles": [{"name": o.name, "type": o.type, "center": o.center,
                           "height": o.height, "radius": o.radius} for o in self.obstacles],
            "targets": [{"name": t.name, "center": t.center, "radius": t.radius} for t in self.targets],
            "drones": len(self.drones),
        }
