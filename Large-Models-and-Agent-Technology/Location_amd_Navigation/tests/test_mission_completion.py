"""任务完成后代理应强制停止工具调用，避免重复执行整条任务。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_adapter_proxy as proxy  # noqa: E402


class MissionCompletionGuardTest(unittest.TestCase):
    def _payload(self):
        return {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c", "type": "function",
                            "function": {"name": "move_along_path",
                                         "arguments": json.dumps({"input_json": "{}"})}}],
        }}]}

    def test_force_done_when_completed(self):
        messages = [{"role": "user", "content": "x"},
                    {"role": "tool", "content": json.dumps({"completed": True, "ok": True})}]
        result, note = proxy.enforce_mission_completion(self._payload(), messages)
        self.assertEqual(note, "mission_completed_forced_done")
        message = result["choices"][0]["message"]
        self.assertEqual(message["tool_calls"], [])
        self.assertIn("[TASK DONE]", message["content"])

    def test_no_change_when_not_completed(self):
        messages = [{"role": "user", "content": "x"},
                    {"role": "tool", "content": json.dumps({"completed": False, "ok": False})}]
        result, note = proxy.enforce_mission_completion(self._payload(), messages)
        self.assertIsNone(note)
        self.assertEqual(result["choices"][0]["message"]["tool_calls"], self._payload()["choices"][0]["message"]["tool_calls"])


if __name__ == "__main__":
    unittest.main()
