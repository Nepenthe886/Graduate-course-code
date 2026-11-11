"""五机目标扫查 + 三段定向移动解析测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

PORT = resolve("../MultiUAV-Plat-v0.49_Windows/UAVSessions/6. port_container_inventory.json")
WORLD = WorldMap.load(PORT)

TEXT = (
    "Five-drone target sweep: Yard 15, Yard 29, Yard 11, Yard 21, and Yard 12 take off "
    "together to 23 meters altitude. Yard 15 flies to target Bay 069, Yard 29 flies to "
    "target Bay 077, Yard 11 flies to target Bay 031, Yard 21 flies to target Bay 082, and "
    "Yard 12 flies to target Bay 097. After reaching the targets, all five drones move west "
    "for 62m, then south for 102m, then north for 97m together. Each drone must complete the "
    "specified directional movements regardless of starting position."
)

DRONES = {"Yard 15", "Yard 29", "Yard 11", "Yard 21", "Yard 12"}


class SweepDirectionsTest(unittest.TestCase):
    def test_altitude_with_together(self):
        from src.mission_run import _parse_altitude
        self.assertEqual(_parse_altitude("take off together to 23 meters altitude", 32.0),
                         23.0)

    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 23.0, False, world=WORLD)

    def test_one_to_one_target_pairs(self):
        self.assertIsNone(self.spec.get("error"))
        actions = self.spec["actions"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        self.assertEqual(len(navs), 5)
        self.assertEqual({item["params"]["drone_id"] for item in navs}, DRONES)
        self.assertEqual(len({(item["params"]["x"], item["params"]["y"]) for item in navs}), 5)

    def test_directional_moves_appended_for_each_drone(self):
        actions = self.spec["actions"]
        by_drone: dict[str, list[tuple[float, float]]] = {}
        for item in actions:
            if item.get("name") != "move_direction":
                continue
            params = item["params"]
            by_drone.setdefault(params["drone_id"], []).append(
                (params["heading"], params["distance"]))
        self.assertEqual(set(by_drone), DRONES)
        for drone, direction in by_drone.items():
            self.assertEqual(direction, [(270.0, 62.0), (180.0, 102.0), (0.0, 97.0)], drone)


if __name__ == "__main__":
    unittest.main()
