# maps/

把要用的地图放这里，或者直接用 `configs/default.json` 里 `external_map_dirs` 指向的目录
（默认已经指向 `../MultiUAV-Plat-v0.46_Windows/UAVSessions`，即 MultiUAV-Plat 的会话文件）。

地图格式 = MultiUAV-Plat 的 session JSON（必须包含）：

| 字段 | 用途 |
|---|---|
| `name` / `id` | 地图标识 |
| `canvas_width` / `canvas_height` | 世界边界（米） |
| `obstacles[]` | `type`(circle/ellipse/polygon) + `position` + `radius`/`width`/`length`/`vertices` + `height` |
| `targets[]` | `position` + `radius` |
| `drones[]` | `position` + `heading` + `max_altitude`（用于初始化位姿） |

选择方式（启动时）：
```bat
:: 1) 直接给相对路径
.venv\Scripts\python.exe -m src.cli show --map maps\my_map.json

:: 2) 给地图名（自动在 map_dir 与 external_map_dirs 中查找）
.venv\Scripts\python.exe -m src.cli show --map-name "Area Search Easy 3"
```
