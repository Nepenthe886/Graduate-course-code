"""模板动作库测试：10 个官方模板 -> 原子动作序列展开。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.actions import ACTION_LIBRARY, ATOMIC_ACTIONS
from src.bt_defs import build_mission
from src.config import load_config
from src.template_actions import (TEMPLATES, build_template_spec, detect_template)
from src.worldmap import WorldMap

CASES = {
    "altitude_stage": {"drone_id": "Drone 1", "alt1": 15, "alt2": 36},
    "grid_survey": {"drone_id": "Drone 1", "altitude": 25,
                    "corners": [[120, 520], [720, 520], [720, 220], [120, 220]]},
    "target_photo": {"drone_id": "Drone 1", "x": 512, "y": 384, "altitude": 30},
    "line_formation": {"altitude": 22,
                       "drones": [{"drone_id": "Drone 1", "x": 280, "y": 380},
                                  {"drone_id": "Drone 2", "x": 480, "y": 380}]},
    "perimeter_patrol": {"drone_id": "Drone 1", "altitude": 20,
                         "corners": [[150, 150], [850, 150], [850, 600], [150, 600]]},
    "relay_message": {"altitude": 30,
                      "drone1": {"drone_id": "Drone 1", "x": 250, "y": 400},
                      "drone2": {"drone_id": "Drone 2", "x": 600, "y": 400},
                      "message": "wind gust"},
    "corridor_detour": {"drone_id": "Drone 1", "start": [131, 156.3], "end": [881, 624.2],
                        "altitude": 28.2, "clearance": 59, "photos": 2},
    "battery_cycle": {"drone_id": "Drone 1", "altitude": 18, "charge_amount": 30},
    "target_coverage": {"drone_id": "Drone 1", "center": [512, 384], "radius": 150,
                        "altitude": 24, "sweep": 50, "coverage": 0.7},
    "emergency_land": {"drone_id": "Drone 1", "message": "emergency stop",
                       "drones": ["Drone 1", "Drone 2"]},
}


class TemplateActionTests(unittest.TestCase):
    def test_all_templates_covered(self):
        self.assertEqual(set(CASES), set(TEMPLATES))

    def test_action_library_has_message_and_charge(self):
        for name in ("send_message", "charge"):
            self.assertIn(name, ACTION_LIBRARY)
            self.assertIn(name, ATOMIC_ACTIONS)
            self.assertIn("endpoint", ACTION_LIBRARY[name])

    def test_each_template_expands(self):
        for template_id, params in CASES.items():
            with self.subTest(template=template_id):
                spec = build_template_spec(template_id, params)
                self.assertNotIn("error", spec)
                self.assertTrue(spec["actions"])
                self.assertIn("altitude", spec)
                kinds = [a["kind"] for a in spec["actions"]]
                self.assertEqual(kinds[0], "take_off" if template_id != "emergency_land" else "broadcast")
                self.assertIn(kinds[-1], ("hover", "land", "charge"))

    def test_corridor_detour_sequence(self):
        spec = build_template_spec("corridor_detour", CASES["corridor_detour"])
        kinds = [a["kind"] for a in spec["actions"]]
        self.assertEqual(kinds.count("take_photo"), 2)
        self.assertIn("move_along_path", kinds)
        # 起点 move_to + 绕行航段
        move_to = next(a for a in spec["actions"] if a["kind"] == "move_to")
        self.assertAlmostEqual(move_to["params"]["x"], 131.0)
        self.assertAlmostEqual(move_to["params"]["y"], 156.3)
        path = next(a for a in spec["actions"] if a["kind"] == "move_along_path")
        waypoints = path["params"]["waypoints"]
        self.assertEqual(len(waypoints), 3)
        self.assertAlmostEqual(waypoints[-1]["x"], 881.0)
        self.assertAlmostEqual(waypoints[-1]["y"], 624.2)
        # 净空 59 m：绕行点应偏离走廊中线
        mid = waypoints[0]
        self.assertTrue(mid["x"] != 131.0 or mid["y"] != 156.3)
        self.assertEqual(kinds[-1], "hover")

    def test_multi_drone_actions_carry_drone_ids(self):
        spec = build_template_spec("line_formation", CASES["line_formation"])
        drone_ids = {a["params"].get("drone_id") for a in spec["actions"]}
        self.assertEqual(drone_ids, {"Drone 1", "Drone 2"})
        relay = build_template_spec("relay_message", CASES["relay_message"])
        msg = next(a for a in relay["actions"] if a["kind"] == "send_message")
        self.assertEqual(msg["params"]["target_drone_id"], "Drone 2")
        self.assertEqual(msg["params"]["message"], "wind gust")

    def test_emergency_land_sequence(self):
        spec = build_template_spec("emergency_land", CASES["emergency_land"])
        kinds = [a["kind"] for a in spec["actions"]]
        self.assertEqual(kinds[0], "broadcast")
        self.assertEqual(kinds.count("land"), 2)
        self.assertFalse(spec["land_at_end"])

    def test_unknown_template(self):
        spec = build_template_spec("nope", {})
        self.assertIn("error", spec)

    def test_detect_template(self):
        self.assertEqual(detect_template("Cover the corridor from (131, 156) to (881, 624)"),
                         "corridor_detour")
        self.assertEqual(detect_template("Set up a two-station relay ... message"), "relay_message")
        self.assertEqual(detect_template("perimeter patrol around the compound"), "perimeter_patrol")

    def test_build_mission_accepts_template_spec(self):
        config = load_config()
        world = WorldMap.load(str(ROOT / config.default_map))
        for template_id, params in CASES.items():
            with self.subTest(template=template_id):
                spec = build_template_spec(template_id, params)
                tree, steps = build_mission(world, spec, spec["altitude"])
                self.assertIsNotNone(tree)
                self.assertEqual(len(steps), len(spec["actions"]))
                self.assertTrue(all("kind" in step for step in steps))


if __name__ == "__main__":
    unittest.main()
