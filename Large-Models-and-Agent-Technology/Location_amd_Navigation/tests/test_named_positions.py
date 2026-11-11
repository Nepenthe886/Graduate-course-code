"""多机各自到指定坐标点（guard point）解析测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import _named_position_spec, build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "5. urban_parcel_delivery.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Deploy drones Cargo 9, Cargo 3, Cargo 1, and Cargo 8 for perimeter search. "
        "All take off to 23 meters altitude. Position Cargo 9 at guard point (486,675,23), "
        "Cargo 3 at guard point (840,431,23), Cargo 1 at guard point (492,340,23), and "
        "Cargo 8 at west guard point (203,542,23). All drones hover, take photos at their "
        "positions, and maintain search.")

EXPECTED = {
    "Cargo 9": (486.0, 675.0),
    "Cargo 3": (840.0, 431.0),
    "Cargo 1": (492.0, 340.0),
    "Cargo 8": (203.0, 542.0),
}


class NamedPositionTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 23.0, False, world=WORLD)

    def test_each_drone_gets_own_point(self):
        self.assertIsNone(self.spec.get("error"))
        moves = {item["params"]["drone_id"]: item["params"]
                 for item in self.spec["actions"] if item.get("name") == "move_to"}
        self.assertEqual(set(moves), set(EXPECTED))
        for name, (x, y) in EXPECTED.items():
            self.assertAlmostEqual(moves[name]["x"], x)
            self.assertAlmostEqual(moves[name]["y"], y)

    def test_takeoff_hover_photo_per_drone(self):
        actions = self.spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        hovers = [item for item in actions if item.get("name") == "hover"]
        photos = [item for item in actions if item.get("name") == "take_photo"]
        self.assertEqual(len(takeoffs), 4)
        self.assertTrue(all(item["params"]["altitude"] == 23.0 for item in takeoffs))
        self.assertEqual({item["params"]["drone_id"] for item in hovers}, set(EXPECTED))
        self.assertEqual({item["params"]["drone_id"] for item in photos}, set(EXPECTED))

    def test_single_drone_single_point_not_stolen(self):
        text = "Drone Cargo 13 take off to 20 meters, fly to (500, 400, 20) and take a photo."
        self.assertIsNone(_named_position_spec(text, WORLD, 20.0, False))


if __name__ == "__main__":
    unittest.main()
