"""任务层航点顺序优化：判断任务是否要求按给定顺序，未要求时按最短路径重排。

设计要点：
- 起点固定（当前无人机位置）；
- 任务明确要求“按顺序/回环/最后一点/返航/降落”时保持原顺序；
- 未要求顺序时，对中间航点做最近邻 + 2-opt 近似 TSP；
- 只重排访问顺序，不改变每个航点附带的动作（拍照/悬停等）。
"""
from __future__ import annotations

import math
import re
from typing import Any

ORDER_HINTS = (
    r"in order", r"order listed", r"listed order", r"in sequence", r"sequential",
    r"one by one", r"as listed", r"in that order", r"follow the order", r"do not cut",
)
UNORDERED_HINTS = (
    r"any order", r"no particular order", r"order does not matter",
    r"shortest", r"optimize", r"optimise", r"shortest path",
)
END_FIXED_HINTS = (
    r"return home", r"finish", r"hover above", r"back to", r"\bland\b", r"touch\s*down",
    r"end\s*point", r"last\s*point",
)
PREFIX_FIXED_HINTS = (
    r"start(?:ing)?\s*point", r"first\s*point", r"begin(?:ning)?\s*at", r"starting\s*at",
)
MOVE_KINDS = ("move_to", "navigate_to_pose")
TERMINAL_KINDS = ("land", "return_home")


def order_required(task_text: str) -> bool:
    """任务是否要求按给定顺序访问航点。默认：不要求（可重排）。"""
    text = (task_text or "").lower()
    if any(re.search(pattern, text, re.I) for pattern in UNORDERED_HINTS):
        return False
    return any(re.search(pattern, text, re.I) for pattern in ORDER_HINTS)


def end_should_stay_fixed(task_text: str, land_at_end: bool = False) -> bool:
    text = (task_text or "").lower()
    return bool(land_at_end) or any(re.search(p, text, re.I) for p in END_FIXED_HINTS)


def fixed_prefix_count(task_text: str) -> int:
    """任务明确指定“起点/第一个点”时，返回需要固定的开头点数（目前最多 1 个）。"""
    text = (task_text or "").lower()
    return 1 if any(re.search(p, text, re.I) for p in PREFIX_FIXED_HINTS) else 0


def fixed_suffix_count(task_text: str) -> int:
    """任务明确指定“最终/终点/返航/降落”时，返回需要固定的结尾点数（目前最多 1 个）。"""
    text = (task_text or "").lower()
    return 1 if any(re.search(p, text, re.I) for p in END_FIXED_HINTS) else 0


def point_of(item: dict) -> tuple[float, float] | None:
    try:
        return float(item.get("x")), float(item.get("y"))
    except (TypeError, ValueError, AttributeError):
        return None


def _leg_distance(planner, start: tuple[float, float], goal: tuple[float, float],
                  altitude: float, exact: bool) -> float:
    """一段的代价：exact 时用规划器真实绕障长度，否则用直线距离（快）。"""
    if exact:
        try:
            route = planner.plan(start, goal, altitude=altitude)
            length = float(getattr(route, "length_3d", None) or getattr(route, "length", 0.0)
                           or 0.0)
            if length > 0:
                return length
        except Exception:  # noqa: BLE001
            pass
    return math.hypot(goal[0] - start[0], goal[1] - start[1])


def _distance_matrix(planner, start, points, altitude, exact):
    nodes = [start] + list(points)
    size = len(nodes)
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            distance = _leg_distance(planner, nodes[i], nodes[j], altitude, exact)
            matrix[i][j] = matrix[j][i] = distance
    return matrix


def _nearest_neighbor(matrix: list[list[float]], count: int) -> list[int]:
    """从节点 0 出发、访问 1..count-1 的最近邻顺序（返回不含 0 的节点列表）。"""
    remaining = set(range(1, count))
    order: list[int] = []
    current = 0
    while remaining:
        nxt = min(remaining, key=lambda idx: matrix[current][idx])
        order.append(nxt)
        remaining.remove(nxt)
        current = nxt
    return order


def _two_opt_open(matrix: list[list[float]], order: list[int]) -> list[int]:
    """开链 2-opt 优化：路径为 0 -> order[0] -> ... -> order[-1]。"""
    best = list(order)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                a = 0 if i == 0 else best[i - 1]
                b = best[i]
                c = best[j]
                d = best[j + 1] if j + 1 < len(best) else None
                before = matrix[a][b] + (matrix[c][d] if d is not None else 0.0)
                after = matrix[a][c] + (matrix[b][d] if d is not None else 0.0)
                if after + 1e-9 < before:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True
    return best


def _path_length(matrix: list[list[float]], order: list[int]) -> float:
    total = 0.0
    current = 0
    for node in order:
        total += matrix[current][node]
        current = node
    return total


def reorder_points(planner, start_xy: tuple[float, float],
                   points: list[tuple[float, float]], altitude: float,
                   end_fixed: bool = False) -> tuple[list[int], dict[str, Any]]:
    """返回 (新顺序索引列表, 统计信息)；索引对应传入 points。"""
    count = len(points)
    if count <= 2:
        return list(range(count)), {"optimized": False, "reason": "too_few_points"}
    exact = count <= 12
    matrix = _distance_matrix(planner, start_xy, points, altitude, exact)

    fixed_last = count - 1 if end_fixed and count > 2 else None
    work_count = count - 1 if fixed_last is not None else count
    if work_count <= 1:
        return list(range(count)), {"optimized": False, "reason": "end_fixed"}

    # 工作子矩阵：节点 0（起点）+ 点 1..work_count
    work_nodes = [0] + list(range(1, work_count + 1))
    sub = [[matrix[a][b] for b in work_nodes] for a in work_nodes]
    order_local = _nearest_neighbor(sub, len(work_nodes))
    optimized_local = _two_opt_open(sub, order_local)

    before_points = list(range(count))
    before_nodes = [index + 1 for index in before_points]
    before_distance = _path_length(matrix, before_nodes)
    after_local = optimized_local + ([fixed_last + 1] if fixed_last is not None else [])
    after_distance = _path_length(matrix, after_local)

    result_points = [node - 1 for node in optimized_local]
    if fixed_last is not None:
        result_points.append(fixed_last)
    if result_points == before_points:
        return result_points, {"optimized": False, "reason": "already_shortest"}
    return result_points, {
        "optimized": True,
        "exact": exact,
        "before_distance": round(before_distance, 1),
        "after_distance": round(after_distance, 1),
        "points": count,
    }


def reorder_waypoints(planner, start_xy: tuple[float, float],
                      waypoints: list[dict], altitude: float,
                      end_fixed: bool = False) -> tuple[list[dict], dict]:
    points = []
    for item in waypoints:
        point = point_of(item)
        if point is None:
            return waypoints, {"optimized": False, "reason": "bad_point"}
        points.append(point)
    order, stats = reorder_points(planner, start_xy, points, altitude, end_fixed)
    if not stats.get("optimized"):
        return waypoints, stats
    return [waypoints[index] for index in order], stats


def _action_name(action: dict) -> str:
    return str(action.get("kind") or action.get("name") or "")


def reorder_actions(planner, start_xy: tuple[float, float],
                    actions: list[dict], altitude: float, end_fixed: bool = False):
    """移动动作作为簇头，其后的拍照/悬停等动作跟随该点一起重排。"""
    prefix: list[dict] = []
    clusters: list[list[dict]] = []
    current: list[dict] | None = None
    for action in actions:
        if _action_name(action) in MOVE_KINDS and point_of(action.get("params") or {}) is not None:
            current = [action]
            clusters.append(current)
        elif current is not None:
            current.append(action)
        else:
            prefix.append(action)
    if len(clusters) <= 2:
        return actions, {"optimized": False, "reason": "too_few_move_actions"}
    terminal: list[dict] = []
    if clusters:
        last_cluster = clusters[-1]
        while len(last_cluster) > 1 and _action_name(last_cluster[-1]) in TERMINAL_KINDS:
            terminal.insert(0, last_cluster.pop())
    points = [point_of(cluster[0].get("params") or {}) for cluster in clusters]
    if any(point is None for point in points):
        return actions, {"optimized": False, "reason": "bad_point"}
    order, stats = reorder_points(planner, start_xy, points, altitude, end_fixed)
    if not stats.get("optimized"):
        return actions, stats
    result = list(prefix)
    for index in order:
        result.extend(clusters[index])
    result.extend(terminal)
    return result, stats


COORD_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:,\s*(-?\d+(?:\.\d+)?)\s*)?\)")
CLAUSE_SPLIT = re.compile(
    r"(?<!\d)\.(?!\d)|;|\bthen\b|\bnext\b|\bafter that\b|\bfinally\b|\bfollowed by\b", re.I)
ORDER_WORDS = (r"in order", r"in sequence", r"sequential", r"one by one", r"listed order",
               r"order listed", r"follow the order", r"do not cut", r"as listed",
               r"in that order")
UNORDERED_WORDS = (r"waypoints?", r"\bpoints?\b", r"zigzag", r"any order", r"following",
                   r"\bthrough\b", r"visit (?:these|the following)", r"set of")


def parse_route_segments(task_text: str) -> list[dict]:
    """按语句把航点分组：ordered=按给定顺序，unordered=可做多点最短路径。"""
    text = task_text or ""
    parts: list[str] = []
    last = 0
    for match in CLAUSE_SPLIT.finditer(text):
        parts.append(text[last:match.start()])
        last = match.end()
    parts.append(text[last:])
    segments: list[dict] = []
    index = 0
    for clause in parts:
        coords = [(float(m.group(1)), float(m.group(2))) for m in COORD_RE.finditer(clause)]
        if not coords:
            continue
        low = clause.lower()
        ordered_explicit = any(re.search(word, low, re.I) for word in ORDER_WORDS)
        unordered_cue = any(re.search(word, low, re.I) for word in UNORDERED_WORDS)
        policy = "ordered"
        if not ordered_explicit and unordered_cue and len(coords) >= 3:
            policy = "unordered"
        indices = list(range(index, index + len(coords)))
        index += len(coords)
        segments.append({"indices": indices, "policy": policy, "coords": coords,
                         "text": clause.strip()[:80]})
    return segments


def reorder_segments(planner, start_xy: tuple[float, float],
                     points: list[tuple[float, float]], segments: list[dict],
                     altitude: float) -> tuple[list[int], dict[str, Any]]:
    """按段处理：有序段原样、无序段做固定入口/出口的 TSP，终点段最后执行。"""
    if not segments:
        return list(range(len(points))), {"optimized": False, "reason": "no_segments"}
    permutation: list[int] = []
    cursor = start_xy
    changed = False
    for seg_index, segment in enumerate(segments):
        indices = segment["indices"]
        if segment["policy"] != "unordered" or len(indices) < 3:
            permutation.extend(indices)
            if indices:
                cursor = points[indices[-1]]
            continue
        next_start = None
        for following in segments[seg_index + 1:]:
            if following["indices"]:
                next_start = points[following["indices"][0]]
                break
        nodes = [points[i] for i in indices]
        if next_start is not None:
            nodes = nodes + [next_start]
        order, stats = reorder_points(planner, cursor, nodes, altitude,
                                      end_fixed=next_start is not None)
        if stats.get("optimized"):
            changed = True
            subset = [indices[i] for i in order if i < len(indices)]
        else:
            subset = list(indices)
        permutation.extend(subset)
        cursor = points[subset[-1]]
    return permutation, {
        "optimized": changed,
        "mode": "segmented",
        "segments": [{"policy": s["policy"], "count": len(s["indices"])} for s in segments],
    }


def reorder_segments_waypoints(planner, start_xy: tuple[float, float],
                               waypoints: list[dict], segments: list[dict],
                               altitude: float) -> tuple[list[dict], dict]:
    points = []
    for item in waypoints:
        point = point_of(item)
        if point is None:
            return waypoints, {"optimized": False, "reason": "bad_point"}
        points.append(point)
    permutation, stats = reorder_segments(planner, start_xy, points, segments, altitude)
    if not stats.get("optimized"):
        return waypoints, stats
    return [waypoints[index] for index in permutation], stats


def reorder_segments_actions(planner, start_xy: tuple[float, float],
                             actions: list[dict], segments: list[dict], altitude: float):
    prefix_actions: list[dict] = []
    clusters: list[list[dict]] = []
    current: list[dict] | None = None
    for action in actions:
        if _action_name(action) in MOVE_KINDS and point_of(action.get("params") or {}) is not None:
            current = [action]
            clusters.append(current)
        elif current is not None:
            current.append(action)
        else:
            prefix_actions.append(action)
    if len(clusters) <= 2:
        return actions, {"optimized": False, "reason": "too_few_move_actions"}
    terminal: list[dict] = []
    last_cluster = clusters[-1]
    while len(last_cluster) > 1 and _action_name(last_cluster[-1]) in TERMINAL_KINDS:
        terminal.insert(0, last_cluster.pop())
    points = [point_of(cluster[0].get("params") or {}) for cluster in clusters]
    if any(point is None for point in points):
        return actions, {"optimized": False, "reason": "bad_point"}
    permutation, stats = reorder_segments(planner, start_xy, points, segments, altitude)
    if not stats.get("optimized"):
        return actions, stats
    result = list(prefix_actions)
    for index in permutation:
        result.extend(clusters[index])
    result.extend(terminal)
    return result, stats
