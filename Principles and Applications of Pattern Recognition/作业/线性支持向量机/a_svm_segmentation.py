"""作业 a：自主设计的线性 SVM（简化 SMO 对偶解法）→ 水天分割 + 水天线检测。

运行：
    py -3.12 a_svm_segmentation.py
    py -3.12 a_svm_segmentation.py --image image.jpg --C 1.0
输出：
    results/a_svm_segmentation.png   分割叠加 + 逐列水天线(红) + 鲁棒拟合线(黄)
    results/a_waterline_scatter.png  逐列边界散点与拟合直线
    results/a_waterline.npz          逐列边界坐标（供 e 脚本评估）
    results/a_metrics.json           指标
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

from common import (OUT, boundary_error, classification_metrics, fit_robust_line,
                    labels_to_map, load_image, median_smooth, pixel_features,
                    pseudo_labels, save_metrics, save_plot, save_segmentation,
                    stratified_sample, waterline_from_labels)
from svm import train_dual

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    ap = argparse.ArgumentParser(description="a: 自设计 SVM 水天分割与水天线检测")
    ap.add_argument("--image", default=None, help="图片路径（默认自动查找 image.jpg / demo_boat.png）")
    ap.add_argument("--C", type=float, default=1.0, help="正则化参数 C")
    ap.add_argument("--max-per-class", type=int, default=400, help="每类训练样本上限")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    img, path, original = load_image(args.image)
    h, w, _ = img.shape
    print(f"[a] 图片: {path.name}  原尺寸 {original}  处理尺寸 {w}x{h}")

    X_all = pixel_features(img)
    lab = pseudo_labels(img)
    valid = lab.ravel() != 0

    idx, y_tr = stratified_sample(lab, args.max_per_class, args.seed)
    X_tr = X_all[idx]
    print(f"[a] 训练样本 {len(y_tr)}（每类约 {args.max_per_class}）")

    t0 = time.time()
    model = train_dual(X_tr, y_tr, C=args.C, seed=args.seed)
    train_time = time.time() - t0

    pred = model.predict(X_all)
    label_map = labels_to_map(pred, (h, w))
    xs, ys = waterline_from_labels(label_map, min_run=3)
    ys_smooth = median_smooth(ys.astype(float), 9)
    a_fit, b_fit, inlier, resid_std = fit_robust_line(xs, ys_smooth)

    metrics = {
        "script": "a_svm_segmentation",
        "image": str(path),
        "image_size_processed": [w, h],
        "C": args.C,
        "train_samples": int(len(y_tr)),
        "train_time_s": round(train_time, 3),
        "n_support_vectors": model.n_support_vectors(),
        "support_ratio": round(model.support_ratio(), 4),
        "dual_objective": round(model.dual_objective(), 6),
        "w": [round(float(v), 6) for v in model.w],
        "b": round(float(model.b), 6),
        "segmentation": classification_metrics(lab.ravel()[valid], pred[valid]),
        "waterline_columns": int(len(xs)),
        "waterline_coverage": round(float(len(xs) / w), 4),
        "line_fit": {"slope": round(a_fit, 5), "intercept": round(b_fit, 2),
                     "inlier_ratio": round(inlier, 4),
                     "residual_std_px": round(resid_std, 3)},
    }

    save_segmentation(img, label_map, "a_svm", (xs, ys_smooth), (a_fit, b_fit))

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.scatter(xs, ys, s=6, c="#d62728", label="逐列水天线")
    xg = np.linspace(0, w - 1, 100)
    ax.plot(xg, a_fit * xg + b_fit, c="#f2b705", lw=2,
            label=f"鲁棒拟合 y={a_fit:.3f}x+{b_fit:.1f}")
    ax.set_xlabel("列坐标 x")
    ax.set_ylabel("水天线行坐标 y")
    ax.set_title("a：逐列水天线与鲁棒直线拟合")
    ax.invert_yaxis()
    ax.legend(fontsize=8)
    save_plot(fig, "a_waterline_scatter")
    plt.close(fig)

    np.savez(OUT / "a_waterline.npz", xs=xs, ys=ys_smooth)
    np.save(OUT / "a_label_map.npy", label_map)
    metrics_path = save_metrics(metrics, "a_metrics")

    print("[a] 分割指标（相对空间先验伪标签）:", metrics["segmentation"])
    print(f"[a] 支持向量 {metrics['n_support_vectors']} 个（占比 {metrics['support_ratio']:.1%}）"
          f"，训练 {train_time:.2f}s")
    print(f"[a] 水天线覆盖 {metrics['waterline_columns']}/{w} 列，"
          f"拟合 y={a_fit:.4f}x+{b_fit:.2f}，内点率 {inlier:.1%}，残差 {resid_std:.2f}px")
    print(f"[a] 结果已保存到: {OUT}")
    print(f"[a] 指标文件: {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
