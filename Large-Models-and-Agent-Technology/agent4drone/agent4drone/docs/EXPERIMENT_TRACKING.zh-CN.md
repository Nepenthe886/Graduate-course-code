# 实验日志（格式版本 3）

本目录采用 agent4drone_ablation 的日志目录、请求历史和模型记录格式。
学生 baseline 使用基础查询和控制工具，已移除黑板与自动路径规划；日志记录不引入执行锁、Session 校验、作业恢复或新的取消状态。

```text
logs/
├── service/<service_instance_id>/
│   ├── service.json
│   └── service.log
└── batches/<时间戳>-<experiment_id或default>/
    ├── batch.json
    ├── runs.jsonl
    └── runs/<run_id>/
        ├── manifest.json
        ├── events.jsonl
        ├── requests.jsonl
        ├── llm_calls.jsonl
        ├── toolchain.json
        └── related_apis.jsonl
```

服务日志按 5 MiB 轮转，保留 5 个备份。批次引用采用相对路径，允许整体移动；旧日志不迁移、不删除。
API 执行始终记录运行和 UAV 请求。CLI/GUI 在模型或工具链记录开启时创建运行目录。
`toolchain_json_recording` 控制可选工具链；`llm_io_recording` 默认 true，独立控制模型正文采集。GUI 保存配置时保留模型采集设置。

API 请求可增加 `experiment`，字段与消融版一致，例如：

```json
{"command":"执行任务", "experiment":{"experiment_id":"eid-comparison", "run_id":"rid-baseline-001", "session_id":"session-id", "task_id":"task-id", "repeat_index":1, "note":"原版对照组"}}
```

批次和运行编号使用 eid-/rid- 前缀。未传批次时使用进程默认批次。
原版 job_id 仍按原规则生成，不改变取消接口。Session/Task 和快照哈希为关联元数据，不新增平台校验请求。
相同显式 run_id 再次提交仍执行；为防止覆盖，日志生成新 run_id，并保存 requested_run_id，不引入请求幂等或 HTTP 409 执行拦截。
批次备注只保存首份，不加入提示词。

requests.jsonl 按请求完成逐行追加，index 表示开始顺序；未完成请求仅有 request_started 事件。
请求记录保存完整解析响应以及 HTTP/操作成功字段；发送参数、工具收到的精简反馈和异常处理保持原逻辑。
toolchain.json 引用同目录 requests.jsonl，不重复嵌入 api_calls。related_apis.jsonl 是派生候选，不修改任务，也不自动回放。

llm_calls.jsonl 使用记录格式版本 1，包含 capture_started、call_started、call_request、call_finished、call_failed 和 call_interrupted 事件。
记录框架消息、适配器实际请求正文、结构化响应、调用耗时、Agent 尝试序号和工具关联。
SDK 内部 HTTP 重试不可逐次观测；输出不是原始 HTTP 响应体。认证头和传输客户端不保存，任务正文不截断。
未完成或写入失败的采集标记 partial；采集故障不重试模型、不改变任务返回结果。

原版异常分支可能返回空工具轨迹和零 Token 汇总，迁移后保持该返回值；模型调用日志仍保存已捕获用量，两者来源不同。
execution_status=completed 表示原版调用或作业正常返回，不等于任务成功；查看 result.success 和外部环境验证。
任务验收由平台或 Checker 负责。Agent 服务不接收 `POST /agent/runs/{run_id}/task-check`，也不生成 `task_check_status`；调用方完成验收后应在自己的结果记录中保存结论。
取消沿用原版状态机和接口，但修复 LangChain 吞掉取消异常的问题：取消回调设置 `raise_error = True`，并在模型和工具调用结束时再次检查。已发出的调用需要先返回；取消后不再继续执行新的模型或工具步骤。Checker 应以 `execution_finished_at` 确认后台执行和日志收尾已经退出，不能仅凭 `cancelled` 状态开始下一任务。重启不重分类旧 Run，不恢复内存作业、不自动重跑；无终结事件的历史日志可作为未结束记录检查。

只读接口：

- GET /agent/runs/{run_id}
- GET /agent/runs/{run_id}/artifacts/request_history
- GET /agent/runs/{run_id}/artifacts/events
- GET /agent/runs/{run_id}/artifacts/llm_calls
- GET /agent/runs/{run_id}/artifacts/toolchain
- GET /agent/runs/{run_id}/artifacts/related_apis

未生成的可选文件返回 404。模型调用耗时精确到毫秒，其余时间戳精确到秒。
两组实验应使用相同采集开关，按 Session、Task 和 repeat_index 配对；配置、代码和提示词哈希用于核验版本。
