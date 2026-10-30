# -*- coding: utf-8 -*-
"""微调前后输出对比：同一批测试指令，分别用基座模型与 LoRA 模型回答。"""
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
LORA_PATH = os.path.join(os.path.dirname(__file__), "qwen0.5b-lora")

TEST_SENTENCES = [
    "把门关上", "去开会", "把文件给我", "快去打印材料",
    "别收拾桌面", "把水杯递过来", "帮我拿把伞", "把椅子放下",
    "去回复邮件", "把书递过来",
]


def ask(model, tokenizer, text, max_new_tokens=60):
    messages = [
        {"role": "user", "content": f"把下面的句子改写成礼貌、委婉的语气\n句子：{text}"},
    ]
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    ).to(model.device)
    with torch.no_grad():
        out = model.generate(
            inputs, max_new_tokens=max_new_tokens, do_sample=False
        )
    reply = tokenizer.decode(out[0][inputs.shape[-1]:],
                             skip_special_tokens=True)
    return reply.strip().split("\n")[0]


def main():
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, torch_dtype=torch.bfloat16, device_map="auto")

    print("== 1. 加载 LoRA 适配器 ==")
    tuned = PeftModel.from_pretrained(base, LORA_PATH)

    print("\n== 2. 前后对比（每行: 原句 | 基座输出 | 微调输出）==\n")
    for text in TEST_SENTENCES:
        reply_base = ask(base, tokenizer, text)
        reply_tuned = ask(tuned, tokenizer, text)
        print(f"原句: {text}")
        print(f"  基座: {reply_base}")
        print(f"  微调: {reply_tuned}")
        print("-" * 60)

    print("\n== 3.（选做）合并权重便于部署 ==")
    merged = tuned.merge_and_unload()
    merged_dir = os.path.join(os.path.dirname(__file__), "qwen0.5b-lora-merged")
    merged.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)
    print(f"合并后完整模型已保存: {merged_dir}")
    print("后续可用 llama.cpp 转为 GGUF 并导入 Ollama（见 README）")


if __name__ == "__main__":
    main()
