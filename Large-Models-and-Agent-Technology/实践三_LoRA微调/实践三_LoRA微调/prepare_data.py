# -*- coding: utf-8 -*-
"""生成"礼貌语气改写"指令数据集：data/train.jsonl

让模型学会把命令式句子改写为礼貌委婉的表达。
可替换 make_pair 逻辑生成自己的任务数据。
"""
import json
import os
import random

OUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT_PATH = os.path.join(OUT_DIR, "train.jsonl")
N_SAMPLES = 300

RUDE_TEMPLATES = [
    "把{obj}给我", "去{action}", "把{obj}关上", "帮我拿{obj}",
    "别{action}", "把{obj}放下", "快去{action}", "把{obj}递过来",
]
POLITE_TEMPLATES = [
    "麻烦您把{obj}给我，可以吗？", "请您{action}，谢谢！",
    "劳驾把{obj}关上，好吗？", "能麻烦您帮我拿一下{obj}吗？非常感谢！",
    "请您不要{action}，可以吗？", "麻烦您把{obj}放下，谢谢您！",
    "麻烦您抽空去{action}，好吗？", "请您把{obj}递给我，谢谢！",
]
assert len(RUDE_TEMPLATES) == len(POLITE_TEMPLATES)
OBJECTS = ["门", "窗户", "书", "水杯", "文件", "笔", "椅子", "包", "伞", "盒子"]
ACTIONS = ["开会", "打印材料", "回复邮件", "收拾桌面", "检查作业", "买咖啡"]


def make_pair(rng):
    """同一语义的粗鲁/礼貌句一一对应。"""
    obj, act = rng.choice(OBJECTS), rng.choice(ACTIONS)
    i = rng.randrange(len(RUDE_TEMPLATES))
    rude = RUDE_TEMPLATES[i].format(obj=obj, action=act)
    polite = POLITE_TEMPLATES[i].format(obj=obj, action=act)
    return rude, polite


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = random.Random(42)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for _ in range(N_SAMPLES):
            rude, polite = make_pair(rng)
            sample = {
                "instruction": "把下面的句子改写成礼貌、委婉的语气",
                "input": rude,
                "output": polite,
            }
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    print(f"已生成 {N_SAMPLES} 条样本 -> {OUT_PATH}")
    with open(OUT_PATH, encoding="utf-8") as f:
        print("示例:", f.readline().strip())


if __name__ == "__main__":
    main()
