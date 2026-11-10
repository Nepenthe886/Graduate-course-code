"""航点顺序优化测试：顺序判定、最短路径重排、末尾固定。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.paths import find_map  # noqa: E402
from src.planner import Planner  # noqa: E402
from src.route_order import (  # noqa: E402
    end_should_stay_fixed,
    order_required,
    parse_route_segments,
    reorder_actions,
    reorder_points,
    reorder_segments,
    reorder_segments_waypoints,
    reorder_waypoints,
)
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
FIXTURE = Path(__file__).resolve().parent / "data" / "area_search_easy_3.json"
MAP_PATH = FIXTURE if FIXTURE.is_file() else find_map("Area Search Easy 3", CONFIG.__dict__)
WORLD = WorldMap.load(MAP_PATH)
PLANNER = Planner(WORLD, clearance=CONFIG.clearance, grid_step=CONFIG.grid_step)


class OrderDetectionTest(unittest.TestCase):
    def test_ordered_text(self):
        self.assertTrue(order_required("visit the waypoints in order"))
        self.assertTrue(order_required("follow the order listed and do not cut the loop"))

    def test_then_is_not_ordering_cue(self):
        self.assertFalse(order_required("fly to A, then continue to B and return home"))

    def test_unordered_text(self):
        self.assertFalse(order_required("visit (100, 100), (200, 200), (300, 300)"))
        self.assertFalse(order_required("visit them in any order"))

    def test_end_fixed(self):
        self.assertTrue(end_should_stay_fixed("fly to the points then land"))
        self.assertTrue(end_should_stay_fixed("return home at the end"))
        self.assertFalse(end_should_stay_fixed("fly to (100,100) and (200,200)"))


class ReorderTest(unittest.TestCase):
    START = (100.0, 100.0)
    POINTS = [(900.0, 700.0), (150.0, 120.0), (850.0, 150.0), (200.0, 650.0)]

    def test_reorder_reduces_distance(self):
        order, stats = reorder_points(PLANNER, self.START, self.POINTS, 50.0)
        self.assertTrue(stats.get("optimized"))
        self.assertEqual(sorted(order), list(range(len(self.POINTS))))
        self.assertLessEqual(stats["after_distance"], stats["before_distance"] + 1e-6)

    def test_end_fixed_keeps_last_point(self):
        order, _ = reorder_points(PLANNER, self.START, self.POINTS, 50.0, end_fixed=True)
        self.assertEqual(order[-1], len(self.POINTS) - 1)

    def test_reorder_waypoints(self):
        waypoints = [{"x": x, "y": y, "z": 50.0} for x, y in self.POINTS]
        reordered, stats = reorder_waypoints(PLANNER, self.START, waypoints, 50.0)
        self.assertTrue(stats.get("optimized"))
        self.assertEqual(len(reordered), len(waypoints))

    def test_reorder_actions_keeps_photo_with_point(self):
        actions = []
        for x, y in self.POINTS:
            actions.append({"kind": "move_to", "params": {"x": x, "y": y, "z": 50.0}})
            actions.append({"kind": "take_photo", "params": {}})
        reordered, stats = reorder_actions(PLANNER, self.START, actions, 50.0)
        self.assertTrue(stats.get("optimized"))
        kinds = [action["kind"] for action in reordered]
        self.assertEqual(kinds.count("take_photo"), len(self.POINTS))
        self.assertEqual(kinds[0], "move_to")
        # 每个 move_to 后面紧跟一个 take_photo（拍照动作没有脱离航点）
        for index in range(0, len(reordered), 2):
            self.assertEqual(reordered[index]["kind"], "move_to")
            self.assertEqual(reordered[index + 1]["kind"], "take_photo")


    def test_reorder_actions_keeps_land_at_end(self):
        actions = []
        for x, y in self.POINTS:
            actions.append({"kind": "move_to", "params": {"x": x, "y": y, "z": 50.0}})
        actions.append({"kind": "land", "params": {}})
        reordered, stats = reorder_actions(PLANNER, self.START, actions, 50.0)
        self.assertTrue(stats.get("optimized"))
        self.assertEqual(reordered[-1]["kind"], "land")
        self.assertEqual([item["kind"] for item in reordered].count("land"), 1)


class SegmentPlanTest(unittest.TestCase):
    TEXT = ("Fly to start point (100, 100, 30), then zigzag through the waypoints: "
            "(900, 700, 30), (150, 120, 30), (850, 150, 30), (200, 650, 30). "
            "Finally fly to end point (500, 500, 30) and land.")

    def test_parse_segments(self):
        segments = parse_route_segments(self.TEXT)
        self.assertEqual([segment["policy"] for segment in segments],
                         ["ordered", "unordered", "ordered"])
        self.assertEqual([len(segment["indices"]) for segment in segments], [1, 4, 1])

    def test_segmented_reorder_keeps_bounds(self):
        points = [(100.0, 100.0), (900.0, 700.0), (150.0, 120.0),
                  (850.0, 150.0), (200.0, 650.0), (500.0, 500.0)]
        segments = parse_route_segments(self.TEXT)
        permutation, stats = reorder_segments(PLANNER, (100.0, 100.0), points,
                                              segments, 30.0)
        self.assertTrue(stats.get("optimized"))
        self.assertEqual(permutation[0], 0)
        self.assertEqual(permutation[-1], 5)
        self.assertEqual(sorted(permutation), list(range(6)))

    def test_segmented_waypoints_shorter(self):
        waypoints = [{"x": x, "y": y, "z": 30.0} for x, y in
                     [(100.0, 100.0), (900.0, 700.0), (150.0, 120.0),
                      (850.0, 150.0), (200.0, 650.0), (500.0, 500.0)]]
        segments = parse_route_segments(self.TEXT)
        ordered, stats = reorder_segments_waypoints(PLANNER, (100.0, 100.0),
                                                    waypoints, segments, 30.0)
        self.assertTrue(stats.get("optimized"))
        self.assertEqual((ordered[0]["x"], ordered[0]["y"]), (100.0, 100.0))
        self.assertEqual((ordered[-1]["x"], ordered[-1]["y"]), (500.0, 500.0))


if __name__ == "__main__":
    unittest.main()
