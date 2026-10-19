"""Structured JSON export for completed UAV agent tool chains."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

from serialization_utils import make_json_safe


def message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(part for part in parts if part).strip()
    if value is None:
        return ""
    return str(value)


def tool_chain_from_steps(steps: List[Any]) -> List[Dict[str, Any]]:
    tool_chain: List[Dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, (list, tuple)) and len(step) >= 2:
            action, observation = step[0], step[1]
        else:
            action, observation = step, ""

        if isinstance(action, dict):
            tool = action.get("tool")
            tool_input = action.get("tool_input")
            log = action.get("log")
        else:
            tool = getattr(action, "tool", None)
            tool_input = getattr(action, "tool_input", None)
            log = getattr(action, "log", None)

        tool_chain.append(
            {
                "index": index,
                "tool": str(tool) if tool is not None else "",
                "tool_input": make_json_safe(tool_input),
                "observation": make_json_safe(message_text(observation)),
                "log": message_text(log),
            }
        )
    return tool_chain


def _atomic_write_text(final_path: Path, payload: str) -> Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.parent / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, final_path)
    return final_path


def write_toolchain_json(record: Dict[str, Any], output_dir: Path | None = None) -> Path:
    if output_dir is None:
        from experiment_tracking import STORE
        run_id = (record.get('runtime') or {}).get('run_id') or record['command_id']
        output_dir = STORE.path(run_id)
    final_path = Path(output_dir) / 'toolchain.json'

    payload = json.dumps(make_json_safe(record), ensure_ascii=False, indent=2, sort_keys=True)
    return _atomic_write_text(final_path, payload + "\n")
