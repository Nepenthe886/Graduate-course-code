"""NavigateToPose 底层回退（backoff）测试。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import NavigateToPose  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))


class _RecordingApi:
    def __init__(self, position=(140.0, 215.0, 22.0)):
        self.position = {"x": position[0], "y": position[1], "z": position[2]}
        self.calls = []

    def drone(self, drone_id):
        return {"position": dict(self.position), "heading": 0.0}

    def command(self, drone_id, command, params=None, body=None):
        self.calls.append((command, dict(params or {})))
        return 200, {"status": "success"}


class BackoffTest(unittest.TestCase):
    def test_backoff_moves_away_from_obstacle(self):
        api = _RecordingApi()
        node = NavigateToPose(500.0, 300.0, z=22.0, drone_id="d1")
        node._backoff(api, WORLD, "d1")
        self.assertTrue(api.calls)
        command, params = api.calls[0]
        self.assertEqual(command, "move_to")
        target = (params["x"], params["y"])
        distance = math.hypot(target[0] - 140.0, target[1] - 215.0)
        self.assertAlmostEqual(distance, 2.0, places=2)
        self.assertGreaterEqual(WORLD.clearance_to_obstacles(target), 0.8)


if __name__ == "__main__":
    unittest.main()
