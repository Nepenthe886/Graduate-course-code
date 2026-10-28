"""动作库（L3，薄封装）：只做官方 11 个无人机命令的 1:1 映射，不含任何编排逻辑。

官方任务模版（75 会话 / 1500 条实例）涉及的命令：
    move_to(3357) take_off(2605) move_towards(1917) take_photo(844) hover(738)
    land(430) move_along_path(167) broadcast(96) change_altitude(38)
    return_home(33) rotate(27)

业务编排（覆盖/编队/定向位移等）一律放在行为树模板里，见 src/bt_defs.py。
"""
from __future__ import annotations

ACTION_LIBRARY: dict[str, dict] = {
    "take_off": {
        "endpoint": "POST /drones/{id}/command/take_off",
        "params": {"altitude": "float (optional, query)"},
        "desc": "起飞到指定高度",
    },
    "land": {
        "endpoint": "POST /drones/{id}/command/land",
        "params": {},
        "desc": "降落",
    },
    "change_altitude": {
        "endpoint": "POST /drones/{id}/command/change_altitude",
        "params": {"altitude": "float (required, query)"},
        "desc": "改变高度",
    },
    "rotate": {
        "endpoint": "POST /drones/{id}/command/rotate",
        "params": {"heading": "float 0-359 (required, query)"},
        "desc": "旋转到指定朝向",
    },
    "move_to": {
        "endpoint": "POST /drones/{id}/command/move_to",
        "params": {"x": "float", "y": "float", "z": "float (optional)"},
        "desc": "飞到一个点（参数放 query）",
    },
    "move_towards": {
        "endpoint": "POST /drones/{id}/command/move_towards",
        "params": {"x": "float", "y": "float", "z": "float (optional)", "distance": "float (optional)"},
        "desc": "朝一个点/方向持续推进",
    },
    "move_along_path": {
        "endpoint": "POST /drones/{id}/command/move_along_path",
        "params": {"waypoints": "[{x,y,z}, ...]", "allow_partial_move": "bool"},
        "desc": "沿航点序列飞行（body 传参）",
    },
    "hover": {
        "endpoint": "POST /drones/{id}/command/hover",
        "params": {"duration": "float seconds (optional)"},
        "desc": "悬停",
    },
    "take_photo": {
        "endpoint": "POST /drones/{id}/command/take_photo",
        "params": {},
        "desc": "拍照",
    },
    "broadcast": {
        "endpoint": "POST /drones/{id}/command/broadcast",
        "params": {"content": "str (required)"},
        "desc": "广播消息",
    },
    "return_home": {
        "endpoint": "POST /drones/{id}/command/return_home",
        "params": {},
        "desc": "返航",
    },
}

ATOMIC_ACTIONS = tuple(ACTION_LIBRARY.keys())


def run_atomic(api, drone_id: str, name: str, params: dict | None = None) -> dict:
    """执行一个官方原子动作，返回 {ok, name, error, body}。"""
    params = dict(params or {})
    if name not in ACTION_LIBRARY:
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
            "error": None if ok else str((body or {}).get("message") if isinstance(body, dict) else body)[:160],
            "body": body}
