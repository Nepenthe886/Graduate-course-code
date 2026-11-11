"""Cargo 命名编队的靠近（approach）与位置配对测试。"""
from __future__ import annotations

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

TEXT = ("Drones Cargo 5 and Cargo 4 form a line formation: both take off to 17 meters. "
        "Cargo 5 positions at (551, 639, 17) and Cargo 4 flys to (514, 602, 17) first, "
        "and then move to 21 meters at most to Cargo 5. Both maintain formation for "
        "monitoring.")

SHUFFLED = ("Drones Cargo 5 and Cargo 4 form a line formation: both take off to 17 meters. "
            "Cargo 4 flys to (514, 602, 17) first and Cargo 5 positions at (551, 639, 17). "
            "Both maintain formation for monitoring.")


class CargoFormationTest(unittest.TestCase):
    def test_approach_generated_for_cargo_names(self):
        spec = build_spec_from_task(TEXT, 17.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        approaches = [item for item in spec["actions"]
                      if item.get("name") == "approach_drone"]
        self.assertEqual(len(approaches), 1)
        params = approaches[0]["params"]
        self.assertEqual(params["drone_id"], "Cargo 4")
        self.assertEqual(params["target_drone_id"], "Cargo 5")
        self.assertEqual(params["max_distance"], 21.0)
        self.assertEqual(params["z"], 17.0)

    def test_positions_paired_by_name(self):
        spec = build_spec_from_task(SHUFFLED, 17.0, False, world=WORLD)
        moves = {item["params"]["drone_id"]: item["params"]
                 for item in spec["actions"] if item.get("name") == "move_to"}
        self.assertAlmostEqual(moves["Cargo 4"]["x"], 514.0)
        self.assertAlmostEqual(moves["Cargo 4"]["y"], 602.0)
        self.assertAlmostEqual(moves["Cargo 5"]["x"], 551.0)
        self.assertAlmostEqual(moves["Cargo 5"]["y"], 639.0)


if __name__ == "__main__":
    unittest.main()
