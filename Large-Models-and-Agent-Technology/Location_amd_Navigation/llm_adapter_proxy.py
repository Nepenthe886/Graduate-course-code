#!/usr/bin/env python
"""LLM 侧适配代理：8011 -> 8010（llama-server），不修改官方 agent4drone。

能力（都可开关）：
  1) 请求侧：把 configs/llm_rules.json 的规则追加到第一条 system 消息末尾
  2) 响应侧 A（完成校验）：
       - 本轮没有任何 role=tool 结果却输出 [TASK DONE] -> 改写成 [TASK FAILED]
       - 同时出现 FAILED/DONE -> 删掉 DONE（FAILED 优先）
  3) 响应侧 B（工具调用修正，v4）：
       - 从任务里解析目标名 -> 查地图得到真实坐标/半径
       - move_to / move_along_path 落点不在目标半径内 -> 改到目标中心
       - 覆盖类任务若只发了一个航点 -> 替换为蛇形覆盖航线（覆盖率实测 >95%）

用法：
    py -3.12 llm_adapter_proxy.py --port 8011 --upstream http://127.0.0.1:8010
    agent GUI -> Settings -> LLM base_url: http://127.0.0.1:8011/v1
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RULES_FILE = ROOT / "configs" / "llm_rules.json"
TOOL_REWRITE_FILE = ROOT / "configs" / "tool_rewrite.json"
SESSIONS_DIR = ROOT.parent / "MultiUAV-Plat-v0.46_Windows" / "UAVSessions"
DEFAULT_UAV_BASE = "http://127.0.0.1:8000"

DONE_RE = re.compile(r"\[\s*task\s*[-_ ]?done\s*\]", re.IGNORECASE)
FAILED_RE = re.compile(r"\[\s*task\s*[-_ ]?fail(?:ed)?\s*\]", re.IGNORECASE)
TARGET_RE = re.compile(r"((?:polygon|circle|ellipse|polygon obstacle|circle obstacle)\s*target\s*\d+)",
                       re.IGNORECASE)
MOVE_TOOLS = ("move_to", "move_along_path")
COVERAGE_KEYWORDS = ("coverage", "95%", "area search", "area coverage", "cover the area", "search at least")


# ------------------------------------------------------------------ 配置
def load_rules() -> str:
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    rules = data.get("rules") if isinstance(data, dict) else None
    if isinstance(rules, list):
        return "\n".join(f"- {line}" for line in rules if str(line).strip())
    return str(rules or "")


def load_tool_rewrite_config() -> dict:
    default = {
        "enabled": True,
        "coverage_keywords": list(COVERAGE_KEYWORDS),
        "coverage_spacing_ratio": 0.2,
        "max_coverage_waypoints": 60,
        "min_coverage_radius": 20.0,
    }
    try:
        data = json.loads(TOOL_REWRITE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            default.update({k: v for k, v in data.items() if not k.startswith("_")})
    except Exception:
        pass
    return default


# ------------------------------------------------------------------ 地图
_WORLD_CACHE = {"world": None, "path": None, "ts": 0.0}
_UAV_BASE = [DEFAULT_UAV_BASE]  # 由 Handler 在启动时写入，供工具改写时查无人机位置


def _http_json(url: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _find_session_file(session_id: str, sessions_dir: Path) -> Path | None:
    if not session_id or not sessions_dir.is_dir():
        return None
    matches = sorted(sessions_dir.glob(f"*_{session_id}.json"))
    return matches[0] if matches else None


def load_world(map_path: str | None = None, uav_base: str = DEFAULT_UAV_BASE,
               sessions_dir: Path = SESSIONS_DIR, ttl: float = 60.0):
    """按 --map 或 UAV 当前会话加载地图；带缓存。"""
    now = time.time()
    if _WORLD_CACHE["world"] is not None and now - _WORLD_CACHE["ts"] < ttl:
        return _WORLD_CACHE["world"]
    path = Path(map_path) if map_path else None
    if path is None:
        session = _http_json(f"{uav_base.rstrip('/')}/sessions/current") or {}
        session_id = str(session.get("id") or "")
        path = _find_session_file(session_id, sessions_dir)
    if path is None or not path.is_file():
        # 会话/文件暂不可查时，复用上次成功加载的地图，避免改写失效
        if _WORLD_CACHE['world'] is not None:
            return _WORLD_CACHE['world']
        return None
    try:
        from src.worldmap import WorldMap

        world = WorldMap.load(path)
    except Exception:
        return None
    _WORLD_CACHE.update({"world": world, "path": str(path), "ts": now})
    return world


# ------------------------------------------------------------------ 工具调用改写
def _normalise(name: str) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().lower()


def requested_targets(task_text: str, world) -> list:
    if world is None or not task_text:
        return []
    found = []
    seen = set()
    for match in TARGET_RE.finditer(task_text):
        key = _normalise(match.group(1))
        if key in seen:
            continue
        seen.add(key)
        for target in world.targets:
            if _normalise(target.name) == key:
                found.append(target)
                break
    return found


def is_coverage_task(task_text: str, config: dict) -> bool:
    low = (task_text or "").lower()
    return any(keyword.lower() in low for keyword in config.get("coverage_keywords", COVERAGE_KEYWORDS))


def _parse_args(tool_call: dict) -> dict:
    function = tool_call.get("function") or {}
    raw = function.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    inner = raw.get("input_json")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:
            inner = None
    return inner if isinstance(inner, dict) else raw


def _set_args(tool_call: dict, args: dict) -> None:
    function = tool_call.setdefault("function", {})
    original = function.get("arguments")
    if isinstance(original, str):
        try:
            outer = json.loads(original)
        except Exception:
            outer = {}
        if isinstance(outer, dict) and "input_json" in outer:
            outer["input_json"] = json.dumps(args, ensure_ascii=False)
            function["arguments"] = json.dumps(outer, ensure_ascii=False)
            return
    function["arguments"] = json.dumps(args, ensure_ascii=False)


def _waypoints_of(args: dict) -> list[dict]:
    waypoints = args.get("waypoints")
    if isinstance(waypoints, list):
        return [w for w in waypoints if isinstance(w, dict)]
    if "x" in args and "y" in args:
        return [{"x": args.get("x"), "y": args.get("y"), "z": args.get("z")}]
    return []


def _target_footprint(target):
    getter = getattr(target, "footprint", None)
    if callable(getter):
        return getter()
    return None


def _coverage_area(target) -> float:
    approx = getattr(target, "approx_area", None)
    if callable(approx):
        try:
            return float(approx())
        except Exception:
            return 0.0
    radius = float(getattr(target, "radius", 0.0) or 0.0)
    return math.pi * radius * radius


COORD_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def parse_task_waypoints(task_text: str) -> list[dict]:
    """从任务文本解析有序 3D 坐标清单：(x, y, z)。"""
    points = []
    for match in COORD_RE.finditer(task_text or ""):
        try:
            points.append({"x": float(match.group(1)), "y": float(match.group(2)),
                           "z": float(match.group(3))})
        except ValueError:
            continue
    return points


def _plan_legs(world, start_xy, points: list[dict], clearance: float = 45.0) -> list[dict]:
    """从 start 依次到每个点做绕障规划，拼成完整航点列表。"""
    if not points:
        return []
    try:
        from src.planner import Planner

        planner = Planner(world, clearance=clearance) if world is not None else None
    except Exception:
        planner = None
    result: list[dict] = []
    current = start_xy
    for point in points:
        goal = (float(point["x"]), float(point["y"]))
        leg: list[tuple[float, float, float]] = []
        if planner is not None:
            try:
                route = planner.plan(current, goal, altitude=float(point.get("z", 20.0)))
                leg = route.waypoints_xyz or [(goal[0], goal[1], float(point.get("z", 20.0)))]
            except Exception:
                leg = [(goal[0], goal[1], float(point.get("z", 20.0)))]
        else:
            leg = [(goal[0], goal[1], float(point.get("z", 20.0)))]
        for xyz in leg:
            item = {"x": round(float(xyz[0]), 2), "y": round(float(xyz[1]), 2), "z": round(float(xyz[2]), 2)}
            if (not result or abs(result[-1]["x"] - item["x"]) > 1e-6
                    or abs(result[-1]["y"] - item["y"]) > 1e-6):
                result.append(item)
        current = goal
    return result


def _drone_xy(uav_base: str, drone_id: str):
    """从 UAV API 查无人机当前 (x, y)。"""
    if not drone_id:
        return None
    data = _http_json(f"{uav_base.rstrip('/')}/drones/{drone_id}", timeout=3.0)
    if not isinstance(data, dict):
        return None
    position = data.get("position") or {}
    try:
        return (float(position.get("x", 0.0)), float(position.get("y", 0.0)))
    except (TypeError, ValueError):
        return None


def _route_prefix(world, start_xy, cover_first: dict, altitude: float) -> list[dict]:
    """用导航模块规划 start -> 覆盖起点，返回中间绕障航点（不含两端）。"""
    if world is None or start_xy is None:
        return []
    try:
        from src.planner import Planner

        planner = Planner(world, clearance=45.0)
        route = planner.plan(start_xy, (float(cover_first["x"]), float(cover_first["y"])), altitude=altitude)
        points = route.waypoints_xyz[1:-1] if route.waypoints_xyz else []
        return [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(p[2], 2)} for p in points]
    except Exception:
        return []


def _serpentine_waypoints(target, altitude: float, config: dict) -> list[dict]:
    """按目标真实廓形生成覆盖航点（多边形用顶点，圆/椭圆用参数）。"""
    from src.coverage import serpentine_polygon, serpentine_route

    swath = float(config.get("coverage_swath", 40.0))
    ratio = float(config.get("coverage_spacing_ratio", 0.2))
    footprint = _target_footprint(target)
    if footprint and len(footprint) >= 3 and str(getattr(target, "type", "")).lower() != "circle":
        xs = [p[0] for p in footprint]
        ys = [p[1] for p in footprint]
        min_dim = max(1.0, min(max(xs) - min(xs), max(ys) - min(ys)))
        spacing = max(8.0, min(swath * 0.9, min_dim * max(ratio, 0.05) * 4))
        points = serpentine_polygon(footprint, spacing)
    else:
        radius = float(getattr(target, "radius", 0.0) or 0.0)
        spacing = max(8.0, swath * 0.9)
        points = serpentine_route(target.center, radius, spacing)
    limit = int(config.get("max_coverage_waypoints", 60))
    if len(points) > limit:
        stride = max(1, len(points) // limit)
        points = points[::stride]
    return [{"x": round(p[0], 2), "y": round(p[1], 2), "z": round(altitude, 2)} for p in points]


def _rewrite_mission_route(payload: dict, task_text: str, world, config: dict):
    """任务里给了 >=4 个坐标时，一次性改写成完整航线（逐段绕障）。"""
    notes: list[str] = []
    if world is None or not config.get("enabled", True):
        return payload, notes
    points = parse_task_waypoints(task_text)
    if len(points) < 4:
        return payload, notes
    if is_coverage_task(task_text, config):
        return payload, notes
    result = json.loads(json.dumps(payload))
    message = result["choices"][0].get("message") or {}
    applied = False
    for call in message.get("tool_calls") or []:
        name = str((call.get("function") or {}).get("name") or "")
        if name not in MOVE_TOOLS:
            continue
        args = _parse_args(call)
        drone_id = str(args.get("drone_id") or "")
        start_xy = _drone_xy(_UAV_BASE[0], drone_id) if drone_id else None
        full = _plan_legs(world, start_xy, points)
        limit = int(config.get("max_mission_waypoints", 150))
        if len(full) > limit:
            stride = max(1, len(full) // limit)
            full = full[::stride] + [full[-1]]
        if not full:
            continue
        args["waypoints"] = full
        args.setdefault("allow_partial_move", False)
        args.pop("x", None)
        args.pop("y", None)
        if name == "move_to":
            call["function"]["name"] = "move_along_path"
        _set_args(call, args)
        applied = True
        notes.append(f"mission_route:{len(points)}pts->{len(full)}wp")
    return (result if applied else payload), notes


def rewrite_tool_calls(payload: dict, task_text: str, world, config: dict):
    """返回 (新 payload, 说明列表)。只改 move_to / move_along_path。"""
    notes: list[str] = []
    if not isinstance(payload, dict) or world is None or not config.get("enabled", True):
        return payload, notes
    choices = payload.get("choices") or []
    if not choices:
        return payload, notes
    message = choices[0].get("message") or {}
    tool_calls = message.get("tool_calls") or []
    if not tool_calls:
        return payload, notes

    # 长航点任务优先：一次性生成完整航线
    payload, mission_notes = _rewrite_mission_route(payload, task_text, world, config)
    if mission_notes:
        return payload, mission_notes

    targets = requested_targets(task_text, world)
    if not targets:
        return payload, notes
    wanted = targets[0]
    radius = max(float(getattr(wanted, "radius", 1.0) or 1.0), 5.0)
    coverage = is_coverage_task(task_text, config)

    result = json.loads(json.dumps(payload))
    changed = False
    for index, tool_call in enumerate(result["choices"][0]["message"]["tool_calls"]):
        name = str((tool_call.get("function") or {}).get("name") or "")
        if name not in MOVE_TOOLS:
            continue
        args = _parse_args(tool_call)
        waypoints = _waypoints_of(args)
        if not waypoints:
            continue
        last = waypoints[-1]
        try:
            lx, ly = float(last.get("x", 0.0)), float(last.get("y", 0.0))
        except (TypeError, ValueError):
            continue
        distance = ((lx - wanted.center[0]) ** 2 + (ly - wanted.center[1]) ** 2) ** 0.5
        altitude = float(last.get("z") or 22.0)

        area = _coverage_area(wanted)
        at_target = distance <= max(radius, 5.0) + 5.0
        need_coverage = coverage and at_target and area >= float(
            config.get("min_coverage_area", 200.0))
        if need_coverage:
            cover_points = _serpentine_waypoints(wanted, altitude, config)
            drone_id = str(args.get("drone_id") or "")
            start_xy = _drone_xy(_UAV_BASE[0], drone_id) if drone_id else None
            prefix = _route_prefix(world, start_xy, cover_points[0] if cover_points else {}, altitude)
            approach = prefix if prefix else [w for w in waypoints[:-1]]
            merged = []
            for point in approach + cover_points:
                if (not merged
                        or abs(float(merged[-1].get("x", 0)) - float(point.get("x", 0))) > 1e-6
                        or abs(float(merged[-1].get("y", 0)) - float(point.get("y", 0))) > 1e-6):
                    merged.append(point)
            args["waypoints"] = merged
            args.setdefault("allow_partial_move", False)
            args.pop("x", None)
            args.pop("y", None)
            if name == "move_to":
                tool_call["function"]["name"] = "move_along_path"
            _set_args(tool_call, args)
            notes.append(f"coverage_route:{wanted.name}:{len(cover_points)}pts"
                         f"(+{len(approach)}approach)")
            changed = True
            continue

        if distance > radius:
            last["x"], last["y"] = round(wanted.center[0], 2), round(wanted.center[1], 2)
            if len(waypoints) > 1:
                args["waypoints"] = waypoints
            else:
                args["x"], args["y"] = last["x"], last["y"]
            _set_args(tool_call, args)
            notes.append(f"retarget:{wanted.name}:moved {distance:.0f}m -> center")
            changed = True

    return (result if changed else payload), notes


# ------------------------------------------------------------------ 完成校验
def tool_results_since_last_user(messages: list) -> int:
    count = 0
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            break
        if role == "tool":
            count += 1
    return count


MISSION_TOOL = "nav_execute_mission"
MOVE_OR_PLAN_TOOLS = ("move_to", "move_along_path", "nav_plan_route", "nav_points_route",
                      "nav_route_to_target", "nav_coverage_route", "move_towards", "move_along_path")


def _find_drone_in_text(task_text: str) -> str:
    import re as _re
    match = _re.search(r"\bDrone\s+(\d+)\b", task_text or "", _re.IGNORECASE)
    return str(match.group(1)) if match else ""


def _resolve_drone_id(task_text: str, uav_base: str) -> str:
    """把任务里的无人机（Bravo / Drone N）解析成真实 id（查 /drones）。"""
    text = task_text or ""
    name_hint = _find_drone_in_text(text)
    drones = _http_json(f"{uav_base.rstrip('/')}/drones", timeout=3.0)
    if not isinstance(drones, list):
        # UAV API 不可用时退回名字，由 nav_execute_mission 工具自己解析 id
        return ("Drone " + name_hint) if name_hint else ""
    lowered = text.lower()
    for drone in drones:
        name = str(drone.get("name") or "").strip()
        if name and re.search(r"\b" + re.escape(name.lower()) + r"\b", lowered):
            return str(drone.get("id") or "")
    if name_hint:
        wanted = f"drone {name_hint}".lower()
        for drone in drones:
            if str(drone.get("name", "")).strip().lower() == wanted:
                return str(drone.get("id", ""))
    return str((drones[0] or {}).get("id", ""))


def _mission_already_called(messages: list) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for call in (message.get("tool_calls") or []):
            function = call.get("function") or {}
            if str(function.get("name") or "") == MISSION_TOOL:
                return True
    return False


def enforce_mission_delegation(payload: dict, request_messages: list, task_text: str,
                               uav_base: str):
    """返回 (payload, note)。"""
    if not isinstance(payload, dict):
        return payload, None
    points = parse_task_waypoints(task_text)
    coverage_task = bool(re.search(r"(polygon|circle|ellipse)\s*target\s*\d+", task_text or "", re.I)) \
        and bool(re.search(r"coverage|search pattern|serpentine|grid|spiral", task_text or "", re.I))
    formation_task = bool(re.search(r"formation", task_text or "", re.I)) \
        and bool(re.search(r"move\s+(north|south|east|west)", task_text or "", re.I))
    multitarget_task = bool(re.search(r"(polygon|circle|ellipse)\s+target\s*\d+", task_text or "", re.I)) \
        and bool(re.search(r"visit|in sequence|sequential|multi-target|at each target", task_text or "", re.I))
    inspection_task = bool(re.search(r"(polygon|circle|ellipse)\s+target\s*\d+", task_text or "", re.I)) \
        and bool(re.search(r"inspect", task_text or "", re.I))
    photo_task = bool(re.search(r"photo|photograph|image", task_text or "", re.I)) and len(points) >= 2
    relay_task = bool(re.search(r"relay|broadcast", task_text or "", re.I))
    fleet_task = bool(re.search(r"target sweep|guard point|perimeter|photographs?\s+targets?|"
                                r"surveys?\s+target|assign|coordinated search sweep", task_text or "", re.I))
    template_task = multitarget_task or inspection_task or photo_task or relay_task or fleet_task
    if len(points) < 4 and not coverage_task and not formation_task and not template_task:
        return payload, None
    if _mission_already_called(request_messages):
        return payload, None
    drone_id = _resolve_drone_id(task_text, uav_base)
    if not drone_id:
        return payload, "mission_enforce_skipped_no_drone"
    altitude_match = re.search(r"take\s+off\s+to\s+(\d+(?:\.\d+)?)\s*m", task_text or "", re.I)
    if altitude_match:
        altitude = float(altitude_match.group(1))
    elif points:
        altitude = float(points[0].get("z", 32.0))
    else:
        altitude = 32.0
    json_args = {"task_text": task_text, "drone_id": drone_id, "altitude": altitude}
    forced = {
        "id": "call_proxy_mission_1",
        "type": "function",
        "function": {
            "name": MISSION_TOOL,
            "arguments": json.dumps({"input_json": json.dumps(json_args, ensure_ascii=False)},
                                    ensure_ascii=False),
        },
    }
    result = json.loads(json.dumps(payload))
    choice = (result.get("choices") or [{}])[0]
    message = choice.setdefault("message", {})
    existing = message.get("tool_calls") or []
    names = [str((c.get("function") or {}).get("name") or "") for c in existing]
    if existing and any(n in MOVE_OR_PLAN_TOOLS for n in names):
        message["tool_calls"] = [forced]
        message["content"] = None
        choice["finish_reason"] = "tool_calls"
        return result, "mission_enforced_replaced:" + ",".join(names)
    # 覆盖/编队任务：除查询类工具外，一律改成行为树入口
    if existing and (coverage_task or formation_task or template_task):
        allowlist = {"nav_execute_mission", "list_drones", "get_session_info", "get_weather",
                     "get_drone_status", "nav_map_info", "nav_nearby_obstacles"}
        if any(name not in allowlist for name in names):
            message["tool_calls"] = [forced]
            message["content"] = None
            choice["finish_reason"] = "tool_calls"
            return result, "mission_enforced_forced_task:" + ",".join(names)
    if not existing:
        message["tool_calls"] = [forced]
        message["content"] = None
        choice["finish_reason"] = "tool_calls"
        return result, "mission_enforced_forced"
    return payload, None


FAILURE_HINTS = ('"ok": false', '"ok":false', "out_of_bounds", "航点超出地图边界",
                 "规划失败", "超预算")


def failure_evidence(request_messages: list):
    """只看最近一条工具结果：成功则无失败证据，避免旧的失败掩盖新的成功。"""
    for message in reversed(request_messages or []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "user":
            break
        if role not in ("tool", "function"):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            failures = data.get("failures")
            ok = data.get("ok")
            error = str(data.get("error") or "").strip()
            if ok is False or (isinstance(failures, list) and failures) or error:
                first = failures[0] if isinstance(failures, list) and failures else {}
                reason = str((first or {}).get("error") or "").strip() if isinstance(first, dict) else ""
                return reason or error or "工具返回 ok=false（%d 项失败）" % len(failures or [])
            return None
        if any(hint in text for hint in FAILURE_HINTS):
            return "工具执行失败（详见工具输出）"
        return None
    return None


MAX_TOOL_CONTENT = 3000

TEMPLATE_TOOL = "nav_template_action"
ATOMIC_RAW = {"take_off", "land", "move_to", "move_towards", "move_along_path", "hover",
              "take_photo", "change_altitude", "rotate", "broadcast", "send_message",
              "charge", "return_home"}


def _detect_template(task_text: str) -> str | None:
    try:
        from src.template_actions import detect_template
        return detect_template(task_text)
    except Exception:  # noqa: BLE001
        return None


def _template_called(messages: list) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for call in (message.get("tool_calls") or []):
            function = call.get("function") or {}
            if str(function.get("name") or "") == TEMPLATE_TOOL:
                return True
    return False


def _coords_of(text: str) -> list[list[float]]:
    return [[float(a), float(b)] for a, b in
            re.findall(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)", text or "")]


def _first_float(pattern: str, text: str):
    match = re.search(pattern, text or "", re.I)
    return float(match.group(1)) if match else None


def _quoted(text: str) -> str | None:
    match = re.search(r'["“]([^"”]+)["”]', text or "")
    return match.group(1) if match else None


def parse_template_params(template: str, text: str, drone_id: str) -> dict | None:
    """从任务文本粗解析模板参数；解析不完整返回 None（不强行改写）。"""
    coords = _coords_of(text)
    alt = _first_float(r"(?:to|at)\s+(\d+(?:\.\d+)?)\s*m(?:eters?)?\b", text) or 30.0
    params: dict = {"altitude": alt}
    if drone_id:
        params["drone_id"] = drone_id
    if template == "altitude_stage":
        alts = [float(x) for x in re.findall(r"to\s+(\d+(?:\.\d+)?)\s*m", text or "", re.I)]
        if not alts:
            return None
        # 单高度任务：起飞即巡航高度，alt1 == alt2（不再二次爬升）
        params.update(alt1=alts[0], alt2=alts[1] if len(alts) > 1 else alts[0])
    elif template == "grid_survey":
        if len(coords) < 3:
            return None
        params.update(corners=coords, photos=max(len(coords), 4))
    elif template == "perimeter_patrol":
        if len(coords) < 4:
            return None
        params.update(corners=coords[:4])
    elif template == "line_formation":
        if len(coords) < 2:
            return None
        names = re.findall(r"([A-Z][a-zA-Z]+)\s+and\s+([A-Z][a-zA-Z]+)", text or "")
        first, second = names[0] if names else ("Drone 1", "Drone 2")
        params["drones"] = [
            {"drone_id": first, "x": coords[0][0], "y": coords[0][1]},
            {"drone_id": second, "x": coords[1][0], "y": coords[1][1]},
        ]
    elif template == "relay_message":
        if len(coords) < 2:
            return None
        names = re.findall(r"([A-Z][a-zA-Z]+)\s+climbs", text or "")
        first = names[0] if len(names) > 0 else "Drone 1"
        second = names[1] if len(names) > 1 else "Drone 2"
        params.update(drone1={"drone_id": first, "x": coords[0][0], "y": coords[0][1]},
                      drone2={"drone_id": second, "x": coords[1][0], "y": coords[1][1]},
                      message=_quoted(text) or "relay message")
    elif template == "corridor_detour":
        if len(coords) < 2:
            return None
        params.update(start=coords[0], end=coords[1],
                      clearance=_first_float(r"(\d+(?:\.\d+)?)\s*m(?:eters?)?\s+away", text) or 40.0,
                      photos=2)
    elif template == "target_coverage":
        match = re.search(r"(?:Search|Cover)\s+([A-Z][\w]*(?:\s+[A-Z][\w]*)*)\s+until", text or "")
        if not match:
            match = re.search(r"([A-Z][\w]*(?:\s+[A-Z][\w]*)*)\s+until at least", text or "")
        if not match:
            return None
        params["target"] = match.group(1).strip()
        params["sweep"] = _first_float(r"sweep of\s+(\d+(?:\.\d+)?)", text) or 50.0
        percent = _first_float(r"(\d+(?:\.\d+)?)\s*percent", text) or 70.0
        params["coverage"] = max(0.1, min(1.0, percent / 100.0))
        params["photos"] = 1
    elif template == "battery_cycle":
        params["charge_amount"] = _first_float(r"(\d+(?:\.\d+)?)\s*percent", text) or 30.0
    elif template == "target_photo":
        match = re.search(r"target\s+([A-Z][\w-]*(?:\s+[A-Z][\w-]*)*)", text or "")
        if not match:
            return None
        params["target"] = match.group(1).strip()
    else:
        return None
    return params


def _call_template_name(call: dict) -> str | None:
    """从一次 nav_template_action 调用里取出 template 名。"""
    function = call.get("function") or {}
    raw = function.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if isinstance(args, dict) and isinstance(args.get("input_json"), str):
            args = json.loads(args["input_json"])
        if isinstance(args, dict):
            return str(args.get("template") or args.get("template_id") or "") or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _called_templates(messages: list) -> set[str]:
    found: set[str] = set()
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for call in (message.get("tool_calls") or []):
            if str((call.get("function") or {}).get("name") or "") == TEMPLATE_TOOL:
                name = _call_template_name(call)
                if name:
                    found.add(name)
    return found


def _norm_text(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _call_args(call: dict) -> dict | None:
    function = call.get("function") or {}
    raw = function.get("arguments")
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        if isinstance(args, dict) and isinstance(args.get("input_json"), str):
            args = json.loads(args["input_json"])
        return args if isinstance(args, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _fix_template_message(payload: dict, task_text: str, template: str):
    """模板已调用但消息内容被模型改坏时，按任务原文纠正。"""
    if template != "relay_message":
        return payload, None
    expected = _quoted(task_text)
    if not expected:
        return payload, None
    try:
        result = json.loads(json.dumps(payload))
    except Exception:  # noqa: BLE001
        return payload, None
    message = ((result.get("choices") or [{}])[0].setdefault("message", {}))
    changed = False
    for call in (message.get("tool_calls") or []):
        if str((call.get("function") or {}).get("name") or "") != TEMPLATE_TOOL:
            continue
        if _call_template_name(call) != template:
            continue
        args = _call_args(call)
        params = (args or {}).get("params") or {}
        current = str(params.get("message") or "")
        if current.strip() != expected:
            params["message"] = expected
            call["function"]["arguments"] = json.dumps(
                {"input_json": json.dumps({**(args or {}), "params": params}, ensure_ascii=False)},
                ensure_ascii=False)
            changed = True
    if changed:
        return result, "template_message_fixed"
    return payload, None


def _coverage_executed(messages: list) -> bool:
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for call in (message.get("tool_calls") or []):
            if str((call.get("function") or {}).get("name") or "") != "nav_coverage_route":
                continue
            if bool((_call_args(call) or {}).get("execute")):
                return True
    return False


def enforce_template_delegation(payload: dict, request_messages: list, task_text: str,
                                uav_base: str):
    """模板任务禁用裸原子动作：把 take_off/hover/move_to 等改写为 nav_template_action。"""
    if not isinstance(payload, dict):
        return payload, None
    template = _detect_template(task_text)
    if not template:
        return payload, None
    if template in _called_templates(request_messages):
        return _fix_template_message(payload, task_text, template)
    try:
        result = json.loads(json.dumps(payload))
    except Exception:  # noqa: BLE001
        return payload, None
    choice = (result.get("choices") or [{}])[0]
    message = choice.setdefault("message", {})
    calls = message.get("tool_calls") or []
    if not calls:
        return payload, None
    names = [str((c.get("function") or {}).get("name") or "") for c in calls]
    raw_calls = [name for name in names if name in ATOMIC_RAW]
    wrong_template = [
        call for call in calls
        if str((call.get("function") or {}).get("name") or "") == TEMPLATE_TOOL
        and _call_template_name(call) not in (None, template)
    ]
    coverage_preview = [
        call for call in calls
        if str((call.get("function") or {}).get("name") or "") == "nav_coverage_route"
        and not bool((_call_args(call) or {}).get("execute"))
    ]
    mission_calls = [
        call for call in calls
        if str((call.get("function") or {}).get("name") or "") == MISSION_TOOL
    ]
    if not raw_calls and not wrong_template and not coverage_preview and not mission_calls:
        return payload, None
    drone_id = _resolve_drone_id(task_text, uav_base) or ""
    params = parse_template_params(template, task_text, drone_id)
    if not params:
        return payload, None
    args = {"template": template, "params": params}
    message["tool_calls"] = [{
        "id": "call_proxy_template_1",
        "type": "function",
        "function": {
            "name": TEMPLATE_TOOL,
            "arguments": json.dumps({"input_json": json.dumps(args, ensure_ascii=False)},
                                    ensure_ascii=False),
        },
    }]
    message["content"] = None
    choice["finish_reason"] = "tool_calls"
    return result, f"template_enforced_replaced:{','.join(names)}"



def trim_tool_messages(messages: list) -> list:
    """截断过大的工具结果（覆盖航线/执行明细），避免超出模型上下文。"""
    result = []
    for message in (messages or []):
        if isinstance(message, dict) and str(message.get("role") or "") in ("tool", "function"):
            content = message.get("content")
            if isinstance(content, str) and len(content) > MAX_TOOL_CONTENT:
                kept = content[:MAX_TOOL_CONTENT]
                updated = dict(message)
                updated["content"] = kept + "\n...[truncated %d chars]" % (len(content) - MAX_TOOL_CONTENT)
                result.append(updated)
                continue
        result.append(message)
    return result


def _recent_tool_errors(messages: list) -> tuple[int, str]:
    """统计最近连续失败的 tool 结果数量与最后一条错误信息。"""
    count = 0
    last = ""
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "user":
            break
        if role not in ("tool", "function"):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001
            data = None
        if not isinstance(data, dict):
            continue
        status = str(data.get("status") or "")
        failed = data.get("ok") is False or status in ("error", "failed") or bool(data.get("error"))
        if failed:
            count += 1
            last = str(data.get("message") or data.get("error") or last)
        else:
            break
    return count, last


def enforce_loop_breaker(payload: dict, request_messages: list):
    """连续 3 次以上工具失败时停止重试，直接给出失败结论。"""
    count, last = _recent_tool_errors(request_messages)
    if count < 3:
        return payload, None
    try:
        result = json.loads(json.dumps(payload))
    except Exception:  # noqa: BLE001
        return payload, None
    choice = (result.get("choices") or [{}])[0]
    message = choice.setdefault("message", {})
    message["tool_calls"] = []
    message["content"] = ("[TASK FAILED] 连续 %d 次工具调用失败，已停止重试。最后错误：%s"
                          % (count, last[:160]))
    choice["finish_reason"] = "stop"
    return result, "loop_breaker:%d" % count


def rewrite_completion(payload: dict, request_messages: list, guard_done: bool = True,
                       task_text: str = ""):
    if not isinstance(payload, dict):
        return payload, None
    choices = payload.get("choices") or []
    if not choices:
        return payload, None
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return payload, None

    has_done = bool(DONE_RE.search(content))
    has_failed = bool(FAILED_RE.search(content))
    tool_calls = message.get("tool_calls") or []

    # 模板只作为可选的加速路径：不强制“必须调用模板”才算完成。
    # 完成是否成立交给工具结果与最终校验（失败证据/未执行工具仍会拦截）。

    reason = failure_evidence(request_messages)
    if has_done and reason:
        new_content = DONE_RE.sub("[TASK FAILED] " + reason, content)
        return _replace_content(payload, new_content), "done_with_failures"

    # 复读退化 / 超长且无终态标记：截断并补终态，避免死循环与"无 TASK DONE"
    if not tool_calls and len(content) > 1200:
        cleaned = content[:600].rstrip()
        marker = "[TASK FAILED] " + (reason or "执行失败（输出过长/重复，已截断）")
        return _replace_content(payload, cleaned + "\n\n" + marker), "degraded_truncated"
    if not tool_calls and reason and not has_done and not has_failed:
        return _replace_content(payload, content.rstrip() + "\n\n[TASK FAILED] " + reason), "failure_marker_added"

    if has_failed and has_done:
        return _replace_content(payload, DONE_RE.sub("", content).strip()), "removed_conflicting_task_done"
    if guard_done and has_done and not tool_calls and tool_results_since_last_user(request_messages) == 0:
        new_content = ("[TASK FAILED] No tool call was executed in this turn, so the task was not performed. "
                       "Re-plan and actually call the tools before reporting completion.\n"
                       + DONE_RE.sub("[TASK FAILED]", content))
        return _replace_content(payload, new_content), "hallucinated_completion"
    return payload, None


def _replace_content(payload: dict, new_content: str) -> dict:
    result = json.loads(json.dumps(payload))
    result["choices"][0]["message"]["content"] = new_content
    return result


def inject_rules(messages: list, rules_text: str, marker: str):
    if not rules_text or not messages:
        return messages, False
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "system":
            content = str(message.get("content") or "")
            if marker in content:
                return messages, False
            result = list(messages)
            updated = dict(message)
            updated["content"] = content + "\n\n" + rules_text
            result[index] = updated
            return result, True
    return [{"role": "system", "content": rules_text}] + list(messages), True


# ------------------------------------------------------------------ HTTP
def forward(method, url, body, headers, timeout=600.0):
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        if key.lower() in ("host", "content-length", "connection", "accept-encoding"):
            continue
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Handler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:8010"
    rules_text = ""
    guard_done = True
    marker = "[[llm-adapter-rules-v1]]"
    rewrite_config: dict = {"enabled": False}
    map_path: str | None = None
    uav_base = DEFAULT_UAV_BASE
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[llm-proxy] " + (fmt % args) + "\n")

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def _handle(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else None
        target = f"{self.upstream}{self.path}"
        request_messages: list = []
        task_text = ""
        is_completion = "/chat/completions" in self.path
        stream = False

        if raw and is_completion:
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                data = None
            if isinstance(data, dict):
                # 生成上限：没有 max_tokens 就补上，避免 llama-server 无限生成导致超时
                limit = data.get("max_tokens")
                if limit is None:
                    data["max_tokens"] = 768
                    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                elif isinstance(limit, (int, float)) and limit > 1024:
                    data["max_tokens"] = 1024
                    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                stream = bool(data.get("stream"))
                messages = data.get("messages")
                if isinstance(messages, list):
                    request_messages = messages
                    for message in reversed(messages):
                        if isinstance(message, dict) and message.get("role") == "user":
                            task_text = str(message.get("content") or "")
                            break
                    if self.rules_text:
                        new_messages, changed = inject_rules(
                            messages, self.marker + "\n" + self.rules_text, self.marker)
                        if changed:
                            data["messages"] = new_messages
                            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    trimmed = trim_tool_messages(data.get("messages") or [])
                    if trimmed != data.get("messages"):
                        data["messages"] = trimmed
                        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")

        status, payload = forward(method, target, raw, dict(self.headers))

        if is_completion and status == 200 and not stream:
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except Exception:
                parsed = None
            if parsed is not None:
                fixed, note = rewrite_completion(parsed, request_messages, self.guard_done,
                                                 task_text)
                if note:
                    sys.stderr.write(f"[llm-proxy] rewrite: {note}\n")
                enforced, enforce_note = enforce_mission_delegation(
                    fixed, request_messages, task_text, self.uav_base)
                if enforce_note:
                    sys.stderr.write(f"[llm-proxy] {enforce_note}\n")
                    fixed = enforced
                    payload = json.dumps(fixed, ensure_ascii=False).encode("utf-8")
                templated, template_note = enforce_template_delegation(
                    fixed, request_messages, task_text, self.uav_base)
                if template_note:
                    sys.stderr.write(f"[llm-proxy] {template_note}\n")
                    fixed = templated
                looped, loop_note = enforce_loop_breaker(fixed, request_messages)
                if loop_note:
                    sys.stderr.write(f"[llm-proxy] {loop_note}\n")
                    fixed = looped
                if self.rewrite_config.get("enabled", False):
                    world = load_world(self.map_path, self.uav_base)
                    fixed, notes = rewrite_tool_calls(fixed, task_text, world, self.rewrite_config)
                    for item in notes:
                        sys.stderr.write(f"[llm-proxy] tool-rewrite: {item}\n")
                payload = json.dumps(fixed, ensure_ascii=False).encode("utf-8")

        self._send(status, payload)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()


def main(argv=None):
    parser = argparse.ArgumentParser(description="LLM 适配代理（规则注入 + 完成校验 + 工具调用修正）")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--upstream", default="http://127.0.0.1:8010")
    parser.add_argument("--uav-base", default=DEFAULT_UAV_BASE, help="用于查询当前会话的地图")
    parser.add_argument("--map", default=None, help="强制指定地图 JSON（否则按当前会话自动找）")
    parser.add_argument("--no-rules", action="store_true")
    parser.add_argument("--no-guard", action="store_true")
    parser.add_argument("--no-tool-rewrite", action="store_true")
    args = parser.parse_args(argv)

    Handler.upstream = args.upstream.rstrip("/")
    Handler.rules_text = "" if args.no_rules else load_rules()
    Handler.guard_done = not args.no_guard
    Handler.uav_base = args.uav_base.rstrip("/")
    _UAV_BASE[0] = Handler.uav_base
    Handler.map_path = args.map
    Handler.rewrite_config = load_tool_rewrite_config()
    if args.no_tool_rewrite:
        Handler.rewrite_config["enabled"] = False

    server = ThreadingHTTPServer((args.listen, args.port), Handler)
    print(f"[llm-proxy] http://{args.listen}:{args.port} -> {Handler.upstream}", flush=True)
    print(f"[llm-proxy] rules={'on' if Handler.rules_text else 'off'} "
          f"guard={'on' if Handler.guard_done else 'off'} "
          f"tool_rewrite={'on' if Handler.rewrite_config.get('enabled') else 'off'} "
          f"map={Handler.map_path or 'auto(from session)'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
