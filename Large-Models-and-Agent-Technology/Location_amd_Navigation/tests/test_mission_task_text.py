"""nav_execute_mission 参数简化（只允许 task_text）测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_adapter_proxy as proxy  # noqa: E402

TASK_TEXT = "Drone 2 flies to (100, 100, 20) and lands."


def _payload(arguments: str):
    return {"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "nav_execute_mission",
                                     "arguments": arguments}}],
    }}]}


def _inner(result):
    raw = result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    return json.loads(json.loads(raw)["input_json"])


class MissionTaskTextTest(unittest.TestCase):
    def test_actions_rewritten_to_task_text(self):
        args = json.dumps({"input_json": json.dumps({
            "task_text": "short",
            "actions": [{"name": "move_to", "params": {"x": 1, "y": 2, "z": 3}}],
        })})
        result, note = proxy.enforce_mission_task_text(_payload(args), TASK_TEXT,
                                                       "http://127.0.0.1:8000")
        self.assertEqual(note, "mission_task_text_enforced")
        inner = _inner(result)
        self.assertEqual(inner["task_text"], TASK_TEXT)
        self.assertNotIn("actions", inner)

    def test_truncated_arguments_rewritten(self):
        truncated = '{"input_json": "{\\"task_text\\": \\"x\\", \\"actions\\": [{\\"name\\": \\"move_to\\"'
        result, note = proxy.enforce_mission_task_text(_payload(truncated), TASK_TEXT,
                                                       "http://127.0.0.1:8000")
        self.assertEqual(note, "mission_task_text_enforced")
        inner = _inner(result)
        self.assertEqual(inner["task_text"], TASK_TEXT)

    def test_task_text_call_untouched(self):
        args = json.dumps({"input_json": json.dumps({"task_text": TASK_TEXT})})
        result, note = proxy.enforce_mission_task_text(_payload(args), TASK_TEXT,
                                                       "http://127.0.0.1:8000")
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
