# UAV Agent 学生 Baseline

本项目提供使用 LangChain Agent 和基础 UAV API 工具完成任务的学生 baseline。工具只执行对应的查询或控制操作；任务分解、路径选择、避障决策和区域搜索策略由模型根据指令与观测决定，供学生进一步实现和比较。

## 基础工具（22 个）

| 类别 | 工具 |
| --- | --- |
| 信息查询 | `list_drones`、`get_session_info`、`get_weather`、`get_drone_status`、`get_nearby_entities`、`get_target_info`、`get_obstacle_info` |
| 飞行与移动 | `take_off`、`land`、`hover`、`move_to`、`move_towards`、`move_along_path`、`change_altitude`、`rotate` |
| 维护与返航 | `return_home`、`set_home`、`calibrate`、`charge` |
| 拍照与通信 | `take_photo`、`send_message`、`broadcast` |

已删除黑板记忆及跨命令共享、自动避障导航、覆盖路径生成与缓存，以及移动后自动感知的组合工具和相关规划模块。`move_along_path` 仅执行显式航点，支持二维或三维坐标。查询返回 API 客户端提供的完整观测，包含目标和障碍物几何信息。

## 运行

需要 Python 3.10+ 和可访问的 UAV 模拟器。安装运行依赖：

```bash
pip install -r requirements.txt
cp llm_settings.example.json llm_settings.json
```

配置模型提供商、模型名称和 `uav_base_url` 后运行。配置保留 Ollama、OpenAI、火山引擎、DeepSeek、PPIO 等提供商的地址与模型选项，可通过 `selected_provider` 切换。

密钥通过环境变量提供，例如 `OPENAI_API_KEY` 或 `LLM_API_KEY`，模拟器鉴权使用可选的 `UAV_API_KEY`。GUI 中输入的密钥只用于当前进程，保存配置时会移除密钥字段。

```bash
python agent_api_service.py  # REST 服务，默认端口 18000
python main.py               # 图形界面，需要 Tkinter
python uav_agent.py --help   # 命令行参数说明
```

模型按需单独调用 `get_nearby_entities` 获取移动后的观测。航点任务需检查部分成功和最终位置，避免把受阻移动视为完成。

## 学生扩展入口

- `uav_langchain_tools.py`：基础工具及注册清单。
- `template/system_prompt.py`：Agent 的系统提示词。
- `uav_agent.py`：Agent 运行与执行流程。
- `uav_api_client.py`：模拟器 API 客户端。

实验日志、模型输入输出记录、服务任务接口和取消行为仍可使用。服务说明见 [README_SERVICE.md](README_SERVICE.md)，实验记录说明见 [docs/EXPERIMENT_TRACKING.zh-CN.md](docs/EXPERIMENT_TRACKING.zh-CN.md)。

这是精简的学生分发版本，项目中不包含测试、历史实验数据或语音输入依赖。运行时会自动生成 `logs/`；本地配置和日志已加入 `.gitignore`。
