"""Basic UAV tools for the student baseline.

Each tool calls one UAV API operation. Parameterized tools accept JSON strings.
"""
import json
import re

from langchain.tools import tool
from uav_api_client import UAVAPIClient


def _bool_param(params: dict, key: str, default: bool) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


def _normalize_waypoint(waypoint: dict) -> dict:
    source = waypoint.get("coordinates") if isinstance(waypoint.get("coordinates"), dict) else waypoint
    normalized = {
        "x": float(source["x"]),
        "y": float(source["y"]),
    }
    if "z" in source:
        normalized["z"] = float(source["z"])
    return normalized


def _normalize_waypoints(waypoints: list[dict]) -> list[dict]:
    return [_normalize_waypoint(waypoint) for waypoint in waypoints]


# ---------------------------------------------------------------------------
# 名字 -> 真实 id 解析代理
# 说明：本地小模型经常把 "Drone 1" 这样的名字当 id 传给 API（导致 404）。
#       这里在工具层兜底：参数名是 drone_id / target_drone_id 时自动解析成 id。
# ---------------------------------------------------------------------------
import inspect as _inspect


class _IdResolvingClient:
    """包一层 UAVAPIClient：调用前把 drone 名字/模糊编号解析成真实 id。"""

    _DRONE_KEYS = ("drone_id", "target_drone_id")

    def __init__(self, client):
        self._client = client
        self._id_by_name: dict = {}

    # ---- 归一化：去掉空格/连字符/斜杠，小写，并去掉数字段前导零 ----
    @staticmethod
    def _norm(value) -> str:
        text = str(value).strip().lower()
        text = "".join(ch for ch in text if ch.isalnum())
        text = re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)
        return text

    @staticmethod
    def _looks_like_id(value: str) -> bool:
        return len(value) == 8 and all(c in "0123456789abcdefABCDEF" for c in value)

    def _refresh(self) -> None:
        try:
            drones = self._client.list_drones() or []
        except Exception:
            drones = []
        mapping: dict = {}
        for drone in drones:
            if not isinstance(drone, dict):
                continue
            drone_id = drone.get("id") or drone.get("drone_id")
            name = drone.get("name")
            if not drone_id:
                continue
            mapping[self._norm(drone_id)] = drone_id
            if name:
                mapping[self._norm(name)] = drone_id
        self._id_by_name = mapping

    def resolve_drone(self, value):
        if not isinstance(value, str) or not value.strip():
            return value
        raw = value.strip()
        # 1) 精确 8 位 id
        if self._looks_like_id(raw):
            return raw
        key = self._norm(raw)
        # 2) 直接命中（名字或 id 归一化后）
        if key in self._id_by_name:
            return self._id_by_name[key]
        # 3) 刷新一次再试
        self._refresh()
        if key in self._id_by_name:
            return self._id_by_name[key]
        # 4) 纯数字 / 带编号：drone-003 / 3 / 003 -> drone3
        digits = re.findall(r"\d+", raw)
        if digits:
            candidate = "drone" + str(int(digits[-1]))
            if candidate in self._id_by_name:
                return self._id_by_name[candidate]
        # 5) 子串包含匹配
        for name, drone_id in self._id_by_name.items():
            if key and (key in name or name in key):
                return drone_id
        return raw  # 解析不了就原样传，让 API 报错

    # ---- 代理 ----
    def __getattr__(self, item):
        attr = getattr(self._client, item)
        if not callable(attr) or item.startswith("_"):
            return attr
        try:
            signature = _inspect.signature(attr)
        except (TypeError, ValueError):
            return attr

        def wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            for key in list(bound.arguments):
                if key in self._DRONE_KEYS:
                    bound.arguments[key] = self.resolve_drone(bound.arguments[key])
            return attr(*bound.args, **bound.kwargs)

        wrapper.__name__ = getattr(attr, "__name__", item)
        wrapper.__doc__ = getattr(attr, "__doc__", None)
        return wrapper


def create_uav_tools(client: UAVAPIClient) -> list:
    """Create basic query and control tools without automatic mission planning."""
    client = _IdResolvingClient(client)  # 名字 -> id 兜底

    @tool
    def list_drones() -> str:
        """List all available drones in the current session with their status, battery level, and position.
        Use this to see what drones are available before trying to control them.

        No input required."""
        try:
            drones = client.list_drones()
            return json.dumps(drones, indent=2)
        except Exception as e:
            return f"Error listing drones: {str(e)}"

    @tool
    def get_session_info() -> str:
        """Get current session information including task type, statistics, and status.
        Use this to understand what mission you need to complete.

        No input required."""
        try:
            session = client.get_current_session()
            return json.dumps(session, indent=2)
        except Exception as e:
            return f"Error getting session info: {str(e)}"

    @tool
    def get_weather() -> str:
        """Get current weather conditions including wind speed, visibility, and weather type.
        Check this before takeoff to ensure safe flying conditions.

        No input required."""
        try:
            weather = client.get_weather()
            return json.dumps(weather, indent=2)
        except Exception as e:
            return f"Error getting weather: {str(e)}"

    @tool
    def get_drone_status(input_json: str) -> str:
        """Get detailed status of a specific drone including position, battery, heading, and visited targets.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            status = client.get_drone_status(drone_id)
            return json.dumps(status, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error getting drone status: {str(e)}"

    @tool
    def get_target_info(input_json: str) -> str:
        """Get detailed information for one specific target after discovering its ID from get_nearby_entities.

        Input should be a JSON string with:
        - target_id: The ID of the target (required)

        Example: {{"target_id": "target-area-1"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            target_id = params.get("target_id")

            if not target_id:
                return "Error: target_id is required"

            target = client.get_target(target_id)
            return json.dumps(target, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"target_id\": \"target-area-1\"}}"
        except Exception as e:
            return f"Error getting target info: {str(e)}"

    @tool
    def get_obstacle_info(input_json: str) -> str:
        """Get detailed information for one specific obstacle after discovering its ID from get_nearby_entities.

        Input should be a JSON string with:
        - obstacle_id: The ID of the obstacle (required)

        Example: {{"obstacle_id": "obstacle-1"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            obstacle_id = params.get("obstacle_id")

            if not obstacle_id:
                return "Error: obstacle_id is required"

            obstacle = client.get_obstacle(obstacle_id)
            return json.dumps(obstacle, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"obstacle_id\": \"obstacle-1\"}}"
        except Exception as e:
            return f"Error getting obstacle info: {str(e)}"

    @tool
    def get_nearby_entities(input_json: str) -> str:
        """Get drones, targets, and obstacles near a specific drone (within its perception radius).

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            nearby = client.get_nearby_entities(drone_id)
            return json.dumps(nearby, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error getting nearby entities: {str(e)}"

    @tool
    def land(input_json: str) -> str:
        """Command a drone to land at its current position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.land(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error during landing: {str(e)}"

    @tool
    def hover(input_json: str) -> str:
        """Command a drone to hover at its current position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - duration: Optional duration in seconds to hover (optional)

        Example: {{"drone_id": "drone-001", "duration": 5.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            duration = params.get('duration')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.hover(drone_id, duration)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error hovering: {str(e)}"

    @tool
    def return_home(input_json: str) -> str:
        """Command a drone to return to its home position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.return_home(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error returning home: {str(e)}"

    @tool
    def set_home(input_json: str) -> str:
        """Set the drone's current position as its new home position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.set_home(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error setting home: {str(e)}"

    @tool
    def calibrate(input_json: str) -> str:
        """Calibrate the drone's sensors.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.calibrate(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error calibrating: {str(e)}"

    @tool
    def take_photo(input_json: str) -> str:
        """Command a drone to take a photo.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)

        Example: {{"drone_id": "drone-001"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')

            if not drone_id:
                return "Error: drone_id is required"

            result = client.take_photo(drone_id)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\"}}"
        except Exception as e:
            return f"Error taking photo: {str(e)}"

    @tool
    def take_off(input_json: str) -> str:
        """Command a drone to take off to a specified altitude.
        Drone must be on ground (idle or ready status).

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - altitude: Target altitude in meters (optional, default: 10.0)

        Example: {{"drone_id": "drone-001", "altitude": 15.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            altitude = params.get('altitude', 10.0)

            if not drone_id:
                return "Error: drone_id is required"

            result = client.take_off(drone_id, altitude)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"altitude\": 15.0}}"
        except Exception as e:
            return f"Error during takeoff: {str(e)}"

    @tool
    def change_altitude(input_json: str) -> str:
        """Change a drone's altitude while maintaining X/Y position.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - altitude: Target altitude in meters (required)

        Example: {{"drone_id": "drone-001", "altitude": 20.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            altitude = params.get('altitude')

            if not drone_id:
                return "Error: drone_id is required"
            if altitude is None:
                return "Error: altitude is required"

            result = client.change_altitude(drone_id, altitude)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"altitude\": 20.0}}"
        except Exception as e:
            return f"Error changing altitude: {str(e)}"

    @tool
    def rotate(input_json: str) -> str:
        """Rotate a drone to face a specific direction.
        0=North, 90=East, 180=South, 270=West.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - heading: Target heading in degrees 0-360 (required)

        Example: {{"drone_id": "drone-001", "heading": 90.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            heading = params.get('heading')

            if not drone_id:
                return "Error: drone_id is required"
            if heading is None:
                return "Error: heading is required"

            result = client.rotate(drone_id, heading)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"heading\": 90.0}}"
        except Exception as e:
            return f"Error rotating: {str(e)}"

    @tool
    def send_message(input_json: str) -> str:
        """Send a message from one drone to another.

        Input should be a JSON string with:
        - drone_id: The ID of the sender drone (required)
        - target_drone_id: The ID of the recipient drone (required)
        - message: The message content (required)

        Example: {{"drone_id": "drone-001", "target_drone_id": "drone-002", "message": "Hello"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            target_drone_id = params.get('target_drone_id')
            message = params.get('message')

            if not drone_id:
                return "Error: drone_id is required"
            if not target_drone_id:
                return "Error: target_drone_id is required"
            if not message:
                return "Error: message is required"

            result = client.send_message(drone_id, target_drone_id, message)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"target_drone_id\": \"drone-002\", \"message\": \"...\"}}"
        except Exception as e:
            return f"Error sending message: {str(e)}"

    @tool
    def broadcast(input_json: str) -> str:
        """Broadcast a message from one drone to all other drones.

        Input should be a JSON string with:
        - drone_id: The ID of the sender drone (required)
        - message: The message content (required)

        Example: {{"drone_id": "drone-001", "message": "Alert"}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            message = params.get('message')

            if not drone_id:
                return "Error: drone_id is required"
            if not message:
                return "Error: message is required"

            result = client.broadcast(drone_id, message)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"message\": \"...\"}}"
        except Exception as e:
            return f"Error broadcasting: {str(e)}"

    @tool
    def charge(input_json: str) -> str:
        """Command a drone to charge its battery.
        Drone must be landed at a charging station.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - charge_amount: Amount to charge in percent (required)

        Example: {{"drone_id": "drone-001", "charge_amount": 25.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            charge_amount = params.get('charge_amount')

            if not drone_id:
                return "Error: drone_id is required"
            if charge_amount is None:
                return "Error: charge_amount is required"

            result = client.charge(drone_id, charge_amount)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"charge_amount\": 25.0}}"
        except Exception as e:
            return f"Error charging: {str(e)}"

    @tool
    def move_towards(input_json: str) -> str:
        """Move a drone a specific distance in a direction.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - distance: Distance to move in meters (required)
        - heading: Heading direction in degrees 0-360 (optional, default: current heading)
        - dz: Vertical component in meters (optional)

        Example: {{"drone_id": "drone-001", "distance": 10.0, "heading": 90.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            distance = params.get('distance')
            heading = params.get('heading')
            dz = params.get('dz')

            if not drone_id:
                return "Error: drone_id is required"
            if distance is None:
                return "Error: distance is required"

            result = client.move_towards(drone_id, distance, heading, dz)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"distance\": 10.0}}"
        except Exception as e:
            return f"Error moving towards: {str(e)}"

    @tool
    def move_along_path(input_json: str) -> str:
        """Move a drone along an explicit list of 2D or 3D waypoints.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - waypoints: List of points with x/y and optional z coordinates (required)
        - allow_partial_move: Whether the server may stop at the last reachable waypoint (optional, default true).
          If partial movement occurs, the response may report partial_success instead of success.

        Example: {{"drone_id": "drone-001", "waypoints": [{{"x": 10, "y": 10}}, {{"x": 20, "y": 20}}]}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            waypoints = params.get('waypoints')
            allow_partial_move = _bool_param(params, 'allow_partial_move', True)
            if not drone_id:
                return "Error: drone_id is required"


            if not waypoints:
                return "Error: waypoints list is required"

            normalized_waypoints = _normalize_waypoints(waypoints)
            result = client.move_along_path(
                drone_id,
                normalized_waypoints,
                allow_partial_move=allow_partial_move,
            )
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return (
                f"Error parsing JSON input: {str(e)}. Expected format: "
                "{\"drone_id\": \"drone-001\", \"waypoints\": [{\"x\": 10, \"y\": 10}]}"
            )
        except Exception as e:
            return f"Error moving along path: {str(e)}"

    @tool
    def move_to(input_json: str) -> str:
        """Move a drone to specific 3D coordinates (x, y, z).
        Check nearby obstacles with get_nearby_entities before moving.

        Input should be a JSON string with:
        - drone_id: The ID of the drone (required)
        - x: Target X coordinate in meters (required)
        - y: Target Y coordinate in meters (required)
        - z: Target Z coordinate (altitude) in meters (required)

        Example: {{"drone_id": "drone-001", "x": 100.0, "y": 50.0, "z": 20.0}}
        """
        try:
            params = json.loads(input_json) if isinstance(input_json, str) else input_json
            drone_id = params.get('drone_id')
            x = params.get('x')
            y = params.get('y')
            z = params.get('z')

            if not drone_id:
                return "Error: drone_id is required"
            if x is None or y is None or z is None:
                return "Error: x, y, and z coordinates are required"

            result = client.move_to(drone_id, x, y, z)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError as e:
            return f"Error parsing JSON input: {str(e)}. Expected format: {{\"drone_id\": \"drone-001\", \"x\": 100.0, \"y\": 50.0, \"z\": 20.0}}"
        except Exception as e:
            return f"Error moving drone: {str(e)}"


    return [
        list_drones,
        get_session_info,
        get_weather,
        get_drone_status,
        get_target_info,
        get_obstacle_info,
        get_nearby_entities,
        land,
        hover,
        return_home,
        set_home,
        calibrate,
        take_photo,
        take_off,
        change_altitude,
        rotate,
        send_message,
        broadcast,
        charge,
        move_towards,
        move_along_path,
        move_to,
    ]
