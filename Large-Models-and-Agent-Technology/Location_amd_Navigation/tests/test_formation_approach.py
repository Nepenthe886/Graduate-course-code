"""编队靠拢任务测试：新增 approach_drone 动作。"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt_defs import build_mission  # noqa: E402
from src.bt_defs import Status  # noqa: E402
from src.config import load_config  # noqa: E402
from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))

TEXT = ("Drones Drone 2 and Drone 1 form a line formation: both take off to 17 meters. "
        "Drone 2 positions at (454, 490, 17) and Drone 1 flys to (483, 549, 17) first, "
        "and then move to 15 meters at most to Drone 2. Both maintain formation for monitoring.")


class _FakeApi:
    def __init__(self):
        self.pos = {"f140e8c7": {"x": 100.0, "y": 100.0, "z": 0.0},
                    "712aca20": {"x": 900.0, "y": 900.0, "z": 0.0}}

    def drone(self, drone_id):
        return {"position": self.pos[drone_id], "heading": 0.0}

    def command(self, drone_id, command, params=None, body=None):
        if command == "take_off":
            self.pos[drone_id]["z"] = params.get("altitude", 0)
        if command == "move_to":
            self.pos[drone_id].update(x=params.get("x", self.pos[drone_id]["x"]),
                                      y=params.get("y", self.pos[drone_id]["y"]))
        if command == "move_along_path":
            points = (body or {}).get("waypoints") or []
            if points:
                self.pos[drone_id].update(x=points[-1]["x"], y=points[-1]["y"])
        return 200, {"status": "success"}

    def wait_arrival(self, *args, **kwargs):
        return True


class FormationApproachTest(unittest.TestCase):
    def test_spec_has_approach_action(self):
        spec = build_spec_from_task(TEXT, 17.0, False, world=WORLD)
        names = [item.get("name") for item in spec["actions"]]
        self.assertIn("approach_drone", names)
        approach = next(item for item in spec["actions"]
                        if item.get("name") == "approach_drone")["params"]
        self.assertEqual(approach["drone_id"], "Drone 1")
        self.assertEqual(approach["target_drone_id"], "Drone 2")
        self.assertEqual(approach["max_distance"], 15.0)

    def test_execution_enters_distance(self):
        spec = build_spec_from_task(TEXT, 17.0, False, world=WORLD)
        tree, steps = build_mission(WORLD, spec, 17.0)
        self.assertIn("approach_drone", [step["kind"] for step in steps])
        api = _FakeApi()
        board = {"api": api, "drone_id": "712aca20", "world": WORLD, "results": []}
        status = tree.tick(board)
        self.assertEqual(status, Status.SUCCESS)
        drone2, drone1 = api.pos["f140e8c7"], api.pos["712aca20"]
        distance = math.hypot(drone1["x"] - drone2["x"], drone1["y"] - drone2["y"])
        self.assertLessEqual(distance, 15.0)
        self.assertAlmostEqual(drone2["x"], 454.0, places=1)
        self.assertAlmostEqual(drone2["y"], 490.0, places=1)


if __name__ == "__main__":
    unittest.main()
