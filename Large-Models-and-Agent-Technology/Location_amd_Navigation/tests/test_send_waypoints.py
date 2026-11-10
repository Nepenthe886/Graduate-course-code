"""发送航点与规划起点一致性测试（画布外起点场景）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import _send_waypoints_from_route  # noqa: E402


class SendWaypointsTest(unittest.TestCase):
    def test_keeps_clamped_start_when_different(self):
        route = [[500.0, 768.0, 20.0], [420.0, 576.0, 20.0], [511.0, 558.0, 20.0]]
        points = _send_waypoints_from_route(route, (500.0, 1200.0), 20.0)
        self.assertEqual(points[0]["y"], 768.0)
        self.assertEqual(len(points), 3)

    def test_drops_start_when_same(self):
        route = [[500.0, 891.0, 20.0], [420.0, 576.0, 20.0]]
        points = _send_waypoints_from_route(route, (500.0, 891.0), 20.0)
        self.assertEqual(points[0]["y"], 576.0)
        self.assertEqual(len(points), 1)


if __name__ == "__main__":
    unittest.main()
