# 启动说明（STARTUP）

本目录下 `start_all.py` / `start_all.cmd` 统一管理所有启动命令。

## 一、原来手动启动的四类命令

| # | 组件 | 端口 | 原命令 | 说明 |
|---|---|---|---|---|
| 1 | **UAV 后端** | 8000 | `cd MultiUAV-Plat-v0.46_Windows` → `Start-Server-UTF8.cmd`（或 `chcp 65001 && MultiUAV-Plat.Server.v0.46.exe`） | **必须 UTF-8 控制台**，否则该 exe 打印 `©` 会 `UnicodeEncodeError` 崩溃 |
| 2 | **本地大模型** | 8010 | `cd Qwen3-7B` → `scripts\run_server.cmd`（内部：`.venv\Scripts\python.exe -m src.server`） | llama.cpp + GGUF Q4_K_M，32k 上下文，`--reasoning off`；需先跑过 `setup_env.cmd`/`setup_runtime.cmd`/`download_model.cmd` |
| 3 | **agent GUI** | — | `cd agent4drone\agent4drone` → `py -3.12 main.py` | 用 PIM 的 Python 3.12；启动时自动加载导航模块（`nav_settings.json`） |
| 4 | **导航模块** | 无 | **不需要单独启动** | 作为库被 agent 进程加载，暴露 7 个 `nav_*` 工具，不占端口 |

> 导航模块自带离线 CLI（不依赖 1/2/3）：`cd Location_amd_Navigation` → `py -3.12 -m src.cli maps / show / check / plan / route / pose`

## 二、一键启动

```bat
:: 方式 A：双击
start_all.cmd

:: 方式 B：命令行
py -3.12 start_all.py
```

执行顺序（与手动一致，且带就绪检测）：

```
预检（文件是否齐全）
  → 8000 UAV 后端（已在跑则跳过）：新控制台 + chcp 65001，等待 GET /drones 返回列表
  → 8010 本地模型（已在跑则跳过）：新控制台，等待 GET /health 返回 status=ok
  → 打印状态
  → py -3.12 main.py 启动 agent GUI
```

## 三、参数

| 参数 | 作用 |
|---|---|
| （无） | 起两个服务 + agent |
| `--no-agent` | 只起两个服务，不弹 GUI |
| `--status` | 只报告状态（端口/PID/健康/文件是否齐全） |
| `--dry-run` | 只打印将要执行的命令 |
| `--stop` | 停止 8000 与 8010（不动已开的 agent 窗口） |
| `--restart` | 先停再起（清掉卡住的实例） |
| `--uav-timeout 120` / `--llm-timeout 180` | 就绪等待上限（秒） |

## 四、成功判据

| 组件 | 判据 |
|---|---|
| UAV 后端 | `GET http://127.0.0.1:8000/drones` 返回列表 |
| 本地模型 | `GET http://127.0.0.1:8010/health` → `{"status":"ok"}`；浏览器 `http://127.0.0.1:8010` 有 Web UI |
| 导航模块 | agent 窗口日志：`已注入导航工具 7 个（模块 ...\Location_amd_Navigation）` |
| 工具总数 | agent 窗口：`✅ Created 29 tools`（22 UAV + 7 nav） |

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| UAV 窗口秒退，报 `UnicodeEncodeError: 'gbk' ... ©` | 没用 UTF-8 控制台。用 `start_all.py` / `Start-Server-UTF8.cmd`，别直接双击 exe |
| 模型窗口报 `端口 8010 已被占用` | 旧实例没退：`py -3.12 start_all.py --stop` 或 `--restart` |
| `缺少 llama-server / GGUF 模型` | 在 `Qwen3-7B` 里先跑 `scripts\setup_runtime.cmd` / `scripts\download_model.cmd` |
| agent 里只有 22 tools | `nav_settings.json` 缺失或 `enabled:false`，或 `module_dir` 路径不对 |
| agent 规划的障碍和场景不符 | `nav_settings.json` 的 `map` 要与 UAV 后端当前会话一致（当前 = `Area Search Easy 3` / `9ddae16c`） |
| 模型报 `exceeds the available context size` | 上下文不够，改 `Qwen3-7B/configs/default.json` 的 `context_size`（现为 32768） |

## 六、文件位置

| 文件 | 位置 |
|---|---|
| 启动器 | `Large-Models-and-Agent-Technology/start_all.py` |
| 双击包装 | `Large-Models-and-Agent-Technology/start_all.cmd` |
| 启动日志 | `Large-Models-and-Agent-Technology/logs/start_all_YYYYMMDD.log` |
| 导航配置 | `agent4drone/agent4drone/nav_settings.json` |
| 模型配置 | `Qwen3-7B/configs/default.json` |

## 七、导航地图如何选定（v2：自动跟随）

gent4drone/agent4drone/nav_settings.json 的 `map` 字段：

| 取值 | 行为 |
|---|---|
| `null` 或 `"auto"`（默认） | agent 启动时查 UAV 后端 `/sessions/current`，按 session id 在 `MultiUAV-Plat-v0.46_Windows/UAVSessions/*_{id}.json` 找地图；找不到则用导航模块的 `default_map` |
| 相对路径（如 `../X.json`） | 固定使用该地图，忽略会话 |

注意：**内置会话**（如 `Example Session`，服务内存里、UAVSessions 无文件）无法自动匹配，
启动器会在“会话一致性”里报 `[WARN]`；此时请二选一：
1. 在 Controller GUI 里切换到磁盘上的会话（如 `Area Search Easy 3`）；
2. 把 `map` 改成固定相对路径。

## 八、适配代理（可选，用于修复"红任务"）

官方 agent4drone 不能改，因此把修复放在网络层（都是本仓库自己的代码）：

| 代理 | 端口 | 作用 | 启动 |
|---|---|---|---|
| UAV 适配代理 | 8001 → 8000 | 天气改写为 clear；`take_off` 遇"悬停"自动转 `change_altitude` | `py -3.12 Location_amd_Navigation\uav_adapter_proxy.py --port 8001` |
| LLM 适配代理 | 8011 → 8010 | 注入规则（天气仅供参考/多机逐机/长航点一次发完/无动作不得报完成）；纠正"假完成"与 FAILED/DONE 冲突 | `py -3.12 Location_amd_Navigation\llm_adapter_proxy.py --port 8011` |

启用方式（**只改 GUI 配置，不改代码**）：

1. agent GUI → **Settings → UAV API 地址**：`http://127.0.0.1:8001`
2. agent GUI → **Settings → LLM base_url**：`http://127.0.0.1:8011/v1`
3. 重启 agent 生效

两个代理都是透明转发（除上述改写），停止代理即可恢复直连。
