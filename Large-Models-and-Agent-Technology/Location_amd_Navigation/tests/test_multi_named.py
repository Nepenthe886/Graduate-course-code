"""单机多具名目标巡检解析测试（含路线优化）。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "5. urban_parcel_delivery.json")
WORLD = WorldMap.load(MAP)

TEXT = ("Drone Cargo 13 performs sequential multi-target mission: take off to 22 meters, "
        "visit targets Customer Drop 17, Customer Drop 04, and Customer Drop 15 in sequence. "
        "At each target, get within the target radius, hover for observation, and take a "
        "photo. Plan optimal route to minimize flight distance.")

SEQUENCE_TEXT = ("Drone Cargo 13 take off to 22 meters, visit targets Customer Drop 17, "
                 "Customer Drop 04, and Customer Drop 15 in sequence, take a photo at each.")


class MultiNamedTargetTest(unittest.TestCase):
    def test_actions_and_radius(self):
        spec = build_spec_from_task(TEXT, 32.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        actions = spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        navs = [item for item in actions if item.get("name") == "navigate_to_pose"]
        photos = [item for item in actions if item.get("name") == "take_photo"]
        hovers = [item for item in actions if item.get("name") == "hover"]
        self.assertEqual(len(takeoffs), 1)
        self.assertEqual(takeoffs[0]["params"]["altitude"], 22.0)
        self.assertEqual(len(navs), 3)
        self.assertEqual(len(photos), 3)
        self.assertEqual(len(hovers), 3)
        for nav in navs:
            self.assertEqual(nav["params"]["drone_id"], "Cargo 13")
            self.assertAlmostEqual(nav["params"]["tolerance"], 7.2, places=1)

    def test_route_is_optimized(self):
        spec = build_spec_from_task(TEXT, 22.0, False, world=WORLD)
        self.assertTrue(spec.get("optimized"))
        names = spec["targets"]
        self.assertEqual(set(names), {"Customer Drop 17", "Customer Drop 04",
                                      "Customer Drop 15"})
        points = [WORLD.target_by_name(name).center for name in names]
        drone = WORLD.drone_by_name("Cargo 13")
        start = (float(drone["position"]["x"]), float(drone["position"]["y"]))

        def length(order):
            total = 0.0
            previous = start
            for point in order:
                total += math.dist(previous, (point[0], point[1]))
                previous = (point[0], point[1])
            return total

        original = [WORLD.target_by_name(name).center
                    for name in ("Customer Drop 17", "Customer Drop 04", "Customer Drop 15")]
        self.assertLessEqual(length(points), length(original) + 1e-6)

    def test_sequence_kept_without_optimize_request(self):
        spec = build_spec_from_task(SEQUENCE_TEXT, 22.0, False, world=WORLD)
        self.assertFalse(spec.get("optimized"))
        self.assertEqual(spec["targets"],
                         ["Customer Drop 17", "Customer Drop 04", "Customer Drop 15"])


if __name__ == "__main__":
    unittest.main()
