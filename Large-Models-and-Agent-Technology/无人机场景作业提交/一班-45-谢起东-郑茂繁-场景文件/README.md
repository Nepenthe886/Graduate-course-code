# 场景文件（10 个）

适用平台：MultiUAV-Plat v0.48（或更新版本）
每个 JSON 是一个完整会话（session）导出：含地图画布、无人机、目标、障碍物、环境与全部任务。

导入方法：MultiUAV-Plat Controller → Session Manager → Import → 选择 JSON；
或把 JSON 放进平台目录下的 `UAVSessions/` 文件夹。

| # | 场景名称 | 任务类型 | 无人机 | 目标 | 障碍物 | 任务数 | 由本组模板生成 |
|---|---|---|---|---|---|---|---|
| 1 | City Depot Night Shift | area_assignment_and_patrol | 3 | 4 | 3 | 20 | 20 |
| 2 | Harbour Container Terminal | area_search | 4 | 4 | 4 | 20 | 20 |
| 3 | Wildfire Ridge Watch | area_search | 3 | 4 | 3 | 20 | 20 |
| 4 | Mountain Pass Cargo Run | target_assignment | 4 | 4 | 3 | 20 | 20 |
| 5 | Solar Farm Perimeter | area_assignment_and_patrol | 3 | 4 | 2 | 20 | 20 |
| 6 | Stadium Event Security | target_assignment | 3 | 4 | 3 | 20 | 20 |
| 7 | Highway Bridge Inspection | target_assignment | 4 | 4 | 3 | 20 | 20 |
| 8 | Wind Farm Offshore Array | area_search | 4 | 4 | 3 | 20 | 20 |
| 9 | Quarry Blast Survey | area_assignment_and_patrol | 3 | 4 | 3 | 20 | 20 |
| 10 | Coastal Strip Emergency Drill | target_tracking | 5 | 4 | 3 | 20 | 20 |

合计 200 个任务，其中 200 个由本组 10 个模板实例化生成（100%），满足「一半以上由任务模板生成」的要求。

## 场景设计要点

* 画布统一 1024×768，与平台默认坐标系一致；高度上限 100，任务高度集中在 20–40 m。
* 每个场景 3–5 架无人机、3 个目标、2–4 个障碍物，并配置天气/温度/湿度/气压/风向风速/能见度，
  便于在 Environment 面板里核对。
* 任务顺序按「起飞 → 侦察/测量 → 作业 → 返航/降落」排列，与真实作业流程一致；
  任务的 `originated_from` 字段写明模板名，可在平台上核对任务来源。
* 10 个场景覆盖城市仓储、港口、山火、山地运输、光伏电站、体育场安保、桥梁检测、海上风电、采石场、海岸应急
  十类典型作业，难度由 2 架单机任务递增到 5 架多目标协同。

## 自检

生成脚本对 10 个模板与 10 个场景做了自动校验，结果 `problems: none`：

* 无残留占位符（每个字符串值都检查 `{xxx}` 形式）；
* 命令端点与检查端点在平台允许列表内，检查参数名与平台签名一致；
* 每个场景任务数 ≥ 20，模板来源占比 > 50%；
* JSON 结构与本平台自带示例 session 逐字段比对，键集合完全一致（无人机/目标/障碍物/任务/环境/统计/历史）。