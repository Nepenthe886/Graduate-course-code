"""动作库（L3 薄层）与行为树模板（L2）测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.actions import ACTION_LIBRARY, ATOMIC_ACTIONS
from src.bt import Sequence, Status
from src.bt_defs import MoveDirection, build_mission
from src.worldmap import Target, WorldMap

EXPECTED_ATOMIC = {
    "take_off", "land", "change_altitude", "rotate", "move_to", "move_towards",
    "move_along_path", "hover", "take_photo", "broadcast", "send_message",
    "charge", "return_home",
}


class FakeApi:
    """模拟官方接口：move_to 用 query 参数，并更新自身位置。"""

    def __init__(self, position=(100.0, 100.0, 0.0)):
        self.position = {"x": position[0], "y": position[1], "z": position[2]}
        self.calls = []

    def command(self, drone_id, command, *, params=None, body=None):
        self.calls.append((drone_id, command, params, body))
        if command == "move_to" and params:
            self.position = {"x": float(params.get("x", self.position["x"])),
                             "y": float(params.get("y", self.position["y"])),
                             "z": float(params.get("z", self.position["z"]))}
        return 200, {"status": "success", "command": command}

    def drone(self, drone_id):
        return {"position": dict(self.position), "status": "hovering", "heading": 0.0}


def build_world():
    return WorldMap(
        source=Path("dummy_test_session.json"),
        id="test", name="test", width=1024.0, height=768.0,
        drones=[{"id": "d1", "name": "Drone 1",
                 "position": {"x": 100.0, "y": 100.0, "z": 0.0}}],
        targets=[
            Target(id="t1", name="Polygon Target 2", center=(500.0, 400.0), radius=1.0,
                   type="polygon",
                   vertices=[{"x": 400.0, "y": 300.0}, {"x": 600.0, "y": 300.0},
                             {"x": 600.0, "y": 500.0}, {"x": 400.0, "y": 500.0}]),
            Target(id="t2", name="Circle Target 1", center=(200.0, 200.0), radius=80.0),
        ],
    )


class ActionLibraryTest(unittest.TestCase):
    def test_atomic_actions_match_official_commands(self):
        self.assertEqual(set(ATOMIC_ACTIONS), EXPECTED_ATOMIC)
        for name in EXPECTED_ATOMIC:
            self.assertIn("endpoint", ACTION_LIBRARY[name])

    def test_no_business_logic_in_action_library(self):
        for forbidden in ("coverage_search", "formation", "move_direction", "visit_targets"):
            self.assertNotIn(forbidden, ACTION_LIBRARY)


class MissionTemplateTest(unittest.TestCase):
    def mission(self, actions, altitude=21.0):
        spec = {"altitude": altitude, "actions": actions, "ensure_takeoff": False}
        return build_mission(build_world(), spec)

    def test_coverage_search_template(self):
        tree, steps = self.mission([
            {"name": "coverage_search",
             "params": {"target": "Polygon Target 2", "spacing": 36.0, "photos": 3}}])
        kinds = [s["kind"] for s in steps]
        self.assertEqual(kinds[0], "navigate_to_pose")
        self.assertGreater(kinds.count("navigate_to_pose"), 5)
        self.assertEqual(kinds.count("take_photo"), 3)

    def test_visit_targets_template(self):
        _, steps = self.mission([{"name": "visit_targets",
                                  "params": {"targets": ["Polygon Target 2", "Circle Target 1"]}}])
        self.assertEqual([s["kind"] for s in steps], ["move_to", "move_to"])
        self.assertAlmostEqual(steps[0]["params"]["x"], 500.0)

    def test_patrol_template_closes_loop(self):
        _, steps = self.mission([{"name": "patrol",
                                  "params": {"points": [{"x": 1, "y": 1}, {"x": 2, "y": 2},
                                                        {"x": 3, "y": 1}], "closed": True}}])
        waypoints = steps[0]["params"]["waypoints"]
        self.assertEqual(len(waypoints), 4)
        self.assertEqual(waypoints[0], waypoints[-1])

    def test_formation_hold_adds_hover_per_drone(self):
        _, steps = self.mission([{"name": "formation_hold",
                                  "params": {"kind": "line", "center": [500.0, 400.0],
                                             "drones": ["d1", "d2"]}}])
        self.assertEqual([s["kind"] for s in steps], ["move_to", "move_to", "hover", "hover"])

    def test_ensure_takeoff_prepended(self):
        spec = {"altitude": 17.0, "actions": ["hover"], "ensure_takeoff": True}
        _, steps = build_mission(build_world(), spec)
        self.assertEqual(steps[0]["kind"], "take_off")


class MoveDirectionNodeTest(unittest.TestCase):
    def test_north_then_east(self):
        api = FakeApi()
        board = {"api": api, "drone_id": "d1", "results": []}
        status = MoveDirection(0.0, 74.0, z=17.0).tick(board)
        self.assertEqual(status, Status.SUCCESS)
        self.assertAlmostEqual(api.position["y"], 174.0, places=3)
        status = MoveDirection(90.0, 62.0, z=17.0).tick(board)
        self.assertEqual(status, Status.SUCCESS)
        self.assertAlmostEqual(api.position["x"], 162.0, places=3)

    def test_sequence_uses_per_drone_binding(self):
        api = FakeApi()
        tree, _ = build_mission(build_world(), {
            "altitude": 17.0, "ensure_takeoff": False,
            "actions": [{"name": "move_direction",
                         "params": {"heading": 0.0, "distance": 30.0, "drone_id": "d1"}}]})
        self.assertIsInstance(tree, Sequence)
        status = tree.tick({"api": api, "drone_id": "other", "results": []})
        self.assertEqual(status, Status.SUCCESS)
        self.assertAlmostEqual(api.position["y"], 130.0, places=3)


class FormationTaskSpecTest(unittest.TestCase):
    TASK = ("Three-drone coordination exercise: Drone 2, Drone 1, and Drone 3 take off together to 17 meters "
            "altitude, then hold formation at positions (649, 427, 17), (707, 579, 17), and (585, 846, 17) "
            "respectively for synchronized operation. Then all three drones move north for 74m, and next move "
            "east for 62m.")

    def test_spec_expands_to_formations_and_directions(self):
        from src.mission_run import build_spec_from_task

        spec = build_spec_from_task(self.TASK, 17.0)
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names.count("take_off"), 3)
        self.assertEqual(names.count("move_to"), 3)
        self.assertEqual(names.count("move_direction"), 6)
        self.assertEqual(names.count("hover"), 3)
        self.assertFalse(spec["land_at_end"])
        directions = [(a["params"]["heading"], a["params"]["distance"])
                      for a in spec["actions"] if a["name"] == "move_direction"]
        self.assertEqual(directions[:3], [(0.0, 74.0)] * 3)
        self.assertEqual(directions[3:], [(90.0, 62.0)] * 3)


class TemplateParsingTest(unittest.TestCase):
    def parse(self, text, altitude=18.0):
        from src.mission_run import build_spec_from_task
        return build_spec_from_task(text, altitude, False, world=build_world())

    def test_sequential_multitarget_expands_to_bt(self):
        spec = self.parse("Drone Drone 2 performs sequential multi-target mission: take off to 18 meters, "
                          "visit targets Circle Target 1 and Polygon Target 2 in sequence. "
                          "At each target, get within the target radius, hover for observation, and take a photo.")
        tree, steps = build_mission(build_world(), spec, 18.0)
        kinds = [s["kind"] for s in steps]
        self.assertNotIn("fail", kinds)
        self.assertEqual(kinds.count("navigate_to_pose"), 2)
        self.assertEqual(kinds.count("hover"), 2)
        self.assertEqual(kinds.count("take_photo"), 2)

    def test_target_inspection_single(self):
        spec = self.parse("Drone 4 must take off to 18 meters, navigate to target "
                          "Polygon Target 2, and take a photo.")
        self.assertEqual([a["name"] for a in spec["actions"]], ["inspect_target"])
        self.assertEqual(spec["actions"][0]["params"]["target"], "Polygon Target 2")

    def test_photo_documentation_per_point(self):
        spec = self.parse("Drone 1 performs photo documentation mission: take off to 18 meters, "
                          "visit three waypoints at (182, 388, 18), (400, 200, 18), (600, 500, 18).")
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names.count("navigate_to_pose"), 3)
        self.assertEqual(names.count("take_photo"), 3)
        self.assertEqual(spec["photo_count"], 3)

    def test_communication_relay_broadcasts(self):
        spec = self.parse('Communication relay mission: Drone 1 take off to 23 meters, '
                          'position at relay point (500, 300, 23), message: "relay online".')
        names = [a["name"] for a in spec["actions"]]
        self.assertIn("broadcast", names)
        broadcast = next(a for a in spec["actions"] if a["name"] == "broadcast")
        self.assertIn("relay online", broadcast["params"]["content"])

    def test_hover_observation_duration(self):
        spec = self.parse("Drone 1 stationary observation mission: take off to 20 meters, "
                          "fly to observation point (552, 535, 20) and hover for 30 seconds.")
        hover = next(a for a in spec["actions"] if a["name"] == "hover")
        self.assertEqual(hover["params"]["duration"], 30.0)

    def test_rotation_test(self):
        spec = self.parse("Drone 2 takes off to 12 meters altitude, then rotates to face "
                          "heading 350 degrees and hovers.")
        self.assertEqual(spec["actions"][0]["name"], "rotate")
        self.assertEqual(spec["actions"][0]["params"]["heading"], 350.0)


class FleetTemplateTest(unittest.TestCase):
    def parse(self, text, altitude=17.0):
        from src.mission_run import build_spec_from_task
        return build_spec_from_task(text, altitude, False, world=build_world())

    def test_fleet_target_sweep(self):
        spec = self.parse(
            "Three-drone target sweep: Drone 2, Drone 1 take off together to 17 meters altitude. "
            "Drone 2 flies to target Polygon Target 2, Drone 1 flies to target Circle Target 1. "
            "After reaching the targets, all drones move west for 67m, then south for 54m.")
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names.count("take_off"), 2)
        self.assertEqual(names.count("navigate_to_pose"), 2)
        self.assertEqual(names.count("move_direction"), 4)
        self.assertFalse(spec["land_at_end"])

    def test_each_drone_photographs_own_targets(self):
        spec = self.parse(
            "Drones Drone 2, Drone 1, and Drone 3 must take off to 23 meters. "
            "Drone 1 photographs targets Polygon Target 2 and Polygon Target 2. "
            "Drone 3 photographs targets Circle Target 1 and Circle Target 1. "
            "Drone 2 photographs targets Circle Target 1 and Circle Target 1.")
        actions = spec["actions"]
        per_drone = {}
        for action in actions:
            if action["name"] != "inspect_target":
                continue
            drone = action["params"].get("drone_id")
            per_drone[drone] = per_drone.get(drone, 0) + 1
        self.assertEqual(per_drone.get("Drone 1"), 2)
        self.assertEqual(per_drone.get("Drone 3"), 2)
        self.assertEqual(per_drone.get("Drone 2"), 2)
        self.assertEqual(sum(1 for a in actions if a["name"] == "take_off"), 3)

    def test_perimeter_guard_points(self):
        spec = self.parse("Deploy drones Drone 1 and Drone 2 for perimeter search. All take off to 22 meters. "
                          "Position Drone 1 at guard point (500,400,22), Drone 2 at guard point (600,300,22).")
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names.count("take_off"), 2)
        self.assertEqual(names.count("navigate_to_pose"), 2)
        self.assertEqual(names.count("take_photo"), 2)

    def test_target_assignment_visits_all_targets(self):
        spec = self.parse("Take off necessary drones and assign them to reach required targets "
                          "Polygon Target 2 and Circle Target 1; continue until progress exceeds 50%.")
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names[0], "take_off")
        self.assertEqual(names.count("navigate_to_pose"), 2)

    def test_coordinated_sweep_splits_across_drones(self):
        spec = self.parse("Drones Drone 1 and Drone 2 execute coordinated search sweep of target area "
                          "Polygon Target 2. Both take off to 20 meters and take photos.")
        names = [a["name"] for a in spec["actions"]]
        self.assertEqual(names.count("take_off"), 2)
        self.assertEqual(names.count("coordinated_sweep"), 1)
        _, steps = build_mission(build_world(), spec, 20.0)
        kinds = [s["kind"] for s in steps]
        self.assertEqual(kinds.count("move_along_path"), 2)
        self.assertEqual(kinds.count("take_photo"), 2)


if __name__ == "__main__":
    unittest.main()
