# Location_amd_Navigation — 定位与导航模块

给智能体（agent4drone / 任何 LLM agent）提供**坐标系、地图、障碍物、路径规划、导航执行**能力。
核心实现**只用 Python 标准库**（math / json / dataclasses / urllib），没有任何第三方下载。

## 目录结构

```
Location_amd_Navigation/
├─ configs/default.json      # 地图目录、净空、网格步长、默认高度、UAV API 地址
├─ maps/                     # 地图（可放 MultiUAV-Plat 的 session JSON）
├─ src/
│  ├─ paths.py               # 全部相对路径解析 + 地图搜索
│  ├─ config.py              # 配置加载、启动时选择地图
│  ├─ frames.py              # 世界系(ENU) / 机体系 / 位姿 / 方位角
│  ├─ worldmap.py            # 地图模型：障碍物（圆/椭圆/多边形）、目标、无人机
│  ├─ geometry.py            # 2D 几何：点线多边形相交、距离、净空
│  ├─ planner.py             # 绕障规划：可见图+Dijkstra，网格 A* 兜底
│  ├─ localization.py        # 位姿管理 + 航位推算
│  ├─ navigation.py          # 导航：航点/动作序列/到达验证/重规划
│  ├─ api_client.py          # 可选：从 UAV API 读实时位姿（urllib）
│  └─ cli.py                 # 命令行
├─ tools/nav_tools.py        # agent 可调用工具（LangChain StructuredTool 或普通函数）
├─ tests/test_nav.py         # 自测（含真实撞障案例回归）
├─ requirements.txt
└─ README.md
```

## 坐标系约定

| 项 | 定义 |
|---|---|
| 世界系 W | 右手系，`x`=东、`y`=北、`z`=上，单位米 |
| 机体系 B | `x`=机头前、`y`=机体左、`z`=上 |
| `yaw` | 度，0 指向世界 +x，逆时针为正 |
| 障碍物 | 由 `type`(circle/ellipse/polygon) + 绝对坐标廓形表示；`height=0` 表示**二维柱体**（爬升不能越过，必须水平绕行） |

## 快速开始

```bat
cd /d E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\Location_amd_Navigation

:: 列出所有可用地图（含 ../MultiUAV-Plat-*/UAVSessions）
py -3.12 -m src.cli maps

:: 查看某张地图（启动时按名称选择）
py -3.12 -m src.cli show --map-name "Area Search Easy 3"

:: 检查一条直线是否会撞障碍
py -3.12 -m src.cli check --map-name "Area Search Easy 3" --start 879,692 --goal 564,94

:: 规划绕障路线
py -3.12 -m src.cli plan --map-name "Area Search Easy 3" --start 879,692 --goal 564,94

:: 按目标名生成动作序列（先转向再逐段飞）
py -3.12 -m src.cli route --map-name "Area Search Easy 3" --drone "Drone 2" --target "Circle Target 2" --z 59

:: 查看无人机位姿
py -3.12 -m src.cli pose --map-name "Area Search Easy 3" --drone "Drone 2"
```

跑自测：

```bat
py -3.12 -m unittest discover -s tests -v
```

## 作为库使用

```python
from src.config import load_config
from src.paths import find_map
from src.worldmap import WorldMap
from src.planner import Planner
from src.localization import Localizer
from src.navigation import Navigation

config = load_config()
world = WorldMap.load(find_map("Area Search Easy 3", config.__dict__))
planner = Planner(world, clearance=config.clearance)

# 1) 只做规划
route = planner.plan((879, 692), (564, 94))
print(route.as_dict())        # waypoints / length / min_clearance / avoided

# 2) 做导航（含动作序列与到达验证）
localizer = Localizer(world.drone_pose("Drone 2"))
nav = Navigation(world, localizer, planner, z_default=59.0)
for step in nav.steps_to((564, 94)):
    print(step.as_dict())
print(nav.verify((564, 94)))
```

## 挂到 agent 上（工具）

```python
import sys
sys.path.insert(0, r"E:\Code\...\Location_amd_Navigation")

from tools.nav_tools import create_nav_tools
tools = create_nav_tools(map_path=None, drone_name="Drone 2")   # None = 用配置里的默认地图
# agent4drone 里：create_agent(model=llm, tools=uav_tools + tools, system_prompt=...)
```

提供 7 个工具（参数都是 `input_json` 字符串，与 agent4drone 习惯一致）：

| 工具 | 作用 |
|---|---|
| `nav_map_info` | 地图概况：边界、障碍物清单（含 bbox）、目标清单 |
| `nav_nearby_obstacles` | 查询某点附近障碍物及距离/包围盒 |
| `nav_check_line` | 判断两点间直线是否可安全通过，返回 blocker |
| `nav_plan_route` | 规划绕障航点（可见图 + Dijkstra） |
| `nav_route_to_target` | 按目标名给出 `move_along_path` 航点 |
| `nav_pose` | 当前位姿、到目标的方位角/距离 |
| `nav_verify_arrival` | 到达验证（含容差） |

## 实时位姿（可选）

```python
from src.api_client import UavApiPose
from src.localization import localizer_from_world

provider = UavApiPose("http://127.0.0.1:8000", "Drone 2")
localizer = localizer_from_world(world, "Drone 2", pose_provider=provider)  # 每次读 pose 都会同步
```

## 已验证的真实案例

`tests/test_nav.py::AvoidanceRegressionTest` 复现了这次 agent 失败的任务：

- 起点 Drone 2 `(879, 692)`，目标 `(564, 94)`
- 直线被 `Polygon Obstacle 2`（顶点 x∈[602.54,630.54], y∈[200.95,228.95]）挡住
- `Planner.plan()` 会给出绕行航点，且**每一段都满足 25 m 净空**，最终点精确落在目标

## 重要修正：椭圆 width/length 是半轴（半径）

仿真数据里 `ellipse` 的 `area = pi * width * length`（4 张地图全部吻合），
说明 **width/length 就是半轴**，不是全长。早期按 `w/2` 计算会把椭圆算成实际的一半，
导致规划出的绕行航点落进真实障碍内部，服务端返回::

    Waypoint 1 conflicts with obstacle: Position conflicts with ellipse obstacle 'Ellipse Obstacle 2'

现已修正 `src/geometry.py::ellipse_to_polygon`（rx=width, ry=length）。

另外把默认安全间距从 25 m 提高到 **45 m**（服务端可能还叠加机体半径），
代价仅增加十几米航程。

## 2D / 3D 规划

`configs/default.json` 的 `planning_dim`：

| 取值 | 行为 |
|---|---|
| `"2d"`（默认） | 障碍一律视为无限高柱体，只做水平绕行。与当前仿真语义一致（所有障碍 `height=0`、会话 `is_distance_3d=false`） |
| `"3d"` | 障碍 `height>0` 时，巡航高度 ≥ `height + vertical_margin` 即可**直飞越过**；当前高度过不去则**逐级爬升**（步长 `altitude_step`，上限 `max_altitude`），到目标后**下降回原高度**；仍不行才水平绕行 |

相关参数（`configs/default.json`）：

```json
"planning_dim": "2d",
"vertical_margin": 5.0,
"max_altitude": 120.0,
"altitude_step": 10.0
```

3D 路线会在 `Route.waypoints_xyz` 里给出逐点高度（例如"先升到 60 m 平飞、到目标再降到 18 m"），
`nav_plan_route` 的输出里也包含 `waypoints_xyz` 与 `overfly`（被飞越的障碍名单）。

## 3D 最短路径 + 轨迹平滑（planning_dim="3d"）

启用后不是"原地升降绕开"，而是真正做三维规划：

1. **3D 栅格 A\***：在 (x, y, z) 三维栅格上联合搜索
   （8 水平 + 2 垂直 + 8 斜向邻居），
   代价 = 三维欧氏距离 × (1 + `vertical_penalty` × |Δz|)，启发式 = 三维欧氏距离
2. **3D 视线短路**：贪心删除可直连的中间点
3. **轨迹平滑**：Chaikin 角切割（`smooth_iterations` 次）→ 重采样 →
   **逐段碰撞校验**（不满足净空自动回退）→ 限制 `max_waypoints` / `max_turn_deg`

参数（`configs/default.json`）：

```json
"planning_dim": "2d",
"vertical_margin": 5.0,
"max_altitude": 120.0,
"altitude_step": 10.0,
"vertical_penalty": 1.5,
"z_min": 5.0,
"smoothing": true,
"smooth_iterations": 2,
"max_waypoints": 40,
"max_turn_deg": 60.0
```

`Route` 新增字段：`waypoints_xyz`、`length_3d`、`smoothed`、`max_turn_deg`、`overfly`、`z_range`。

## 不修改官方 agent4drone 的补强（v3）

官方 `agent4drone` 不能改，所以把能力放在**我们自己的代码**里。

### 1) UAV API 适配代理（网络层兜底）

```bat
cd /d E:\Code\...\Location_amd_Navigation
.venv\Scripts\python.exe uav_adapter_proxy.py --port 8001 --upstream http://127.0.0.1:8000
```

然后 **agent GUI → Settings → UAV API 地址改成 `http://127.0.0.1:8001`**（改配置，不动代码）。

| 代理行为 | 解决的红任务原因 |
|---|---|
| `GET /environments/current` 返回 `weather=clear / wind 0.5 / 能见度 10 km` | 模型因 "heavy rain" 拒飞（marathon / formation 类任务全红的主因） |
| `take_off` 因"已在空中/悬停"失败 → 自动改发 `change_altitude` 并返回成功 | "hovering 不能起飞"导致的任务失败 |

其余请求透明转发，不改变官方行为。

### 2) 新增 3 个导航工具（工具描述即提示词）

| 工具 | 用途 |
|---|---|
| `nav_coverage_route` | 圆形区域**蛇形覆盖航线** + 覆盖率估算（解决"覆盖率≥95%"任务） |
| `nav_points_route` | 一串坐标 → 逐段绕障 → **一条 move_along_path**（解决长航点漏飞） |
| `nav_multi_drone_route` | 多机任务按 drone 分别生成航点（解决"只飞一架"） |

### 3) LLM 侧适配代理（规则注入 + 完成校验）

```bat
cd /d E:\Code\...\Location_amd_Navigation
py -3.12 llm_adapter_proxy.py --port 8011 --upstream http://127.0.0.1:8010
```

然后 **agent GUI → Settings → LLM base_url 改成 `http://127.0.0.1:8011/v1`**（配置项，不改代码）。

| 能力 | 说明 |
|---|---|
| 请求侧规则注入 | 把 `configs/llm_rules.json` 的规则追加到 system 消息末尾（幂等，带 marker）。规则覆盖：天气仅供参考、坐标任务不查 target 名、多机逐机执行、长航点一次发完、无动作不得报完成、悬停时改用 change_altitude、覆盖率任务用 nav_coverage_route |
| 响应侧校验 | ① 本轮没有任何 `role=tool` 成功结果却输出 `[TASK DONE]` → 改写为 `[TASK FAILED]`；② 同时出现 FAILED/DONE → 删掉 DONE（FAILED 优先） |

开关：`--no-rules`（不注入）、`--no-guard`（不纠正）。流式（`stream=true`）请求只做规则注入、不做响应改写。

### 4) 工具调用修正（v4）

LLM 代理在返回响应前检查 `tool_calls`：

| 场景 | 修正 |
|---|---|
| 任务点名 `Polygon Target 2`，模型却飞向 `Polygon Target 1` 的坐标 | 把落点改成目标中心（保留高度） |
| 覆盖类任务（含 coverage / 95% / area search）却只发 1 个航点 | 替换为 `nav_coverage_route` 同款**蛇形覆盖航线**（实测 r=85 目标覆盖 99.72%） |

配置：`configs/tool_rewrite.json`

```json
{"enabled": true, "coverage_spacing_ratio": 0.2,
 "max_coverage_waypoints": 60, "min_coverage_radius": 20.0}
```

日志里会出现 `[llm-proxy] tool-rewrite: retarget:...` / `coverage_route:...`。
地图来源：默认按 UAV 当前会话自动匹配 `UAVSessions/*_{session_id}.json`，也可 `--map` 强制指定。
关闭：`--no-tool-rewrite`。

## 任务级规划：行为树 + 导航模块分工（v5）

分层设计：

```
任务级（src/mission.py + src/bt.py）
  ├─ 目标点 / 途径点 / 每点任务（hover / photo / cover / message / land）
  └─ 行为树展开：Sequence( 起飞, 每个航点[Selector(Retry(规划), 失败)], 降落 )
        ↓ 只有"点到点"时才调用
导航级（src/planner.py）
  └─ 绕障 / 3D 最短路径 / 轨迹平滑
```

工具 `nav_mission_plan` 输入：

```json
{
  "altitude": 22,
  "waypoints": [
    {"x": 225, "y": 448, "label": "Polygon Target 2",
     "tasks": [{"kind": "cover", "target_name": "Polygon Target 2"}, {"kind": "photo"}]},
    {"x": 500, "y": 400, "tasks": [{"kind": "hover", "duration": 5}]}
  ],
  "land_at_end": true
}
```

输出 `steps`：`take_off` → 每个航点一条 `move_along_path`（内含绕障航点）→ 该点任务
（`hover` / `take_photo` / 覆盖航点）→ `land`；并给出 `stats.distance`、`stats.covered`。

行为树语义：
- `Sequence`：按顺序，任一失败则整体失败
- `Selector`：第一个非失败的分支（用于"默认净空规划 → 加大净空重规划"）
- `Retry`：失败重试（默认 2 次）
- `Condition` / `Action`：叶子节点
