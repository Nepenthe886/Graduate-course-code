"""任务失败时的降落兜底测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.mission_run as mission_run  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paths import resolve  # noqa: E402

CONFIG = load_config()
MAP_PATH = str(resolve(CONFIG.default_map))


class _FakeApi:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def drone(self, drone_id):
        return {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "heading": 0.0}

    def command(self, drone_id, command, params=None, body=None):
        self.calls.append((drone_id, command))
        if command in ("move_to", "move_along_path"):
            return 200, {"status": "error", "message": "blocked"}
        return 200, {"status": "success"}

    def wait_arrival(self, *args, **kwargs):
        return False


class FailureCleanupTest(unittest.TestCase):
    def _run(self, spec):
        original = mission_run.UavApi
        mission_run.UavApi = _FakeApi
        try:
            return mission_run.execute_actions(MAP_PATH, "d1", spec)
        finally:
            mission_run.UavApi = original

    def test_lands_on_failure(self):
        spec = {"actions": [{"kind": "move_to",
                             "params": {"drone_id": "d1", "x": 100.0, "y": 100.0, "z": 20.0}}],
                "altitude": 20.0}
        result = self._run(spec)
        self.assertFalse(result["ok"])
        cleanups = [item for item in result["executed"] if item.get("cleanup")]
        self.assertTrue(cleanups)
        self.assertEqual(cleanups[0]["name"], "land")

    def test_no_cleanup_when_disabled(self):
        spec = {"actions": [{"kind": "move_to",
                             "params": {"drone_id": "d1", "x": 100.0, "y": 100.0, "z": 20.0}}],
                "altitude": 20.0, "land_on_failure": False}
        result = self._run(spec)
        self.assertFalse(result["ok"])
        self.assertFalse([item for item in result["executed"] if item.get("cleanup")])


if __name__ == "__main__":
    unittest.main()
