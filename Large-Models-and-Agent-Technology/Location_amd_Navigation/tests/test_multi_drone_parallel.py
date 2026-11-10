"""多机并行执行测试：按机合并 + 编队靠拢屏障。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.mission_run as mission_run  # noqa: E402
from tools.nav_tools import NavToolkit  # noqa: E402

DRONES = ("Drone 6", "Drone 4", "Drone 1")


def _actions(include_approach: bool = False) -> list[dict]:
    actions = []
    for drone in DRONES:
        actions.append({"name": "take_off", "params": {"drone_id": drone, "altitude": 20}})
    for drone in DRONES:
        actions.append({"name": "move_to",
                        "params": {"drone_id": drone, "x": 100.0, "y": 100.0, "z": 20.0}})
    if include_approach:
        actions.append({"name": "approach_drone",
                        "params": {"drone_id": "Drone 1", "target_drone_id": "Drone 6",
                                   "max_distance": 15.0, "z": 20.0}})
    for drone in DRONES:
        actions.append({"name": "hover", "params": {"drone_id": drone}})
    return actions


class MultiDroneParallelTest(unittest.TestCase):
    def _run(self, include_approach: bool):
        records: list[dict] = []

        def fake(map_path, drone_id, spec, api_base=None, config_path=None):
            start = time.time()
            time.sleep(0.15)
            kinds = [str(a.get("name") or a.get("kind")) for a in (spec.get("actions") or [])]
            records.append({"start": start, "end": time.time(), "kinds": kinds})
            return {"ok": True, "executed": [{"name": kind} for kind in kinds],
                    "failures": []}

        original = mission_run.execute_mission
        mission_run.execute_mission = fake
        try:
            toolkit = NavToolkit()
            started = time.time()
            result = toolkit.nav_execute_mission({"actions": _actions(include_approach),
                                                  "altitude": 20})
            elapsed = time.time() - started
        finally:
            mission_run.execute_mission = original
        return result, elapsed, records

    def test_parallel_runs(self):
        result, elapsed, records = self._run(False)
        self.assertEqual(result.get("mode"), "multi_drone_parallel")
        self.assertTrue(result.get("ok"))
        self.assertEqual(sorted(result.get("drones") or []), sorted(DRONES))
        self.assertLess(elapsed, 3 * 0.15 * 3 * 0.7)

    def test_approach_runs_after_formations(self):
        _, _, records = self._run(True)
        pre = [record for record in records
               if any(kind != "approach_drone" for kind in record["kinds"])]
        post = [record for record in records
                if any(kind == "approach_drone" for kind in record["kinds"])]
        self.assertTrue(pre and post)
        self.assertGreaterEqual(min(item["start"] for item in post),
                                max(item["end"] for item in pre) - 0.05)


if __name__ == "__main__":
    unittest.main()
