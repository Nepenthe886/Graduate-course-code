"""v4 工具调用修正测试。"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.worldmap import Obstacle, Target, WorldMap  # noqa: E402


def load_proxy():
    spec = importlib.util.spec_from_file_location("llm_proxy_v4", ROOT / "llm_adapter_proxy.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


def build_world():
    return WorldMap(
        source=Path("virt.json"), name="virt", width=1024.0, height=768.0,
        obstacles=[], targets=[
            Target(id="t1", name="Polygon Target 1", center=(260.0, 184.0), radius=30.0),
            Target(id="t2", name="Polygon Target 2", center=(225.0, 448.0), radius=1.0,
                   type="polygon",
                   vertices=[{"x": 91.61, "y": 315.38}, {"x": 357.61, "y": 315.38},
                             {"x": 357.61, "y": 581.38}, {"x": 91.61, "y": 581.38}]),
        ], drones=[],
    )


def call_payload(name, args, tool_id="call_1"):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": tool_id, "type": "function",
         "function": {"name": name, "arguments": json.dumps({"input_json": json.dumps(args)})}}
    ]}}]}


class RetargetTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()
        self.world = build_world()
        self.config = {"enabled": True, "coverage_keywords": ["coverage", "95%"],
                       "coverage_swath": 40.0, "max_coverage_waypoints": 60,
                       "min_coverage_area": 200.0}

    def test_retarget_wrong_target(self):
        task = "Drone 3 executes comprehensive area search of Polygon Target 2. Take off to 23 meters."
        payload = call_payload("move_along_path",
                               {"waypoints": [{"x": 260.0, "y": 184.0, "z": 23.0}]})
        fixed, notes = self.proxy.rewrite_tool_calls(payload, task, self.world, self.config)
        args = json.loads(fixed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        inner = json.loads(args["input_json"])
        last = inner["waypoints"][-1]
        self.assertAlmostEqual(last["x"], 225.0, places=2)
        self.assertAlmostEqual(last["y"], 448.0, places=2)
        self.assertTrue(any(n.startswith("retarget") or n.startswith("coverage_route") for n in notes))

    def test_coverage_task_expands_two_waypoint_approach(self):
        """真实失败案例：模型给 1 个 approach + 1 个目标点，也应展开覆盖。"""
        task = ("Drone 3 executes comprehensive area search of Polygon Target 2. Take off to 23 meters "
                "and perform a systematic coverage pattern to search at least 95% of the target area.")
        payload = call_payload("move_along_path", {"waypoints": [
            {"x": 595.0, "y": 470.56, "z": 23.0},
            {"x": 225.0, "y": 448.0, "z": 23.0},
        ]})
        fixed, notes = self.proxy.rewrite_tool_calls(payload, task, self.world, self.config)
        args = json.loads(fixed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
        waypoints = json.loads(args["input_json"])["waypoints"]
        self.assertGreater(len(waypoints), 5, f"应展开覆盖，实际 {len(waypoints)}")
        self.assertEqual(waypoints[0]["x"], 595.0, "应保留原 approach 航点")
        self.assertTrue(any("coverage_route" in n for n in notes))

    def test_coverage_task_expands_single_waypoint(self):
        task = "Perform a systematic coverage pattern to search at least 95% of the target area of Polygon Target 2."
        payload = call_payload("move_to", {"x": 225.0, "y": 448.0, "z": 23.0})
        fixed, notes = self.proxy.rewrite_tool_calls(payload, task, self.world, self.config)
        call = fixed["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "move_along_path")
        args = json.loads(call["function"]["arguments"])
        inner = json.loads(args["input_json"])
        self.assertGreaterEqual(len(inner["waypoints"]), 4, "应替换为蛇形覆盖航线")

    def test_no_target_in_task_leaves_untouched(self):
        payload = call_payload("move_to", {"x": 10.0, "y": 10.0, "z": 20.0})
        fixed, notes = self.proxy.rewrite_tool_calls(payload, "fly to (10, 10)", self.world, self.config)
        self.assertEqual(notes, [])
        self.assertEqual(fixed, payload)

    def test_non_move_tool_untouched(self):
        payload = call_payload("take_photo", {})
        fixed, notes = self.proxy.rewrite_tool_calls(
            payload, "photo of Polygon Target 2", self.world, self.config)
        self.assertEqual(notes, [])

    def test_rewrite_disabled(self):
        cfg = dict(self.config, enabled=False)
        payload = call_payload("move_to", {"x": 260.0, "y": 184.0, "z": 23.0})
        fixed, notes = self.proxy.rewrite_tool_calls(payload, "Polygon Target 2", self.world, cfg)
        self.assertEqual(notes, [])
