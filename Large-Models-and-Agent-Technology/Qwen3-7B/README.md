# Qwen3-7B 本地部署（llama.cpp + GGUF）

> 单卡 Windows 本地推理工程：**llama.cpp 预编译二进制 + GGUF 量化模型**，提供 OpenAI 兼容接口。
> Python 仅用于"下载 / 启动器 / 客户端"，**不需要 torch**。

## 为什么选 GGUF Q4_K_M（对应"不要让性能压榨太极限"）

| 档位 | 模型大小 | 16 GB 卡上的负载 | 单流速度（8B） |
|---|---|---|---|
| Q8_0 | ≈8.1 GB | 偏高，KV 空间紧张 | 慢 |
| Q5_K_M | ≈5.5 GB | 中等 | 中等（`configs/quality.json` 可选） |
| **Q4_K_M（默认）** | **≈4.7 GB** | **轻**，8k 上下文从容 | **约 45–65 tok/s** |
| Q2_K | ≈3.0 GB | 最轻但质量明显下降 | 快 |

默认 **Q4_K_M + 8192 上下文 + 全部层放 GPU**：显存约 6 GB，留足余量，不会因为压榨极限而变慢。

## 目录结构

```
Qwen3-7B/
├─ configs/
│  ├─ default.json          # 默认：Q4_K_M / 8k / 8010
│  └─ quality.json          # 高质量档：Q5_K_M
├─ src/
│  ├─ paths.py              # 项目根 + 把所有缓存/TEMP 锁进项目内
│  ├─ config.py             # 配置加载（json -> 环境变量 -> 命令行）
│  ├─ download.py           # 下 GGUF（ModelScope 直链 + Range 续传）
│  ├─ runtime.py            # 下 llama.cpp CUDA 预编译包（经 gh-proxy）
│  ├─ server.py             # 启动 llama-server（端口预检 + 相对路径）
│  ├─ cli.py                # OpenAI 客户端
│  └─ check_git.py          # 检查 git 是否跟踪了大文件
├─ scripts/
│  ├─ setup_env.cmd         # py -3.12 建 venv + 装轻依赖（缓存锁项目内）
│  ├─ setup_runtime.cmd     # 下 llama.cpp 二进制
│  ├─ download_model.cmd    # 下 GGUF 模型
│  ├─ run_server.cmd        # 起服务
│  ├─ chat.cmd              # 命令行对话
│  └─ check_git_large_files.cmd
├─ runtime/llama/           # llama-server.exe + CUDA DLL（gitignore）
├─ models/                  # *.gguf（gitignore）
├─ .cache/{temp,pip,modelscope,hf,downloads,pycache}/   # 全部缓存（gitignore）
├─ data/  logs/
├─ requirements.txt
└─ README.md
```

## 快速开始（4 步）

```bat
cd /d E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\Qwen3-7B

:: 1) 建环境（py -3.12 -> .venv，只装 requests/psutil/openai）
scripts\setup_env.cmd

:: 2) 下 llama.cpp 运行时（~600 MB，经 gh-proxy 绕过 GitHub 封锁）
scripts\setup_runtime.cmd

:: 3) 下 GGUF 模型（Q4_K_M ≈4.7 GB，下到 models\）
scripts\download_model.cmd

:: 4) 启动服务（OpenAI base_url = http://127.0.0.1:8010/v1）
scripts\run_server.cmd
```

自测：

```bat
scripts\chat.cmd
```

## 关键约定

### 1) 全部相对路径
- 代码里所有路径从 `src/paths.py` 的 `PROJECT_ROOT` 推导，**与当前工作目录无关**；
- 所有 `.cmd` 先 `cd /d "%~dp0.."`；
- 配置里的 `model_dir` / `runtime_dir` 都是相对项目根。

### 2) 不往 C 盘写任何东西
- `src/paths.lock_caches_into_project()` 会把 `TMP/TEMP/PIP_CACHE_DIR/HF_HOME/MODELSCOPE_CACHE/PYTHONPYCACHEPREFIX` 全部指向项目 `.cache/`；
- 每个 `.cmd` 也显式设置这些变量；
- 下载的二进制/模型只落在 `runtime/` 与 `models/`；
- 验证方法：跑完看 C 盘可用空间是否变化。

### 3) 大文件不上 GitHub
- `.gitignore` 已忽略：`runtime/`、`models/`、`.cache/`、`*.gguf`、`*.exe`、`*.dll`、`*.zip`；
- 提交前跑一次检查：
  ```bat
  scripts\check_git_large_files.cmd
  ```
  它会列出被 git 跟踪且 >20 MB 或疑似二进制的文件，正常应输出 `[ok]`。

### 4) PIM / py -3.12
- 只有 `setup_env.cmd` 用 `py -3.12` 创建 `.venv`；
- 运行期一律用相对路径 `.venv\Scripts\python.exe`；
- 直接 `py -3.12 -m src.server` 会绕过 venv（那是基础解释器），别这么用。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/v1/models` | 模型列表 |
| POST | `/v1/chat/completions` | 对话，支持 `stream`、**工具调用**（`--jinja`） |

llama-server 也自带 Web UI：浏览器打开 `http://127.0.0.1:8010`。

## 调参（都不影响"轻负载"原则）

| 目的 | 做法 |
|---|---|
| 换更高精度 | `scripts\run_server.cmd --config configs\quality.json`（Q5_K_M） |
| 更大上下文 | `scripts\run_server.cmd --ctx 16384`（显存足够时） |
| 换端口 | `scripts\run_server.cmd --port 8011` |
| 少用显存 | `scripts\run_server.cmd --ngl 24`（部分层放 CPU，变慢但省显存） |

## 常见问题

| 现象 | 原因/解决 |
|---|---|
| `找不到 llama-server.exe` | 先跑 `scripts\setup_runtime.cmd` |
| 启动报缺 `cudart64_*.dll` | cudart 包没解压，重跑 `setup_runtime.cmd --force` |
| 启动即退出、日志显示 SM 不支持 | CUDA 版本不匹配，改 `configs\default.json` 的 `cuda_tag`（如 `win-cuda-12.4-x64`）后 `--force` 重下 |
| 端口被占用 | 现在会**秒失败**并提示，换 `--port` |
| 下载很慢/中断 | `download_model.cmd` 支持断点续传，重跑即可 |
| 想连 agent4drone | base_url 填 `http://127.0.0.1:8010/v1`，模型名填 `Qwen3-8B` |

---

## 实测结果（本机 RTX 5060 Ti 16 GB / Q4_K_M / ctx 8192 / ngl 99）

| 指标 | 结果 |
|---|---|
| decode 速度 | **约 70 tok/s**（对比 transformers+bnb 4bit 的 17.5 tok/s，**约 4 倍**） |
| 首次响应（短 prompt） | 0.6–0.8 s |
| 显存占用 | 7.59 GiB / 15.9 GiB（模型 4.68 GiB + KV 8k + 运行时） |
| 工具调用 | ✅ 正常返回 `tool_calls`（`finish_reason=tool_calls`），0.66 s |
| 中文输出 | ✅ 正常 |

### 重要：Qwen3 思考模式已默认关闭

Qwen3 默认会先"思考"（输出到 `reasoning_content`），表现为 **content 为空、响应慢 3–4 倍**。
本项目在 `src/server.py` 里自动传 `--reasoning off`；如需思考模式，改 `configs/default.json`::

    "enable_thinking": true

### 运行时文件说明

- 运行时来自 llama.cpp release（本机用 b11946 的 `win-cuda-13.4-x64` 包，含 cublasLt64_13.dll）
- 下载走 `gh-proxy.com` + **8 路并行 Range**（单连接仅 14 KB/s，多连接约 0.8–1 MB/s）
- 若某天 GitHub 完全不可达：手动下两个 zip 放进 `.cache/downloads/`，执行
  `.venv\Scripts\python.exe -m src.runtime --from-zip`

### 上下文窗口（重要）

默认 **32768 token**（`-c 32768`），因为 agent 多轮对话会不断累积：
system prompt + 22 个工具 schema（约 4k）+ 历史与工具结果。

- 8k 时代实测会被拒：`request (8326 tokens) exceeds the available context size (8192 tokens)`
- 32k 后显存占用约 **11.1 GiB / 15.9 GiB**（KV cache 约 4.8 GiB），仍有 4.9 GiB 余量
- 实测 9044 token 的请求可正常处理（3.25 s）
- 如果单次任务极长（累积 >32k），最省事的办法是开新对话；也可以 `--ctx 49152` 但显存会接近上限（不建议）
