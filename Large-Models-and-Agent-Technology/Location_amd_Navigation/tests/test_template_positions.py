"""模板坐标字符串容错解析测试（line_formation）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.template_actions import build_template_spec  # noqa: E402


class TemplatePositionTest(unittest.TestCase):
    def test_positions_with_drone_and_string(self):
        spec = build_template_spec("line_formation", {
            "altitude": 17,
            "drones": ["Drone 1", "Drone 2"],
            "positions": [{"drone": "Drone 2", "position": "(454, 490, 17)"},
                          {"drone": "Drone 1", "position": "(483, 549, 17)"}],
        })
        moves = [item for item in spec["actions"] if item["kind"] == "move_to"]
        self.assertEqual(len(moves), 2)
        self.assertEqual((moves[0]["params"]["x"], moves[0]["params"]["y"]), (454.0, 490.0))
        self.assertEqual((moves[1]["params"]["x"], moves[1]["params"]["y"]), (483.0, 549.0))

    def test_plain_string_positions(self):
        spec = build_template_spec("line_formation", {
            "altitude": 17,
            "drones": ["Drone 2", "Drone 1"],
            "positions": ["454, 490, 17", "483, 549, 17"],
        })
        moves = [item for item in spec["actions"] if item["kind"] == "move_to"]
        self.assertEqual([(m["params"]["x"], m["params"]["y"]) for m in moves],
                         [(454.0, 490.0), (483.0, 549.0)])


if __name__ == "__main__":
    unittest.main()
