"""多机“同一目标/支援”任务解析测试（含无规律目标名）。"""
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

TARGET = "Shelter access assessment 02"

TEXT = ("Drone Relief UAV 10 should first find and reach target "
        "Shelter access assessment 02. After that, drone Relief UAV 04 should fly to "
        "the same target as support. Both drones should operate at 18 meters.")


class SupportSameTargetTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 18.0, False, world=WORLD)

    def test_both_drones_go_to_same_target(self):
        self.assertIsNone(self.spec.get("error"))
        actions = self.spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs},
                         {"Relief UAV 10", "Relief UAV 04"})
        self.assertTrue(all(item["params"]["altitude"] == 18.0 for item in takeoffs))
        self.assertEqual(len(navs), 2)
        center = WORLD.target_by_name(TARGET).center
        for nav in navs:
            self.assertIn(nav["params"]["drone_id"], {"Relief UAV 10", "Relief UAV 04"})
            self.assertAlmostEqual(nav["params"]["x"], float(center[0]), places=1)
            self.assertAlmostEqual(nav["params"]["y"], float(center[1]), places=1)


if __name__ == "__main__":
    unittest.main()
