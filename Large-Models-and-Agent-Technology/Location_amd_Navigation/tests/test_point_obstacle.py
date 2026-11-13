"""point 类型障碍 footprint 测试（radius 必须生效，不能被固定 10m 低估）。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import _segment_blocker  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.planner import Planner  # noqa: E402
from src.worldmap import Obstacle, WorldMap  # noqa: E402

WORLD = WorldMap.load(resolve(load_config().default_map))


class PointObstacleFootprintTest(unittest.TestCase):
    def test_point_obstacle_uses_radius(self):
        point = next(o for o in WORLD.obstacles if o.name == "Point Obstacle 2")
        self.assertEqual(float(point.radius), 13.0)
        center = point.center
        distances = [math.hypot(x - center[0], y - center[1])
                     for x, y in point.footprint()]
        self.assertGreaterEqual(min(distances), 13.0 - 1e-6)
        self.assertEqual(WORLD.clearance_to_obstacles(center), 0.0)

    def test_path_through_point_obstacle_detected(self):
        blocker = _segment_blocker(WORLD, (684.0, 732.0), (372.0, 768.0), 0.2)
        self.assertEqual(blocker, "Point Obstacle 2")

    def test_planner_avoids_point_obstacle(self):
        planner = Planner(WORLD, clearance=0.3)
        route = planner.plan((915.0, 607.0), (155.0, 739.0))
        points = [(915.0, 607.0)] + [tuple(point) for point in route.waypoints]
        for start, goal in zip(points, points[1:]):
            self.assertIsNone(_segment_blocker(WORLD, start, goal, 0.2),
                              (start, goal))

    def test_unknown_type_prefers_radius(self):
        obstacle = Obstacle(id="x", name="x", type="mystery",
                            center=(0.0, 0.0), radius=7.0)
        distances = [math.hypot(x, y) for x, y in obstacle.footprint()]
        self.assertGreaterEqual(min(distances), 7.0 - 1e-6)


if __name__ == "__main__":
    unittest.main()
