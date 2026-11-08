"""生成 action 库文档与配置：官方任务模板 -> 组合动作映射。

用法（在 Location_amd_Navigation 目录下）：
    py -3.12 tools/gen_action_library.py
    py -3.12 tools/gen_action_library.py --templates-dir "E:\\...\\任务模板"

输出：
    docs/ACTION_LIBRARY.md      人读的映射表（模板 -> 组合动作 -> 原子命令）
    configs/action_library.json 机器可读（供工具/代理规则加载）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.actions import ACTION_LIBRARY  # noqa: E402
from src.template_actions import TEMPLATES  # noqa: E402

DEFAULT_TEMPLATES = Path(
    r"E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\无人机场景作业提交"
    r"\无人机场景作业提交\班级-序号-谢起东-队员2-任务模板")


def load_official(templates_dir: Path) -> list[dict]:
    """读取官方模板 JSON（id/template.name/category/difficulty/related_apis）。"""
    items: list[dict] = []
    if not templates_dir.is_dir():
        return items
    for path in sorted(templates_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        template = data.get("template") or {}
        apis = template.get("related_apis") or []
        endpoints = []
        for api in apis:
            endpoint = str(api.get("endpoint") or "")
            name = endpoint.rsplit("/", 1)[-1] if endpoint else ""
            if name and name not in [e["name"] for e in endpoints]:
                endpoints.append({"name": name, "endpoint": endpoint,
                                  "params": api.get("parameters") or {}})
        items.append({
            "id": data.get("id") or path.stem,
            "name": template.get("name") or path.stem,
            "category": template.get("category") or "",
            "difficulty": template.get("difficulty") or "",
            "description": template.get("description") or "",
            "content": template.get("content") or "",
            "atomic_endpoints": endpoints,
        })
    return items


def build_library(official: list[dict]) -> dict:
    """合并：官方模板信息 + 本地组合动作元数据。"""
    by_name = {}
    for item in official:
        by_name[item["id"]] = item
    entries = []
    for template_id, meta in TEMPLATES.items():
        atomics = meta.get("atomic") or []
        official_item = by_name.get("tpl_" + template_id) or by_name.get(template_id) or {}
        entries.append({
            "template_id": template_id,
            "official_template_id": official_item.get("id", ""),
            "name": official_item.get("name") or meta.get("name"),
            "category": official_item.get("category") or meta.get("category"),
            "difficulty": official_item.get("difficulty", ""),
            "composite_tool": "nav_template_action",
            "params": meta.get("params"),
            "atomic_sequence": atomics,
            "official_apis": [e["name"] for e in official_item.get("atomic_endpoints", [])],
            "note": meta.get("name", ""),
        })
    return {
        "atomic_actions": {name: info for name, info in ACTION_LIBRARY.items()},
        "composite_actions": entries,
        "templates_source": str(DEFAULT_TEMPLATES),
    }


def render_md(library: dict) -> str:
    lines = ["# 无人机 Action 库（由官方任务模板自动生成）", "",
             "## 一、原子动作（官方命令，L3 薄封装）", "",
             "| 动作 | 官方接口 | 参数 | 说明 |", "|---|---|---|---|"]
    for name, info in library["atomic_actions"].items():
        params = ", ".join(info.get("params", {})) or "-"
        lines.append(f"| `{name}` | `{info.get('endpoint','')}` | {params} | {info.get('desc','')} |")
    lines += ["", "## 二、组合动作（每个官方模板一个，L1 只需调用一次）", "",
              "| 模板 | 名称 | 类别 | 组合工具 | 参数 | 内部原子序列 |",
              "|---|---|---|---|---|---|"]
    for e in library["composite_actions"]:
        params = ", ".join(f"{k}" for k in (e["params"] or {}))
        seq = " → ".join(e["atomic_sequence"])
        lines.append(f"| `{e['template_id']}` | {e['name']} | {e['category']} | "
                     f"`{e['composite_tool']}` | {params} | {seq} |")
    lines += ["", "## 三、调用方式", "",
              "L1 模型只需一次工具调用：", "",
              "```json",
              '{"template": "corridor_detour",',
              ' "params": {"drone_id": "Bravo", "start": [131, 156.3], "end": [881, 624.2],',
              '            "altitude": 28.2, "clearance": 59, "photos": 2, "end_hover": true}}',
              "```", "",
              "工具内部会：起飞 → 到起点拍照 → 绕障碍航段 → 到终点拍照 → 悬停。", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates-dir", default=str(DEFAULT_TEMPLATES))
    ap.add_argument("--out-docs", default=str(ROOT / "docs" / "ACTION_LIBRARY.md"))
    ap.add_argument("--out-config", default=str(ROOT / "configs" / "action_library.json"))
    args = ap.parse_args()

    templates_dir = Path(args.templates_dir)
    official = load_official(templates_dir)
    library = build_library(official)
    library["templates_source"] = str(templates_dir)

    docs_path = Path(args.out_docs)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_md(library), encoding="utf-8")

    config_path = Path(args.out_config)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"官方模板: {len(official)} 个 | 原子动作: {len(library['atomic_actions'])} 个 | "
          f"组合动作: {len(library['composite_actions'])} 个")
    print("已生成:", docs_path)
    print("已生成:", config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
