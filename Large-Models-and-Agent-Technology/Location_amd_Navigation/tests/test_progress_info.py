"""会话进度统计能力提示测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.nav_tools as nav_tools  # noqa: E402
from tools.nav_tools import NavToolkit  # noqa: E402

PROGRESS_TEXT = ("reach required targets and continue until the session task progress "
                 "exceeds 50%.")


class ProgressInfoTest(unittest.TestCase):
    def setUp(self):
        self.toolkit = NavToolkit()
        self.original = nav_tools._load_json_url

    def tearDown(self):
        nav_tools._load_json_url = self.original

    def test_no_progress_tracking_reported(self):
        nav_tools._load_json_url = lambda *args, **kwargs: {
            "task_type": "others", "progress_percentage": 0,
            "status_message": "No progress tracking for this task type"}
        info = self.toolkit._progress_info(PROGRESS_TEXT)
        self.assertIsNotNone(info)
        self.assertFalse(info["supported"])
        self.assertIn("不支持进度统计", info["note"])

    def test_supported_progress(self):
        nav_tools._load_json_url = lambda *args, **kwargs: {
            "task_type": "target_assignment", "progress_percentage": 40,
            "status_message": ""}
        info = self.toolkit._progress_info(PROGRESS_TEXT)
        self.assertTrue(info["supported"])
        self.assertEqual(info["progress"], 40)

    def test_non_progress_task_skipped(self):
        self.assertIsNone(self.toolkit._progress_info("navigate to target Bay 036"))

    def test_mission_compact_attaches_progress(self):
        nav_tools._load_json_url = lambda *args, **kwargs: {
            "task_type": "others", "progress_percentage": 0,
            "status_message": "No progress tracking for this task type"}
        compact = self.toolkit._mission_compact({"ok": True}, PROGRESS_TEXT)
        self.assertIn("task_progress", compact)
        self.assertFalse(compact["task_progress"]["supported"])


if __name__ == "__main__":
    unittest.main()
