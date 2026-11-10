"""双机马拉松（Yard 28 / Yard 15 命名）解析与并行测试。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.mission_run as mission_run  # noqa: E402
from src.config import load_config  # noqa: E402
from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402
from tools.nav_tools import NavToolkit  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))

TEXT = (
    "Drones Yard 28 and Yard 15 perform a dual marathon flight at 27 meters. "
    "Yard 28 route: fly to and start at (58, 113, 27), then waypoints (117, 127, 27), "
    "(910, 257, 27), then end (950, 874, 27) and land. "
    "Yard 15 route: fly to and start at (913, 60, 27), then waypoints (897, 220, 27), "
    "(136, 215, 27), then end (98, 958, 27) and land."
)


class YardDualRouteTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 27.0, False, world=WORLD)

    def test_split_by_named_routes(self):
        self.assertTrue(self.spec.get("multi_drone"))
        actions = self.spec["actions"]
        by_drone: dict[str, list] = {}
        for action in actions:
            by_drone.setdefault(action["params"]["drone_id"], []).append(action)
        self.assertEqual(set(by_drone), {"Yard 28", "Yard 15"})
        moves_28 = [a["params"] for a in by_drone["Yard 28"] if a["kind"] == "move_to"]
        moves_15 = [a["params"] for a in by_drone["Yard 15"] if a["kind"] == "move_to"]
        self.assertEqual((moves_28[-1]["x"], moves_28[-1]["y"]), (950.0, 874.0))
        self.assertEqual((moves_15[-1]["x"], moves_15[-1]["y"]), (98.0, 958.0))
        self.assertEqual(by_drone["Yard 28"][-1]["kind"], "land")
        self.assertEqual(by_drone["Yard 15"][-1]["kind"], "land")

    def test_parallel_execution(self):
        records: list[tuple[str, int]] = []

        def fake(map_path, drone_id, spec, api_base=None, config_path=None):
            time.sleep(0.2)
            records.append((drone_id, len(spec.get("actions") or [])))
            return {"ok": True, "executed": [{"name": "x"}], "failures": []}

        original = mission_run.execute_mission
        mission_run.execute_mission = fake
        try:
            toolkit = NavToolkit()
            started = time.time()
            result = toolkit.nav_execute_mission({"task_text": TEXT, "altitude": 27})
            elapsed = time.time() - started
        finally:
            mission_run.execute_mission = original
        self.assertEqual(result.get("mode"), "multi_drone_parallel")
        self.assertEqual(len(records), 2)
        self.assertLess(elapsed, 2 * 0.2 * 0.9)


if __name__ == "__main__":
    unittest.main()
