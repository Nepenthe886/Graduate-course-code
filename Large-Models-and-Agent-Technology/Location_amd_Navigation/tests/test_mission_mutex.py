"""任务互斥/取消机制单元测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import (  # noqa: E402
    begin_mission_task,
    end_mission_task,
    mission_cancelled,
)


class MissionMutexTest(unittest.TestCase):
    def test_new_task_cancels_previous(self):
        first = begin_mission_task(wait_seconds=0.2)
        try:
            self.assertFalse(mission_cancelled(first))
            second = begin_mission_task(wait_seconds=0.2)
            try:
                self.assertTrue(mission_cancelled(first))
                self.assertFalse(mission_cancelled(second))
            finally:
                end_mission_task(second)
        finally:
            end_mission_task(first)

    def test_end_marks_cancelled(self):
        event = begin_mission_task(wait_seconds=0.1)
        end_mission_task(event)
        self.assertTrue(mission_cancelled(event))
        self.assertFalse(mission_cancelled(None))


if __name__ == "__main__":
    unittest.main()
