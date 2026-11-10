"""双机任务解析测试：按机拆分航点、各自终点降落、不做跨机重排。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))

TEXT = (
    "Drones Drone 4 and Drone 7 perform a dual marathon flight at 29 meters. "
    "Drone 4 route: fly to and start at (94, 62, 29), then waypoints (139, 209, 29), "
    "(922, 238, 29), then end (891, 928, 29) and land. "
    "Drone 7 route: fly to and start at (926, 116, 29), then waypoints (916, 205, 29), "
    "(152, 258, 29), then end (111, 890, 29) and land."
)


class DualRouteTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 29.0, False, world=WORLD)

    def test_marked_multi_drone(self):
        self.assertTrue(self.spec.get("multi_drone"))
        self.assertTrue(self.spec.get("actions"))
        self.assertFalse(self.spec.get("waypoints"))

    def test_each_drone_ends_at_own_point(self):
        actions = self.spec["actions"]
        drone4 = [item for item in actions if item["params"].get("drone_id") == "Drone 4"]
        drone7 = [item for item in actions if item["params"].get("drone_id") == "Drone 7"]
        self.assertEqual(drone4[-1]["kind"], "land")
        self.assertEqual(drone7[-1]["kind"], "land")
        drone4_moves = [item for item in drone4 if item["kind"] == "move_to"]
        drone7_moves = [item for item in drone7 if item["kind"] == "move_to"]
        self.assertEqual((drone4_moves[-1]["params"]["x"], drone4_moves[-1]["params"]["y"]),
                         (891.0, 928.0))
        self.assertEqual((drone7_moves[-1]["params"]["x"], drone7_moves[-1]["params"]["y"]),
                         (111.0, 890.0))

    def test_no_cross_drone_merging(self):
        actions = self.spec["actions"]
        drones = [item["params"].get("drone_id") for item in actions]
        # 每架机是连续的一段，不会互相穿插
        compressed = [d for index, d in enumerate(drones) if index == 0 or d != drones[index - 1]]
        self.assertEqual(compressed, ["Drone 4", "Drone 7"])

    def test_single_drone_task_not_affected(self):
        spec = build_spec_from_task("Fly to (100, 100, 30) and take a photo.", 30.0, False,
                                    world=WORLD)
        self.assertFalse(spec.get("multi_drone"))


if __name__ == "__main__":
    unittest.main()
