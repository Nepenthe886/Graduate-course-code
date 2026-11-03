"""作业 d：量化评估“原空间求解”与“对偶空间求解”的线性 SVM 差异。

对比对象：
    - 原空间法：svm.LinearSVMPrimal（hinge 损失 + L2 的次梯度下降）
    - 对偶空间法：OpenCV LIBSVM（对偶 QP），不可用时回退自实现 SMO
评估维度：
    1) 训练耗时、支持向量数量
    2) 原空间目标值（越小越好）、对偶目标值、对偶间隙 P - D
    3) 分割指标（相对空间先验伪标签）：accuracy / precision / recall / F1
    4) 两解法在全图像素上的判决一致率、权重余弦相似度、||w|| 差异
    5) 两条逐列水天线之间的 MAE / RMSE / ±2px 内点率

运行：
    py -3.12 d_compare.py
输出：
    results/d_compare.csv / d_compare.json / d_compare_bars.png / d_waterline_compare.png
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
                    pseudo_labels, save_metrics, save_plot, stratified_sample,
                    waterline_from_labels)
from svm import train_dual, train_primal

try:
    from c_dual_svm import DualQPScipy
except Exception:  # noqa: BLE001
    DualQPScipy = None

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    ap = argparse.ArgumentParser(description="d: 原空间法 vs 对偶空间法 量化对比")
    ap.add_argument("--image", default=None)
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--epochs", type=int, default=3000)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--decay", type=float, default=0.5)
    ap.add_argument("--max-per-class", type=int, default=200,
                    help="每类训练样本上限（对偶 QP 规模）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    img, path, original = load_image(args.image)
    h, w, _ = img.shape
    print(f"[d] 图片: {path.name}  处理尺寸 {w}x{h}")

    X_all = pixel_features(img)
    lab = pseudo_labels(img)
    valid_mask = lab.ravel() != 0
    idx, y_tr = stratified_sample(lab, args.max_per_class, args.seed)
    X_tr = X_all[idx]
    print(f"[d] 两解法使用同一训练集：{len(y_tr)} 样本，C={args.C}")

    # ---- 原空间 ----
    t0 = time.time()
    primal = train_primal(X_tr, y_tr, C=args.C, epochs=args.epochs, lr=args.lr,
                          lr_decay=args.decay, seed=args.seed)
    t_primal = time.time() - t0
    pred_primal = primal.predict(X_all)
    obj_primal = primal.objective(X_tr, y_tr)

    # ---- 对偶空间 ----
    dual = None
    solver = ""
    t0 = time.time()
    if DualQPScipy is not None:
        try:
            dual = DualQPScipy(C=args.C).fit(X_tr, y_tr)
            solver = "scipy SLSQP（对偶 QP）"
        except Exception as exc:  # noqa: BLE001
            print(f"[d] SLSQP 不可用（{exc}），改用自实现 SMO")
    if dual is None:
        dual = train_dual(X_tr, y_tr, C=args.C, seed=args.seed)
        solver = "自实现简化 SMO（对偶）"
    t_dual = time.time() - t0
    pred_dual = dual.predict(X_all)
    obj_dual_at_dual = dual.dual_objective()
    obj_primal_at_dual = float(0.5 * dual.w @ dual.w
                               + args.C * np.maximum(0.0, 1.0 - y_tr * (X_tr @ dual.w + dual.b)).sum())

    # ---- 指标 ----
    m_primal = classification_metrics(lab.ravel()[valid_mask], pred_primal[valid_mask])
    m_dual = classification_metrics(lab.ravel()[valid_mask], pred_dual[valid_mask])
    agreement = float(np.mean(pred_primal == pred_dual))
    cos = float(dual.w @ primal.w / (np.linalg.norm(dual.w) * np.linalg.norm(primal.w) + 1e-12))
    w_norm_diff = float(np.linalg.norm(primal.w - dual.w) / (np.linalg.norm(primal.w) + 1e-12))

    # 逐列水天线对比
    lm_p = labels_to_map(pred_primal, (h, w))
    lm_d = labels_to_map(pred_dual, (h, w))
    xp, yp = waterline_from_labels(lm_p)
    xd, yd = waterline_from_labels(lm_d)
    yp_s = median_smooth(yp.astype(float), 9)
    yd_s = median_smooth(yd.astype(float), 9)
    b_err = boundary_error(xp, yp_s, xd, yd_s)

    a_p, b_p, inl_p, _ = fit_robust_line(xp, yp_s)
    a_d, b_d, inl_d, _ = fit_robust_line(xd, yd_s)

    n_sv_primal = primal.n_support_vectors(X_tr, y_tr)
    n_sv_dual = int(getattr(dual, "n_sv", 0)) or int(np.sum(getattr(dual, "alpha", np.array([])) > 1e-6))

    rows = [
        ("求解器", "原空间次梯度", solver),
        ("训练耗时(s)", round(t_primal, 3), round(t_dual, 3)),
        ("支持向量数", n_sv_primal, n_sv_dual),
        ("原空间目标值 P(w,b)", round(obj_primal, 6), round(obj_primal_at_dual, 6)),
        ("对偶目标值 D(α)", "-", round(obj_dual_at_dual, 6)),
        ("对偶间隙 P-D", "-", round(obj_primal_at_dual - obj_dual_at_dual, 6)),
        ("分割 accuracy", m_primal["accuracy"], m_dual["accuracy"]),
        ("分割 precision", m_primal["precision"], m_dual["precision"]),
        ("分割 recall", m_primal["recall"], m_dual["recall"]),
        ("分割 F1", m_primal["f1"], m_dual["f1"]),
        ("||w||", round(float(np.linalg.norm(primal.w)), 4), round(float(np.linalg.norm(dual.w)), 4)),
        ("权重余弦相似度", 1.0, round(cos, 6)),
        ("w 相对差异", 0.0, round(w_norm_diff, 6)),
        ("全像素判决一致率", 1.0, round(agreement, 6)),
        ("水天线 MAE(px)", "-", b_err["mae"]),
        ("水天线 RMSE(px)", "-", b_err["rmse"]),
        ("水天线 ±2px 内点率", "-", b_err["inlier_2px"]),
        ("水天线公共列数", len(xp), len(xd)),
    ]
    metrics = {
        "image": str(path),
        "C": args.C,
        "train_samples": int(len(y_tr)),
        "dual_solver": solver,
        "primal": {"train_time_s": round(t_primal, 3), "n_sv": n_sv_primal,
                   "w": [round(float(v), 6) for v in primal.w], "b": round(float(primal.b), 6),
                   "objective": round(obj_primal, 6), "segmentation": m_primal},
        "dual": {"train_time_s": round(t_dual, 3), "n_sv": n_sv_dual,
                 "w": [round(float(v), 6) for v in dual.w], "b": round(float(dual.b), 6),
                 "dual_objective": round(obj_dual_at_dual, 6),
                 "primal_objective_at_dual_solution": round(obj_primal_at_dual, 6),
                 "duality_gap": round(obj_primal_at_dual - obj_dual_at_dual, 6),
                 "segmentation": m_dual},
        "agreement": round(agreement, 6),
        "cosine_similarity_w": round(cos, 6),
        "relative_w_diff": round(w_norm_diff, 6),
        "waterline_diff": b_err,
        "line_fit_primal": {"slope": round(a_p, 5), "intercept": round(b_p, 2)},
        "line_fit_dual": {"slope": round(a_d, 5), "intercept": round(b_d, 2)},
    }
    save_metrics(metrics, "d_compare")

    # CSV
    import csv
    with open(OUT / "d_compare.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["指标", "原空间法", "对偶空间法"])
        for name, a, b in rows:
            writer.writerow([name, a, b])

    # 柱状图
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    label_names = ["accuracy", "precision", "recall", "f1"]
    x = np.arange(len(label_names))
    axes[0].bar(x - 0.18, [m_primal[k] for k in label_names], width=0.36, label="原空间", color="#4c72b0")
    axes[0].bar(x + 0.18, [m_dual[k] for k in label_names], width=0.36, label="对偶空间", color="#c44e52")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(label_names)
    axes[0].set_ylim(0.0, 1.05)
    axes[0].set_title("分割指标对比")
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=.25)

    axes[1].bar(["原空间", "对偶空间"], [t_primal, t_dual], color=["#4c72b0", "#c44e52"])
    axes[1].set_title("训练耗时（秒）")
    for i, v in enumerate([t_primal, t_dual]):
        axes[1].text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    axes[1].grid(axis="y", alpha=.25)

    axes[2].bar(["原空间", "对偶空间"], [n_sv_primal, n_sv_dual], color=["#4c72b0", "#c44e52"])
    axes[2].set_title("支持向量数量")
    for i, v in enumerate([n_sv_primal, n_sv_dual]):
        axes[2].text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    axes[2].grid(axis="y", alpha=.25)
    save_plot(fig, "d_compare_bars")
    plt.close(fig)

    # 水天线对比图
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    ax.scatter(xp, yp_s, s=5, c="#4c72b0", label="原空间法边界")
    ax.scatter(xd, yd_s, s=5, c="#c44e52", label="对偶空间法边界")
    xg = np.linspace(0, w - 1, 100)
    ax.plot(xg, a_p * xg + b_p, c="#4c72b0", lw=1.8, ls="--")
    ax.plot(xg, a_d * xg + b_d, c="#c44e52", lw=1.8, ls=":")
    ax.set_xlabel("列坐标 x")
    ax.set_ylabel("水天线行坐标 y")
    ax.invert_yaxis()
    ax.set_title(f"d：两种解法水天线对比（MAE={b_err['mae']}px，一致率={agreement:.1%}）")
    ax.legend(fontsize=8)
    save_plot(fig, "d_waterline_compare")
    plt.close(fig)

    print("\n[d] 对比结果")
    for name, a, b in rows:
        print(f"  {name}：原空间 = {a} ｜ 对偶空间 = {b}")
    print(f"\n[d] 结果已保存: {OUT / 'd_compare.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
