"""四机两目标（含第二阶段、无 target 关键词）分配解析测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "4. flooded_meander_settlement.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Assign drone Relief UAV 01 to target Civilian utility inspection 04 and drone "
        "Relief UAV 04 to target Civilian Damage Survey 2. After those targets are found, "
        "send drone Relief UAV 07 to Civilian utility inspection 04 and drone Relief UAV 02 "
        "to Civilian Damage Survey 2. All four drones should operate at 17 meters.")

UTILITY = "Civilian utility inspection 04"
DAMAGE = "Civilian Damage Survey 2"

EXPECTED = {
    "Relief UAV 01": UTILITY,
    "Relief UAV 04": DAMAGE,
    "Relief UAV 07": UTILITY,
    "Relief UAV 02": DAMAGE,
}


class QuadAssignmentTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 17.0, False, world=WORLD)

    def test_all_four_drones_take_off(self):
        self.assertIsNone(self.spec.get("error"))
        takeoffs = [item for item in self.spec["actions"]
                    if item.get("name") == "take_off"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs}, set(EXPECTED))
        self.assertTrue(all(item["params"]["altitude"] == 17.0 for item in takeoffs))

    def test_each_drone_gets_its_target_without_keyword(self):
        navs = [item for item in self.spec["actions"]
                if item.get("name") == "navigate_to_pose"]
        self.assertEqual(len(navs), 4)
        for nav in navs:
            drone = nav["params"]["drone_id"]
            target = WORLD.target_by_name(EXPECTED[drone])
            self.assertAlmostEqual(nav["params"]["x"], float(target.center[0]), places=1)
            self.assertAlmostEqual(nav["params"]["y"], float(target.center[1]), places=1)


if __name__ == "__main__":
    unittest.main()
