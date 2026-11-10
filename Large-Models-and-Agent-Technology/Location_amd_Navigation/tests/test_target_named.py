"""按目标名导航/拍照：解析与代理强制转 mission 测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_adapter_proxy as proxy  # noqa: E402
from src.config import load_config  # noqa: E402
from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

FIXTURE = resolve(load_config().default_map)
WORLD = WorldMap.load(FIXTURE)


class TargetSpecTest(unittest.TestCase):
    def test_multi_target_photo_builds_all_targets(self):
        text = ("Drone Yard 8 must take off to 21 meters, navigate to target Fixed Target 1, "
                "take a photo, then navigate to target Fixed Target 2 and take another photo.")
        spec = build_spec_from_task(text, 21.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        actions = spec["actions"]
        navs = [action for action in actions if action.get("name") == "navigate_to_pose"]
        photos = [action for action in actions if action.get("name") == "take_photo"]
        self.assertEqual(len(navs), 2)
        self.assertEqual(len(photos), 2)
        first = WORLD.target_by_name("Fixed Target 1")
        second = WORLD.target_by_name("Fixed Target 2")
        self.assertAlmostEqual(navs[0]["params"]["x"], float(first.center[0]), places=1)
        self.assertAlmostEqual(navs[1]["params"]["x"], float(second.center[0]), places=1)

    def test_single_target_still_works(self):
        text = ("Drone Yard 7 must take off to 18 meters, navigate to target "
                "Fixed Target 1, and take a photo.")
        spec = build_spec_from_task(text, 18.0, False, world=WORLD)
        names = [action.get("name") for action in spec["actions"]]
        self.assertEqual(names.count("navigate_to_pose"), 1)
        self.assertEqual(names.count("take_photo"), 1)


class ProxyTargetDelegationTest(unittest.TestCase):
    def test_get_nearby_entities_replaced(self):
        payload = {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "get_nearby_entities",
                                         "arguments": json.dumps({"drone_id": "69f0b0f4"})}}],
        }}]}
        text = ("Drone Yard 7 must take off to 18 meters, navigate to target Bay 036, "
                "and take a photo.")
        original = proxy._resolve_drone_id
        proxy._resolve_drone_id = lambda *args, **kwargs: "69f0b0f4"
        try:
            result, note = proxy.enforce_mission_delegation(payload, [], text, "http://127.0.0.1:8000")
        finally:
            proxy._resolve_drone_id = original
        self.assertTrue(note)
        call = result["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "nav_execute_mission")


if __name__ == "__main__":
    unittest.main()
