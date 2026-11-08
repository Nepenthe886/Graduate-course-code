# 无人机 Action 库（由官方任务模板自动生成）

## 一、原子动作（官方命令，L3 薄封装）

| 动作 | 官方接口 | 参数 | 说明 |
|---|---|---|---|
| `take_off` | `POST /drones/{id}/command/take_off` | altitude | 起飞到指定高度 |
| `land` | `POST /drones/{id}/command/land` | - | 降落 |
| `change_altitude` | `POST /drones/{id}/command/change_altitude` | altitude | 改变高度 |
| `rotate` | `POST /drones/{id}/command/rotate` | heading | 旋转到指定朝向 |
| `move_to` | `POST /drones/{id}/command/move_to` | x, y, z | 飞到一个点（参数放 query） |
| `move_towards` | `POST /drones/{id}/command/move_towards` | x, y, z, distance | 朝一个点/方向持续推进 |
| `move_along_path` | `POST /drones/{id}/command/move_along_path` | waypoints, allow_partial_move | 沿航点序列飞行（body 传参） |
| `hover` | `POST /drones/{id}/command/hover` | duration | 悬停 |
| `take_photo` | `POST /drones/{id}/command/take_photo` | - | 拍照 |
| `broadcast` | `POST /drones/{id}/command/broadcast` | content | 广播消息 |
| `send_message` | `POST /drones/{id}/command/send_message` | target_drone_id, message | 给指定无人机发消息（中继任务） |
| `charge` | `POST /drones/{id}/command/charge` | charge_amount | 充电 |
| `return_home` | `POST /drones/{id}/command/return_home` | - | 返航 |

## 二、组合动作（每个官方模板一个，L1 只需调用一次）

| 模板 | 名称 | 类别 | 组合工具 | 参数 | 内部原子序列 |
|---|---|---|---|---|---|
| `altitude_stage` | Staged Altitude Change Corridor | Basics | `nav_template_action` | drone_id, alt1, alt2 | take_off → hover → change_altitude → hover |
| `grid_survey` | Grid Survey Coverage Sweep | Survey | `nav_template_action` | drone_id, corners, altitude, photos | take_off → move_to → take_photo → ... → hover |
| `target_photo` | Target Inspection Photo Pass | Inspection | `nav_template_action` | drone_id, x, y, altitude, photos | take_off → move_to → take_photo → take_photo → hover |
| `line_formation` | Two-Drone Line-Abreast Formation | Formation | `nav_template_action` | drones, altitude | take_off x2 → move_to x2 → hover x2 |
| `perimeter_patrol` | Perimeter Patrol With Return | Patrol | `nav_template_action` | drone_id, corners, altitude | take_off → move_to x4 → return_home → land |
| `relay_message` | Relay Link Message Handoff | Communication | `nav_template_action` | drone1, drone2, altitude, message | take_off x2 → move_to x2 → send_message → hover x2 |
| `corridor_detour` | Obstacle Corridor Detour Survey | Navigation | `nav_template_action` | drone_id, start, end, altitude, clearance, obstacle_center, photos, end_hover | take_off → move_to(start) → take_photo → move_along_path(detour) → take_photo → hover |
| `battery_cycle` | Battery Cycle And Ground Readiness | Logistics | `nav_template_action` | drone_id, altitude, charge_amount | take_off → hover → land → charge |
| `target_coverage` | Target Area Coverage Pass | Search | `nav_template_action` | drone_id, center, radius, altitude, sweep, coverage, photos | take_off → move_along_path(serpentine) → take_photo → hover |
| `emergency_land` | Emergency Broadcast And Fleet Landing | Safety | `nav_template_action` | drone_id, message, drones | broadcast → land xN |

## 三、调用方式

L1 模型只需一次工具调用：

```json
{"template": "corridor_detour",
 "params": {"drone_id": "Bravo", "start": [131, 156.3], "end": [881, 624.2],
            "altitude": 28.2, "clearance": 59, "photos": 2, "end_hover": true}}
```

工具内部会：起飞 → 到起点拍照 → 绕障碍航段 → 到终点拍照 → 悬停。
