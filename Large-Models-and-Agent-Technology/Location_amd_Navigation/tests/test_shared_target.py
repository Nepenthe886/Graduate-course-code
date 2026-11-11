"""多机共同前往同一目标的解析测试。"""
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

TEXT = ("Send drones Drone 1 and Drone 2 to target Fixed Target 1. Both drones should "
        "take off to 20 meters and reach the same target.")


class SharedTargetTest(unittest.TestCase):
    def test_both_drones_go_to_same_target(self):
        spec = build_spec_from_task(TEXT, 32.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        actions = spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs},
                         {"Drone 1", "Drone 2"})
        self.assertTrue(all(item["params"]["altitude"] == 20.0 for item in takeoffs))
        self.assertEqual(len(navs), 2)
        target = WORLD.target_by_name("Fixed Target 1")
        for nav in navs:
            self.assertIn(nav["params"]["drone_id"], {"Drone 1", "Drone 2"})
            self.assertAlmostEqual(nav["params"]["x"], float(target.center[0]), places=1)
            self.assertAlmostEqual(nav["params"]["y"], float(target.center[1]), places=1)

    def test_one_to_one_not_treated_as_shared(self):
        text = ("Drone 1 should reach target Fixed Target 1, and Drone 2 should reach "
                "target Fixed Target 2.")
        spec = build_spec_from_task(text, 20.0, False, world=WORLD)
        navs = [item for item in spec["actions"] if item.get("name") == "navigate_to_pose"]
        xs = {(item["params"]["drone_id"]): item["params"]["x"] for item in navs}
        self.assertEqual(len(navs), 2)
        self.assertNotEqual(xs["Drone 1"], xs["Drone 2"])


if __name__ == "__main__":
    unittest.main()
