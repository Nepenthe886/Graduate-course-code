# Task Template 与 Placeholder 参考（0.4.7）

适用版本：Controller / Server `0.4.7`。更新日期：2026-10-28。本指南说明当前 Controller 的模板格式、参数替换和已知限制；操作练习与 Agent 辅助生成方法见学生教程。

学生教程：[创建与使用 Task Template](../guide/task%20template/README.md)。

## 1. 管理入口、生成入口与存储位置

| 入口 | 用途 | 双击行为 |
|---|---|---|
| Session Manager → TaskTem，或 Tools → TaskTem… | 管理本地模板：新建、预览、编辑、复制、导入、导出、删除 | 自定义模板进入编辑，内置模板查看详情 |
| Controller → Tasks → From Template | 在当前 Session 中选择模板并创建 Task | 使用选中的模板 |
| Session Manager → TaskUI | 先选择 Session，再选模板，按每个 Session 的资源创建 Task | 在模板选择窗口使用模板 |

TaskTem 管理窗口不要求先选 Session，不显示 Use Template 按钮。模板保存到本地；生成 Task、执行任务与运行检查是后续的不同步骤。

- 自定义模板库是 **Settings → Template Storage** 指定目录中的 `task_templates.json`；以 TaskTem 窗口底部 `Library` 显示的路径为准。
- 默认配置目录是 `./templates`，相对于运行程序的工作目录。`~/.multiuav/templates/` 不是当前固定默认位置。
- 内置模板来源为 Controller 的 `default_templates.py`。内置项不能直接删除或编辑，可用 Copy & Edit / Duplicate 创建自定义副本。
- 默认模板导出的单文件 JSON 在 Controller 的 `guide/task template/default_templates/`，供阅读、分享和导入。应用不会自动扫描此目录。
- 自定义模板库采用临时文件替换写入；写入失败会恢复内存中的旧数据。编辑模板不会回写到已创建的 Task。

## 2. 可直接导入的 JSON 示例

以下是完整的单模板文件，可保存为 `doc_takeoff.json`，通过 TaskTem → Import… 导入。它需要至少一架无人机，指令、候选操作和完成检查使用同一个命名高度变量。

```json
{
  "id": "doc_takeoff",
  "template": {
    "name": "Doc Takeoff and Hover",
    "description": "需要一架无人机；任务完成时在指定高度保持空中状态。",
    "content": "让 {drone_name} 起飞至 {random_docalt:12:18:1} 米并悬停，结束时保持在空中。",
    "content_aliases": [
      "请指挥 {drone_name} 升至 {random_docalt:12:18:1} 米后悬停，完成时不要降落。"
    ],
    "category": "Tutorial",
    "difficulty": "easy",
    "creator": "Student",
    "is_builtin": false,
    "suitable_task": ["all"],
    "exclude_in_random_generation": false,
    "related_apis": [
      {
        "endpoint": "/drones/{id}/command/take_off",
        "parameters": {
          "id": "{drone_id}",
          "altitude": "{random_docalt:12:18:1}"
        }
      },
      {
        "endpoint": "/drones/{id}/command/hover",
        "parameters": {"id": "{drone_id}"}
      }
    ],
    "execution_check_apis": {
      "logic": "and",
      "checks": [
        {
          "endpoint": "/check/drone_altitude",
          "parameters": {
            "drone_id": "{drone_id}",
            "expected_altitude": "{random_docalt:12:18:1}",
            "tolerance": 2.0
          }
        },
        {
          "endpoint": "/check/drone_on_ground",
          "parameters": {"drone_id": "{drone_id}", "tolerance": 1.5},
          "expect": false
        }
      ]
    }
  }
}
```

JSON 必须使用双引号，不允许注释、尾随逗号或 `true|false` 这样的占位写法。数字字面值直接写为数字；待替换的 placeholder 使用字符串。

### 字段要求

| 字段 | 当前规则与用途 |
|---|---|
| `name`、`content` | 编辑器保存与 Import 要求非空文本 |
| `description` | 给模板使用者的前提与目标说明，不应代替任务指令 |
| `content_aliases` | 字符串列表；每条都应保留完成同一任务必需的信息 |
| `category` | 分类文本 |
| `difficulty` | 编辑器支持 `easy`、`medium`、`hard`，选择其中一个值 |
| `creator` | 模板作者；编辑器／Import 使用当前设置的用户名 |
| `is_builtin` | Import 自动转为 `false`，不能通过导入文件覆盖内置项 |
| `suitable_task` | `all`，或 `area_search`、`area_assignment_and_patrol`、`target_assignment`、`target_tracking`、`others` 的列表；空列表兼容为全部，`all` 优先 |
| `exclude_in_random_generation` | 布尔值；为 `true` 时排除自动／随机选取，仍可手动使用 |
| `related_apis` | 候选命令列表；不是实际执行历史，也不是任意 HTTP 请求列表 |
| `execution_check_apis` | 完成条件检查树，支持 `and`、`or` 与嵌套 `checks` |
| `commands` | 可选命令名列表；含 Related APIs 的 Task 实例化时会按规范化操作重新生成 |
| `created_at`、`last_modified` | 管理器保存时维护；Import 会重新记录时间 |

Import 接受上例的 `id` / `template` 封装，也接受单个原始模板对象。整份 `task_templates.json` 是 ID 到模板的映射，不能当成单模板文件导入。重复 ID 会添加数字后缀建立副本，保留原模板。

Import 的结构校验不等同于完整语义校验；例如仍需核对任务是否可完成、接口是否支持、参数类型及检查覆盖是否合理。直接调用管理器的 `add_template()` 也不等同于 GUI Import 校验，应先调用 `validate_import()`。

## 3. Placeholder 基本规则与实体选择

使用半角花括号，精确匹配拼写与大小写，例如 `{drone_name}`。不支持自动求值 `{altitude + 5}`、嵌套 `{{drone_name}}` 或把带空格的 `{ drone_name }` 当作同名变量。

| 实体 | 单实体 | 常用编号参考项 |
|---|---|---|
| 无人机 | `{drone_id}`、`{drone_name}` | `{drone_1_id}` / `{drone_1_name}` 至 `{drone_5_id}` / `{drone_5_name}` |
| 目标 | `{target_id}`、`{target_name}` | `{target_1_id}` / `{target_1_name}` 至 `{target_3_id}` / `{target_3_name}` |
| 障碍物 | `{obstacle_id}`、`{obstacle_name}` | `{obstacle_1_id}` / `{obstacle_1_name}` 至 `{obstacle_3_id}` / `{obstacle_3_name}` |

编号表示模板角色，不是 Server 上实际对象的编号。`drone` 和 `drone_1` 是不同参数组；同一角色在文本、API 和检查中应统一使用同一组。名称供人阅读，ID 用于 API 实体绑定。

GUI 会从模板参数中识别实体组；选择实体后，填入该组所使用的 ID／名称。`[RANDOM]` 在同类角色资源足够时尽量分配不同实体，资源不足可能重复；`[ORDERED]` 按列表顺序循环。指定两个不同编号不保证一定得到两架不同无人机，应检查实际结果。TaskUI 按每个 Session 解析策略，不提供跨 Session 固定指定实体的选项。

直接调用 `instantiate_template()` 不会仅凭无人机 ID 查询并补上名称。程序调用需要显式提供任务中使用的 ID 和名称，或由调用方先从真实 Session 构建参数。目标坐标可在提供匹配的 `target*_id` 和 `_context_targets` 时从对应目标位置补充；不要把这种行为泛化为任意实体属性自动查询。

## 4. 普通参数与自定义文本

内置普通数值参数包括：

- `position_x`、`position_y`、`position_z`、`altitude`、`altitude_change`。
- `distance`、`heading`、`speed`、`duration`、`hover_time`、`radius`。
- `waypoint_1_x`、`waypoint_1_y`、`waypoint_1_z`、`waypoint_2_x`、`waypoint_2_y`、`waypoint_2_z`。

`{message}` 是内置文本参数。GUI 为已知数值类型提供输入或 Range 选择；这些参数本身不会因为有示例值就自动成为随机数。

自定义普通文本建议使用 `{mission_label}`、`{area_label}`。避免给普通说明变量使用 `_id` / `_name` 后缀：例如 `{mission_name}` 会被归入 `mission` 实体组，而不是预期的普通文本输入，当前界面没有这种实体类型的选择控件。

自动／随机生成主要填充实体和随机值，不会替你回答任意普通参数。未提供的 placeholder 通常会残留；Related APIs 参数中遗留 placeholder 会在协议规范化时被拒绝，但 Content 中残留并不一定阻止 Task 创建，所以必须检查生成结果。

## 5. 随机数语法和缓存

### 5.1 预定义命名随机数

下列值在一条 Task 内复用，生成下一条 Task 时重新抽取。当前均按 0 位小数取整；范围不是根据 Session 尺寸自动推导的。

| Placeholder | 当前默认范围 |
|---|---|
| `{random_altitude}` | 5–100 |
| `{random_x}` | 0–1024 |
| `{random_y}` | 0–768 |
| `{random_z}` | 5–100 |
| `{random_heading}` | 0–360 |
| `{random_distance}` | 10–500 |
| `{random_speed}` | 1–30 |
| `{random_hovertime}` | 2–20 |
| `{random_duration}` | 5–30 |

### 5.2 命名与匿名随机数

| 语法 | 行为 |
|---|---|
| `{random_docalt:12:18:1}` | 命名数值，范围 12–18，保留 1 位小数，同一 Task 内复用 |
| `{random_docalt:12:18}` | 命名数值，默认按 0 位小数取整 |
| `{randint_doccount:2:5}` | 命名整数，包含上下界，同一 Task 内复用 |
| `{random:12:18:1}`、`{random:12:18}` | 匿名数值，每次出现单独取值 |
| `{randint:2:5}` | 匿名整数，每次出现单独取值 |

通用随机数的命名部分使用英文字母或下划线开头，后续可包含字母、数字、下划线；学生模板建议使用清晰的英文短名。`randint` 上下界必须为整数，其他通用随机数支持常规十进制数；不要使用科学记数法或省略整数部分的 `.5` 写法。最小值不得大于最大值。

普通命名随机数可先使用带范围的定义，再用 `{random_docalt}` / `{randint_doccount}` 引用。引用先于有效定义时可能保持未替换。不要跨 `random_` 和 `randint_` 复用同一个名称，因为缓存使用名称作为键。

建议在所有字段中复制相同的完整定义。不要让同一变量出现不同范围或精度；普通命名随机数按先建立的缓存复用，协调坐标的预扫描又有不同规则，不能把“第一处定义一定生效”作为所有类型的统一保证。

## 6. 随机坐标、协调组与当前限制

### 6.1 分量写法

```text
让 {drone_name} 前往 ({randx_wp1:200:400:1}, {randy_wp1:200:400:1}, {random_docalt:12:18:1})。
```

- `randx`、`randy`、`randz` 分别为 X、Y、Z；无名称的 `{randx}`、`{randy}`、`{randz}` 是匿名抽样。
- `{randx_wp1:200:400:1}` 是命名坐标，带范围和小数位；当前直接解析语法要求范围为整数上下界。
- 坐标后缀建议使用 `wp1`、`wp2`，不要再包含下划线；当前直接解析器与预扫描器对后缀的接受范围不完全一致。
- 相同后缀的 X 和 Y 在**同一次文本替换**中被识别为协调组；可选 Z。只有 X、或只有 X 和 Z，不构成 XY 组。
- 每个字段都使用同样的完整定义，尽量在 Content 中明确写出全部坐标，然后在 Alias、API 参数和检查参数中复用。

### 6.2 组合坐标的输出格式

| 写法示例 | 文本格式 |
|---|---|
| `{randxy_wp1}` | `x y` |
| `{randxyc_wp1}` | `x, y` |
| `{randxyz_wp1}` | `x y z` |
| `{randpos_wp1}` | `x, y, z` |

这些组合类型也可用不带名称的匿名形式，或添加整数范围及精度。它们输出的是文本，不会自动变成 API 要求的 JSON 数组或坐标对象。

**0.4.7 当前实现限制：不要依赖组合坐标与同名分量一致。**组合值与分量预扫描缓存存在不一致，混用 `{randxyz_wp1:10:20}` 和 `{randx_wp1}` 等分量可能得到不同位置；即使分量写在组合之前，也不能据此保证一致。需要文本、API 和检查统一坐标时，使用上节的分量方案，或由调用方显式提供确定的坐标值。此处是已知实现限制，不是已经修复的功能。

协调分量还会预扫描文本，同名坐标的后续定义可能覆盖之前定义。不要使用重复定义不同范围、无范围引用与带范围定义混杂来控制优先级。无论哪种坐标写法，都应验证生成结果，而不是依靠旧指南中的“第一次定义”“组合必定优先”承诺。

### 6.3 边界与避障的实际含义

默认平面为 1024 × 768，高度上限为 100；部分位置生成使用 50 的默认边距。自定义 XY 范围不会自动裁剪到真实 Session 地图，显式范围也不一定保留默认边距。

避障需要调用方提供障碍物上下文，当前碰撞检查还依赖可用的 Server 检查接口；它检查位置的二维占地，不证明三维路线可达或整条路径安全。接口失败时可能按无碰撞处理；重试耗尽会返回最后一个候选点。因此应描述为“尝试避障”，不能承诺“保证生成无碰撞位置”。

## 7. 替换范围与 Related APIs 协议

当前实例化顺序为 Content → Aliases → Related APIs 参数 → Execution Check APIs 参数／expect。每次 Task 实例化使用一个新的随机缓存；提供的匹配参数优先于随机生成。协调组预扫描只作用于当前替换的文本，不是整份模板的一次全局扫描。

| 位置 | 当前行为 |
|---|---|
| `content`、`content_aliases` | 可在句子中替换多处 placeholder |
| API／检查 `parameters` 中的完整 `"{placeholder}"` | 可替换为具体值，字典和列表递归处理；数值应最终成为数字 |
| API 参数中的 `"{altitude} meters"` | 不按一般句子模板替换，不适合作为数值参数 |
| `name`、`description`、`creator`、`difficulty` | 可以由调用参数整体赋值，不执行一般花括号插值 |
| `endpoint`、旧 `path` 字段 | 不执行一般模板路径插值；命令必须遵守当前协议 |

候选命令使用 `endpoint`，例如 `/drones/{id}/command/move_to`，具体无人机通过 `parameters.id` 绑定。标准 endpoint 中的 `{id}` 保留原样，不是残留的任务参数。旧写法 `path`、`/drones/{drone_1_id}/...` 或实际 ID 嵌入 endpoint 不符合当前 Related APIs 目录要求。

Related APIs 只接受当前协议支持的 POST 命令；可省略 `method`，实例化会规范化为 `POST`。操作需要的参数、合法名称及数值类型必须符合协议，不能把检查 GET 请求或任意 HTTP 接口加入候选命令。

嵌套参数可递归替换不代表 GUI 会同样完整地发现所有深层手动输入字段。执行所需实体、位置、阈值等应出现在 Content 和每条 Alias 中，避免只藏在 API 参数深层列表里。

## 8. 完成检查与可评分要求

检查叶子使用 `endpoint`、`parameters`，可用 `expect: false` 表示期望条件为假。父节点使用 `logic: "and"` 或 `"or"` 与 `checks` 列表建立树；编辑器提供 +Check 和 +Group。

当前状态与历史检查的含义不同。例如 `drone_altitude` 检查当前高度，`drone_has_visited_position` 检查历史到访。要求“到访后返回”的任务，不能只用“最终停在航点”作为完成标准。历史记录也可能来自之前运行，实验应安排相应的场景／历史重置。

本指南起飞示例验证高度在容差内且不在地面，不验证持续悬停时长。检查结果通过不代表所有自然语言要求都被覆盖，应明确每条要求对应的检查及尚未覆盖的顺序、时间或路径条件。

## 9. 当前默认模板

Controller 当前导出 58 个默认模板。可从 `basic_takeoff`、`quick_checkpoint`、`fly_several_waypoints`、`patrol_mission` 开始，再研究 `dual_patrol`、`formation_triangle`、`target_inspection` 等多实体任务。以 `default_templates.py` 的实际 ID 和导出索引为准。

旧指南列出的 `basic_takeoff_land`、`search_task`、`grid_search` 不是当前模板 ID，不应继续用于查询或实例化。

完整索引：[全部默认模板 JSON](../guide/task%20template/default_templates/README.md)。

## 10. 可运行的离线 Python 示例

在 **Controller 项目根目录**运行，使用已安装的项目依赖。这里的实体是离线样本，不是实际 Session ID；代码只生成内存中的 Task，不发送飞行请求。临时目录用于避免修改自己的模板库。

```python
import re
from tempfile import TemporaryDirectory
from task_template_manager import TaskTemplateManager

with TemporaryDirectory() as directory:
    manager = TaskTemplateManager(template_dir=directory)
    task = manager.instantiate_template("formation_triangle", {
        "name": "Triangle Patrol",
        "creator": "Student",
        "drone_1_id": "sample-alpha",
        "drone_1_name": "Alpha",
        "drone_2_id": "sample-bravo",
        "drone_2_name": "Bravo",
        "drone_3_id": "sample-charlie",
        "drone_3_name": "Charlie"
    })
    assert task is not None
    assert not re.search(r"\{[^}]+\}", task["content"])
    for alias in task.get("content_aliases", []):
        assert not re.search(r"\{[^}]+\}", alias)
    print(task["content"])
```

不要把离线样本 ID 直接提交为真实 Task 的实体参数。真实使用应先读取 Session，提供同一实体的 ID 和名称，以及模板需要的其他参数／上下文。实例化函数不会因只提供 ID 就自动补齐所有人类可读字段。

## 11. 验证与常见问题

- Detect & Validate 辅助识别 Content 与 Aliases 的语法，不是全模板执行验证；自定义文本或无范围随机引用也可能被标为 Unknown。
- 编辑器 Preview 使用示例参数预览文本，不验证真实 Session 资源、所有 API 参数或完成检查。
- 保存／Import 后先实例化一条，核对实体、随机范围、数值类型和所有任务参数是否替换；标准命令 endpoint 的 `{id}` 除外。
- 同名高度不一致：检查是否误用了匿名随机值、不同名称或冲突定义。
- 组合坐标和分量不一致：按第 6 节的当前限制改用分量方案，不要仅调整文本出现顺序。
- 内容仍有无人机名称 placeholder：程序调用补充 `_name`；GUI 检查实体参数组及当前 Session。
- 使用自定义参数的自动生成失败：先手动填写参数，或改用可解析的实体、命名随机数或固定值。
- 导入成功但任务创建报 Unsupported command：检查 `endpoint` 是否在当前 Related APIs 协议目录中，以及参数是否规范。

本指南与学生教程分别提供格式参考和操作练习，不再使用“其他文档可以删除”的旧说明。当前坐标限制在文档中明确记录，不能把说明更新误认为实现已修复。
