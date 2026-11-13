"""Controlled nearest-free-point fallback for blocked NavigateToPose goals."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt import Status  # noqa: E402
from src.bt_defs import NavigateToPose  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

FLOODED = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
           / "4. flooded_meander_settlement.json")
WORLD = WorldMap.load(FLOODED)
IN_CORDON = (493.0, 419.0)
DEEP_IN_CORDON = (493.0, 450.0)


class FakeApi:
    def __init__(self, position=(598.0, 382.0, 21.0)):
        self.position = {"x": position[0], "y": position[1], "z": position[2]}

    def command(self, drone_id, command, *, params=None, body=None):
        if command == "move_to" and params:
            self.position = {"x": float(params.get("x", self.position["x"])),
                             "y": float(params.get("y", self.position["y"])),
                             "z": float(params.get("z", self.position["z"]))}
        return 200, {"status": "success", "command": command}

    def drone(self, drone_id):
        return {"position": dict(self.position), "status": "hovering", "heading": 0.0}


class BlockedTargetFallbackTest(unittest.TestCase):
    def tick(self, target, position=(598.0, 382.0, 21.0)):
        api = FakeApi(position)
        board = {"api": api, "drone_id": "370417fd", "world": WORLD,
                 "results": []}
        status = NavigateToPose(target[0], target[1], z=21.0, tolerance=2.0).tick(board)
        return status, api, board

    def test_slightly_blocked_target_uses_nearby_free_point(self):
        self.assertTrue(WORLD.obstacles)
        status, api, board = self.tick(IN_CORDON)
        self.assertEqual(status, Status.SUCCESS)
        distance = math.hypot(api.position["x"] - IN_CORDON[0],
                              api.position["y"] - IN_CORDON[1])
        self.assertLessEqual(distance, 3.0)
        result = board["results"][-1]
        self.assertIn("fallback_goal", result)
        self.assertLessEqual(result["fallback_distance"], 3.0)

    def test_deep_blocked_target_still_fails(self):
        status, _api, board = self.tick(DEEP_IN_CORDON)
        self.assertEqual(status, Status.FAILURE)
        self.assertIn("回退阈值", board["results"][-1]["error"])

    def test_far_side_start_fails_without_crossing(self):
        # UAV04 started east of the long cordon; the target-side fallback is
        # not straight-line reachable, so the node must fail instead of sending
        # a crossing move_to.
        status, api, board = self.tick(IN_CORDON, (943.29, 502.44, 21.0))
        self.assertEqual(status, Status.FAILURE)
        self.assertIn("另一侧", board["results"][-1]["error"])
        self.assertAlmostEqual(api.position["x"], 943.29)
        self.assertAlmostEqual(api.position["y"], 502.44)

    def test_reachable_target_has_no_fallback(self):
        status, api, board = self.tick((620.0, 620.0), (620.0, 600.0, 21.0))
        self.assertEqual(status, Status.SUCCESS)
        self.assertNotIn("fallback_goal", board["results"][-1])


if __name__ == "__main__":
    unittest.main()
