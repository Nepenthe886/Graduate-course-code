"""自测：几何、地图加载、以及"真实失败案例"的绕障回归测试。

运行：
    py -3.12 -m unittest discover -s tests -v
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import geometry  # noqa: E402
from src.config import load_config  # noqa: E402
from src.frames import Pose, bearing_deg, body_to_world, distance_xy, world_to_body  # noqa: E402
from src.localization import Localizer  # noqa: E402
from src.navigation import Navigation  # noqa: E402
from src.paths import find_map  # noqa: E402
from src.planner import Planner  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

CONFIG = load_config()
MAP_PATH = find_map("Area Search Easy 3", CONFIG.__dict__)


class GeometryTest(unittest.TestCase):
    def test_point_in_polygon(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(geometry.point_in_polygon((5, 5), square))
        self.assertFalse(geometry.point_in_polygon((15, 5), square))

    def test_segment_polygon(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(geometry.segment_polygon_intersection((-5, 5), (15, 5), square))
        self.assertFalse(geometry.segment_polygon_intersection((-5, 20), (15, 20), square))

    def test_segment_distance(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertAlmostEqual(geometry.segment_polygon_distance((-5, 12), (15, 12), square), 2.0, places=6)

    def test_circle_distance(self):
        self.assertAlmostEqual(geometry.segment_circle_distance((-10, 0), (10, 0), (0, 5), 2.0), 3.0, places=6)


class FramesTest(unittest.TestCase):
    def test_roundtrip(self):
        pose = Pose(x=100, y=200, z=50, yaw_deg=30)
        point = (140, 260, 55)
        body = world_to_body(pose, point)
        back = body_to_world(pose, body)
        for a, b in zip(point, back):
            self.assertAlmostEqual(a, b, places=6)

    def test_bearing(self):
        self.assertAlmostEqual(bearing_deg((0, 0), (0, 10)), 90.0, places=6)
        self.assertAlmostEqual(abs(bearing_deg((0, 0), (-10, 0))), 180.0, places=6)

    def test_dead_reckon(self):
        localizer = Localizer(Pose(0, 0, 0, 0))
        localizer.dead_reckon(forward=10)
        self.assertAlmostEqual(localizer.pose.x, 10.0, places=6)
        self.assertAlmostEqual(localizer.pose.y, 0.0, places=6)


class MapTest(unittest.TestCase):
    def setUp(self):
        self.world = WorldMap.load(MAP_PATH)

    def test_loaded(self):
        self.assertEqual(self.world.name, "Area Search Easy 3")
        self.assertGreater(len(self.world.obstacles), 0)
        self.assertGreaterEqual(len(self.world.drones), 3)
        self.assertIsNotNone(self.world.drone_by_name("Drone 2"))
        self.assertEqual(self.world.width, 1024.0)

    def test_obstacle_lookup(self):
        obstacle = self.world.obstacle_by_name("Polygon Obstacle 2")
        self.assertIsNotNone(obstacle)
        polygon = obstacle.footprint()
        self.assertEqual(len(polygon), 4)


class AvoidanceRegressionTest(unittest.TestCase):
    """真实失败案例：Drone 2 (879,692) -> (564,94)，直线会撞 Polygon Obstacle 2。"""

    def setUp(self):
        self.world = WorldMap.load(MAP_PATH)
        self.planner = Planner(self.world, clearance=CONFIG.clearance, grid_step=CONFIG.grid_step)
        self.start = (879.0, 692.0)
        self.goal = (564.0, 94.0)

    def test_straight_line_is_blocked(self):
        check = self.planner.segment(self.start, self.goal)
        self.assertFalse(check.clear, "直线本应被阻挡")
        self.assertEqual(check.blocker, "Polygon Obstacle 2")

    def test_route_detours_and_reaches_goal(self):
        route = self.planner.plan(self.start, self.goal)
        self.assertTrue(route.detoured)
        self.assertIn("Polygon Obstacle 2", route.avoided)
        self.assertGreaterEqual(len(route.waypoints), 2, "绕障后至少要多一段")
        self.assertAlmostEqual(route.waypoints[-1][0], self.goal[0], places=3)
        self.assertAlmostEqual(route.waypoints[-1][1], self.goal[1], places=3)
        # 每段都必须满足净空
        previous = self.start
        for waypoint in route.waypoints:
            check = self.planner.segment(previous, waypoint)
            self.assertTrue(check.clear, f"段 {previous}->{waypoint} 净空不足: {check.as_dict()}")
            previous = waypoint
        self.assertGreater(route.length, distance_xy(self.start, self.goal))

    def test_navigation_steps(self):
        localizer = Localizer(self.world.drone_pose("Drone 2"))
        navigation = Navigation(self.world, localizer, self.planner,
                                arrival_tolerance=CONFIG.arrival_tolerance, z_default=CONFIG.z_default)
        steps = navigation.steps_to(self.goal)
        kinds = [step.kind for step in steps]
        self.assertEqual(kinds[0], "turn")
        self.assertIn("goto", kinds)
        self.assertEqual(kinds[-1], "hover")
        self.assertGreaterEqual(sum(1 for k in kinds if k == "goto"), 2)

    def test_target_route(self):
        localizer = Localizer(Pose(879, 692, 0, 0))
        navigation = Navigation(self.world, localizer, self.planner,
                                arrival_tolerance=CONFIG.arrival_tolerance, z_default=CONFIG.z_default)
        route = navigation.plan_to_target("Circle Target 2")
        self.assertIsNotNone(route)
        self.assertTrue(len(route.waypoints) >= 1)


class CircleAvoidanceTest(unittest.TestCase):
    def setUp(self):
        self.world = WorldMap.load(MAP_PATH)
        self.planner = Planner(self.world, clearance=CONFIG.clearance, grid_step=CONFIG.grid_step)

    def test_circle_obstacle(self):
        # Circle Obstacle 2 圆心 (939,440) 半径 61
        start, goal = (939.0, 250.0), (939.0, 620.0)
        check = self.planner.segment(start, goal)
        self.assertFalse(check.clear)
        route = self.planner.plan(start, goal)
        self.assertTrue(route.detoured)
        previous = start
        for waypoint in route.waypoints:
            self.assertTrue(self.planner.segment(previous, waypoint).clear)
            previous = waypoint


class ThreeDPlanningTest(unittest.TestCase):
    """3D 规划：矮障碍可飞越，高障碍仍需绕行。"""

    def _world(self):
        from src.worldmap import Obstacle, WorldMap

        obstacle = Obstacle(id="o1", name="Low Wall", type="circle",
                            center=(500.0, 400.0), height=20.0, radius=60.0)
        return WorldMap(source=Path("virtual.json"), name="virt", width=1000.0, height=800.0,
                        obstacles=[obstacle], targets=[], drones=[])

    def test_2d_still_detours(self):
        world = self._world()
        planner = Planner(world, clearance=20.0, planning_dim="2d", altitude=18.0)
        check = planner.segment((100.0, 400.0), (900.0, 400.0))
        self.assertFalse(check.clear)
        route = planner.plan((100.0, 400.0), (900.0, 400.0))
        self.assertTrue(route.detoured)
        self.assertEqual(route.planning_dim, "2d")

    def test_3d_fly_over_low_obstacle(self):
        world = self._world()
        planner = Planner(world, clearance=20.0, planning_dim="3d", altitude=18.0,
                          vertical_margin=5.0, max_altitude=120.0, altitude_step=10.0)
        # 18 m 过不去（20+5=25 > 18），应爬升
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=18.0)
        self.assertEqual(route.planning_dim, "3d")
        self.assertTrue(route.detoured, "低高度应判为过不去")
        self.assertGreaterEqual(route.altitude, 25.0, "应爬升到 25 m 以上")
        self.assertIn("Low Wall", route.overfly)
        zs = [p[2] for p in route.waypoints_xyz]
        self.assertGreater(max(zs), 25.0, "航点里应有高高度")
        self.assertAlmostEqual(zs[-1], 18.0, places=3, msg="最后应下降回目标高度")

    def test_3d_high_enough_goes_straight(self):
        world = self._world()
        planner = Planner(world, clearance=20.0, planning_dim="3d", altitude=60.0)
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=60.0)
        self.assertFalse(route.detoured, "60 m 高于 25 m，应直飞")
        self.assertEqual(len(route.waypoints), 1)
        self.assertEqual(route.overfly, [])

    def test_3d_infinite_column_still_blocks(self):
        from src.worldmap import Obstacle, WorldMap

        tall = Obstacle(id="o2", name="Tower", type="circle", center=(500.0, 400.0),
                        height=0.0, radius=50.0)
        world = WorldMap(source=Path("virt2.json"), name="virt2", width=1000.0, height=800.0,
                         obstacles=[tall], targets=[], drones=[])
        planner = Planner(world, clearance=20.0, planning_dim="3d", altitude=90.0,
                          max_altitude=120.0)
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=90.0)
        self.assertTrue(route.detoured, "height=0 是无限高柱体，爬升无效，必须绕行")


class ThreeDShortestPathTest(unittest.TestCase):
    """3D 最短路径 + 平滑：应显著短于纯水平绕行，且平滑后仍安全。"""

    def _world(self):
        from src.worldmap import Obstacle, WorldMap

        obstacles = [
            Obstacle(id="w1", name="Low Wall", type="circle", center=(500.0, 400.0),
                     height=20.0, radius=70.0),
            Obstacle(id="w2", name="Mid Wall", type="circle", center=(650.0, 400.0),
                     height=18.0, radius=50.0),
        ]
        return WorldMap(source=Path("virt3.json"), name="virt3", width=1000.0, height=800.0,
                        obstacles=obstacles, targets=[], drones=[])

    def test_3d_shortest_is_shorter_than_2d(self):
        world = self._world()
        start, goal = (100.0, 400.0), (900.0, 400.0)
        route_2d = Planner(world, clearance=20.0, planning_dim="2d", altitude=18.0).plan(
            start, goal, altitude=18.0)
        route_3d = Planner(world, clearance=20.0, planning_dim="3d", altitude=18.0,
                           vertical_margin=5.0, max_altitude=80.0, altitude_step=10.0,
                           z_min=18.0).plan(start, goal, altitude=18.0)
        self.assertEqual(route_3d.planning_dim, "3d")
        self.assertAlmostEqual(route_3d.waypoints_xyz[-1][2], 18.0, places=3)
        self.assertGreaterEqual(route_3d.z_max, 25.0, "应爬升越过 20 m 的墙")
        self.assertLess(route_3d.length_3d, route_2d.length, "3D 飞越应比 2D 绕行更短")

    def test_3d_path_is_smooth_and_clear(self):
        world = self._world()
        planner = Planner(world, clearance=20.0, planning_dim="3d", altitude=18.0,
                          max_altitude=80.0, altitude_step=10.0, z_min=18.0,
                          smoothing=True, smooth_iterations=2, max_turn_deg=60.0)
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=18.0)
        self.assertTrue(route.smoothed, "应成功平滑")
        self.assertLessEqual(route.max_turn_deg, 60.0 + 1e-6)
        points = route.waypoints_xyz
        for i in range(len(points) - 1):
            self.assertTrue(planner.segment_free_3d(points[i], points[i + 1]),
                            f"平滑后第 {i} 段不满足净空")

    def test_infinite_column_forces_horizontal_detour(self):
        from src.worldmap import Obstacle, WorldMap

        world = WorldMap(source=Path("virt4.json"), name="virt4", width=1000.0, height=800.0,
                         obstacles=[Obstacle(id="t", name="Tower", type="circle",
                                             center=(500.0, 400.0), height=0.0, radius=50.0)],
                         targets=[], drones=[])
        planner = Planner(world, clearance=20.0, planning_dim="3d", altitude=40.0, max_altitude=80.0)
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=40.0)
        self.assertTrue(route.detoured)
        self.assertEqual(route.overfly, [])
        for xyz in route.waypoints_xyz:
            self.assertGreaterEqual(((xyz[0] - 500) ** 2 + (xyz[1] - 400) ** 2) ** 0.5, 20.0 - 1e-6)


class ThreeDSmoothingTest(unittest.TestCase):
    def test_max_waypoints_respected(self):
        from src.worldmap import Obstacle, WorldMap

        world = WorldMap(source=Path("virt5.json"), name="virt5", width=1000.0, height=800.0,
                         obstacles=[Obstacle(id="w", name="Wall", type="circle",
                                             center=(500.0, 400.0), height=20.0, radius=80.0)],
                         targets=[], drones=[])
        planner = Planner(world, clearance=15.0, planning_dim="3d", altitude=18.0,
                          max_altitude=80.0, altitude_step=10.0, z_min=18.0,
                          max_waypoints=12, smooth_iterations=2)
        route = planner.plan((100.0, 400.0), (900.0, 400.0), altitude=18.0)
        self.assertLessEqual(len(route.waypoints_xyz), 12)


class CoveragePlannerTest(unittest.TestCase):
    def test_serpentine_coverage_above_95(self):
        from src.coverage import estimate_coverage, serpentine_route

        center, radius, spacing = (500.0, 400.0), 80.0, 10.0
        points = serpentine_route(center, radius, spacing)
        lanes = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
        coverage = estimate_coverage(center, radius, lanes, spacing)
        self.assertGreaterEqual(coverage, 0.95, f"覆盖率偏低: {coverage}")

    def test_points_inside_circle(self):
        from src.coverage import serpentine_route

        center, radius = (500.0, 400.0), 60.0
        for x, y in serpentine_route(center, radius, 15.0):
            self.assertLessEqual(math.dist((x, y), center), radius + 1e-6)


class ProxyLogicTest(unittest.TestCase):
    def _load(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("uav_proxy_t", ROOT / "uav_adapter_proxy.py")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(ROOT))
        spec.loader.exec_module(module)
        return module

    def test_weather_rewritten(self):
        module = self._load()
        data = module.rewrite_environment({"weather": "heavy rain", "wind_speed": 8.0, "temperature": 15.0})
        self.assertEqual(data["weather"], "clear")
        self.assertLess(data["wind_speed"], 1.0)

    def test_takeoff_conflict_detection(self):
        module = self._load()
        self.assertTrue(module.should_fallback_to_change_altitude(
            400, {"status": "error", "message": "Drone is currently hovering"}))
        self.assertFalse(module.should_fallback_to_change_altitude(
            200, {"status": "success", "message": "Drone taking off"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
