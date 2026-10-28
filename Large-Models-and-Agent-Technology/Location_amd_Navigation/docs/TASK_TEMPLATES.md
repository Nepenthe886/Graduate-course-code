# 任务模版 → 行为树动作库 对照表

来源：MultiUAV-Plat-v0.46_Windows/UAVSessions/*.json 的 	asks 字段，共 **75 个会话 / 1500 条任务实例 / 50 个模版**。

## 1. 动作库（src/actions.py）

### 原子动作（来自官方 related_apis 统计）

| 动作 | 官方接口 | 说明 | 使用次数 |
|---|---|---|---|
| take_off | POST /drones/{id}/command/take_off | 起飞到指定高度 | 2605 |
| land | POST /drones/{id}/command/land | 降落 | 430 |
| change_altitude | POST /drones/{id}/command/change_altitude | 改变高度（悬停/飞行中） | 38 |
| rotate | POST /drones/{id}/command/rotate | 旋转到指定朝向 | 27 |
| move_to | POST /drones/{id}/command/move_to | 飞到一个点 | 3357 |
| move_towards | POST /drones/{id}/command/move_towards | 朝一个方向/点持续推进（跟踪用） | 1917 |
| move_along_path | POST /drones/{id}/command/move_along_path | 沿航点序列飞行 | 167 |
| hover | POST /drones/{id}/command/hover | 悬停（可指定时长） | 738 |
| take_photo | POST /drones/{id}/command/take_photo | 拍照 | 844 |
| broadcast | POST /drones/{id}/command/broadcast | 广播消息（中继/通信任务） | 96 |
| return_home | POST /drones/{id}/command/return_home | 返航到 home 点 | 33 |

### 复合动作（由原子动作展开）

| 动作 | 展开为 | 说明 |
|---|---|---|
| coverage_search | (composite) move_along_path + take_photo | 对目标区域做蛇形覆盖搜索（覆盖类模版） |
| visit_targets | (composite) planner + move_along_path | 按顺序访问多个目标（多目标巡检/交付） |
| patrol | (composite) move_along_path | 巡逻闭合路线（四边形/周界巡逻） |
| formation | (composite) per-drone move_to | 编队集结（线形/三角形） |
| track_target | (composite) move_towards | 跟踪移动目标 |
| wait | (local) sleep | 等待/滞留 |

## 2. 模版清单与涉及动作

| # | 模版 | 实例数 | 难度 | 涉及动作 | 示例任务 |
|---|---|---|---|---|---|
| 1 | Complete Area Coverage Search | 67 | hard | take_off | Drone 2 executes comprehensive area search of Circle Target 1. Take off to 20 meters and p |
| 2 | Triangle Formation - 3 Drones | 50 | medium | hover, move_to, take_off | Drones Drone 3, Drone 2, and Drone 5 should take off to 21 meters altitude and form a tria |
| 3 | Sequential Multi-Target Visit | 49 | hard | hover, take_off, take_photo | Drone Drone 5 performs sequential multi-target mission: take off to 21 meters, visit targe |
| 4 | Line Formation - 2 Drones | 48 | medium | hover, move_to, take_off | Drones Drone 3 and Drone 5 form a line formation: both take off to 17 meters. Drone 3 posi |
| 5 | Flight Marathon Navigation | 44 | hard | land, move_to, take_off | Drone Drone 4 performs a marathon flight. Take off to 29 meters, fly to start point (108,  |
| 6 | Multiple Target Inspection - Advanced | 44 | hard | take_off, take_photo | Drones Drone 5, Drone 4, and Drone 1 must take off to 22 meters. Drone 5 photographs targe |
| 7 | Target Inspection | 43 | medium | take_off, take_photo | Drone 4 must take off to 18 meters, navigate to target Polygon Target 1, and take a photo. |
| 8 | Multiple Target Inspection | 40 | hard | take_off, take_photo | Drone Drone 5 must take off to 17 meters, navigate to target Polygon Target 2, take a phot |
| 9 | Multi-Target Survey Mission | 39 | medium | hover, take_off, take_photo | Survey mission: Drone Drone 5 surveys target Polygon Target 2 while drone Drone 4 surveys  |
| 10 | Fleet Coordinated Takeoff and Formation (3 Drones) | 39 | hard | move_to, move_towards, take_off | Three-drone coordination exercise: Drone 4, Drone 5, and Drone 1 take off together to 15 m |
| 11 | Simple Altitude Change | 38 | easy | change_altitude, hover, take_off | Drone Drone 4 should take off to 13 meters, then climb to 29 meters altitude and hover. |
| 12 | Complex Obstacle Course Navigation | 38 | hard | land, move_to, take_off | Drone Drone 2 navigates complex obstacle course from start (172, 186, 18) through waypoint |
| 13 | Patrol Mission - Quadrangle | 38 | medium | move_along_path, take_off | Drone Drone 4 should take off to 13 meters altitude, then fly a quadrangle patrol route vi |
| 14 | Fly Several Waypoints | 37 | medium | move_along_path, take_off | Drone Drone 3 should take off to 19 meters and fly through multiple waypoints: (261, 236,  |
| 15 | Fleet Target Sweep (3 Drones) | 37 | hard | move_towards, take_off | Three-drone target sweep: Drone 4, Drone 3, and Drone 2 take off together to 17 meters alt |
| 16 | Multi-Waypoint Delivery Route | 37 | medium | land, move_to, take_off | Drone Drone 3 executes delivery route: take off to 16 meters, fly to waypoint 1 at (240, 3 |
| 17 | Delivery to Specific Location | 36 | medium | land, move_to, take_off | Drone Drone 5 should take off to 14 meters altitude, fly to pickup point at (246, 233, 14) |
| 18 | Multi-Target Reach Verification | 33 | medium | hover, take_off | Drone Drone 3 multi-target verification mission: take off to 21 meters, visit and reach ta |
| 19 | Complete Flight Cycle Verification | 33 | easy | hover, land, move_to, return_home, take_off | Drone Drone 4 complete flight cycle test: verify full operational capability by taking off |
| 20 | Extended Distance Flight Test | 32 | medium | move_along_path, take_off | Drone Drone 3 performs extended distance flight test: take off to 17 meters, fly a route t |
| 21 | Obstacle Avoidance Navigation | 32 | medium | land, move_to, take_off | Drone Drone 2 takes off to 22 meters altitude, navigates from start point (304, 174, 22) t |
| 22 | Communication Relay with Messages | 32 | medium | broadcast, move_to, take_off | Communication relay mission: Drone Drone 1 acts as communication relay. Take off to 23 met |
| 23 | Fleet Target Sweep (5 Drones) | 32 | hard | move_towards, take_off | Five-drone target sweep: Drone 8, Drone 22, Drone 20, Drone 7, and Drone 10 take off toget |
| 24 | Fleet Coordinated Takeoff and Formation (4 Drones) | 31 | hard | move_to, move_towards, take_off | Four-drone coordination exercise: Drone 12, Drone 8, Drone 6, and Drone 2 take off togethe |
| 25 | Fleet Target Sweep (4 Drones) | 31 | hard | move_towards, take_off | Four-drone target sweep: Drone 11, Drone 1, Drone 8, and Drone 14 take off together to 20  |
| 26 | Quick Checkpoint Visit | 30 | easy | hover, move_to, take_off | Drone Drone 1 takes off to 17 meters, flies to checkpoint at (594, 558, 17), and hovers br |
| 27 | Extended Hover Observation | 30 | medium | hover, move_to, take_off | Drone Drone 1 stationary observation mission: take off to 100 meters, fly to observation p |
| 28 | Search Task with Photo | 30 | easy | hover, move_to, take_off, take_photo | Drone Drone 5 must take off to 23 meters, fly to position (702, 250, 23), hover at that lo |
| 29 | Dual Drone Patrol | 30 | medium | move_along_path, take_off | Drones Drone 1 and Drone 3 execute coordinated patrol. Both take off to 13 meters. Drone 1 |
| 30 | Dual Flight Marathon Navigation | 29 | hard | land, move_to, take_off | Drones Drone 1 and Drone 4 perform a dual marathon flight at 27 meters. Drone 1 route: fly |
| 31 | Dual Drone Area Coverage Search | 28 | hard | take_off | Drones Drone 1 and Drone 5 execute a coordinated area coverage search of Polygon Target 2. |
| 32 | Target Area Complete Coverage and Photography Mission | 27 | hard | take_off, take_photo | Drone Drone 4 comprehensive area coverage mission: take off to 24 meters altitude, perform |
| 33 | Simple Rotation Test | 27 | easy | hover, rotate, take_off | Drone Drone 2 takes off to 12 meters altitude, then rotates to face heading 350 degrees an |
| 34 | Randomized Loiter with Photo | 27 | easy | hover, move_to, take_off, take_photo | Drone Drone 1 takes off to 19 meters, flies to (929, 436, 19), loiters for 9 seconds, and  |
| 35 | Photo Documentation Mission | 26 | medium | move_to, take_off, take_photo | Drone Drone 1 performs photo documentation mission: take off to 18 meters, visit three way |
| 36 | Area Search Task Completion | 25 | hard | - | Take off necessary drones and search the assigned area targets with systematic coverage pa |
| 37 | Fleet Coordinated Takeoff and Formation (5 Drones) | 25 | hard | move_to, move_towards, take_off | Five-drone coordination exercise: Drone 2, Drone 21, Drone 11, Drone 12, and Drone 9 take  |
| 38 | Perimeter Search - 4 Drones | 24 | medium | hover, move_to, take_off, take_photo | Deploy drones Drone 19, Drone 23, Drone 7, and Drone 10 for perimeter search. All take off |
| 39 | Coordinated Search Sweep - 3 Drones | 24 | hard | take_off, take_photo | Drones Drone 24, Drone 18, and Drone 27 execute coordinated search sweep of target area Wa |
| 40 | Target Assignment 50% Progress With 3 Targets | 24 | hard | - | Take off necessary drones and assign them to reach required targets Fixed Target 2, Fixed  |
| 41 | Target Assignment Task Completion | 23 | hard | take_off | take off necessary drones and  assigned them to all targets (within task radius) until the |
| 42 | Basic Takeoff | 21 | easy | hover, take_off | Command drone Drone 4 to take off to an altitude of 12 meters and hover in place. |
| 43 | Extreme Flight Marathon Navigation | 18 | hard | land, move_to, take_off | Drone 3 performs an extreme marathon flight. Take off to 28 meters, fly to start point (11 |
| 44 | Any Drone Two-Target Area Coverage Search | 18 | hard | - | Use any available drone or drones to execute comprehensive area coverage searches of Polyg |
| 45 | Target Assignment 70% Progress With 5 Targets | 11 | hard | - | Take off necessary drones and assign them to reach required targets Fixed Target 10, Fixed |
| 46 | Triple Flight Marathon Navigation | 8 | hard | land, move_to, take_off | Drones Drone 29, Drone 5, and Drone 6 perform a triple marathon flight at 28 meters. Drone |
| 47 | Any Drone Area Coverage Search | 7 | hard | - | Use any available drone or drones to execute a comprehensive area coverage search of Circl |
| 48 | Triple Drone Area Coverage Search | 6 | hard | take_off | Drones Drone 6, Drone 4, and Drone 5 execute a coordinated area coverage search of Polygon |
| 49 | Two Find Two Support | 1 | hard | move_to, take_off | Assign drone 1 to target Fixed Target 2 and drone 2 to target Fixed Target 1. After those  |
| 50 | Cross Target Swap After Discovery | 1 | hard | move_to, take_off | Drone 3 should find target Fixed Target 2, and drone 2 should find target Fixed Target 1.  |

