"""二维几何：点/线段/多边形/圆/椭圆的相交与距离。

只依赖 math，够用且无第三方库。
"""
from __future__ import annotations

import math

Point = tuple[float, float]
Polygon = list[Point]

EPS = 1e-9


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def point_in_polygon(p: Point, polygon: Polygon) -> bool:
    """射线法；边界视作内部。"""
    x, y = p
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if point_segment_distance(p, (xi, yi), (xj, yj)) <= EPS:
            return True
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi + EPS) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if (o1 > EPS) != (o2 > EPS) and (o3 > EPS) != (o4 > EPS):
        return True
    # 共线/端点接触
    for o, p, q, r in ((o1, a, b, c), (o2, a, b, d), (o3, c, d, a), (o4, c, d, b)):
        if abs(o) <= EPS and point_segment_distance(r, p, q) <= EPS:
            return True
    return False


def segment_polygon_intersection(a: Point, b: Point, polygon: Polygon) -> bool:
    if len(polygon) < 3:
        return False
    n = len(polygon)
    for i in range(n):
        if segments_intersect(a, b, polygon[i], polygon[(i + 1) % n]):
            return True
    return point_in_polygon(a, polygon) or point_in_polygon(b, polygon)


def segment_polygon_distance(a: Point, b: Point, polygon: Polygon) -> float:
    """线段到多边形边界的最小距离（相交时为 0）。"""
    n = len(polygon)
    best = float("inf")
    for i in range(n):
        c = polygon[i]
        d = polygon[(i + 1) % n]
        if segments_intersect(a, b, c, d):
            return 0.0
        best = min(
            best,
            point_segment_distance(a, c, d),
            point_segment_distance(b, c, d),
            point_segment_distance(c, a, b),
            point_segment_distance(d, a, b),
        )
    if point_in_polygon(a, polygon) or point_in_polygon(b, polygon):
        return 0.0
    return best


def segment_circle_distance(a: Point, b: Point, center: Point, radius: float) -> float:
    return max(0.0, point_segment_distance(center, a, b) - radius)


def circle_to_polygon(center: Point, radius: float, segments: int = 24) -> Polygon:
    cx, cy = center
    return [
        (cx + radius * math.cos(2 * math.pi * i / segments),
         cy + radius * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def ellipse_to_polygon(center: Point, width: float, length: float, segments: int = 24) -> Polygon:
    """width->x 半轴，length->y 半轴。

    注意：MultiUAV-Plat 的 ellipse 里 width/length 就是**半轴（半径）**，
    由 area = pi * width * length 反推确认（4 张地图全部吻合）。
    早期按全长(w/2)计算会把椭圆算小一半，导致规划出的绕行点落进真实障碍内。
    """
    cx, cy = center
    rx, ry = float(width), float(length)
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def polygon_centroid(polygon: Polygon) -> Point:
    n = len(polygon)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in polygon) / n, sum(p[1] for p in polygon) / n)


def polygon_contains_segment(a: Point, b: Point, polygon: Polygon) -> bool:
    return segment_polygon_intersection(a, b, polygon)
