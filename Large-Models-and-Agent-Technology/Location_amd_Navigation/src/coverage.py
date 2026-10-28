"""覆盖航线：蛇形（lawnmower/boustrophedon）+ 覆盖率估算。"""
from __future__ import annotations

import math

Point = tuple[float, float]


def _point_segment_distance(px: float, py: float, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def serpentine_route(center: Point, radius: float, spacing: float) -> list[Point]:
    """圆形区域内的蛇形覆盖航线（相邻平行航段交替方向）。"""
    cx, cy = center
    r = float(radius)
    spacing = max(2.0, float(spacing))
    points: list[Point] = []
    y = -r + spacing / 2.0
    index = 0
    while y <= r:
        half = math.sqrt(max(0.0, r * r - y * y))
        if half > 0.5:
            left, right = (cx - half, cy + y), (cx + half, cy + y)
            a, b = (left, right) if index % 2 == 0 else (right, left)
            if not points or math.dist(points[-1], a) > 1e-9:
                points.append(a)
            points.append(b)
            index += 1
        y += spacing
    return points or [(cx, cy)]


def estimate_coverage(center: Point, radius: float, lanes: list[tuple[Point, Point]],
                      swath: float) -> float:
    """1 m 级栅格估算：格子到任一航段距离 <= swath/2 记为覆盖。"""
    cx, cy = center
    r = max(1.0, float(radius))
    half = float(swath) / 2.0
    step = max(2.0, r / 40.0)
    total = covered = 0
    y = -r
    while y <= r:
        x = -r
        while x <= r:
            if x * x + y * y <= r * r:
                total += 1
                px, py = cx + x, cy + y
                if any(_point_segment_distance(px, py, a, b) <= half for a, b in lanes):
                    covered += 1
            x += step
        y += step
    return (covered / total) if total else 0.0

def serpentine_polygon(vertices: list[tuple[float, float]], spacing: float) -> list[tuple[float, float]]:
    """任意多边形内部的蛇形覆盖航线（按行扫描，取每行最长内部区间）。"""
    if len(vertices) < 3:
        return []
    ys = [v[1] for v in vertices]
    min_y, max_y = min(ys), max(ys)
    spacing = max(2.0, float(spacing))
    span = max_y - min_y
    # 均匀铺满：让首末行正好覆盖上下边界（半行距 <= 覆盖半幅）
    lane_count = max(1, int(math.ceil(span / spacing))) if span > 0 else 1
    effective = (span / lane_count) if span > 0 else spacing
    lanes: list[tuple[tuple[float, float], tuple[float, float]]] = []
    index = 0
    n = len(vertices)
    lane_index = 0
    while lane_index < lane_count:
        y = min_y + effective * (lane_index + 0.5)
        lane_index += 1
        hits: list[float] = []
        for i in range(n):
            x1, y1 = vertices[i]
            x2, y2 = vertices[(i + 1) % n]
            if (y1 <= y < y2) or (y2 <= y < y1):
                t = (y - y1) / (y2 - y1)
                hits.append(x1 + t * (x2 - x1))
        hits.sort()
        if len(hits) >= 2 and hits[-1] - hits[0] > 0.5:
            a, b = (hits[0], y), (hits[-1], y)
            lanes.append((a, b) if index % 2 == 0 else (b, a))
            index += 1
    points: list[tuple[float, float]] = []
    for a, b in lanes:
        if not points or math.dist(points[-1], a) > 1e-9:
            points.append(a)
        points.append(b)
    return points


def estimate_coverage_polygon(vertices: list[tuple[float, float]],
                              lanes: list[tuple[tuple[float, float], tuple[float, float]]],
                              swath: float) -> float:
    """多边形区域覆盖率（1 m 级栅格）。"""
    if not vertices:
        return 0.0
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    half = float(swath) / 2.0
    step = max(2.0, (max(xs) - min(xs)) / 60.0)
    total = covered = 0
    from .geometry import point_in_polygon

    y = min(ys)
    while y <= max(ys):
        x = min(xs)
        while x <= max(xs):
            if point_in_polygon((x, y), vertices):
                total += 1
                if any(_point_segment_distance(x, y, a, b) <= half for a, b in lanes):
                    covered += 1
            x += step
        y += step
    return (covered / total) if total else 0.0
