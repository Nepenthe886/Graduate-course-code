"""定向移动目标点重定位测试：终点在障碍内时改到同方向可达点。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import (  # noqa: E402
    _corridor_clear,
    _detour_candidates,
    _direction_target,
    _forward_clear_length,
)
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

PORT = resolve("../MultiUAV-Plat-v0.49_Windows/UAVSessions/6. port_container_inventory.json")
WORLD = WorldMap.load(PORT)


class DirectionTargetTest(unittest.TestCase):
    def test_exact_point_when_free(self):
        start = {"x": 500.0, "y": 500.0}
        tx, ty = _direction_target(None, start, 0.0, 50.0)
        self.assertAlmostEqual(tx, 500.0)
        self.assertAlmostEqual(ty, 550.0)

    def test_inside_obstacle_relocated_to_free_point(self):
        start = {"x": 223.0, "y": 538.0}
        tx, ty = _direction_target(WORLD, start, 180.0, 102.0)
        self.assertGreater(WORLD.clearance_to_obstacles((tx, ty)), 0.0)
        progress = 538.0 - ty
        self.assertGreaterEqual(progress, 90.0)
        self.assertLess(abs(tx - 223.0), 10.0)

    def test_direction_projection_positive(self):
        start = {"x": 703.0, "y": 538.0}
        tx, ty = _direction_target(WORLD, start, 180.0, 102.0)
        rad = math.radians(180.0)
        progress = (tx - start["x"]) * math.sin(rad) + (ty - start["y"]) * math.cos(rad)
        self.assertGreater(progress, 0.0)


    def test_detour_candidates_are_free(self):
        pose = {"x": 223.0, "y": 538.0}
        candidates = _detour_candidates(WORLD, pose, 0.0, 15.0, 20.0)
        self.assertTrue(candidates)
        for x, y in candidates:
            self.assertGreater(WORLD.clearance_to_obstacles((x, y)), 0.0)

    def test_wide_obstacle_finds_corridor(self):
        flooded = WorldMap.load(ROOT.parent / "MultiUAV-Plat-v0.49_Windows"
                                / "UAVSessions" / "4. flooded_meander_settlement.json")
        pose = {"x": 648.0, "y": 374.0}
        candidates = _detour_candidates(flooded, pose, 0.0, 15.0, 20.0)
        corridors = [point for point in candidates
                     if _corridor_clear(flooded, point, 0.0, 15.0)]
        self.assertTrue(corridors)
        lengths = [_forward_clear_length(flooded, point, 0.0, 120.0)
                   for point in candidates[:3]]
        self.assertGreaterEqual(max(lengths), 60.0)


if __name__ == "__main__":
    unittest.main()
