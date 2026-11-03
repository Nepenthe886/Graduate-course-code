"""公共工具：图片读取、特征构造、伪标签、水天线提取、结果保存与评估指标。

五个作业脚本（a~e）共用本模块；每个脚本都是独立可运行的入口。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
OUT.mkdir(parents=True, exist_ok=True)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


# ---------------------------------------------------------------- 图片与特征
def find_image(explicit: str | None = None) -> Path:
    """按优先级找图片：显式路径 > image.jpg/png > 目录内其它图片 > demo_boat.png。"""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"图片不存在: {explicit}")
        return p
    for name in ("image.jpg", "image.png", "image.jpeg", "boat.jpg", "boat.png"):
        p = HERE / name
        if p.is_file():
            return p
    candidates = []
    for ext in IMAGE_EXTS:
        candidates += [p for p in HERE.glob(f"*{ext}") if not p.name.startswith("demo_")]
    if candidates:
        return sorted(candidates)[0]
    demo = HERE / "demo_boat.png"
    if demo.is_file():
        return demo
    raise FileNotFoundError(
        "未找到图片：请把作业给定图片放到本目录并命名 image.jpg（或运行时加 --image 路径）")


def load_image(explicit: str | None = None, max_width: int = 480):
    """读图 -> (RGB 归一化 float 数组 HxWx3, 路径, 原始尺寸)。"""
    path = find_image(explicit)
    im = Image.open(path).convert("RGB")
    original = im.size
    if im.width > max_width:
        h = max(1, round(im.height * max_width / im.width))
        im = im.resize((max_width, h), Image.BILINEAR)
    return np.asarray(im, dtype=np.float64) / 255.0, path, original


def pixel_features(img: np.ndarray) -> np.ndarray:
    """每个像素的颜色特征（RGB，0~1），形状 (N, 3)。"""
    return img.reshape(-1, 3)


def pseudo_labels(img: np.ndarray, sky_frac: float = 0.20,
                  water_start: float = 0.45, water_end: float = 0.85):
    """用空间先验生成训练伪标签：上部天空=+1，中部水面=-1，其余（含船体/前景）忽略(0)。

    默认参数对应本作业图片结构：天空约占上部 20%，水面约占 45%~85%，
    最下方 15% 常被船体等前景占据，若标为水面会引入大量噪声，故排除。
    """
    h, w, _ = img.shape
    labels = np.zeros((h, w), dtype=np.int8)
    labels[: max(1, int(h * sky_frac))] = 1
    labels[int(h * water_start): int(h * water_end)] = -1
    return labels


def stratified_sample(labels2d: np.ndarray, max_per_class: int = 400, seed: int = 0):
    """分层抽样训练集，返回 (X, y, 索引)。"""
    flat = labels2d.ravel()
    rng = np.random.default_rng(seed)
    sky = rng.permutation(np.flatnonzero(flat == 1))
    water = rng.permutation(np.flatnonzero(flat == -1))
    idx = np.concatenate([sky[:max_per_class], water[:max_per_class]])
    rng.shuffle(idx)
    y = flat[idx].astype(np.float64)
    return idx, y


# ---------------------------------------------------------------- 水天线提取
def labels_to_map(pred: np.ndarray, shape) -> np.ndarray:
    return pred.reshape(shape[:2])


def waterline_from_labels(label_map: np.ndarray, min_run: int = 3):
    """逐列找“由天空转为水面”的行号，返回 (列号数组, 行号数组)，缺失列略过。"""
    h, w = label_map.shape
    xs, ys = [], []
    for c in range(w):
        col = label_map[:, c]
        starts = np.flatnonzero(col == -1)
        for r in starts:
            if r + min_run <= h and np.all(col[r: r + min_run] == -1):
                xs.append(c)
                ys.append(float(r))
                break
    return np.asarray(xs), np.asarray(ys)


def median_smooth(values: np.ndarray, width: int = 9) -> np.ndarray:
    """一维中值平滑（处理 NaN：忽略）。"""
    out = values.copy()
    half = max(1, width // 2)
    for i in range(len(values)):
        seg = values[max(0, i - half): min(len(values), i + half + 1)]
        seg = seg[~np.isnan(seg)]
        if seg.size:
            out[i] = np.median(seg)
    return out


def fit_robust_line(xs: np.ndarray, ys: np.ndarray, rounds: int = 3):
    """鲁棒直线拟合 y = a*x + b，返回 (a, b, 内点率, 残差标准差)。"""
    mask = ~np.isnan(xs) & ~np.isnan(ys)
    x, y = xs[mask], ys[mask]
    if x.size < 2:
        return 0.0, float(np.nanmean(ys)), 0.0, float("nan")
    keep = np.ones_like(x, dtype=bool)
    a, b = 0.0, float(np.mean(y))
    for _ in range(rounds):
        A = np.vstack([x[keep], np.ones(keep.sum())]).T
        coef, *_ = np.linalg.lstsq(A, y[keep], rcond=None)
        a, b = float(coef[0]), float(coef[1])
        resid = y - (a * x + b)
        sigma = max(1.0, 2.5 * float(np.std(resid[keep])))
        keep = np.abs(resid) <= sigma
        if keep.sum() < 2:
            break
    resid = y - (a * x + b)
    inlier_ratio = float(np.mean(np.abs(resid) <= max(2.0, 2.5 * np.std(resid[~np.isnan(resid)]))))
    return a, b, inlier_ratio, float(np.std(resid))


# ---------------------------------------------------------------- 结果保存
def save_segmentation(img: np.ndarray, label_map: np.ndarray, name: str,
                      boundary=None, fitted=None):
    """保存分割叠加图：天空浅蓝 / 水面深蓝 / 水天线红 / 拟合线黄。"""
    h, w, _ = img.shape
    overlay = img.copy()
    sky = label_map == 1
    water = label_map == -1
    overlay[sky] = overlay[sky] * 0.35 + np.array([0.55, 0.78, 1.0]) * 0.65
    overlay[water] = overlay[water] * 0.35 + np.array([0.10, 0.35, 0.85]) * 0.65
    out = Image.fromarray(np.clip(overlay * 255, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(out)
    if boundary is not None:
        xs, ys = boundary
        for i in range(len(xs) - 1):
            if abs(int(xs[i + 1]) - int(xs[i])) <= 2:
                draw.line([(int(xs[i]), int(ys[i])), (int(xs[i + 1]), int(ys[i + 1]))],
                          fill=(255, 60, 60), width=2)
    if fitted is not None:
        a, b = fitted
        draw.line([(0, int(b)), (w - 1, int(a * (w - 1) + b))], fill=(255, 220, 40), width=2)
    out.save(OUT / f"{name}_segmentation.png")
    return OUT / f"{name}_segmentation.png"


def save_plot(fig, name: str):
    path = OUT / f"{name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return path


def save_metrics(metrics: dict, name: str):
    path = OUT / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------- 评估指标
def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """以“水面(-1)”为正类计算 P/R/F1，并给出整体准确率。"""
    y_true = np.asarray(y_true).astype(int)
    y_pred = np.asarray(y_pred).astype(int)
    tp = int(np.sum((y_true == -1) & (y_pred == -1)))
    fp = int(np.sum((y_true != -1) & (y_pred == -1)))
    fn = int(np.sum((y_true == -1) & (y_pred != -1)))
    tn = int(np.sum((y_true != -1) & (y_pred != -1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    acc = (tp + tn) / max(1, tp + tn + fp + fn)
    return {"accuracy": round(acc, 4), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def boundary_error(xs_ref, ys_ref, xs_pred, ys_pred) -> dict:
    """两套逐列水天线之间的误差（只在公共列上比较）。"""
    ref = {int(x): float(y) for x, y in zip(xs_ref, ys_ref) if not np.isnan(y)}
    pred = {int(x): float(y) for x, y in zip(xs_pred, ys_pred) if not np.isnan(y)}
    cols = sorted(set(ref) & set(pred))
    if not cols:
        return {"columns": 0, "mae": float("nan"), "rmse": float("nan"),
                "inlier_2px": 0.0, "inlier_3px": 0.0}
    d = np.array([pred[c] - ref[c] for c in cols])
    return {"columns": len(cols),
            "mae": round(float(np.mean(np.abs(d))), 3),
            "rmse": round(float(np.sqrt(np.mean(d ** 2))), 3),
            "inlier_2px": round(float(np.mean(np.abs(d) <= 2)), 3),
            "inlier_3px": round(float(np.mean(np.abs(d) <= 3)), 3),
            "bias": round(float(np.mean(d)), 3)}


def gradient_waterline_reference(img: np.ndarray, band=(0.25, 0.80), smooth_width: int = 9):
    """独立的参考水天线：逐列在中间带内找竖直梯度最大处（用于 e 的精度评估）。"""
    gray = img @ np.array([0.299, 0.587, 0.114])
    # 先做竖直方向平滑，抑制噪声
    kernel = np.ones(5) / 5.0
    smooth = np.apply_along_axis(lambda c: np.convolve(c, kernel, mode="same"), 0, gray)
    grad = np.abs(np.diff(smooth, axis=0))
    h, w = grad.shape
    y0, y1 = int(h * band[0]), int(h * band[1])
    xs, ys = [], []
    for c in range(w):
        r = y0 + int(np.argmax(grad[y0:y1, c]))
        xs.append(c)
        ys.append(float(r))
    ys = median_smooth(np.asarray(ys), smooth_width)
    return np.asarray(xs), ys
