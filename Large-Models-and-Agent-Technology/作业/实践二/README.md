# 实践二：本地大模型对话调用（llama.cpp 版）

本目录把课件中的 **ollama 对话示例**改写为 **llama.cpp（llama-server）** 版本，
模型由 `Qwen3-7B\scripts\run_model.cmd` 启动（OpenAI 兼容接口，默认 `127.0.0.1:8010`）。

## 本目录文件

| 文件 | 对应课件 | 作用 |
|---|---|---|
| `chat_llama.py` | 3.2 对话调用 | `chat(...)` 的 llama 等价实现，一次性返回完整结果 |
| `stream_chat_llama.py` | **3.3 流式调用** | `stream=True` 逐字输出，统计首字延迟（TTFT）；`--compare` 对比同步/流式 |
| `chat_loop_llama.py` | **3.4 命令行聊天** | 多轮对话：history 累积 history→模型具备"记忆"；流式打印并回填 assistant 回复 |
| `run_chat.cmd` | — | 一键：启动 `Qwen3-7B\scripts\run_model.cmd` → 等就绪 → 运行 `chat_llama.py` |
| `run_stream.cmd` | — | 一键：同上，运行 `stream_chat_llama.py`（流式） |
| `run_loop.cmd` | — | 一键：同上，运行 `chat_loop_llama.py`（多轮聊天） |
| `README.md` | — | 本文档 |

## 一、与课件代码的对应关系

课件原始代码（ollama）：

```python
from ollama import chat

response = chat(
    model="qwen3.5:0.8b",
    messages=[
        {"role": "system", "content": "你是一个简洁的助手"},
        {"role": "user", "content": "什么是大语言模型？"},
    ],
)
print(response.message.content)
```

llama.cpp 等价写法（本目录 `chat_llama.py` 的核心）：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8010/v1", api_key="sk-local")
response = client.chat.completions.create(
    model="Qwen3-8B",
    messages=[
        {"role": "system", "content": "你是一个简洁的助手"},
        {"role": "user", "content": "什么是大语言模型？"},
    ],
    stream=False,
)
print(response.choices[0].message.content)
```

| 课件（ollama） | 本实现（llama.cpp） |
|---|---|
| `from ollama import chat` | `from openai import OpenAI` + `/v1/chat/completions` |
| `model="qwen3.5:0.8b"` | `model="Qwen3-8B"`（服务端不校验，可填路径/别名） |
| `messages=[{role, content}]` | **完全相同**：system / user / assistant |
| `response.message.content` | `response.choices[0].message.content` |
| 默认返回完整结果 | `stream=False` 时同样一次性返回完整结果 |
| — | `stream=True` 可流式输出（脚本 `--stream`） |

## 二、前置条件

1. 本地模型已启动（只启动模型，不涉及代理/UAV）：

```bat
cd /d "E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\Qwen3-7B"
scripts\run_model.cmd
```

2. 依赖：`py -3.12 -m pip install openai`（本机已装）。

## 三、运行方式

**方式 1（推荐，一键）**：`run_chat.cmd` 会自动启动模型、等待就绪、再对话：

```bat
cd /d "E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\作业\实践二"
run_chat.cmd
run_chat.cmd --question "什么是智能体？"
run_chat.cmd --stream
```

**方式 2（模型已启动时）**：

```bat
py -3.12 chat_llama.py
py -3.12 chat_llama.py --question "什么是智能体？" --stream
py -3.12 chat_llama.py --base-url http://127.0.0.1:8012/v1
```

## 四、预期输出

```
服务地址: http://127.0.0.1:8010/v1
模型标识: E:\...\Qwen3-7B\models\Qwen3-8B-Q4_K_M.gguf
system  : 你是一个简洁的助手
user    : 什么是大语言模型？
============================================================
模型回复：
大语言模型是基于大量文本数据训练的深度学习模型，能够理解和生成自然语言。它具备以下特点：
……
------------------------------------------------------------
finish_reason: stop
usage: prompt=27 completion=147 total=174
```

（`模型标识` 为 GGUF 文件路径是 llama.cpp 的正常行为；脚本也接受手动指定 `--model Qwen3-8B`。）

## 五、代码要点（与课件"代码要点"对应）

1. **messages 采用角色—内容结构**：`system` 设定助手风格，`user` 提问，`assistant` 为多轮对话中的模型历史回复；
2. **一次调用返回完整结果**：`stream=False`，适合短问答；`response` 中同时包含 `usage`、`finish_reason` 等元信息；
3. **取回复文本**：ollama 为 `response.message.content`，llama.cpp/OpenAI 兼容接口为 `response.choices[0].message.content`；
4. 多轮对话只需把上一轮的 assistant 回复追加到 `messages` 再调用即可（脚本可自行扩展）。

## 六、常见问题

| 现象 | 处理 |
|---|---|
| `无法连接模型服务` | 先运行 `Qwen3-7B\scripts\run_model.cmd`，确认 `http://127.0.0.1:8010/health` 返回 `{"status":"ok"}` |
| 端口被占用 | 默认 8010；换端口启动 `run_model.cmd --port 8012`，脚本加 `--base-url http://127.0.0.1:8012/v1` |
| 回复带思考过程/很长 | Qwen3 在配置里已 `--reasoning off`；如仍异常可加 `--question` 更明确，或降低 `max_tokens` |
| 想流式看字 | 加 `--stream` |
| 模型窗口关掉后连接失败 | 模型进程随窗口结束，重新运行 `run_chat.cmd` 即可 |

## 七、3.3 流式调用：stream

课件原始代码（ollama）：

```python
from ollama import chat

stream = chat(
    model="qwen3.5:0.8b",
    messages=[{"role": "user", "content": "写一首关于秋天的短诗"}],
    stream=True,
)
for chunk in stream:
    print(chunk["message"]["content"], end="", flush=True)
```

llama.cpp 等价写法（`stream_chat_llama.py` 核心）：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8010/v1", api_key="sk-local")
stream = client.chat.completions.create(
    model="Qwen3-8B",
    messages=[{"role": "user", "content": "写一首关于秋天的短诗"}],
    stream=True,
)
for chunk in stream:
    text = chunk.choices[0].delta.content
    if text:
        print(text, end="", flush=True)
```

| 课件（ollama） | 本实现（llama.cpp） |
|---|---|
| `chat(..., stream=True)` | `client.chat.completions.create(..., stream=True)` |
| `for chunk in stream` | 同左，逐块迭代 |
| `chunk["message"]["content"]` | `chunk.choices[0].delta.content`（首个分片可能为 `None`，需判空） |
| `print(..., end="", flush=True)` | 同左 |

**运行**：

```bat
cd /d "E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\作业\实践二"
run_stream.cmd
run_stream.cmd --question "写一首关于冬天的短诗"
run_stream.cmd --compare        :: 同步 vs 流式 首字延迟对比
```

**实测输出**（模型 Qwen3-8B Q4_K_M，8010）：

```
【流式调用 stream=True】
user: 写一首关于秋天的短诗
assistant: 秋风轻拂叶纷飞，
金黄满地映斜晖。
寒蝉低语诉离情，
岁月无声入梦回。
------------------------------------------------------------
首字延迟 TTFT: 74 ms
总耗时: 0.57 s | 分片数: 33 | 字符数: 41 | 平均速度: 72.3 字/秒
```

**同步 vs 流式对比**（同一问题，`--compare`）：

```
同步：首字可见要等 494 ms，总耗时 0.49 s
流式：首字仅 43 ms，总耗时 0.50 s
→ 首字延迟降低约 451 ms
```

结论与课件一致：两者**总生成时间接近**，但流式把"首字可见"从约 0.5 s 降到约 0.05 s，
交互体验明显更好，因此聊天界面普遍采用流式输出。


## 八、3.4 完整实践：命令行聊天

课件原始逻辑（ollama）：

```python
# chat_loop.py — 多轮对话核心逻辑
history = []
while True:
    q = input("\n你: ")
    if q in ("exit", "quit"):
        break
    history.append({"role": "user", "content": q})
    stream = chat(model=MODEL, messages=history, stream=True)
    print("助手: ", end="")
    ans = ""
    for chunk in stream:
        t = chunk["message"]["content"]
        ans += t
        print(t, end="", flush=True)
    history.append({"role": "assistant", "content": ans})
```

llama.cpp 等价写法（`chat_loop_llama.py` 核心）：

```python
history = []
while True:
    q = input("\n你: ")
    if q.lower() in ("exit", "quit"):
        break
    history.append({"role": "user", "content": q})
    stream = client.chat.completions.create(
        model=MODEL, messages=history, stream=True)
    print("助手: ", end="")
    ans = ""
    for chunk in stream:
        t = chunk.choices[0].delta.content or ""
        ans += t
        print(t, end="", flush=True)
    history.append({"role": "assistant", "content": ans})
```

| 课件（ollama） | 本实现（llama.cpp） |
|---|---|
| `chat(model=MODEL, messages=history, stream=True)` | `client.chat.completions.create(model=..., messages=history, stream=True)` |
| `chunk["message"]["content"]` | `chunk.choices[0].delta.content`（可能为 `None`，用 `or ""` 兜底） |
| `input()` + `exit/quit` 退出 | 同左（额外支持 `/clear`、`/history`） |
| `history` 累积 user/assistant | 同左，这是"记忆"的关键 |

**运行**：

```bat
cd /d "E:\Code\Graduate-course-code\Large-Models-and-Agent-Technology\作业\实践二"
run_loop.cmd
run_loop.cmd --system "你是一个简洁的助手"
:: 或模型已启动时
py -3.12 chat_loop_llama.py
```

**实测会话**（验证多轮记忆）：

```
你: 我叫小明，请记住。
助手: 你好，小明！很高兴认识你。有什么我可以帮助你的吗？😊
[本轮 0.31s，历史 2 条消息]

你: 我叫什么名字？
助手: 你叫小明呀！我刚刚已经记住你的名字了，是不是很神奇？😊
[本轮 0.44s，历史 4 条消息]

你: /history
[当前历史 4 条消息，已完成 2 轮对话]
```

第二问能准确回答"小明"，说明 history 回填生效——模型本身无状态，**"记忆"完全来自每次请求携带的 messages 历史**。
