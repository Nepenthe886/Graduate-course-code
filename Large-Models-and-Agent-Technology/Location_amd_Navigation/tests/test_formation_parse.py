"""编队解析：按机名配对坐标、排除目标中心、数量校验。"""
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

TRIANGLE = (
    "Drones Drone 6, Drone 4, and Drone 1 should take off to 20 meters altitude and form a "
    "triangle formation around target at position (591, 503, 20). Drone 6 positions at "
    "(591, 482, 20), Drone 4 at (522, 497, 20), and Drone 1 at (443, 515, 20). After formation "
    "is established, all drones maintain hovering."
)


class FormationParseTest(unittest.TestCase):
    def test_positions_paired_by_drone_name(self):
        spec = build_spec_from_task(TRIANGLE, 20.0, False, world=WORLD)
        self.assertIsNone(spec.get("error"))
        moves = {item["params"]["drone_id"]: (item["params"]["x"], item["params"]["y"])
                 for item in spec["actions"] if item.get("name") == "move_to"}
        self.assertEqual(moves["Drone 6"], (591.0, 482.0))
        self.assertEqual(moves["Drone 4"], (522.0, 497.0))
        self.assertEqual(moves["Drone 1"], (443.0, 515.0))

    def test_target_center_not_used_as_position(self):
        spec = build_spec_from_task(TRIANGLE, 20.0, False, world=WORLD)
        used = {(item["params"]["x"], item["params"]["y"])
                for item in spec["actions"] if item.get("name") == "move_to"}
        self.assertNotIn((591.0, 503.0), used)

    def test_mismatch_returns_error(self):
        text = "Drones Drone 1 and Drone 2 form a formation, Drone 1 at (100, 100, 20)."
        spec = build_spec_from_task(text, 20.0, False, world=WORLD)
        self.assertIn("编队解析失败", str(spec.get("error")))


if __name__ == "__main__":
    unittest.main()
