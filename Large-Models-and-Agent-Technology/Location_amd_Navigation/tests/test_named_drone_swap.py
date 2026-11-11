"""无编号机名（Foxtrot/Echo）交换任务解析测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "3. canyon_switchback_coordination.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Drone Foxtrot should find target Far-Side Depot South, and drone Echo "
        "should find target Far-Side Depot North. After the first discovery, the drones "
        "swap assignments: Foxtrot goes to Far-Side Depot North and Echo goes to "
        "Far-Side Depot South at 22 meters.")

SOUTH = "Far-Side Depot South"
NORTH = "Far-Side Depot North"


class NamedDroneSwapTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 22.0, False, world=WORLD)

    def test_both_drones_take_off(self):
        self.assertIsNone(self.spec.get("error"))
        takeoffs = [item for item in self.spec["actions"]
                    if item.get("name") == "take_off"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs},
                         {"Foxtrot", "Echo"})
        self.assertTrue(all(item["params"]["altitude"] == 22.0 for item in takeoffs))

    def test_swap_visits_both_targets(self):
        navs = [item for item in self.spec["actions"]
                if item.get("name") == "navigate_to_pose"]
        self.assertEqual(len(navs), 4)
        per_drone: dict[str, list[tuple[float, float]]] = {}
        for nav in navs:
            per_drone.setdefault(nav["params"]["drone_id"], []).append(
                (nav["params"]["x"], nav["params"]["y"]))
        south = WORLD.target_by_name(SOUTH).center
        north = WORLD.target_by_name(NORTH).center
        for drone, first, second in (("Foxtrot", south, north), ("Echo", north, south)):
            self.assertEqual(len(per_drone[drone]), 2, drone)
            self.assertAlmostEqual(per_drone[drone][0][0], float(first[0]), places=1)
            self.assertAlmostEqual(per_drone[drone][0][1], float(first[1]), places=1)
            self.assertAlmostEqual(per_drone[drone][1][0], float(second[0]), places=1)
            self.assertAlmostEqual(per_drone[drone][1][1], float(second[1]), places=1)


if __name__ == "__main__":
    unittest.main()
