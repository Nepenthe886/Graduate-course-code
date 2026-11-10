"""障碍物名称被当作目标查询时，应返回“无需前往”的提示而不是错误。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402
from tools.nav_tools import NavToolkit  # noqa: E402

FIXTURE = resolve("tests/data/area_search_easy_3.json")


class ObstacleNameRouteTest(unittest.TestCase):
    def setUp(self):
        self.toolkit = NavToolkit()
        self.toolkit._last_refresh = time.time() + 1000  # 避免 refresh 覆盖世界
        self.toolkit.world = WorldMap.load(FIXTURE)
        self.name = self.toolkit.world.obstacles[0].name

    def test_obstacle_name_is_skipped(self):
        result = self.toolkit.nav_route_to_target({"target": self.name, "z": 20})
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("obstacle"), self.name)
        self.assertIn("障碍物", str(result.get("hint")))

    def test_unknown_name_still_errors(self):
        result = self.toolkit.nav_route_to_target({"target": "No Such Target", "z": 20})
        self.assertIn("地图里没有目标", str(result.get("error")))


if __name__ == "__main__":
    unittest.main()
