"""评测侧修正：给包含“session task progress”类任务的会话配置 target_assignment 类型。

背景：`/check/task_progress` 只有在会话 task_type 支持进度统计时才有效；
canyon / flooded 等会话若配置为 `others`，progress 恒为 0，任务中的
“progress > 50%”要求无法被任何飞行动作满足。

该脚本默认 dry-run；加 `--apply` 才会写文件（首次写前生成 .bak 备份）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402

PROGRESS_HINTS = (
    "session task progress",
    "task progress",
    "progress with 3 targets",
    "task completion",
)


def _session_needs_progress(session: dict) -> bool:
    for task in session.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        text = " ".join(str(task.get(key) or "")
                        for key in ("name", "content", "description")).lower()
        if any(hint in text for hint in PROGRESS_HINTS):
            return True
    return False


def _iter_session_dirs() -> list[Path]:
    dirs: list[Path] = []
    for raw in load_config().external_map_dirs:
        path = Path(raw)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修正会话 task_type（评测侧）")
    parser.add_argument("--session-dir", action="append", default=None,
                        help="额外的会话目录（可多次指定）")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = parser.parse_args(argv)
    direct = [Path(item) for item in (args.session_dir or [])]
    dirs = _iter_session_dirs() + [path for path in direct if path.is_dir()]
    changed: list[tuple[Path, str, str]] = []
    for directory in dirs:
        for path in sorted(directory.glob("*.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                session = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(session, dict) or not _session_needs_progress(session):
                continue
            old_type = str(session.get("task_type") or "")
            if old_type.lower() == "target_assignment":
                continue
            changed.append((path, old_type, str(session.get("name") or path.stem)))
            if args.apply:
                backup = path.with_suffix(path.suffix + ".bak")
                if not backup.exists():
                    backup.write_text(raw, encoding="utf-8")
                updated = re.sub(r'("task_type"\s*:\s*)"[^"]*"',
                                 r'\1"target_assignment"', raw, count=1)
                if updated == raw:
                    continue
                path.write_text(updated, encoding="utf-8")
    for path, old_type, name in changed:
        prefix = "UPDATED" if args.apply else "WOULD UPDATE"
        print(f"{prefix} {path.name} ({name}): task_type {old_type} -> target_assignment")
    if not changed:
        print("no session needs fixing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
