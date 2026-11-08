# 任务模板文件（10 个）

适用平台：MultiUAV-Plat v0.48（或更新版本）
文件格式：每个 JSON 为一个模板导出对象 `{"id": ..., "template": {...}}`，与平台 Session Manager / Template Editor 的 Import 格式一致。

导入方法：打开 MultiUAV-Plat Controller → Task Template → Import → 逐个选择 JSON；
也可以把 10 个 JSON 直接放进平台目录下的 `templates/` 文件夹，重启后出现在模板列表里。

| # | 模板名称 | 类别 | 难度 | 命令数 | 检查点数 | 适用任务类型 |
|---|---|---|---|---|---|---|
| 1 | Grid Survey Coverage Sweep | Survey | medium | 7 | 4 | area_search, area_assignment_and_patrol |
| 2 | Altitude Hold Checkpoint | Basics | easy | 2 | 2 | all |
| 3 | Target Inspection Photo Pass | Inspection | medium | 5 | 3 | target_assignment, target_tracking, area_search |
| 4 | Two-Drone Line-Abreast Formation | Formation | medium | 6 | 3 | others, area_assignment_and_patrol |
| 5 | Perimeter Patrol With Return | Patrol | medium | 4 | 4 | area_assignment_and_patrol |
| 6 | Relay Link Message Handoff | Communication | medium | 6 | 4 | others, area_assignment_and_patrol |
| 7 | Obstacle Corridor Detour Survey | Navigation | hard | 5 | 3 | area_search, others |
| 8 | Battery Cycle And Ground Readiness | Logistics | easy | 5 | 3 | all, others |
| 9 | Target Area Coverage Pass | Search | hard | 5 | 3 | target_assignment, area_search |
| 10 | Emergency Broadcast And Fleet Landing | Safety | medium | 4 | 3 | all, others |

## 检查点（execution_check_apis）设计说明

1. 每条检查都用平台 `/check/*` 端点，参数可被平台自动判定，不写主观描述。
2. 阈值与任务几何必须自洽：地形移动坐标固定写死，距离类检查的 max_distance 按实际间距留 5%–10% 余量，
   避免出现「按任务描述完成却判不过」的情况。
3. 用 `expect: false` 表达「不该发生」：例如避障任务检查 `obstacle_within_drone_distance` 为 false，
   即无人机全程不得贴近障碍物。
4. 同一任务内多检查用 `logic: "and"`，全部满足才计通过；需要任选其一时用嵌套 `or` 组。
5. 高度、悬停、到访位置这类「状态型」检查与「历史型」检查分开用（drone_altitude vs drone_has_visited_position），
   前者看当前值，后者看到访记录，不能互相替代。

## 占位符使用

模板内只使用平台支持的占位符：`{drone_id}` `{drone_name}` `{drone_1_id}` `{drone_2_name}` `{target_id}` `{target_name}`
`{obstacle_id}` `{obstacle_name}` `{message}`，以及命名随机 `{random_alt:20:26:1}`、坐标分量 `{randx_wp1:120:320:1}`。
随机量在同一任务内同名复用，保证正文描述与检查阈值一致。