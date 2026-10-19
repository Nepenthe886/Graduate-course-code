# UAV Agent REST 服务

安装与模型配置见 [README.md](README.md)。启动服务：

```bash
python agent_api_service.py
```

默认地址为 `http://localhost:18000`，交互式接口文档位于 `/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 查询服务和模型初始化状态 |
| POST | `/agent/command/async` | 提交异步任务，返回 `job_id` |
| GET | `/agent/jobs/{job_id}` | 查询任务状态、结果与执行退出时间 |
| POST | `/agent/jobs/{job_id}/cancel` | 请求取消任务 |
| POST | `/agent/command` | 同步执行指令 |
| GET | `/agent/session` | 查询当前模拟器会话摘要 |
| GET | `/agent/runs/{run_id}` | 查询实验运行记录 |
| GET | `/agent/runs/{run_id}/artifacts/{artifact}` | 下载运行产物 |

提交示例：

```bash
curl -X POST http://localhost:18000/agent/command/async \
  -H 'Content-Type: application/json' \
  -d '{"command":"列出当前可用的无人机"}'
```

任务通常需要多次模型与工具调用，建议使用异步接口并轮询任务状态。取消后，应等到 `execution_finished_at` 有值再开始下一任务；已经发出的模型或模拟器调用需先返回。

模型设置从 `llm_settings.json` 读取。`UAV_API_URL` 可覆盖模拟器地址；`UAV_API_KEY` 提供模拟器鉴权，`OPENAI_API_KEY` 或 `LLM_API_KEY` 提供模型鉴权。

`experiment` 可随指令一起提交，用于关联 Session、Task 和实验编号。日志字段及可下载产物见 [实验日志说明](docs/EXPERIMENT_TRACKING.zh-CN.md)。任务验收由平台或 Checker 负责。
