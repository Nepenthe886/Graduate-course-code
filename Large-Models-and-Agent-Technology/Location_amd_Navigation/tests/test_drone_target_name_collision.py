"""机名与目标名重叠时的解析测试（Alpha Home Charger 不应识别出 Alpha 无人机）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import _named_drones_in_text, build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "3. canyon_switchback_coordination.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Drone Delta must take off to 16 meters, navigate to target Search Cell 1, "
        "take a photo, then navigate to target Alpha Home Charger and take another photo.")


class DroneTargetNameCollisionTest(unittest.TestCase):
    def test_target_name_not_treated_as_drone(self):
        self.assertEqual(_named_drones_in_text(WORLD, TEXT), ["Delta"])

    def test_single_drone_visits_both_targets_with_photos(self):
        spec = build_spec_from_task(TEXT, 16.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        actions = spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        photos = [item for item in actions if item.get("name") == "take_photo"]
        self.assertEqual(len(takeoffs), 1)
        self.assertEqual(takeoffs[0]["params"]["drone_id"], "Delta")
        self.assertEqual(takeoffs[0]["params"]["altitude"], 16.0)
        self.assertEqual(len(navs), 2)
        self.assertEqual({item["params"]["drone_id"] for item in navs}, {"Delta"})
        self.assertEqual(len(photos), 2)
        search = WORLD.target_by_name("Search Cell 1").center
        charger = WORLD.target_by_name("Alpha Home Charger").center
        got = {(round(item["params"]["x"], 1), round(item["params"]["y"], 1))
               for item in navs}
        self.assertIn((round(float(search[0]), 1), round(float(search[1]), 1)), got)
        self.assertIn((round(float(charger[0]), 1), round(float(charger[1]), 1)), got)


if __name__ == "__main__":
    unittest.main()
