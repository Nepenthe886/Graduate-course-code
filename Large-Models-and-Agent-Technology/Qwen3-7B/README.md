# Qwen3-7B 本地部署工程

> **目标**：在单卡 Windows 机器上跑一个 **OpenAI 兼容**的 Qwen3 服务，工程可整包拷到别的电脑直接用。
> **环境约定**：Python 由 **Python Install Manager（PIM）** 提供，创建虚拟环境用 **`py -3.12`**；
> 之后所有项目命令都用**相对路径** `.venv\Scripts\python.exe`，不依赖全局 Python。

---

## 1. 目录结构

```
Qwen3-7B/
├─ configs/
│  └─ default.json          # 全部配置（相对路径），可用 --config 指定别的
├─ src/
│  ├─ __init__.py
│  ├─ paths.py              # 项目根/相对路径解析（不依赖 cwd）
│  ├─ config.py             # 配置加载（json -> 环境变量 -> 命令行）
│  ├─ model_loader.py       # 量化 + tokenizer + chat template
│  ├─ download.py           # 权重下载（HF / hf-mirror / ModelScope）
│  ├─ cli.py                # 命令行聊天客户端（HTTP）
│  └─ server/
│     ├─ __init__.py
│     └─ app.py             # FastAPI，OpenAI 兼容接口
├─ scripts/
│  ├─ setup_env.cmd         # 一次性建 .venv + 装依赖（用 py -3.12）
│  ├─ download_model.cmd    # 下载权重
│  ├─ run_server.cmd        # 启动服务
│  └─ chat.cmd              # 命令行对话
├─ models/                  # 权重（gitignore，只保留 .gitkeep）
├─ data/                    # 运行数据（gitignore）
├─ logs/                    # 日志（gitignore）
├─ .cache/hf/               # HuggingFace 缓存（自动创建，gitignore）
├─ requirements.txt
└─ README.md
```

**相对路径规则**：代码里所有路径都从 `src/paths.py` 的 `PROJECT_ROOT`（= 本文件上两级）解析，
与当前工作目录无关；`.cmd` 脚本统一 `cd /d "%~dp0.."`。**整个目录可以随便搬**。

---

## 2. 快速开始（4 步）

```bat
cd /d E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\Qwen3-7B

:: 1) 建环境 + 装依赖（内部用 py -3.12 建 .venv）
scripts\setup_env.cmd

:: 2) 下载模型（默认 Qwen/Qwen3-8B -> models/Qwen3-8B，约 16 GB）
scripts\download_model.cmd

:: 3) 启动服务（默认 127.0.0.1:8010）
scripts\run_server.cmd

:: 4) 另开窗口聊天（或接 agent4drone）
scripts\chat.cmd
```

> **关于 `py -3.12`**：它只用于 `setup_env.cmd` 里创建 `.venv`（以及必要的诊断）。
> 项目运行请始终用 `.venv\Scripts\python.exe` —— **`py -3.12 xxx.py` 会绕过 venv**，
> 用到的是基础解释器，那里没有本项目依赖。

---

## 3. 量化档位（按显存档位选）

| 档位 | 8B 权重占用 | 16 GB 卡 | 说明 |
|---|---|---|---|
| BF16 / FP16 | ≈16.4 GB | ❌ 放不下 | 不推荐 |
| INT8 | ≈8.7 GB | ⚠️ 可用，上下文要短 | 质量≈BF16 |
| **INT4 (nf4)** | **≈5.2 GB** | ✅ **默认** | 16 GB 卡可留 8–9 GB 给 KV（32k 上下文≈4.7 GB） |
| GGUF Q4 | ≈4.7 GB | ✅（需 llama.cpp） | Windows 上通常更快 |

切换：改 `configs/default.json` 的 `quant`，或 `scripts\run_server.cmd --quant 8bit`。

**KV cache 估算**（Qwen3-8B：36 层 / 8 KV heads / head_dim 128 / BF16）：约 **147 KB/token**
→ 8k≈1.2 GB，32k≈4.7 GB。

---

## 4. 配置说明（`configs/default.json`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `model_dir` | `models/Qwen3-8B` | **相对项目根** |
| `hf_repo` / `hf_method` | `Qwen/Qwen3-8B` / `hf` | 下载用；`hf_method` 可改 `modelscope` |
| `hf_endpoint` | `https://hf-mirror.com` | 国内镜像 |
| `hf_disable_xet` | `true` | 关 Xet（国内必须，否则下载会静默挂住） |
| `quant` / `device` | `4bit` / `cuda` | 量化与设备 |
| `host` / `port` | `127.0.0.1` / `8010` | **别占 8000**（agent4drone 的 UAV API） |
| `max_new_tokens` | 1024 | 单次生成上限 |
| `enable_thinking` | `false` | Qwen3 思考模式，agent 场景建议关 |
| `api_key` | 空 | 设置后客户端需 `Bearer` |
| `system_prompt` | 一句通用提示 | 可改成你项目的角色 |

**覆盖优先级**：`configs/default.json` → 环境变量（`QWEN3_MODEL_DIR`、`QWEN3_QUANT`、
`QWEN3_PORT`、`QWEN3_HF_ENDPOINT` 等）→ 命令行参数。

---

## 5. 接口（OpenAI 兼容）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（含模型名、量化、CUDA 状态） |
| GET | `/v1/models` | 模型列表 |
| POST | `/v1/chat/completions` | 支持 `stream=true`；额外支持 `enable_thinking` |

```powershell
# 自测
curl.exe http://127.0.0.1:8010/health
.venv\Scripts\python.exe -m src.cli --prompt "介绍一下 MoE" --stream
```

**接入 agent4drone**（在它的 provider 配置里加）：

```json
"LocalQwen3": {
  "type": "openai-compatible",
  "base_url": "http://127.0.0.1:8010/v1",
  "models_endpoint": "/models",
  "chat_endpoint": "/chat/completions",
  "requires_api_key": false,
  "default_model": "Qwen3-8B"
}
```

---

## 6. 换一台电脑怎么用

1. 拷整个 `Qwen3-7B` 目录（**不要拷 `.venv` 和 `models`**，见 `.gitignore`）；
2. 确保新机器有 PIM 的 Python 3.12：`py install 3.12`；
3. 跑 `scripts\setup_env.cmd`（会用新机器的 `py -3.12` 重建 `.venv`）；
4. 跑 `scripts\download_model.cmd` 下权重（或用 U 盘拷 `models\` 目录）；
5. `scripts\run_server.cmd`。

因为一切都是相对路径，**不需要改任何配置**。

---

## 7. 常见坑

| 现象 | 原因 | 解决 |
|---|---|---|
| `AssertionError: Torch not compiled with CUDA enabled` | 装成了 CPU 版 torch | `pip uninstall -y torch` 后 `pip install torch --index-url https://download.pytorch.org/whl/cu128` |
| `no kernel image is available` | torch 不是 cu128 构建 | 同上；RTX 50 系必须 cu128+ |
| `.venv\Scripts\activate` 报「不是文件」 | PowerShell 用错了脚本名 | 用 `.\.venv\Scripts\Activate.ps1`（cmd 用 `activate.bat`），或干脆**不激活**，直接用 `.venv\Scripts\python.exe` |
| 下载停在 `[info]` 后不动 | Xet 协议连不上 | 已默认 `HF_HUB_DISABLE_XET=1`；改用 `--method modelscope` |
| `CUDA out of memory` | 显存被其他程序占用 | 关浏览器/微信；或 `--quant 8bit` 换更小模型 |
| 加载阶段系统卡死 | 4bit 加载峰值吃 10–16 GB **内存** | 关后台程序；内存 < 16 GB 建议用 4B 模型 |
| 端口冲突 | 8010 被占 | `--port 8011` |
| 报 `py -3.12` 找不到 | PIM 没装 3.12 | `py install 3.12` |

---

## 8. 模型尺寸说明

Qwen3 官方尺寸：0.6B / 1.7B / 4B / **8B** / 14B / 32B（+ MoE 30B-A3B / 235B-A22B）。
**没有 7B**，本项目默认 `Qwen/Qwen3-8B`；想更小改配置 `hf_repo` + `model_dir` 为 4B 即可。
