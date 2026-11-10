"""“Avoid obstacles ...”从句应被忽略（导航层自动避障）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
WORLD = WorldMap.load(resolve(CONFIG.default_map))

BASE = (
    "Drone Yard 28 performs a marathon flight. Take off to 23 meters, fly to start point "
    "(133, 162, 23), then navigate through 10 waypoints: (212, 174, 23), (331, 288, 23), "
    "(498, 382, 23), (581, 457, 23), (687, 546, 23), (742, 649, 23), (839, 761, 23), "
    "(930, 846, 23), (874, 200, 23), (119, 891, 23). Finally, fly to end point "
    "(511, 558, 23) and land."
)
WITH_AVOID = BASE.replace(
    "Finally,",
    "Avoid obstacles Container Stack 11, Light Mast 6. Finally,")


class AvoidObstaclesClauseTest(unittest.TestCase):
    def test_avoid_clause_ignored(self):
        spec = build_spec_from_task(WITH_AVOID, 23.0, True, world=WORLD)
        self.assertIsNone(spec.get("error"))
        points = spec.get("waypoints") or []
        self.assertEqual(len(points), 12)
        text = str(spec.get("task_text") or "")
        self.assertNotIn("Avoid obstacles", text)

    def test_same_result_without_clause(self):
        plain = build_spec_from_task(BASE, 23.0, True, world=WORLD)
        with_avoid = build_spec_from_task(WITH_AVOID, 23.0, True, world=WORLD)
        self.assertEqual(len(plain.get("waypoints") or []),
                         len(with_avoid.get("waypoints") or []))


if __name__ == "__main__":
    unittest.main()
