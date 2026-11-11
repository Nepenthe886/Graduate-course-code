"""评测侧会话 task_type 修正脚本的检测逻辑测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.fix_session_task_types import _session_needs_progress  # noqa: E402


class SessionTaskTypeFixTest(unittest.TestCase):
    def test_progress_task_detected(self):
        session = {"tasks": [
            {"name": "Target Assignment 50% Progress With 3 Targets",
             "content": "reach required targets until progress exceeds 50%"},
        ]}
        self.assertTrue(_session_needs_progress(session))

    def test_normal_task_not_changed(self):
        session = {"tasks": [
            {"name": "Target Inspection 1",
             "content": "Drone Charlie must take off to 16 meters and take a photo."},
        ]}
        self.assertFalse(_session_needs_progress(session))

    def test_session_without_tasks(self):
        self.assertFalse(_session_needs_progress({"tasks": []}))


if __name__ == "__main__":
    unittest.main()
