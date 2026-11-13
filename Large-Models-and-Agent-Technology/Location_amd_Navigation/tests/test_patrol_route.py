"""Per-drone patrol route parsing (each drone may have several waypoints)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

URBAN = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
         / "5. urban_parcel_delivery.json")
WORLD = WorldMap.load(URBAN)

PATROL_TEXT = (
    "Drones Cargo 9 and Cargo 14 execute coordinated patrol. Both take off to 18 meters. "
    "Cargo 9 patrols the route through waypoints (297,350,18), (662,503,18) while Cargo 14 "
    "patrols the route through waypoints (263,198,18), (833,269,18)."
)
SINGLE_TEXT = (
    "Drones Cargo 9 and Cargo 14 take off to 18 meters. "
    "Cargo 9 positions at (297,350,18), Cargo 14 at (263,198,18)."
)


class PatrolRouteTest(unittest.TestCase):
    def test_each_drone_keeps_all_patrol_waypoints(self):
        spec = build_spec_from_task(PATROL_TEXT, 18.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        moves = [item["params"] for item in spec["actions"]
                 if item.get("name") == "move_to"]
        self.assertEqual(len(moves), 4)
        self.assertEqual([(m["x"], m["y"]) for m in moves],
                         [(297.0, 350.0), (662.0, 503.0),
                          (263.0, 198.0), (833.0, 269.0)])
        self.assertEqual([m["drone_id"] for m in moves],
                         ["Cargo 9", "Cargo 9", "Cargo 14", "Cargo 14"])

    def test_single_position_task_stays_single(self):
        spec = build_spec_from_task(SINGLE_TEXT, 18.0, False, world=WORLD)
        moves = [item["params"] for item in spec["actions"]
                 if item.get("name") == "move_to"]
        self.assertEqual(len(moves), 2)
        self.assertEqual(spec.get("named_positions") is not None, True)


if __name__ == "__main__":
    unittest.main()
