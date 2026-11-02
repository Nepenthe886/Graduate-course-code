# -*- coding: utf-8 -*-
"""LoRA 微调 Qwen2.5-0.5B-Instruct（transformers + peft + trl）。

产物：./qwen0.5b-lora/（LoRA 适配器，几十 MB）
"""
import os

import torch
from datasets import load_dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer)
from peft import LoraConfig, get_peft_model
from trl import SFTTrainer, SFTConfig

BASE_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Qwen2.5-0.5B-Instruct")
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "train.jsonl")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "qwen0.5b-lora")

LORA_R = 8
LORA_ALPHA = 16
LORA_DROPOUT = 0.05
TARGET_MODULES = ["q_proj", "v_proj"]


def build_prompt(sample):
    """把 instruction/input/output 拼成模型训练文本。"""
    if sample.get("input"):
        user = f"{sample['instruction']}\n句子：{sample['input']}"
    else:
        user = sample["instruction"]
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": sample["output"]},
    ]


def main():
    # ---- 1. 加载基座模型与分词器 ----
    print(f"加载基座模型: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )

    # ---- 2. 注入 LoRA ----
    lora_cfg = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=TARGET_MODULES,
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()  # 观察可训练参数占比！

    # ---- 3. 加载数据 ----
    dataset = load_dataset("json", data_files=DATA_PATH, split="train")
    dataset = dataset.map(
        lambda s: {"messages": build_prompt(s)}, remove_columns=dataset.column_names
    )

    # ---- 4. 训练 ----
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=SFTConfig(
            output_dir=OUTPUT_DIR,
            num_train_epochs=3,
            per_device_train_batch_size=4,
            gradient_accumulation_steps=2,
            learning_rate=2e-4,
            logging_steps=10,
            save_strategy="epoch",
            bf16=torch.cuda.is_bf16_supported(),
            report_to="none",
        ),
        processing_class=tokenizer,
    )
    trainer.train()

    # ---- 5. 保存适配器 ----
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"\n训练完成，LoRA 适配器已保存至: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
