# 无人机任务系统分层架构

![架构图](architecture.png)

## 1. 总体调用链

```
用户任务文本
  → agent4drone Agent（官方运行时）
  → llm_adapter_proxy.py :8010（本地 LLM 适配/规则层）
  → llama-server :8012（Qwen3-8B-Q4_K_M.gguf，本地 llama.cpp）
  → tool_calls
  → nav_tools.py（工具层）
  → mission_run.build_spec_from_task（L0 文本 → 动作 spec）
  → bt_defs.build_mission（L2 行为树）
  → actions.py / planner.py（L3 原子动作与规划）
  → MultiUAV-Plat v0.49 REST :8000
```

## 2. 分层说明与主要文件

| 层 | 职责 | 位置 |
|---|---|---|
| L0 交互/任务下发 | GUI、任务文本、AutoCheck 校验 | `agent4drone/agent4drone/main.py`、`agent_api_service.py` |
| L1 Agent 运行时 | LLM 工具循环、prompt、工具注册 | `agent4drone/agent4drone/`（官方，不修改） |
| L2 LLM 接入层 | 本地模型代理、规则改写、熔断、任务互斥 | `Location_amd_Navigation/llm_adapter_proxy.py`、`configs/llm_rules.json` |
| L3 工具层 | 13 个导航工具的统一入口 | `Location_amd_Navigation/tools/nav_tools.py` |
| L4 任务解析/规划（L0–L1） | 文本 → spec；模板；路线排序；覆盖规划 | `src/mission_run.py`、`src/template_actions.py`、`src/route_order.py`、`src/coverage.py` |
| L5 执行层（L2） | 行为树节点、多机并行、超时/取消/清理 | `src/bt_defs.py`、`src/bt.py`、`src/mission_run.py` |
| L6 原子动作/规划（L3–L4） | 官方 11 命令封装、网格 A*/绕障/平滑/修复、自检 | `src/actions.py`、`src/planner.py`、`src/bt_defs.py`、`src/sensors.py`、`src/geometry.py`、`src/worldmap.py` |
| L7 仿真/校验 | REST API、会话地图、check 接口、日志 | `MultiUAV-Plat-v0.49_Windows/`（服务器、`UAVSessions/`、`server_logs/`） |

## 3. 关键设计

### 3.1 任务解析（L0→spec）

`build_spec_from_task` 支持多种任务形态并按优先级解析：

- 双机 `route:` 任务
- 编队 + 定向移动（north/south/east/west）
- 多机目标分配 / 交换任务
- 多机共同目标、引用式目标（same target）
- 目标列表 + 进度阈值（`progress` / `all targets`）
- 具名多目标巡检、单目标拍照
- 舰队/守护点/按机名分配坐标
- 官方模板、通用 waypoints

目标/无人机识别均基于**会话地图数据分类**，支持无编号命名（`Foxtrot`、`Alpha`）与多词名称（`Civilian utility inspection 04`）。

### 3.2 执行层（L2）

- `execute_actions`：行为树动作序列（多机按 drone_id 分组并行）；
- `execute_mission`：waypoints 逐段 JIT 规划执行；
- 关键节点：`TakeOff`、`NavigateToPose`、`MoveTo`、`MoveDirection`、`ApproachDrone`、`TrackTarget`、`InspectTarget`、`hover/photo/land`；
- 保护机制：任务级 deadline（默认 300 s）、取消事件、单方向 90 s 时限、失败清理 `land`、方向位移按比例余量。

### 3.3 规划与自检（L3/L4）

- `planner.py`：网格 A* / detour 图 Dijkstra、平滑、修复；
- `_route_blocker` / `_repair_route`：发送前自检与绕障点插入；
- 移动目标：`TrackTarget` 每 tick 读取目标当前位置并持续追踪；
- 配置：`clearance=0.3`、`drone_radius=0.2`、`arrival_tolerance=2.0`。

## 4. 生成方式

本图由脚本生成，可重复运行：

```powershell
cd Location_amd_Navigation
py -3.12 tools/draw_architecture.py
# 输出 docs/architecture.png
```
