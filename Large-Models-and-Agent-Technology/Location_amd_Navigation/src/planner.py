"""路径规划：2D 水平绕障 / 3D 最短路径（3D 栅格 A* + 平滑）。

规划维度（configs/default.json 的 planning_dim）：
  "2d"（默认）：障碍视为无限高柱体，只做水平绕行（与当前仿真语义一致）
  "3d"        ：在 (x,y,z) 三维栅格上做 A* 最短路径，允许爬升飞越 `height>0` 的障碍；
                结果再做 3D 视线短路 + Chaikin 平滑，并逐段校验净空

3D 参数：
  vertical_margin   飞越障碍所需的高度余量（米）
  max_altitude      允许爬升的最高高度（米）
  altitude_step     z 方向栅格间距（米）
  vertical_penalty  垂直移动代价倍数（越大越倾向水平绕行）
  smoothing         是否启用轨迹平滑
  smooth_iterations Chaikin 迭代次数
  max_waypoints     平滑后最多保留的航点数
  max_turn_deg      允许的最大航向转角（超过会自动多平滑一轮）
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from . import geometry
from .worldmap import Obstacle, WorldMap

Point = tuple[float, float]
Point3 = tuple[float, float, float]

NEIGHBOURS_HV: list[tuple[int, int, int]] = [
    (dx, dy, 0) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
] + [(0, 0, 1), (0, 0, -1)] + [
    (dx, dy, dz) for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)
]


@dataclass
class SegmentCheck:
    clear: bool
    clearance: float
    blocker: str | None
    altitude: float | None = None

    def as_dict(self) -> dict:
        data = {"clear": self.clear, "clearance": round(self.clearance, 2), "blocker": self.blocker}
        if self.altitude is not None:
            data["altitude"] = round(self.altitude, 2)
        return data


@dataclass
class Route:
    start: Point
    goal: Point
    waypoints: list[Point]
    length: float = 0.0
    clearance: float = 0.0
    detoured: bool = False
    avoided: list[str] = field(default_factory=list)
    planning_dim: str = "2d"
    altitude: float = 0.0
    waypoints_xyz: list[Point3] = field(default_factory=list)
    overfly: list[str] = field(default_factory=list)
    length_3d: float = 0.0
    smoothed: bool = False
    max_turn_deg: float = 0.0
    z_min: float = 0.0
    z_max: float = 0.0

    def as_dict(self) -> dict:
        return {
            "start": list(self.start),
            "goal": list(self.goal),
            "waypoints": [list(p) for p in self.waypoints],
            "waypoints_xyz": [list(p) for p in self.waypoints_xyz],
            "length": round(self.length, 2),
            "length_3d": round(self.length_3d or self.length, 2),
            "min_clearance": round(self.clearance, 2),
            "detoured": self.detoured,
            "avoided": self.avoided,
            "planning_dim": self.planning_dim,
            "altitude": round(self.altitude, 2),
            "overfly": self.overfly,
            "smoothed": self.smoothed,
            "max_turn_deg": round(self.max_turn_deg, 2),
            "z_range": [round(self.z_min, 2), round(self.z_max, 2)],
        }


class Planner:
    def __init__(self, world: WorldMap, clearance: float = 1.5, grid_step: float = 10.0,
                 planning_dim: str = "2d", altitude: float | None = None,
                 vertical_margin: float = 5.0, max_altitude: float = 120.0,
                 altitude_step: float = 10.0, vertical_penalty: float = 1.5,
                 smoothing: bool = True, smooth_iterations: int = 2,
                 max_waypoints: int = 40, max_turn_deg: float = 60.0,
                 z_min: float = 5.0, grid_max_expansions: int = 4000,
                 plan_time_budget: float = 1.5):
        self.world = world
        self.clearance = float(clearance)
        self.grid_step = float(grid_step)
        self.planning_dim = (planning_dim or "2d").lower()
        if self.planning_dim not in ("2d", "3d"):
            raise ValueError(f"planning_dim 只能是 '2d'/'3d'，收到 {planning_dim!r}")
        self.altitude = float(altitude) if altitude is not None else 0.0
        self.vertical_margin = float(vertical_margin)
        self.max_altitude = float(max_altitude)
        self.altitude_step = float(altitude_step)
        self.vertical_penalty = float(vertical_penalty)
        self.smoothing = bool(smoothing)
        self.smooth_iterations = int(smooth_iterations)
        self.max_waypoints = int(max_waypoints)
        self.max_turn_deg = float(max_turn_deg)
        self.z_min = float(z_min)
        self.grid_max_expansions = int(grid_max_expansions)
        self.plan_time_budget = float(plan_time_budget)
        self._footprints = {o.name: o.footprint() for o in world.obstacles}

    # ================================================================ 2D
    def obstacle_blocks(self, obstacle: Obstacle, altitude: float | None) -> bool:
        if self.planning_dim != "3d" or altitude is None:
            return True
        height = float(obstacle.height or 0.0)
        if height <= 0.0:
            return True
        return altitude < height + self.vertical_margin

    def active_obstacles(self, altitude: float | None = None) -> list[Obstacle]:
        if self.planning_dim != "3d":
            return list(self.world.obstacles)
        return [o for o in self.world.obstacles if self.obstacle_blocks(o, altitude)]

    def segment(self, start: Point, goal: Point, clearance: float | None = None,
                altitude: float | None = None) -> SegmentCheck:
        margin = self.clearance if clearance is None else float(clearance)
        alt = self.altitude if altitude is None else float(altitude)
        worst = float("inf")
        blocker = None
        for obstacle in self.active_obstacles(alt if self.planning_dim == "3d" else None):
            dist = geometry.segment_polygon_distance(start, goal, self._footprints[obstacle.name])
            if dist < worst:
                worst, blocker = dist, obstacle.name
        if worst == float("inf"):
            worst = math.hypot(goal[0] - start[0], goal[1] - start[1])
        return SegmentCheck(clear=worst >= margin, clearance=worst, blocker=blocker, altitude=alt)

    def point_clear(self, point: Point, clearance: float | None = None,
                    altitude: float | None = None) -> bool:
        margin = self.clearance if clearance is None else float(clearance)
        # 注意：不检查画布边界——实测服务端允许飞到画布外（如 y=900/943）
        for obstacle in self.active_obstacles(altitude if self.planning_dim == "3d" else None):
            polygon = self._footprints[obstacle.name]
            if geometry.point_in_polygon(point, polygon):
                return False
            if min(geometry.point_segment_distance(point, polygon[i], polygon[(i + 1) % len(polygon)])
                   for i in range(len(polygon))) < margin:
                return False
        return True

    # ---------- 3D 体素级检查 ----------
    def point_free_3d(self, x: float, y: float, z: float, clearance: float | None = None) -> bool:
        margin = self.clearance if clearance is None else float(clearance)
        # 不检查画布边界：服务端允许飞到画布外
        if z < 0.0 or z > self.max_altitude:
            return False
        for obstacle in self.world.obstacles:
            height = float(obstacle.height or 0.0)
            if height > 0.0 and z >= height + self.vertical_margin:
                continue
            polygon = self._footprints[obstacle.name]
            if geometry.point_in_polygon((x, y), polygon):
                return False
            if min(geometry.point_segment_distance((x, y), polygon[i], polygon[(i + 1) % len(polygon)])
                   for i in range(len(polygon))) < margin:
                return False
        return True

    def segment_free_3d(self, a: Point3, b: Point3, clearance: float | None = None) -> bool:
        distance = math.dist(a, b)
        if distance < 1e-6:
            return self.point_free_3d(*a, clearance=clearance)
        step = max(self.grid_step * 0.5, 2.0)
        for index in range(int(distance / step) + 2):
            t = min(1.0, index * step / distance)
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            z = a[2] + (b[2] - a[2]) * t
            if not self.point_free_3d(x, y, z, clearance):
                return False
        return True

    # ================================================================ 入口
    def plan(self, start: Point, goal: Point, clearance: float | None = None,
             altitude: float | None = None, target_altitude: float | None = None) -> Route:
        margin = self.clearance if clearance is None else float(clearance)
        start = self.world.clamp_to_bounds(tuple(float(v) for v in start), 0.0)
        # 目标不做边界夹取：实测仿真允许飞到画布外（如 y=900/937）
        goal = (float(goal[0]), float(goal[1]))
        alt = self.altitude if altitude is None else float(altitude)
        target_alt = alt if target_altitude is None else float(target_altitude)

        if self.planning_dim == "3d":
            return self._plan_3d(start, goal, margin, alt, target_alt)

        check = self.segment(start, goal, margin, alt)
        if check.clear:
            length = math.hypot(goal[0] - start[0], goal[1] - start[1])
            return Route(start, goal, [goal], length=length, clearance=check.clearance,
                         planning_dim="2d", altitude=alt,
                         waypoints_xyz=[(start[0], start[1], alt), (goal[0], goal[1], alt)],
                         length_3d=length)
        route = None
        last_error = None
        for factor in (1.0, 0.65, 0.4):
            clr = margin * factor
            try:
                route = self._fast_grid_plan(start, goal, clr, alt)
                if route is not None:
                    break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            try:
                route = self._plan_horizontal(start, goal, clr, alt, check.blocker)
                break
            except RuntimeError as exc:
                last_error = exc
        if route is None:
            raise RuntimeError(str(last_error) if last_error else 'plan failed')
        route.planning_dim = "2d"
        route.altitude = alt
        route.waypoints_xyz = [(start[0], start[1], alt)] + [(p[0], p[1], alt) for p in route.waypoints]
        route.length_3d = route.length
        return route

    # ================================================================ 3D
    def _z_levels(self, alt_start: float) -> list[float]:
        base = max(self.z_min, min(alt_start, self.max_altitude))
        levels = [base]
        while levels[-1] + self.altitude_step <= self.max_altitude + 1e-9:
            levels.append(levels[-1] + self.altitude_step)
        return levels

    def _plan_3d(self, start: Point, goal: Point, margin: float, alt_start: float,
                 alt_goal: float) -> Route:
        start3: Point3 = (start[0], start[1], alt_start)
        goal3: Point3 = (goal[0], goal[1], alt_goal)

        direct = self.segment_free_3d(start3, goal3, margin)
        if direct:
            length = math.dist(start3, goal3)
            return Route(start, goal, [goal], length=length, clearance=margin,
                         planning_dim="3d", altitude=alt_start,
                         waypoints_xyz=[start3, goal3], length_3d=length,
                         z_min=min(start3[2], goal3[2]), z_max=max(start3[2], goal3[2]))

        lattice_path = self._astar_3d(start3, goal3, margin)
        if lattice_path is None:
            route = self._plan_horizontal(start, goal, margin, alt_start, None)
            route.planning_dim = "3d"
            route.altitude = alt_start
            route.waypoints_xyz = [(start[0], start[1], alt_start)] + [
                (p[0], p[1], alt_start) for p in route.waypoints]
            route.length_3d = route.length
            return route

        points = self._shortcut_3d(lattice_path, margin)
        smoothed, used_smoothing = self._smooth_3d(points, margin)

        length = sum(math.dist(smoothed[i], smoothed[i + 1]) for i in range(len(smoothed) - 1))
        turn = self._max_turn(smoothed)
        overfly = [o.name for o in self.world.obstacles
                   if float(o.height or 0.0) > 0.0 and alt_start < float(o.height) + self.vertical_margin
                   and max(p[2] for p in smoothed) >= float(o.height) + self.vertical_margin]
        avoided = [o.name for o in self.world.obstacles if float(o.height or 0.0) <= 0.0]
        return Route(
            start=start, goal=goal,
            waypoints=[(p[0], p[1]) for p in smoothed[1:]],
            length=length, clearance=margin, detoured=True,
            avoided=avoided, planning_dim="3d", altitude=max(p[2] for p in smoothed),
            waypoints_xyz=list(smoothed), overfly=overfly, length_3d=length,
            smoothed=used_smoothing, max_turn_deg=turn,
            z_min=min(p[2] for p in smoothed), z_max=max(p[2] for p in smoothed),
        )

    # ---------- 3D 栅格 A* ----------
    def _astar_3d(self, start3: Point3, goal3: Point3, margin: float) -> list[Point3] | None:
        step = self.grid_step
        levels = self._z_levels(start3[2])
        if levels[-1] < goal3[2]:
            levels.append(goal3[2])
        nx = int(math.floor(self.world.width / step))
        ny = int(math.floor(self.world.height / step))
        nz = len(levels)

        def snap(point: Point3, preferred_z: float) -> tuple[int, int, int] | None:
            ix = min(max(int(round(point[0] / step)), 0), nx)
            iy = min(max(int(round(point[1] / step)), 0), ny)
            best = None
            for iz in range(nz):
                node = (min(ix, nx), min(iy, ny), iz)
                xyz = (node[0] * step, node[1] * step, levels[node[2]])
                if self.point_free_3d(*xyz, margin):
                    score = abs(levels[iz] - preferred_z) + math.dist(xyz, point)
                    if best is None or score < best[0]:
                        best = (score, node)
            return best[1] if best else None

        start_node = snap(start3, start3[2])
        goal_node = snap(goal3, goal3[2])
        if start_node is None or goal_node is None:
            return None

        def node_xyz(node: tuple[int, int, int]) -> Point3:
            return (node[0] * step, node[1] * step, levels[node[2]])

        def heuristic(node: tuple[int, int, int]) -> float:
            return math.dist(node_xyz(node), goal3)

        open_heap: list[tuple[float, tuple[int, int, int]]] = [(heuristic(start_node), start_node)]
        g_score: dict[tuple[int, int, int], float] = {start_node: 0.0}
        came: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        closed: set[tuple[int, int, int]] = set()

        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            closed.add(current)
            if current == goal_node:
                path = [current]
                while path[-1] in came:
                    path.append(came[path[-1]])
                path.reverse()
                xyz = [node_xyz(n) for n in path]
                xyz[0] = start3
                xyz.append(goal3)
                return xyz
            if len(closed) > 60000:
                return None
            current_xyz = node_xyz(current)
            for dx, dy, dz in NEIGHBOURS_HV:
                neighbour = (current[0] + dx, current[1] + dy, current[2] + dz)
                if not (0 <= neighbour[0] <= nx and 0 <= neighbour[1] <= ny and 0 <= neighbour[2] < nz):
                    continue
                if neighbour in closed:
                    continue
                neighbour_xyz = node_xyz(neighbour)
                if not self.point_free_3d(*neighbour_xyz, margin):
                    continue
                if not self.segment_free_3d(current_xyz, neighbour_xyz, margin):
                    continue
                distance = math.dist(current_xyz, neighbour_xyz)
                move_cost = distance * (1.0 + (self.vertical_penalty - 1.0) * abs(dz))
                tentative = g_score[current] + move_cost
                if tentative < g_score.get(neighbour, float("inf")):
                    g_score[neighbour] = tentative
                    came[neighbour] = current
                    heapq.heappush(open_heap, (tentative + heuristic(neighbour), neighbour))
        return None

    # ---------- 3D 视线短路 ----------
    def _shortcut_3d(self, points: list[Point3], margin: float) -> list[Point3]:
        if len(points) <= 2:
            return points
        result = [points[0]]
        index = 0
        while index < len(points) - 1:
            probe = len(points) - 1
            while probe > index + 1 and not self.segment_free_3d(points[index], points[probe], margin):
                probe -= 1
            result.append(points[probe])
            index = probe
        return result

    # ---------- 轨迹平滑 ----------
    @staticmethod
    def _chaikin(points: list[Point3], iterations: int) -> list[Point3]:
        for _ in range(iterations):
            if len(points) < 3:
                break
            smoothed = [points[0]]
            for i in range(len(points) - 1):
                p0, p1 = points[i], points[i + 1]
                smoothed.append(tuple(0.75 * a + 0.25 * b for a, b in zip(p0, p1)))  # type: ignore[arg-type]
                smoothed.append(tuple(0.25 * a + 0.75 * b for a, b in zip(p0, p1)))  # type: ignore[arg-type]
            smoothed.append(points[-1])
            points = smoothed
        return points

    def _downsample(self, points: list[Point3], limit: int) -> list[Point3]:
        if len(points) <= limit:
            return points
        keep = [points[0]]
        stride = (len(points) - 1) / (limit - 1)
        for k in range(1, limit - 1):
            keep.append(points[int(round(k * stride))])
        keep.append(points[-1])
        return keep

    @staticmethod
    def _max_turn(points: list[Point3]) -> float:
        best = 0.0
        for i in range(1, len(points) - 1):
            a = (points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1], points[i][2] - points[i - 1][2])
            b = (points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1], points[i + 1][2] - points[i][2])
            na, nb = math.sqrt(sum(v * v for v in a)), math.sqrt(sum(v * v for v in b))
            if na < 1e-6 or nb < 1e-6:
                continue
            cos_value = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
            best = max(best, math.degrees(math.acos(cos_value)))
        return best

    def _smooth_3d(self, points: list[Point3], margin: float) -> tuple[list[Point3], bool]:
        if not self.smoothing or len(points) < 3:
            return points, False
        candidate = points
        for iteration in range(1, self.smooth_iterations + 2):
            candidate = self._chaikin(points, iteration)
            candidate = self._downsample(candidate, self.max_waypoints)
            if self._validate_3d(candidate, margin) and self._max_turn(candidate) <= self.max_turn_deg:
                return candidate, True
        return points, False

    def _validate_3d(self, points: list[Point3], margin: float) -> bool:
        return all(self.segment_free_3d(points[i], points[i + 1], margin) for i in range(len(points) - 1))

    # ---------- 快速 2D：占用掩码 + 栅格 A* ----------
    def _free_mask(self, step: float, clearance: float, nx: int, ny: int) -> dict:
        cache = getattr(self, "_mask_cache", None)
        if cache is None:
            cache = {}
            object.__setattr__(self, "_mask_cache", cache)
        key = (round(step, 3), round(clearance, 3), int(nx), int(ny))
        if key in cache:
            return cache[key]
        mask = {}
        for i in range(nx + 1):
            x = i * step
            for j in range(ny + 1):
                mask[(i, j)] = self.point_clear((x, j * step), clearance, self.altitude)
        cache[key] = mask
        return mask

    def _nearest_free(self, node, mask, nx, ny):
        if mask.get(node):
            return node
        best, best_d = None, None
        for radius in range(1, 12):
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    cand = (node[0] + di, node[1] + dj)
                    if not (0 <= cand[0] <= nx and 0 <= cand[1] <= ny):
                        continue
                    if mask.get(cand):
                        d = di * di + dj * dj
                        if best_d is None or d < best_d:
                            best, best_d = cand, d
            if best is not None:
                return best
        return None

    def _fast_grid_plan(self, start: Point, goal: Point, clearance: float, altitude: float):
        step = max(12.0, float(self.grid_step))
        self.altitude = float(altitude)
        # 画布外的点是允许飞的（实测服务端不拦），栅格按 start/goal 动态扩展
        nx = int(max(self.world.width, start[0], goal[0]) // step) + 1
        ny = int(max(self.world.height, start[1], goal[1]) // step) + 1
        mask = self._free_mask(step, clearance, nx, ny)

        def to_node(point: Point):
            return (min(max(int(round(point[0] / step)), 0), nx),
                    min(max(int(round(point[1] / step)), 0), ny))

        start_node = self._nearest_free(to_node(start), mask, nx, ny)
        goal_node = self._nearest_free(to_node(goal), mask, nx, ny)
        if start_node is None or goal_node is None:
            return None

        import heapq as _heap
        import math as _math

        open_heap = [(0.0, start_node)]
        g_score = {start_node: 0.0}
        came = {}
        seen = set()
        while open_heap:
            _, current = _heap.heappop(open_heap)
            if current in seen:
                continue
            seen.add(current)
            if current == goal_node:
                path = [current]
                while path[-1] in came:
                    path.append(came[path[-1]])
                path.reverse()
                points = [(n[0] * step, n[1] * step) for n in path]
                points[0] = start
                points[-1] = goal
                points = self._smooth_2d(points, clearance, altitude)
                total = sum(_math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
                min_clear = min(self.segment(points[i], points[i + 1], 0.0, altitude).clearance
                                for i in range(len(points) - 1)) if len(points) > 1 else clearance
                from .planner import Route as _Route
                blockers = [o.name for o in self.active_obstacles(altitude if self.planning_dim == "3d" else None)
                            if geometry.segment_polygon_distance(start, goal, self._footprints[o.name]) < clearance]
                return _Route(start=start, goal=goal, waypoints=points[1:], length=total,
                              clearance=min_clear, detoured=True, avoided=blockers,
                              planning_dim="2d", altitude=altitude,
                              waypoints_xyz=[(p[0], p[1], altitude) for p in points])
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nb = (current[0] + dx, current[1] + dy)
                if not (0 <= nb[0] <= nx and 0 <= nb[1] <= ny):
                    continue
                if not mask.get(nb):
                    continue
                if dx and dy and not (mask.get((current[0] + dx, current[1])) and mask.get((current[0], current[1] + dy))):
                    continue
                move = _math.hypot(dx, dy) * step
                tentative = g_score[current] + move
                if tentative < g_score.get(nb, float("inf")):
                    g_score[nb] = tentative
                    came[nb] = current
                    h = _math.hypot(nb[0] - goal_node[0], nb[1] - goal_node[1]) * step
                    _heap.heappush(open_heap, (tentative + h, nb))
        return None

    # ================================================================ 2D 绕障（原算法）
    def _plan_horizontal(self, start: Point, goal: Point, margin: float, alt: float,
                         first_blocker: str | None) -> Route:
        blockers = self._blocking_obstacles(start, goal, margin, alt)
        detours = self._detour_nodes(blockers, margin, alt)
        nodes = [start, goal, *detours]
        graph = self._build_graph(nodes, margin, alt)
        index_path = self._dijkstra(graph, 0, 1)
        if index_path is None:
            grid_path = self._grid_astar(start, goal, margin, alt)
            if grid_path is None:
                raise RuntimeError(f"无法规划从 {start} 到 {goal} 的路线（blocker={first_blocker}）")
            points = grid_path
        else:
            points = [nodes[i] for i in index_path]
        points = self._smooth_2d(points, margin, alt)
        total = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        min_clear = min(self.segment(points[i], points[i + 1], 0.0, alt).clearance
                        for i in range(len(points) - 1)) if len(points) > 1 else 0.0
        return Route(start=start, goal=goal, waypoints=points[1:], length=total,
                     clearance=min_clear, detoured=True, avoided=blockers)

    def _blocking_obstacles(self, start: Point, goal: Point, margin: float,
                            alt: float | None) -> list[str]:
        blockers = []
        for obstacle in self.active_obstacles(alt if self.planning_dim == "3d" else None):
            if geometry.segment_polygon_distance(start, goal, self._footprints[obstacle.name]) < margin:
                blockers.append(obstacle.name)
        return blockers

    def _detour_nodes(self, blockers: list[str], margin: float, alt: float | None) -> list[Point]:
        nodes: list[Point] = []
        for name in blockers:
            polygon = self._footprints[name]
            cx, cy = geometry.polygon_centroid(polygon)
            for vertex in polygon:
                dx, dy = vertex[0] - cx, vertex[1] - cy
                norm = math.hypot(dx, dy) or 1.0
                base_x = vertex[0] + dx / norm * (margin + 5.0)
                base_y = vertex[1] + dy / norm * (margin + 5.0)
                for angle in range(0, 360, 45):
                    rad = math.radians(angle)
                    node = self.world.clamp_to_bounds(
                        (base_x + margin * math.cos(rad), base_y + margin * math.sin(rad)), 1.0)
                    if self.point_clear(node, margin, alt):
                        nodes.append(node)
        return nodes

    def _build_graph(self, nodes: list[Point], margin: float,
                     alt: float | None) -> dict[int, list[tuple[int, float]]]:
        graph: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(nodes))}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                if self.segment(nodes[i], nodes[j], margin, alt).clear:
                    weight = geometry.point_segment_distance(nodes[j], nodes[i], nodes[i]) if False else math.dist(nodes[i], nodes[j])
                    graph[i].append((j, weight))
                    graph[j].append((i, weight))
        return graph

    @staticmethod
    def _dijkstra(graph: dict[int, list[tuple[int, float]]], src: int, dst: int) -> list[int] | None:
        dist = {src: 0.0}
        prev: dict[int, int] = {}
        heap: list[tuple[float, int]] = [(0.0, src)]
        visited: set[int] = set()
        while heap:
            cost, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == dst:
                path = [node]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            for neighbour, weight in graph.get(node, []):
                new_cost = cost + weight
                if new_cost < dist.get(neighbour, float("inf")):
                    dist[neighbour] = new_cost
                    prev[neighbour] = node
                    heapq.heappush(heap, (new_cost, neighbour))
        return None

    def _smooth_2d(self, points: list[Point], margin: float, alt: float | None) -> list[Point]:
        if len(points) <= 2:
            return points
        result = [points[0]]
        i = 0
        while i < len(points) - 1:
            j = len(points) - 1
            while j > i + 1 and not self.segment(points[i], points[j], margin, alt).clear:
                j -= 1
            result.append(points[j])
            i = j
        return result

    def _grid_astar(self, start: Point, goal: Point, margin: float,
                    alt: float | None) -> list[Point] | None:
        step = self.grid_step

        def snap(point: Point) -> tuple[int, int]:
            return (int(round(point[0] / step)), int(round(point[1] / step)))

        max_ix = int(self.world.width // step) + 1
        max_iy = int(self.world.height // step) + 1
        start_i, goal_i = snap(start), snap(goal)
        open_heap: list[tuple[float, tuple[int, int]]] = [(0.0, start_i)]
        came: dict[tuple[int, int], tuple[int, int]] = {}
        cost = {start_i: 0.0}
        seen: set[tuple[int, int]] = set()
        import time as _time

        max_expansions = int(getattr(self, "grid_max_expansions", 4000))
        budget = float(getattr(self, "plan_time_budget", 1.5))
        started = _time.time()
        while open_heap:
            if len(seen) > max_expansions or _time.time() - started > budget:
                return None
            _, current = heapq.heappop(open_heap)
            if current in seen:
                continue
            seen.add(current)
            if current == goal_i:
                cells = [current]
                while cells[-1] in came:
                    cells.append(came[cells[-1]])
                cells.reverse()
                points = [(i * step, j * step) for i, j in cells]
                points[0], points[-1] = start, goal
                return points
            for offset in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nxt = (current[0] + offset[0], current[1] + offset[1])
                if not (0 <= nxt[0] <= max_ix and 0 <= nxt[1] <= max_iy):
                    continue
                point = (nxt[0] * step, nxt[1] * step)
                if not self.point_clear(point, margin, alt):
                    continue
                if not self.segment((current[0] * step, current[1] * step), point, margin, alt).clear:
                    continue
                step_cost = step * (math.sqrt(2) if offset[0] and offset[1] else 1.0)
                new_cost = cost[current] + step_cost
                if new_cost < cost.get(nxt, float("inf")):
                    cost[nxt] = new_cost
                    came[nxt] = current
                    heuristic = math.hypot(nxt[0] - goal_i[0], nxt[1] - goal_i[1]) * step
                    heapq.heappush(open_heap, (new_cost + heuristic, nxt))
        return None
