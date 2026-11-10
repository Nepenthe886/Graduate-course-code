"""模板动作库（L2.5）：把官方任务模板展开成一条原子动作序列。

10 个模板来自《无人机场景作业提交/任务模板》，每个模板对应一个组合动作，
模型只需调用一次（给出参数），无需自己拼原子动作。

设计原则：
- 只生成 spec（actions 列表），真正执行交给 mission_run.execute_actions / 行为树；
- 多机模板在每条 action 的 params 里带 drone_id；
- 坐标/高度/消息等必须由调用方给出（L1 从任务文本解析），本模块不做自然语言理解。
"""
from __future__ import annotations

import math
import re
from typing import Any

# 模板元数据：id -> 名称/类别/参数说明/原子动作
TEMPLATES: dict[str, dict[str, Any]] = {
    "altitude_stage": {
        "name": "Staged Altitude Change Corridor",
        "category": "Basics",
        "params": {"drone_id": "str", "alt1": "float", "alt2": "float"},
        "atomic": ["take_off", "hover", "change_altitude", "hover"],
    },
    "grid_survey": {
        "name": "Grid Survey Coverage Sweep",
        "category": "Survey",
        "params": {"drone_id": "str", "corners": "[[x,y] x4]", "altitude": "float", "photos": "int=4"},
        "atomic": ["take_off", "move_to", "take_photo", "...", "hover"],
    },
    "target_photo": {
        "name": "Target Inspection Photo Pass",
        "category": "Inspection",
        "params": {"drone_id": "str", "x": "float", "y": "float", "altitude": "float", "photos": "int=2"},
        "atomic": ["take_off", "move_to", "take_photo", "take_photo", "hover"],
    },
    "line_formation": {
        "name": "Two-Drone Line-Abreast Formation",
        "category": "Formation",
        "params": {"drones": "[{drone_id,x,y}]", "altitude": "float"},
        "atomic": ["take_off x2", "move_to x2", "hover x2"],
    },
    "perimeter_patrol": {
        "name": "Perimeter Patrol With Return",
        "category": "Patrol",
        "params": {"drone_id": "str", "corners": "[[x,y] x4]", "altitude": "float"},
        "atomic": ["take_off", "move_to x4", "return_home", "land"],
    },
    "relay_message": {
        "name": "Relay Link Message Handoff",
        "category": "Communication",
        "params": {"drone1": "{drone_id,x,y}", "drone2": "{drone_id,x,y}", "altitude": "float", "message": "str"},
        "atomic": ["take_off x2", "move_to x2", "send_message", "hover x2"],
    },
    "corridor_detour": {
        "name": "Obstacle Corridor Detour Survey",
        "category": "Navigation",
        "params": {"drone_id": "str", "start": "[x,y]", "end": "[x,y]", "altitude": "float",
                   "clearance": "float=50", "obstacle_center": "[x,y]?",
                   "photos": "int=2", "end_hover": "bool=true"},
        "atomic": ["take_off", "move_to(start)", "take_photo", "move_along_path(detour)", "take_photo", "hover"],
    },
    "battery_cycle": {
        "name": "Battery Cycle And Ground Readiness",
        "category": "Logistics",
        "params": {"drone_id": "str", "altitude": "float", "charge_amount": "float=30"},
        "atomic": ["take_off", "hover", "land", "charge"],
    },
    "target_coverage": {
        "name": "Target Area Coverage Pass",
        "category": "Search",
        "params": {"drone_id": "str", "center": "[x,y]", "radius": "float", "altitude": "float",
                   "sweep": "float=50", "coverage": "float=0.7", "photos": "int=1"},
        "atomic": ["take_off", "move_along_path(serpentine)", "take_photo", "hover"],
    },
    "emergency_land": {
        "name": "Emergency Broadcast And Fleet Landing",
        "category": "Safety",
        "params": {"drone_id": "str", "message": "str", "drones": "[drone_id,...]"},
        "atomic": ["broadcast", "land xN"],
    },
}

# 文本 -> 模板 id（用于 L1 任务识别；按匹配优先级排序）
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("relay_message", ("relay", "sends the message", "message handoff", "中继")),
    ("corridor_detour", ("corridor", "detour", "blocked structure", "avoid", "绕行", "走廊")),
    ("perimeter_patrol", ("perimeter", "patrol", "return home", "巡逻")),
    ("line_formation", ("formation", "line abreast", "lateral spacing", "编队")),
    ("grid_survey", ("survey grid", "four-corner", "grid", "航测")),
    ("target_coverage", ("coverage", "70 percent", "search until", "覆盖")),
    ("battery_cycle", ("battery", "recharge", "charge", "充电")),
    ("emergency_land", ("emergency", "broadcast", "fleet landing", "紧急")),
    ("altitude_stage", ("climb", "altitude", "two-stage", "爬升")),
    ("target_photo", ("photograph", "photo", "inspect", "拍照")),
]


def detect_template(task_text: str) -> str | None:
    text = (task_text or "").lower()
    for template_id, words in _KEYWORDS:
        if any(word in text for word in words):
            return template_id
    return None


def _act(kind: str, **params) -> dict:
    if kind == "hover" and params.get("duration") is None:
        params["duration"] = 5.0  # 记录悬停时长，满足 drone_has_hovered(min_duration) 类检查
    return {"kind": kind, "params": {k: v for k, v in params.items() if v is not None}}


def _spec(actions: list[dict], altitude: float, land_at_end: bool = False) -> dict:
    return {"actions": actions, "altitude": float(altitude), "land_at_end": bool(land_at_end)}


def _point_xy(value):
    """把 {x,y} / [x,y] / "(x, y, z)" / "x, y, z" 统一解析成 (x, y)。"""
    if isinstance(value, dict):
        if "x" in value and "y" in value:
            try:
                return float(value["x"]), float(value["y"])
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().strip("()[]{}")
        parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                return None
    return None


def _xy(point) -> tuple[float, float]:
    parsed = _point_xy(point)
    if parsed is None:
        raise ValueError("无法解析坐标: %r" % (point,))
    return parsed


def _drone(value, default: str = "") -> str:
    if isinstance(value, dict):
        return str(value.get("drone_id") or value.get("id") or default)
    return str(value or default)


def _as_int(value, default: int) -> int:
    """把 photos 之类的参数转成整数：列表按元素个数、字符串按数字、其余用默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (list, tuple)):
        return len(value) if value else default
    if isinstance(value, dict):
        return len(value) if value else default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


_EXAMPLE: dict[str, str] = {
    "altitude_stage": '{"drone_id":"Drone 1","alt1":15,"alt2":36}',
    "grid_survey": '{"drone_id":"Drone 1","altitude":25,"corners":[[120,520],[720,520],[720,220],[120,220]],"photos":4}',
    "target_photo": '{"drone_id":"Drone 1","target":"Depot Yard B","altitude":27,"photos":2}',
    "line_formation": '{"altitude":22,"drones":[{"drone_id":"Drone 1","x":280,"y":380},{"drone_id":"Drone 2","x":480,"y":380}]}',
    "perimeter_patrol": '{"drone_id":"Drone 1","altitude":19,"corners":[[141,140],[843,206],[876,555],[135,586]]}',
    "relay_message": '{"drone1":{"drone_id":"Alpha","x":250,"y":400},"drone2":{"drone_id":"Bravo","x":600,"y":400},"altitude":31.3,"message":"hello"}',
    "corridor_detour": '{"drone_id":"Drone 1","start":[131,156],"end":[881,624],"altitude":28.2,"clearance":49,"photos":2}',
    "battery_cycle": '{"drone_id":"Drone 1","altitude":17,"charge_amount":38}',
    "target_coverage": '{"drone_id":"Drone 1","center":[512,384],"radius":150,"altitude":24,"sweep":50,"coverage":0.7}',
    "emergency_land": '{"drone_id":"Drone 1","message":"emergency","drones":["Drone 1","Drone 2"]}',
}


def build_template_spec(template_id: str, params: dict | None = None) -> dict:
    """把模板 + 参数展开成可执行 spec；未知模板返回 {"error": ...}。"""
    if template_id not in TEMPLATES:
        return {"error": f"未知模板: {template_id}（可选: {', '.join(TEMPLATES)}）"}
    p = dict(params or {})
    builder = _BUILDERS[template_id]
    try:
        return builder(p)
    except KeyError as exc:
        expected = TEMPLATES[template_id].get("params")
        return {"error": f"模板 {template_id} 缺少参数: {exc}",
                "expected_params": expected,
                "example": _EXAMPLE.get(template_id, "")}


# ---------------------------------------------------------------- 各模板展开

def _altitude_stage(p: dict) -> dict:
    d, alt1, alt2 = _drone(p.get("drone_id")), float(p["alt1"]), float(p["alt2"])
    heading = p.get("heading")
    actions = [_act("take_off", drone_id=d, altitude=alt1)]
    if abs(alt2 - alt1) >= 1e-6:
        actions += [_act("hover", drone_id=d),
                    _act("change_altitude", drone_id=d, altitude=alt2)]
    if heading is not None:
        actions.append(_act("rotate", drone_id=d, heading=float(heading)))
    actions.append(_act("hover", drone_id=d))
    return _spec(actions, alt2)


def _grid_survey(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    raw = p.get("corners") or p.get("waypoints") or p.get("points")
    if not raw:
        raise KeyError("corners")
    corners = [_xy(c) for c in raw]
    photos = _as_int(p.get("photos"), len(corners) or 1)
    # 任务常写“回到西南角”：3 个角点 + 回到首点 = 4 个拍照点
    close_loop = bool(p.get("close_loop", photos > len(corners)))
    points = corners + ([corners[0]] if close_loop and corners else [])
    actions = [_act("take_off", drone_id=d, altitude=alt)]
    for x, y in points:
        actions.append(_act("move_to", drone_id=d, x=x, y=y, z=alt))
        actions.append(_act("take_photo", drone_id=d))
    # 照片数仍不够时，在最后一个角点补拍
    for _ in range(max(0, photos - len(points))):
        actions.append(_act("take_photo", drone_id=d))
    actions.append(_act("hover", drone_id=d))
    spec = _spec(actions, alt)
    if p.get("clearance") is not None:
        spec["clearance"] = float(p["clearance"])
    return spec


def _target_photo(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    x, y = _xy([p["x"], p["y"]])
    photos = _as_int(p.get("photos"), 2)
    actions = [_act("take_off", drone_id=d, altitude=alt),
               _act("move_to", drone_id=d, x=x, y=y, z=alt)]
    actions += [_act("take_photo", drone_id=d) for _ in range(photos)]
    actions.append(_act("hover", drone_id=d))
    return _spec(actions, alt)


def _line_formation(p: dict) -> dict:
    alt = float(p["altitude"])
    raw_drones = p.get("drones") or []
    names = [str(_drone(item)) for item in raw_drones if not isinstance(item, dict)]
    entries: list[tuple[str, tuple[float, float] | None]] = []
    for item in raw_drones:
        if not isinstance(item, dict):
            continue
        name = str(_drone(item))
        xy = _point_xy(item)
        if name and xy is not None:
            entries.append((name, xy))
    if not entries:
        positions = p.get("positions") or []
        for index, item in enumerate(positions):
            name = ""
            xy = None
            if isinstance(item, dict):
                name = str(item.get("drone") or item.get("drone_id") or item.get("name") or "")
                xy = _point_xy(item.get("position") or item.get("point") or item)
            else:
                xy = _point_xy(item)
            if not name and index < len(names):
                name = names[index]
            entries.append((name, xy))
    actions = []
    for name, xy in entries:
        if not name or xy is None:
            continue
        actions += [_act("take_off", drone_id=name, altitude=alt),
                    _act("move_to", drone_id=name, x=xy[0], y=xy[1], z=alt, tolerance=2.0),
                    _act("hover", drone_id=name)]
    return _spec(actions, alt)


def _perimeter_patrol(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    corners = [_xy(c) for c in p["corners"]]
    actions = [_act("take_off", drone_id=d, altitude=alt)]
    # 逐角点 move_to：由行为树按段规划绕障，避免整条直线路径被服务器一次性拒绝
    for x, y in corners:
        actions.append(_act("move_to", drone_id=d, x=x, y=y, z=alt))
    actions.append(_act("return_home", drone_id=d))
    actions.append(_act("land", drone_id=d))
    spec = _spec(actions, alt)
    if p.get("clearance") is not None:
        spec["clearance"] = float(p["clearance"])
    return spec


def _relay_message(p: dict) -> dict:
    alt = float(p["altitude"])
    # 兼容两种参数写法：
    # A) {"drone1": {"drone_id","x","y"}, "drone2": {...}}
    # B) {"drone_id": Alpha, "position": {x,y}, "target_drone_id": Bravo, "target_position": {x,y}}
    a = p.get("drone1") if isinstance(p.get("drone1"), dict) else None
    b = p.get("drone2") if isinstance(p.get("drone2"), dict) else None
    if a is not None and b is not None:
        d1, d2 = _drone(a), _drone(b)
        x1, y1 = _xy(a)
        x2, y2 = _xy(b)
    else:
        d1 = _drone(p.get("drone_id") or p.get("drone1") or p.get("drone"))
        d2 = _drone(p.get("target_drone_id") or p.get("drone2") or p.get("target_drone"))
        pos1 = p.get("position") or p.get("start") or p.get("station_a")
        pos2 = p.get("target_position") or p.get("end") or p.get("station_b")
        if pos1 is None or pos2 is None:
            raise KeyError("drone1/drone2 或 position/target_position")
        x1, y1 = _xy(pos1)
        x2, y2 = _xy(pos2)
    message = str(p.get("message") or "relay message")
    actions = []
    for d, x, y in ((d1, x1, y1), (d2, x2, y2)):
        actions += [_act("take_off", drone_id=d, altitude=alt),
                    _act("move_to", drone_id=d, x=x, y=y, z=alt, tolerance=2.0)]
    actions.append(_act("send_message", drone_id=d1, target_drone_id=d2, message=message))
    actions += [_act("hover", drone_id=d1), _act("hover", drone_id=d2)]
    return _spec(actions, alt)


def _corridor_detour(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    start, end = _xy(p["start"]), _xy(p["end"])
    clearance = float(p.get("clearance") or 50.0)
    photos = _as_int(p.get("photos"), 2)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    # 沿走廊方向的垂直方向，偏移量 = 净空 + 20 m 余量
    offset = clearance + 20.0
    px, py = -dy / length, dx / length
    # 若给了障碍中心，选择离障碍更远的绕行侧
    sign = 1.0
    if p.get("obstacle_center"):
        ox, oy = _xy(p["obstacle_center"])
        mid = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        left = (mid[0] + px * offset, mid[1] + py * offset)
        right = (mid[0] - px * offset, mid[1] - py * offset)
        sign = 1.0 if math.hypot(left[0] - ox, left[1] - oy) >= math.hypot(right[0] - ox, right[1] - oy) else -1.0
    p1 = (start[0] + dx / 3.0 + px * offset * sign, start[1] + dy / 3.0 + py * offset * sign)
    p2 = (start[0] + dx * 2.0 / 3.0 + px * offset * sign, start[1] + dy * 2.0 / 3.0 + py * offset * sign)
    detour = [{"x": x, "y": y, "z": alt} for x, y in (p1, p2, end)]
    actions = [_act("take_off", drone_id=d, altitude=alt),
               _act("move_to", drone_id=d, x=start[0], y=start[1], z=alt),
               _act("take_photo", drone_id=d),
               _act("move_along_path", drone_id=d, waypoints=detour),
               _act("take_photo", drone_id=d)]
    if photos > 2:
        actions += [_act("take_photo", drone_id=d) for _ in range(photos - 2)]
    if p.get("end_hover", True):
        actions.append(_act("hover", drone_id=d))
    spec = _spec(actions, alt)
    spec["clearance"] = clearance  # 让规划器按任务要求的净空（如 49 m）规划
    return spec


def _battery_cycle(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    charge = float(p.get("charge_amount") or 30.0)
    actions = [_act("take_off", drone_id=d, altitude=alt),
               _act("hover", drone_id=d)]
    if p.get("return_home", True):
        actions.append(_act("return_home", drone_id=d))  # 先回到停机坪再降落
    actions += [_act("land", drone_id=d),
                _act("charge", drone_id=d, charge_amount=charge)]
    spec = _spec(actions, alt)
    spec["ensure_takeoff"] = False
    return spec


def _target_coverage(p: dict) -> dict:
    d, alt = _drone(p.get("drone_id")), float(p["altitude"])
    cx, cy = _xy(p["center"])
    radius = float(p["radius"])
    sweep = float(p.get("sweep") or 50.0)
    coverage = float(p.get("coverage") or 0.7)
    photos = _as_int(p.get("photos"), 1)
    # 蛇形航线：按 sweep 间距生成覆盖圆内约 coverage 比例的航点
    step = max(5.0, sweep)
    rows = max(1, int(2 * radius / step) + 1)
    waypoints = []
    for r in range(rows):
        y = cy - radius + r * step
        if abs(y - cy) > radius:
            continue
        half = math.sqrt(max(0.0, radius ** 2 - (y - cy) ** 2))
        xs = (cx - half, cx + half) if r % 2 == 0 else (cx + half, cx - half)
        waypoints += [{"x": xs[0], "y": y, "z": alt}, {"x": xs[1], "y": y, "z": alt}]
        if len(waypoints) >= int(coverage * rows * 2):
            break
    actions = [_act("take_off", drone_id=d, altitude=alt)]
    # 逐点 move_to：由行为树一段一段规划/执行，单段受阻不会导致整条覆盖失败
    for point in waypoints:
        actions.append(_act("move_to", drone_id=d, x=point["x"], y=point["y"], z=alt,
                            tolerance=2.0))
    actions += [_act("take_photo", drone_id=d) for _ in range(photos)]
    actions.append(_act("hover", drone_id=d))
    spec = _spec(actions, alt)
    spec["coverage_estimate"] = round(coverage, 4)
    return spec


def _emergency_land(p: dict) -> dict:
    d = _drone(p.get("drone_id"))
    message = str(p.get("message") or "emergency stop")
    drones = [_drone(x) for x in (p.get("drones") or [d])]
    actions = [_act("broadcast", drone_id=d, content=message)]
    actions += [_act("land", drone_id=x) for x in drones]
    spec = _spec(actions, float(p.get("altitude") or 30.0))
    spec["ensure_takeoff"] = False  # 紧急降落不需要先起飞
    return spec


_BUILDERS = {
    "altitude_stage": _altitude_stage,
    "grid_survey": _grid_survey,
    "target_photo": _target_photo,
    "line_formation": _line_formation,
    "perimeter_patrol": _perimeter_patrol,
    "relay_message": _relay_message,
    "corridor_detour": _corridor_detour,
    "battery_cycle": _battery_cycle,
    "target_coverage": _target_coverage,
    "emergency_land": _emergency_land,
}


def execute_template(map_path: str, drone_id: str, template_id: str, params: dict,
                     api_base: str = "http://127.0.0.1:8000",
                     config_path: str | None = None) -> dict:
    """执行模板动作：build spec -> 行为树执行。"""
    from .mission_run import execute_actions

    spec = build_template_spec(template_id, params)
    if spec.get("error"):
        return {"ok": False, "error": spec["error"]}
    return execute_actions(map_path, drone_id, spec, api_base, config_path)
