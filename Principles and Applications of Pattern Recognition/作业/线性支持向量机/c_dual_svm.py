"""作业 c：线性 SVM 的经典求解方法（对偶空间 QP）→ 水天分割。

对偶问题：
    max_α  Σα_i − ½ ΣΣ α_i α_j y_i y_j (x_i·x_j)
    s.t.   0 ≤ α_i ≤ C,  Σ α_i y_i = 0

本脚本用 scipy.optimize.minimize（SLSQP）直接求解该带箱约束与等式约束的二次规划——
即不使用自写 SMO，而是走“通用凸优化求解器解对偶 QP”的经典路径；
若 scipy 不可用，回退到 svm.py 中的自实现 SMO。

运行：
    py -3.12 c_dual_svm.py
    py -3.12 c_dual_svm.py --image image.jpg --C 1.0 --max-per-class 200
输出：
    results/c_dual_segmentation.png
    results/c_waterline.npz
    results/c_metrics.json
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
from svm import train_dual

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


class DualQPScipy:
    """用 SLSQP 求解 SVM 对偶 QP，接口与 svm.py 中的模型保持一致。"""

    def __init__(self, C: float = 1.0, maxiter: int = 300, ftol: float = 1e-9):
        self.C = float(C)
        self.maxiter = int(maxiter)
        self.ftol = float(ftol)
        self.w = None
        self.b = 0.0
        self.alpha = None
        self.solver = "SLSQP 对偶 QP"
        self.n_sv = 0

    def fit(self, X: np.ndarray, y: np.ndarray):
        from scipy.optimize import minimize

        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        n = len(y)
        K = X @ X.T
        Q = (y[:, None] * y[None, :]) * K  # 目标函数 Hessian

        def fun(a):
            return 0.5 * a @ Q @ a - a.sum()

        def jac(a):
            return Q @ a - 1.0

        cons = [{"type": "eq", "fun": lambda a: float(y @ a), "jac": lambda a: y}]
        res = minimize(fun, np.zeros(n), jac=jac, bounds=[(0.0, self.C)] * n,
                       constraints=cons, method="SLSQP",
                       options={"maxiter": self.maxiter, "ftol": self.ftol})
        alpha = np.clip(res.x, 0.0, self.C)
        w = (alpha * y) @ X
        free = (alpha > 1e-6) & (alpha < self.C - 1e-6)
        if free.any():
            b = float(np.median(y[free] - X[free] @ w))
        else:
            pos = X[y > 0] @ w
            neg = X[y < 0] @ w
            b = -0.5 * (float(pos.min()) + float(neg.max()))
        self.alpha, self.w, self.b = alpha, w, b
        self.n_sv = int(np.sum(alpha > 1e-6))
        self.success = bool(getattr(res, "success", False))
        self.message = str(getattr(res, "message", ""))
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(X, dtype=np.float64) @ self.w + self.b

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.where(self.decision_function(X) >= 0, 1, -1)

    def dual_objective(self) -> float:
        return float(self.alpha.sum() - 0.5 * self.w @ self.w)

    def primal_objective(self, X: np.ndarray, y: np.ndarray) -> float:
        margins = y * (X @ self.w + self.b)
        return float(0.5 * self.w @ self.w + self.C * np.maximum(0.0, 1.0 - margins).sum())


def main() -> int:
    ap = argparse.ArgumentParser(description="c: 对偶空间经典解法（QP）水天分割")
    ap.add_argument("--image", default=None)
    ap.add_argument("--C", type=float, default=1.0)
    ap.add_argument("--max-per-class", type=int, default=200,
                    help="每类训练样本上限（对偶 QP 规模，默认 200）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    img, path, original = load_image(args.image)
    h, w, _ = img.shape
    print(f"[c] 图片: {path.name}  处理尺寸 {w}x{h}")

    X_all = pixel_features(img)
    lab = pseudo_labels(img)
    valid = lab.ravel() != 0

    idx, y_tr = stratified_sample(lab, args.max_per_class, args.seed)
    X_tr = X_all[idx]
    print(f"[c] 训练样本 {len(y_tr)}，对偶空间 QP 求解 C={args.C}")

    t0 = time.time()
    try:
        model = DualQPScipy(C=args.C).fit(X_tr, y_tr)
        solver = "scipy SLSQP（对偶 QP）"
    except Exception as exc:  # noqa: BLE001
        print(f"[c] SLSQP 不可用（{exc}），回退到自实现 SMO 对偶解法")
        model = train_dual(X_tr, y_tr, C=args.C, seed=args.seed)
        solver = "自实现简化 SMO（对偶）"
    train_time = time.time() - t0

    pred = model.predict(X_all)
    label_map = labels_to_map(pred, (h, w))
    xs, ys = waterline_from_labels(label_map, min_run=3)
    ys_smooth = median_smooth(ys.astype(float), 9)
    a_fit, b_fit, inlier, resid_std = fit_robust_line(xs, ys_smooth)

    dual_obj = model.dual_objective()
    primal_obj = (model.primal_objective(X_tr, y_tr)
                  if hasattr(model, "primal_objective")
                  else float(0.5 * model.w @ model.w + args.C *
                             np.maximum(0.0, 1.0 - y_tr * (X_tr @ model.w + model.b)).sum()))
    metrics = {
        "script": "c_dual_svm",
        "solver": solver,
        "image": str(path),
        "image_size_processed": [w, h],
        "C": args.C,
        "train_samples": int(len(y_tr)),
        "train_time_s": round(train_time, 3),
        "n_support_vectors": int(getattr(model, "n_sv", 0)),
        "dual_objective": round(dual_obj, 6),
        "primal_objective_at_dual_solution": round(primal_obj, 6),
        "duality_gap": round(primal_obj - dual_obj, 8),
        "w": [round(float(v), 6) for v in model.w],
        "b": round(float(model.b), 6),
        "segmentation": classification_metrics(lab.ravel()[valid], pred[valid]),
        "waterline_columns": int(len(xs)),
        "line_fit": {"slope": round(a_fit, 5), "intercept": round(b_fit, 2),
                     "inlier_ratio": round(inlier, 4),
                     "residual_std_px": round(resid_std, 3)},
    }

    save_segmentation(img, label_map, "c_dual", (xs, ys_smooth), (a_fit, b_fit))

    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    names = ["accuracy", "precision", "recall", "f1"]
    vals = [metrics["segmentation"][k] for k in names]
    bars = ax.bar(names, vals, color=["#4c72b0", "#55a868", "#c44e52", "#8172b2"])
    ax.set_ylim(0.0, 1.05)
    ax.set_title("c：对偶空间 QP 解法分割指标")
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.02, f"{v:.3f}", ha="center", fontsize=9)
    ax.grid(axis="y", alpha=.25)
    save_plot(fig, "c_dual_metrics")
    plt.close(fig)

    np.savez(OUT / "c_waterline.npz", xs=xs, ys=ys_smooth)
    np.save(OUT / "c_label_map.npy", label_map)
    save_metrics(metrics, "c_metrics")

    print(f"[c] 求解器: {solver}，训练 {train_time:.3f}s，支持向量 {metrics['n_support_vectors']}")
    print(f"[c] 对偶目标值 D={metrics['dual_objective']:.6f}，"
          f"原空间目标值 P={metrics['primal_objective_at_dual_solution']:.6f}，"
          f"对偶间隙={metrics['duality_gap']:.2e}")
    print("[c] 分割指标:", metrics["segmentation"])
    print(f"[c] 水天线覆盖 {len(xs)}/{w} 列，拟合 y={a_fit:.4f}x+{b_fit:.2f}")
    print(f"[c] 结果已保存到: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
