"""动作库与行为树叶子测试：确保 11 个原子动作 + 复合动作都被覆盖。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.actions import (ACTION_LIBRARY, ATOMIC_ACTIONS, COMPOSITE_ACTIONS,
                         build_action_steps, expand_composite)
from src.bt import Status, build_tree_from_steps
from src.worldmap import Target, WorldMap

EXPECTED_ATOMIC = {
    "take_off", "land", "change_altitude", "rotate", "move_to", "move_towards",
    "move_along_path", "hover", "take_photo", "broadcast", "return_home",
}


class FakeApi:
    def __init__(self):
        self.calls = []

    def command(self, drone_id, command, *, params=None, body=None):
        self.calls.append((drone_id, command, params, body))
        return 200, {"status": "success", "command": command}

    def drone(self, drone_id):
        return {"position": {"x": 0.0, "y": 0.0, "z": 0.0}}


def build_world():
    return WorldMap(
        source=Path("dummy_test_session.json"),
        id="test",
        name="test",
        width=1024.0,
        height=768.0,
        drones=[{"id": "d1", "name": "Drone 1",
                 "position": {"x": 100.0, "y": 100.0, "z": 0.0}}],
        targets=[
            Target(id="t1", name="Polygon Target 2", center=(500.0, 400.0), radius=1.0,
                   type="polygon",
                   vertices=[{"x": 400.0, "y": 300.0}, {"x": 600.0, "y": 300.0},
                             {"x": 600.0, "y": 500.0}, {"x": 400.0, "y": 500.0}]),
            Target(id="t2", name="Circle Target 1", center=(200.0, 200.0), radius=80.0),
        ],
        obstacles=[],
    )


class ActionLibraryTest(unittest.TestCase):
    def test_all_atomic_actions_from_templates_are_covered(self):
        self.assertEqual(set(ATOMIC_ACTIONS), EXPECTED_ATOMIC)
        for name in EXPECTED_ATOMIC:
            self.assertIn(name, ACTION_LIBRARY)
            self.assertIn("endpoint", ACTION_LIBRARY[name])

    def test_composite_actions_present(self):
        for name in ("coverage_search", "visit_targets", "patrol", "formation",
                     "track_target", "wait"):
            self.assertIn(name, ACTION_LIBRARY)

    def test_coverage_search_expands_to_path_plus_photos(self):
        world = build_world()
        steps = expand_composite(world, "coverage_search",
                                 {"target": "Polygon Target 2", "spacing": 36.0, "photos": 3},
                                 altitude=21.0)
        kinds = [s["kind"] for s in steps]
        self.assertEqual(kinds[0], "move_along_path")
        self.assertEqual(kinds.count("take_photo"), 3)
        waypoints = steps[0]["params"]["waypoints"]
        self.assertGreater(len(waypoints), 2)
        for waypoint in waypoints:
            self.assertAlmostEqual(waypoint["z"], 21.0)

    def test_visit_targets_expands_to_move_to_steps(self):
        steps = expand_composite(build_world(), "visit_targets",
                                 {"targets": ["Polygon Target 2", "Circle Target 1"]}, 20.0)
        self.assertEqual([s["kind"] for s in steps], ["move_to", "move_to"])
        self.assertAlmostEqual(steps[0]["params"]["x"], 500.0)

    def test_patrol_closes_loop(self):
        steps = expand_composite(build_world(), "patrol",
                                 {"points": [{"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0},
                                             {"x": 3.0, "y": 1.0}], "closed": True}, 15.0)
        waypoints = steps[0]["params"]["waypoints"]
        self.assertEqual(len(waypoints), 4)
        self.assertEqual(waypoints[0], waypoints[-1])

    def test_formation_line_offsets(self):
        steps = expand_composite(build_world(), "formation",
                                 {"kind": "line", "center": [500.0, 400.0], "altitude": 21.0,
                                  "drones": ["d1", "d2", "d3"]}, 21.0)
        self.assertEqual(len(steps), 3)
        self.assertAlmostEqual(steps[0]["params"]["x"], 460.0)
        self.assertAlmostEqual(steps[2]["params"]["x"], 540.0)
        self.assertEqual(steps[1]["params"]["drone_id"], "d2")

    def test_build_action_steps_handles_strings(self):
        steps = build_action_steps(build_world(), {"altitude": 20.0,
                                                   "actions": ["take_off", "land"]})
        self.assertEqual([s["kind"] for s in steps], ["take_off", "land"])


class BehaviorTreeLeafTest(unittest.TestCase):
    def test_sequence_executes_all_actions_with_fake_api(self):
        api = FakeApi()
        spec = {"altitude": 21.0, "actions": ["take_off", "take_photo", "land"]}
        steps = build_action_steps(build_world(), spec)
        tree = build_tree_from_steps(steps)
        blackboard = {"api": api, "drone_id": "d1", "results": []}
        status = tree.tick(blackboard)
        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual([c[1] for c in api.calls], ["take_off", "take_photo", "land"])

    def test_failure_steps_short_circuit(self):
        api = FakeApi()
        tree = build_tree_from_steps([{"kind": "take_off", "params": {}},
                                      {"kind": "fail", "params": {"error": "不可达"}},
                                      {"kind": "land", "params": {}}])
        status = tree.tick({"api": api, "drone_id": "d1", "results": []})
        self.assertEqual(status, Status.FAILURE)
        self.assertEqual([c[1] for c in api.calls], ["take_off"])

    def test_move_along_path_passes_waypoints(self):
        api = FakeApi()
        tree = build_tree_from_steps([
            {"kind": "move_along_path",
             "params": {"waypoints": [{"x": 1.0, "y": 2.0, "z": 3.0}]}}])
        status = tree.tick({"api": api, "drone_id": "d1", "results": []})
        self.assertEqual(status, Status.SUCCESS)
        body = api.calls[0][3]
        self.assertEqual(body["waypoints"][0]["x"], 1.0)


if __name__ == "__main__":
    unittest.main()
