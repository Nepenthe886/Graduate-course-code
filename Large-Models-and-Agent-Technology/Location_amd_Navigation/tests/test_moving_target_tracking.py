"""移动目标追踪：解析生成 track_target + 节点行为测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.bt import Status  # noqa: E402
from src.bt_defs import TrackTargetNode, build_mission  # noqa: E402
from src.mission_run import _targets_reach_spec  # noqa: E402
from src.worldmap import WorldMap  # noqa: E402

MAP = (ROOT.parent / "MultiUAV-Plat-v0.49_Windows" / "UAVSessions"
       / "3. canyon_switchback_coordination.json")
WORLD = WorldMap.load(MAP)

TEXT = ("take off necessary drones and assigned them to all targets until the session "
        "task progress exceeds 95%.")


class _FakeApi:
    def drone(self, _drone_id):
        return {"position": {"x": 0.0, "y": 0.0, "z": 20.0}, "status": "hovering"}

    def target(self, _target_id):
        return {"id": "t1", "name": "Mobile Courier 1", "radius": 3.0,
                "position": {"x": 1.0, "y": 1.0, "z": 20.0}}


class MovingTargetSpecTest(unittest.TestCase):
    def test_all_targets_include_track_actions(self):
        spec = _targets_reach_spec(TEXT, WORLD, 20.0, False)
        self.assertIsNotNone(spec)
        tracks = [item for item in spec["actions"]
                  if item.get("name") == "track_target"]
        self.assertEqual(len(tracks), 2)
        ids = {item["params"]["target_id"] for item in tracks}
        moving = {t.id for t in WORLD.targets if str(getattr(t, "type", "")) == "moving"}
        self.assertEqual(ids, moving)


class TrackTargetNodeTest(unittest.TestCase):
    def test_reaches_near_target(self):
        node = TrackTargetNode(target_id="t1", max_distance=5.0, timeout=2.0)
        board = {"api": _FakeApi(), "drone_id": "d1", "results": [], "world": WORLD}
        self.assertEqual(node.tick(board), Status.SUCCESS)

    def test_build_mission_registers_track_target(self):
        _, steps = build_mission(WORLD, {
            "altitude": 20.0, "ensure_takeoff": False,
            "actions": [{"name": "track_target",
                         "params": {"target": "Mobile Courier 1", "drone_id": "Alpha"}}]})
        self.assertEqual(steps[0]["kind"], "track_target")


if __name__ == "__main__":
    unittest.main()
