"""行为树动作库（L3 action 层，单一事实来源）。

所有动作能力来自官方任务模版统计（75 个会话 / 1500 条任务实例 / 50 个模版）：

  move_to(3357) take_off(2605) move_towards(1917) take_photo(844) hover(738)
  land(430) move_along_path(167) broadcast(96) change_altitude(38)
  return_home(33) rotate(27)

另外把任务模版里的"复合动作"（覆盖搜索/多目标访问/编队/巡逻/跟踪）也补齐，
它们是上面 11 个原子动作的组合。

用法：
    from .actions import ACTION_LIBRARY, build_action_steps
    steps = build_action_steps(world, spec)     # -> [{"kind": ..., "params": ...}]
"""
from __future__ import annotations

import math
from typing import Any

from . import coverage

# ---------------------------------------------------------------- 原子动作目录
ACTION_LIBRARY: dict[str, dict] = {
    "take_off": {
        "endpoint": "POST /drones/{id}/command/take_off",
        "params": {"altitude": "float (optional)"},
        "desc": "起飞到指定高度",
    },
    "land": {
        "endpoint": "POST /drones/{id}/command/land",
        "params": {},
        "desc": "降落",
    },
    "change_altitude": {
        "endpoint": "POST /drones/{id}/command/change_altitude",
        "params": {"altitude": "float (required)"},
        "desc": "改变高度（悬停/飞行中）",
    },
    "rotate": {
        "endpoint": "POST /drones/{id}/command/rotate",
        "params": {"heading": "float 0-359 (required)"},
        "desc": "旋转到指定朝向",
    },
    "move_to": {
        "endpoint": "POST /drones/{id}/command/move_to",
        "params": {"x": "float", "y": "float", "z": "float (optional)"},
        "desc": "飞到一个点",
    },
    "move_towards": {
        "endpoint": "POST /drones/{id}/command/move_towards",
        "params": {"x": "float", "y": "float", "z": "float (optional)", "distance": "float (optional)"},
        "desc": "朝一个方向/点持续推进（跟踪用）",
    },
    "move_along_path": {
        "endpoint": "POST /drones/{id}/command/move_along_path",
        "params": {"waypoints": "[{x,y,z}, ...]", "allow_partial_move": "bool"},
        "desc": "沿航点序列飞行",
    },
    "hover": {
        "endpoint": "POST /drones/{id}/command/hover",
        "params": {"duration": "float seconds (optional)"},
        "desc": "悬停（可指定时长）",
    },
    "take_photo": {
        "endpoint": "POST /drones/{id}/command/take_photo",
        "params": {},
        "desc": "拍照",
    },
    "broadcast": {
        "endpoint": "POST /drones/{id}/command/broadcast",
        "params": {"content": "str (required)"},
        "desc": "广播消息（中继/通信任务）",
    },
    "return_home": {
        "endpoint": "POST /drones/{id}/command/return_home",
        "params": {},
        "desc": "返航到 home 点",
    },
    # ---- 复合动作（由原子动作组合，生成后仍是原子步骤）----
    "coverage_search": {
        "endpoint": "(composite) move_along_path + take_photo",
        "params": {"target": "str 目标名", "spacing": "float 可选", "photos": "int 可选"},
        "desc": "对目标区域做蛇形覆盖搜索（覆盖类模版）",
    },
    "visit_targets": {
        "endpoint": "(composite) planner + move_along_path",
        "params": {"targets": "[str 目标名, ...]"},
        "desc": "按顺序访问多个目标（多目标巡检/交付）",
    },
    "patrol": {
        "endpoint": "(composite) move_along_path",
        "params": {"points": "[{x,y,z}, ...]", "closed": "bool"},
        "desc": "巡逻闭合路线（四边形/周界巡逻）",
    },
    "formation": {
        "endpoint": "(composite) per-drone move_to",
        "params": {"kind": "line|triangle|...", "center": "[x, y]", "altitude": "float",
                   "drones": "[drone_id, ...]"},
        "desc": "编队集结（线形/三角形）",
    },
    "track_target": {
        "endpoint": "(composite) move_towards",
        "params": {"target": "str 目标名", "duration": "float"},
        "desc": "跟踪移动目标",
    },
    "wait": {
        "endpoint": "(local) sleep",
        "params": {"seconds": "float"},
        "desc": "等待/滞留",
    },
}

ATOMIC_ACTIONS = ("take_off", "land", "change_altitude", "rotate", "move_to",
                  "move_towards", "move_along_path", "hover", "take_photo",
                  "broadcast", "return_home")
COMPOSITE_ACTIONS = ("coverage_search", "visit_targets", "patrol", "formation",
                     "track_target", "wait")


# ---------------------------------------------------------------- 执行原子动作
def run_atomic(api, drone_id: str, name: str, params: dict | None = None) -> dict:
    """执行一个原子动作，返回 {ok, name, error, body}。"""
    params = dict(params or {})
    if name not in ATOMIC_ACTIONS:
        return {"ok": False, "name": name, "error": "未知原子动作: %s" % name}
    if name == "take_off":
        status, body = api.command(drone_id, "take_off",
                                   params={"altitude": params["altitude"]} if "altitude" in params else None)
    elif name == "land":
        status, body = api.command(drone_id, "land")
    elif name == "change_altitude":
        status, body = api.command(drone_id, "change_altitude", params={"altitude": params["altitude"]})
    elif name == "rotate":
        status, body = api.command(drone_id, "rotate", params={"heading": params["heading"]})
    elif name == "move_to":
        query = {"x": params["x"], "y": params["y"]}
        if params.get("z") is not None:
            query["z"] = params["z"]
        status, body = api.command(drone_id, "move_to", params=query)
    elif name == "move_towards":
        query = {"x": params["x"], "y": params["y"]}
        if params.get("z") is not None:
            query["z"] = params["z"]
        if params.get("distance") is not None:
            query["distance"] = params["distance"]
        status, body = api.command(drone_id, "move_towards", params=query)
    elif name == "move_along_path":
        status, body = api.command(drone_id, "move_along_path",
                                   body={"waypoints": params["waypoints"],
                                         "allow_partial_move": bool(params.get("allow_partial_move", False))})
    elif name == "hover":
        status, body = api.command(drone_id, "hover",
                                   params={"duration": params["duration"]} if params.get("duration") else None)
    elif name == "take_photo":
        status, body = api.command(drone_id, "take_photo")
    elif name == "broadcast":
        status, body = api.command(drone_id, "broadcast", body={"content": params["content"]})
    elif name == "return_home":
        status, body = api.command(drone_id, "return_home")
    else:  # pragma: no cover
        return {"ok": False, "name": name, "error": "未实现"}
    ok = not (isinstance(body, dict) and body.get("status") == "error")
    return {"ok": ok, "name": name,
            "error": None if ok else str(body.get("message") if isinstance(body, dict) else body)[:160],
            "body": body}


# ---------------------------------------------------------------- 复合动作展开
def _find_target(world, name: str):
    if world is None:
        return None
    wanted = str(name or "").strip().lower()
    for target in getattr(world, "targets", []):
        if wanted in (str(target.name).lower(), str(target.id).lower()):
            return target
    return None


def expand_composite(world, name: str, params: dict, altitude: float) -> list[dict]:
    """把复合动作展开成原子动作列表（plan 阶段完成，执行阶段只跑原子动作）。"""
    params = dict(params or {})
    if name == "coverage_search":
        target = _find_target(world, params.get("target"))
        if target is None:
            return [{"kind": "fail", "params": {"error": "找不到目标 %s" % params.get("target")}}]
        spacing = float(params.get("spacing") or 36.0)
        vertices = getattr(target, "vertices", None) or []
        if str(getattr(target, "type", "")).lower() == "polygon" and len(vertices) >= 3:
            polygon = [(float(v["x"]), float(v["y"])) for v in vertices]
            points = coverage.serpentine_polygon(polygon, spacing)
        else:
            center = getattr(target, "center", (0.0, 0.0))
            radius = float(getattr(target, "radius", 0.0) or 0.0)
            points = coverage.serpentine_route((float(center[0]), float(center[1])), radius, spacing)
        waypoints = [{"x": round(p[0], 2), "y": round(p[1], 2), "z": altitude} for p in points]
        if not waypoints:
            return [{"kind": "fail", "params": {"error": "目标 %s 无法生成覆盖航线" % params.get("target")}}]
        photos = int(params.get("photos") or 0)
        steps = [{"kind": "move_along_path", "params": {"waypoints": waypoints}}]
        for index in range(photos):
            steps.append({"kind": "take_photo", "params": {}})
        return steps

    if name == "visit_targets":
        steps = []
        for target_name in (params.get("targets") or []):
            target = _find_target(world, target_name)
            if target is None:
                steps.append({"kind": "fail", "params": {"error": "找不到目标 %s" % target_name}})
                continue
            center = getattr(target, "center", (0.0, 0.0))
            steps.append({"kind": "move_to", "params": {"x": float(center[0]),
                                                        "y": float(center[1]), "z": altitude}})
        return steps

    if name == "patrol":
        points = [{"x": float(p["x"]), "y": float(p["y"]),
                   "z": float(p.get("z", altitude))} for p in (params.get("points") or [])]
        if params.get("closed") and points:
            points = points + [dict(points[0])]
        return [{"kind": "move_along_path", "params": {"waypoints": points}}] if points else []

    if name == "formation":
        center = params.get("center") or [0.0, 0.0]
        drones = params.get("drones") or []
        kind = str(params.get("kind") or "line").lower()
        alt = float(params.get("altitude") or altitude)
        gap = float(params.get("gap") or 40.0)
        steps = []
        for index, drone_id in enumerate(drones):
            if kind == "triangle":
                offsets = [(0.0, 0.0), (-gap, gap * 0.9), (gap, gap * 0.9)]
                dx, dy = offsets[index % len(offsets)]
            else:  # line
                dx, dy = (index - (len(drones) - 1) / 2.0) * gap, 0.0
            steps.append({"kind": "move_to", "params": {"x": float(center[0]) + dx,
                                                        "y": float(center[1]) + dy,
                                                        "z": alt, "drone_id": drone_id}})
        return steps

    if name == "track_target":
        target = _find_target(world, params.get("target"))
        if target is None:
            return [{"kind": "fail", "params": {"error": "找不到目标 %s" % params.get("target")}}]
        center = getattr(target, "center", (0.0, 0.0))
        return [{"kind": "move_towards", "params": {"x": float(center[0]), "y": float(center[1]),
                                                    "z": altitude}}]

    if name == "wait":
        return [{"kind": "wait", "params": {"seconds": float(params.get("seconds") or 5.0)}}]

    if name in ATOMIC_ACTIONS:
        merged = dict(params)
        merged.setdefault("z", altitude)
        return [{"kind": name, "params": merged}]

    return [{"kind": "fail", "params": {"error": "未知动作: %s" % name}}]


def build_action_steps(world, spec: dict) -> list[dict]:
    """任务 spec 的 actions 列表 -> 原子步骤列表。"""
    altitude = float(spec.get("altitude") or 32.0)
    steps: list[dict] = []
    for action in (spec.get("actions") or []):
        if isinstance(action, str):
            name, params = action, {}
        else:
            name = str(action.get("name") or action.get("kind") or "")
            params = action.get("params") or {k: v for k, v in action.items()
                                             if k not in ("name", "kind", "params")}
        steps.extend(expand_composite(world, name, params, altitude))
    return steps
