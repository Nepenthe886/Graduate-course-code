#!/usr/bin/env python
"""行为树任务规划测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt import Action, Condition, Retry, Selector, Sequence, Status  # noqa: E402
from src.frames import Pose  # noqa: E402
from src.mission import plan_mission  # noqa: E402
from src.planner import Planner  # noqa: E402
from src.worldmap import Obstacle, Target, WorldMap  # noqa: E402


def build_world():
    return WorldMap(
        source=Path("virt_bt.json"), name="bt", width=1024.0, height=768.0,
        obstacles=[Obstacle(id="o1", name="Wall", type="circle",
                            center=(450.0, 300.0), height=0.0, radius=40.0)],
        targets=[
            Target(id="p1", name="Polygon Target 1", center=(200.0, 200.0), radius=1.0,
                   type="polygon",
                   vertices=[{"x": 100.0, "y": 100.0}, {"x": 300.0, "y": 100.0},
                             {"x": 300.0, "y": 300.0}, {"x": 100.0, "y": 300.0}]),
            Target(id="c1", name="Circle Target 1", center=(700.0, 500.0), radius=60.0),
        ], drones=[],
    )


class BehaviourTreeTest(unittest.TestCase):
    def test_sequence_stops_on_failure(self):
        order = []
        tree = Sequence(
            Action(lambda bb: order.append(1) or True),
            Action(lambda bb: order.append(2) or False),
            Action(lambda bb: order.append(3) or True),
        )
        self.assertEqual(tree.tick({}), Status.FAILURE)
        self.assertEqual(order, [1, 2])

    def test_selector_takes_first_success(self):
        tree = Selector(Action(lambda bb: False), Action(lambda bb: True))
        self.assertEqual(tree.tick({}), Status.SUCCESS)

    def test_retry_succeeds_eventually(self):
        counter = {"n": 0}

        def flaky(bb):
            counter["n"] += 1
            return counter["n"] >= 3

        self.assertEqual(Retry(Action(flaky), attempts=3).tick({}), Status.SUCCESS)
        self.assertEqual(counter["n"], 3)

    def test_condition(self):
        self.assertEqual(Condition(lambda bb: bb.get("ok")).tick({"ok": True}), Status.SUCCESS)
        self.assertEqual(Condition(lambda bb: bb.get("ok")).tick({}), Status.FAILURE)


class MissionPlanTest(unittest.TestCase):
    def setUp(self):
        self.world = build_world()
        self.start = Pose(10.0, 10.0, 0.0, 0.0)

    def test_steps_order(self):
        spec = {
            "altitude": 20,
            "waypoints": [
                {"x": 700.0, "y": 500.0, "label": "A", "tasks": [{"kind": "photo"}]},
                {"x": 200.0, "y": 600.0, "label": "B", "tasks": [{"kind": "hover", "duration": 4}]},
            ],
            "land_at_end": True,
        }
        plan = plan_mission(spec, self.world, self.start)
        self.assertEqual(plan["status"], "success")
        kinds = [step["kind"] for step in plan["steps"]]
        self.assertEqual(kinds[0], "take_off")
        self.assertEqual(kinds[-1], "land")
        legs = [step for step in plan["steps"] if step["kind"] == "move_along_path"]
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0]["goal"], [700.0, 500.0])
        self.assertEqual(legs[1]["goal"], [200.0, 600.0])
        self.assertIn("take_photo", kinds)
        self.assertIn("hover", kinds)

    def test_leg_detours_around_obstacle(self):
        # 起点(10,10) -> (700,500)，墙在 (450,300) r=40，直线会被挡
        spec = {"altitude": 20, "waypoints": [{"x": 700.0, "y": 500.0}], "land_at_end": False}
        plan = plan_mission(spec, self.world, self.start, Planner(self.world, clearance=20.0))
        leg = next(step for step in plan["steps"] if step["kind"] == "move_along_path")
        self.assertTrue(leg["detoured"], "应触发绕障")
        self.assertGreater(len(leg["waypoints"]), 1)

    def test_cover_task_expands(self):
        spec = {
            "altitude": 22,
            "waypoints": [{"x": 200.0, "y": 200.0,
                           "tasks": [{"kind": "cover", "target_name": "Polygon Target 1", "spacing": 20}]}],
            "land_at_end": False,
        }
        plan = plan_mission(spec, self.world, self.start)
        cover = next(step for step in plan["steps"] if step.get("cover"))
        self.assertEqual(cover["cover"], "Polygon Target 1")
        self.assertGreaterEqual(len(cover["waypoints"]), 10)
        self.assertGreater(cover["estimated_coverage"], 0.9)

    def test_failure_reported(self):
        spec = {"altitude": 20, "waypoints": [{"x": 10.0, "y": 10.0}]}
        plan = plan_mission(spec, self.world, self.start)
        self.assertIn(plan["status"], ("success", "failure"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
