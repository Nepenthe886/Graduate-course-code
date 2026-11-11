"""5 机编队 + 定向移动解析（Yard 通用命名）测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.mission_run import build_spec_from_task  # noqa: E402
from src.paths import resolve  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

PORT = resolve("../MultiUAV-Plat-v0.49_Windows/UAVSessions/6. port_container_inventory.json")
WORLD = WorldMap.load(PORT)

TEXT = (
    "Five-drone coordination exercise: Yard 26, Yard 7, Yard 8, Yard 22, and Yard 9 take off "
    "together to 22 meters altitude, then hold formation at positions (519, 381, 22), "
    "(532, 445, 22), (833, 454, 22), (652, 660, 22), and (403, 623, 22) respectively for "
    "synchronized operation. Then all five drones move north for 74m, and next move east for "
    "79m. Each drone must complete the specified directional movements (north 74m, then east "
    "79m) regardless of starting position."
)

DRONES = {"Yard 26", "Yard 7", "Yard 8", "Yard 22", "Yard 9"}


class YardFormationTest(unittest.TestCase):
    def setUp(self):
        self.spec = build_spec_from_task(TEXT, 22.0, False, world=WORLD)

    def test_drones_and_positions(self):
        self.assertIsNone(self.spec.get("error"))
        actions = self.spec["actions"]
        takeoffs = [item for item in actions if item.get("name") == "take_off"]
        moves = [item for item in actions if item.get("name") == "move_to"]
        self.assertEqual({item["params"]["drone_id"] for item in takeoffs}, DRONES)
        self.assertTrue(all(item["params"]["altitude"] == 22.0 for item in takeoffs))
        self.assertEqual(len(moves), 5)
        coords = {(item["params"]["x"], item["params"]["y"]) for item in moves}
        self.assertIn((519.0, 381.0), coords)
        self.assertIn((403.0, 623.0), coords)

    def test_directional_moves_per_drone(self):
        actions = self.spec["actions"]
        directions: dict[str, list[tuple[float, float]]] = {}
        for item in actions:
            if item.get("name") != "move_direction":
                continue
            params = item["params"]
            directions.setdefault(params["drone_id"], []).append(
                (params["heading"], params["distance"]))
        self.assertEqual(set(directions), DRONES)
        for drone, pairs in directions.items():
            self.assertEqual(pairs, [(0.0, 74.0), (90.0, 79.0)], drone)


if __name__ == "__main__":
    unittest.main()
