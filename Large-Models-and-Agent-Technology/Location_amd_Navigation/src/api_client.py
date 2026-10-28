"""可选：从 MultiUAV-Plat API 读取实时位姿（纯 urllib，无第三方依赖）。"""
from __future__ import annotations

import json
import urllib.request

from .frames import Pose, pose_from_drone


class UavApiPose:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", drone_id_or_name: str = "", timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.target = drone_id_or_name
        self.timeout = timeout

    def _get(self, path: str):
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list_drones(self) -> list[dict]:
        return self._get("/drones")

    def find_drone(self) -> dict | None:
        drones = self.list_drones()
        low = self.target.strip().lower()
        for drone in drones:
            if str(drone.get("id", "")).lower() == low:
                return drone
        for drone in drones:
            if str(drone.get("name", "")).lower() == low:
                return drone
        return None

    def __call__(self) -> Pose | None:
        try:
            drone = self.find_drone()
        except Exception:
            return None
        return pose_from_drone(drone) if drone else None
