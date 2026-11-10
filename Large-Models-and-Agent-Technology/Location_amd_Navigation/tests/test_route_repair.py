"""绕角点修复测试：规划器最后一段切到障碍角时，插入 via 点后应通过自检。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import _repair_route, _route_blocker  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.planner import Planner  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))


class RouteRepairTest(unittest.TestCase):
    def test_planner_output_is_safe(self):
        start = (138.0, 215.0)
        goal = (868.0, 275.0)
        route = Planner(WORLD, clearance=1.5).plan(start, goal, altitude=20.0)
        raw = [(float(p[0]), float(p[1])) for p in (route.waypoints_xyz or [])[1:]]
        self.assertIsNone(_route_blocker(WORLD, start, raw))

    def test_repair_corner_clipping_route(self):
        start = (852.0, 264.0)
        raw = [(868.0, 275.0)]
        self.assertIsNotNone(_route_blocker(WORLD, start, raw))
        repaired = _repair_route(WORLD, start, raw)
        self.assertIsNotNone(repaired)
        points = [(p["x"], p["y"]) for p in repaired]
        self.assertIsNone(_route_blocker(WORLD, start, points))

    def test_clear_route_untouched(self):
        start = (140.0, 215.0)
        points = [(150.0, 230.0), (160.0, 240.0)]
        self.assertIsNone(_route_blocker(WORLD, start, points))
        repaired = _repair_route(WORLD, start, points)
        self.assertEqual(len(repaired), len(points))


if __name__ == "__main__":
    unittest.main()
