"""target_photo 目标名解析 + 代理强制 mission 测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_adapter_proxy as proxy  # noqa: E402
from src.template_actions import build_template_spec  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "3. canyon_switchback_coordination.json")
WORLD = WorldMap.load(MAP)

TARGET = "Foxtrot Home Charger"
TEXT = ("Drone Delta must take off to 22 meters, navigate to target Protected Reserve "
        "Beacon, take a photo, then navigate to target Foxtrot Home Charger and take "
        "another photo.")


class TargetPhotoByNameTest(unittest.TestCase):
    def test_target_name_resolved_to_center(self):
        spec = build_template_spec("target_photo",
                                   {"drone_id": "Delta", "target": TARGET,
                                    "altitude": 22.0}, world=WORLD)
        self.assertIsNone(spec.get("error"))
        moves = [item for item in spec["actions"]
                 if str(item.get("name") or item.get("kind")) == "move_to"]
        self.assertEqual(len(moves), 1)
        center = WORLD.target_by_name(TARGET).center
        self.assertAlmostEqual(moves[0]["params"]["x"], float(center[0]), places=1)
        self.assertAlmostEqual(moves[0]["params"]["y"], float(center[1]), places=1)

    def test_unknown_target_hint(self):
        spec = build_template_spec("target_photo",
                                   {"drone_id": "Delta", "target": "No Such Target",
                                    "altitude": 22.0}, world=WORLD)
        self.assertIn("nav_execute_mission", str(spec.get("error")))


class ProxyTemplateDelegationTest(unittest.TestCase):
    def test_template_rewritten_to_mission(self):
        payload = {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "nav_template_action",
                                         "arguments": json.dumps(
                                             {"template": "battery_cycle",
                                              "params": {"drone_id": "d1"}})}}],
        }}]}
        original_resolve = proxy._resolve_drone_id
        original_best = proxy._best_drone_id
        proxy._resolve_drone_id = lambda *args, **kwargs: "d1"
        proxy._best_drone_id = lambda *args, **kwargs: ""
        try:
            result, note = proxy.enforce_mission_delegation(
                payload, [], TEXT, "http://127.0.0.1:8000")
        finally:
            proxy._resolve_drone_id = original_resolve
            proxy._best_drone_id = original_best
        self.assertTrue(note)
        call = result["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "nav_execute_mission")


if __name__ == "__main__":
    unittest.main()
