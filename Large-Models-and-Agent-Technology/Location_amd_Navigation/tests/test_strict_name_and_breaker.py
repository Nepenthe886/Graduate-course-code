"""严格名字解析 + 任务失败熔断测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llm_adapter_proxy as proxy  # noqa: E402
from src.config import load_config  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))


class StrictDroneNameTest(unittest.TestCase):
    def test_exact_name_resolves(self):
        self.assertIsNotNone(WORLD.drone_by_name("Drone 1"))

    def test_partial_name_does_not_resolve(self):
        self.assertIsNone(WORLD.drone_by_name("Drone"))
        self.assertIsNone(WORLD.drone_by_name("Drone 99"))


class MissionFailureBreakerTest(unittest.TestCase):
    @staticmethod
    def _payload():
        return {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "nav_execute_mission",
                                         "arguments": "{}"}}],
        }}]}

    def _messages(self, failures: int) -> list[dict]:
        messages = [{"role": "user", "content": "do task"}]
        for _ in range(failures):
            messages.append({"role": "tool",
                             "content": json.dumps({"ok": False, "mode": "per_leg_jit"})})
        return messages

    def test_breaks_after_three_failures(self):
        result, note = proxy.enforce_mission_failure_breaker(
            self._payload(), self._messages(3))
        self.assertTrue(note)
        content = result["choices"][0]["message"]["content"]
        self.assertTrue(content.startswith("[TASK FAILED]"))
        self.assertFalse(result["choices"][0]["message"]["tool_calls"])

    def test_two_failures_still_allowed(self):
        result, note = proxy.enforce_mission_failure_breaker(
            self._payload(), self._messages(2))
        self.assertIsNone(note)


if __name__ == "__main__":
    unittest.main()
