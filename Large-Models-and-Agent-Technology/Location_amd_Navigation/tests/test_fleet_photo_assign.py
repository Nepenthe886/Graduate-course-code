"""多机各自拍照目标（含无编号目标名）分配解析测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import _target_spec, build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "5. urban_parcel_delivery.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Drones Cargo 11, Cargo 6, and Cargo 3 must take off to 22 meters. "
        "Cargo 11 photographs targets Charging Hub East and Customer Drop 14. "
        "Cargo 6 photographs targets Customer Drop 03 and Customer Drop 08. "
        "Cargo 3 photographs targets Customer Drop 01 and Charging Hub South.")

ASSIGNMENTS = {
    "Cargo 11": ["Charging Hub East", "Customer Drop 14"],
    "Cargo 6": ["Customer Drop 03", "Customer Drop 08"],
    "Cargo 3": ["Customer Drop 01", "Charging Hub South"],
}


class MultiDronePhotoAssignTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 22.0, False, world=WORLD)

    def test_each_drone_gets_its_targets(self):
        self.assertIsNone(self.spec.get("error"))
        actions = self.spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        photos = [item for item in actions if item.get("name") == "take_photo"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs},
                         set(ASSIGNMENTS))
        self.assertEqual(len(navs), 6)
        self.assertEqual(len(photos), 6)
        per_drone: dict[str, list[tuple[float, float]]] = {}
        for nav in navs:
            per_drone.setdefault(nav["params"]["drone_id"], []).append(
                (nav["params"]["x"], nav["params"]["y"]))
        for drone, names in ASSIGNMENTS.items():
            expected = sorted((round(float(WORLD.target_by_name(name).center[0]), 2),
                               round(float(WORLD.target_by_name(name).center[1]), 2))
                              for name in names)
            self.assertEqual(sorted(per_drone[drone]), expected, drone)

    def test_single_target_parser_defers_for_multi_drone(self):
        self.assertIsNone(_target_spec(TEXT, WORLD, 22.0, False))


if __name__ == "__main__":
    unittest.main()
