# 实践三：大模型的 LoRA 微调（Qwen2.5-0.5B-Instruct）

## 实验目标
1. 理解 LoRA（Low-Rank Adaptation）参数高效微调原理
2. 完成对 1B 以下小模型 Qwen2.5-0.5B-Instruct 的 LoRA 微调全流程
3. 对比微调前后的输出效果

## 环境
```bash
pip install -r requirements.txt
# 显存要求：>=6GB（CPU 也能跑但极慢，建议 Colab / 实验室 GPU 服务器）
```

## 文件说明
| 文件 | 说明 |
|---|---|
| `prepare_data.py` | 生成"礼貌语气改写"指令数据集（data/train.jsonl） |
| `train.py` | LoRA 微调主脚本（transformers + peft + trl） |
| `compare.py` | 微调前后输出对比评测 |
| `data/train.jsonl` | 训练数据（运行 prepare_data.py 生成） |

## 运行顺序
```bash
python prepare_data.py    # 1. 生成数据集（300条）
python train.py           # 2. 训练（产物: ./qwen0.5b-lora/）
python compare.py         # 3. 前后对比（各测10条）
```

## （选做）转 GGUF 导入 Ollama
```bash
# 1. 合并 LoRA 权重（compare.py 已演示 API）
# 2. 用 llama.cpp 转换：convert_hf_to_gguf.py qwen0.5b-lora-merged --outfile model.gguf
# 3. 写 Modelfile 后导入：
#    ollama create my-finetuned -f Modelfile
#    ollama run my-finetuned
```

## 关键超参数速查
| 参数 | 常用值 | 说明 |
|---|---|---|
| r | 8 | 低秩矩阵秩，越大能力越强开销越大 |
| lora_alpha | 16 | 缩放系数，常取 r 的 1~2 倍 |
| target_modules | q_proj, v_proj | 注意力层注入点 |
| learning_rate | 2e-4 | LoRA 专用学习率范围 1e-4~5e-4 |
