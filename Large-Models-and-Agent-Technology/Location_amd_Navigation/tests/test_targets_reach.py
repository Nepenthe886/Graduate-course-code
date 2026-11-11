"""目标列表到达 + 进度阈值 + 代理兜底测试。"""
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

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))

TEXT = ("Take off necessary drones and assign them to reach required targets "
        "Fixed Target 1, Fixed Target 2, and Fixed Target 3; if those targets alone "
        "do not exceed 50% session task progress, continue reaching additional assigned "
        "targets until progress exceeds 50%.")


class TargetsReachTest(unittest.TestCase):
    def test_progress_continues_beyond_listed_targets(self):
        spec = build_spec_from_task(TEXT, 22.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        navs = [item for item in spec["actions"] if item.get("name") == "navigate_to_pose"]
        total_targets = len(WORLD.targets)
        self.assertEqual(spec.get("progress_threshold"), 50.0)
        self.assertGreater(len(navs), 3)
        self.assertGreater(len(navs) / float(total_targets) * 100.0, 50.0)
        self.assertEqual(spec["targets"][:3],
                         ["Fixed Target 1", "Fixed Target 2", "Fixed Target 3"])


    def test_all_targets_progress_95(self):
        text = ("take off necessary drones and assigned them to all targets "
                "(within task radius) until the session task progress exceeds 95%.")
        spec = build_spec_from_task(text, 22.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        navs = [item for item in spec["actions"] if item.get("name") == "navigate_to_pose"]
        total = len(WORLD.targets)
        self.assertEqual(spec.get("progress_threshold"), 95.0)
        self.assertGreater(len(navs) / float(total) * 100.0, 95.0)
        self.assertGreaterEqual(len({item["params"].get("drone_id") for item in navs}), 2)


class BreakerAndFallbackTest(unittest.TestCase):
    def test_missing_param_errors_not_counted(self):
        messages = [{"role": "user", "content": "task"}]
        for _ in range(3):
            messages.append({"role": "tool", "content": json.dumps(
                {"error": "模板 target_coverage 缺少参数: 'altitude'"})})
        count, _last = proxy._recent_tool_errors(messages)
        self.assertEqual(count, 0)

    def test_enforce_mission_without_named_drone(self):
        payload = {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "nav_template_action",
                                         "arguments": "{}"}}],
        }}]}
        original_resolve = proxy._resolve_drone_id
        original_best = proxy._best_drone_id
        proxy._resolve_drone_id = lambda *args, **kwargs: ""
        proxy._best_drone_id = lambda *args, **kwargs: "best-id"
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
