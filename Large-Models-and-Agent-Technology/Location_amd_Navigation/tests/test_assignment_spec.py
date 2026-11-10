"""通用任务分配解析：多机↔目标、交换任务、句尾高度。"""
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

TEXT = ("Drone 1 should find target Fixed Target 1, and Drone 2 should find target "
        "Fixed Target 2. After the first discovery, they swap: Drone 1 goes to "
        "Fixed Target 2 and Drone 2 goes to Fixed Target 1 at 16 meters.")


class AssignmentSpecTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 32.0, False, world=WORLD)

    def test_two_drones_take_off_at_16m(self):
        takeoffs = [a for a in self.spec["actions"] if a.get("name") == "take_off"]
        self.assertEqual({a["params"]["drone_id"] for a in takeoffs}, {"Drone 1", "Drone 2"})
        self.assertTrue(all(a["params"]["altitude"] == 16.0 for a in takeoffs))

    def test_each_drone_visits_both_targets_in_swap_order(self):
        navs = [a for a in self.spec["actions"] if a.get("name") == "navigate_to_pose"]
        self.assertEqual(len(navs), 4)
        t1 = WORLD.target_by_name("Fixed Target 1")
        t2 = WORLD.target_by_name("Fixed Target 2")
        by_drone: dict[str, list[tuple[float, float]]] = {}
        for nav in navs:
            drone = nav["params"]["drone_id"]
            by_drone.setdefault(drone, []).append((nav["params"]["x"], nav["params"]["y"]))
        self.assertEqual(len(by_drone["Drone 1"]), 2)
        self.assertEqual(len(by_drone["Drone 2"]), 2)
        self.assertAlmostEqual(by_drone["Drone 1"][0][0], float(t1.center[0]), places=1)
        self.assertAlmostEqual(by_drone["Drone 1"][1][0], float(t2.center[0]), places=1)
        self.assertAlmostEqual(by_drone["Drone 2"][0][0], float(t2.center[0]), places=1)
        self.assertAlmostEqual(by_drone["Drone 2"][1][0], float(t1.center[0]), places=1)


if __name__ == "__main__":
    unittest.main()
