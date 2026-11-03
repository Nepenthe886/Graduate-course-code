"""作业 b：原空间法求解线性 SVM（hinge 损失 + L2 正则的次梯度下降）→ 水天分割。

运行：
    py -3.12 b_primal_svm.py
    py -3.12 b_primal_svm.py --image image.jpg --C 1.0 --epochs 400
输出：
    results/b_primal_segmentation.png
    results/b_waterline.npz
    results/b_metrics.json
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (OUT, classification_metrics, fit_robust_line, labels_to_map,
                    load_image, median_smooth, pixel_features, pseudo_labels,
                    save_metrics, save_plot, save_segmentation, stratified_sample,
                    waterline_from_labels)
from svm import train_primal

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    ap = argparse.ArgumentParser(description="b: 原空间法线性 SVM 水天分割")
    ap.add_argument("--image", default=None)
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--epochs", type=int, default=3000)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--decay", type=float, default=0.5)
    ap.add_argument("--max-per-class", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    img, path, original = load_image(args.image)
    h, w, _ = img.shape
    print(f"[b] 图片: {path.name}  处理尺寸 {w}x{h}")

    X_all = pixel_features(img)
    lab = pseudo_labels(img)
    valid = lab.ravel() != 0

    idx, y_tr = stratified_sample(lab, args.max_per_class, args.seed)
    X_tr = X_all[idx]
    print(f"[b] 训练样本 {len(y_tr)}，原空间次梯度下降 epochs={args.epochs}, lr={args.lr}, C={args.C}")

    t0 = time.time()
    model = train_primal(X_tr, y_tr, C=args.C, epochs=args.epochs, lr=args.lr,
                         lr_decay=args.decay, seed=args.seed)
    train_time = time.time() - t0

    pred = model.predict(X_all)
    label_map = labels_to_map(pred, (h, w))
    xs, ys = waterline_from_labels(label_map, min_run=3)
    ys_smooth = median_smooth(ys.astype(float), 9)
    a_fit, b_fit, inlier, resid_std = fit_robust_line(xs, ys_smooth)

    metrics = {
        "script": "b_primal_svm",
        "image": str(path),
        "image_size_processed": [w, h],
        "C": args.C,
        "epochs": args.epochs,
        "learning_rate": args.lr,
        "train_samples": int(len(y_tr)),
        "train_time_s": round(train_time, 3),
        "primal_objective": round(model.objective(X_tr, y_tr), 6),
        "n_support_vectors": model.n_support_vectors(X_tr, y_tr),
        "w": [round(float(v), 6) for v in model.w],
        "b": round(float(model.b), 6),
        "segmentation": classification_metrics(lab.ravel()[valid], pred[valid]),
        "waterline_columns": int(len(xs)),
        "line_fit": {"slope": round(a_fit, 5), "intercept": round(b_fit, 2),
                     "inlier_ratio": round(inlier, 4),
                     "residual_std_px": round(resid_std, 3)},
    }
    save_segmentation(img, label_map, "b_primal", (xs, ys_smooth), (a_fit, b_fit))

    fig, ax = plt.subplots(figsize=(6.5, 2.8))
    ax.plot(model.loss_history, lw=1.8, c="#1f77b4")
    ax.set_xlabel("记录点（每 20 轮）")
    ax.set_ylabel("原空间目标值")
    ax.set_title("b：原空间法目标函数收敛曲线")
    ax.grid(alpha=.25)
    save_plot(fig, "b_primal_loss")
    plt.close(fig)

    np.savez(OUT / "b_waterline.npz", xs=xs, ys=ys_smooth)
    np.save(OUT / "b_label_map.npy", label_map)
    save_metrics(metrics, "b_metrics")

    print(f"[b] 原空间目标值 {metrics['primal_objective']:.6f}，"
          f"训练 {train_time:.3f}s，支持向量 {metrics['n_support_vectors']}")
    print("[b] 分割指标:", metrics["segmentation"])
    print(f"[b] 水天线覆盖 {len(xs)}/{w} 列，拟合 y={a_fit:.4f}x+{b_fit:.2f}")
    print(f"[b] 结果已保存到: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
