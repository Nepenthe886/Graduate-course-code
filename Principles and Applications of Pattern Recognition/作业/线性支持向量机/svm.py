"""线性支持向量机：原空间解法 + 对偶空间解法（均为从零实现，不依赖 sklearn）。

样本约定：X 形状 (n, d)，标签 y ∈ {+1, -1}。
"""
from __future__ import annotations

import numpy as np


class LinearSVMPrimal:
    """原空间法：直接对 hinge 损失 + L2 正则做（批量）次梯度下降。

    目标函数：
        min_{w,b}  0.5 * ||w||^2 + C * Σ_i max(0, 1 - y_i (w·x_i + b))
    次梯度：
        ∂/∂w = w - C Σ_{margin<1} y_i x_i
        ∂/∂b =   - C Σ_{margin<1} y_i
    """

    def __init__(self, C: float = 1.0, lr: float = 0.5, epochs: int = 400,
                 lr_decay: float = 1.0, seed: int = 0):
        self.C = float(C)
        self.lr0 = float(lr)
        self.epochs = int(epochs)
        self.lr_decay = float(lr_decay)
        self.seed = int(seed)
        self.w = None
        self.b = 0.0
        self.loss_history = []

    def fit(self, X: np.ndarray, y: np.ndarray):
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        n, d = X.shape
        rng = np.random.default_rng(self.seed)
        w = np.zeros(d)
        b = 0.0
        for epoch in range(self.epochs):
            lr = self.lr0 / (1.0 + self.lr_decay * epoch)
            idx = rng.permutation(n)
            Xe, ye = X[idx], y[idx]
            margin = ye * (Xe @ w + b)
            active = margin < 1.0
            if active.any():
                Xa, ya = Xe[active], ye[active]
                grad_w = w - self.C * (Xa * ya[:, None]).sum(axis=0)
                grad_b = -self.C * ya.sum()
            else:
                grad_w, grad_b = w, 0.0
            w -= lr * grad_w
            b -= lr * grad_b
            if epoch % 20 == 0 or epoch == self.epochs - 1:
                self.loss_history.append(self.objective(X, y, w, b))
        self.w, self.b = w, b
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(X, dtype=np.float64) @ self.w + self.b

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.where(self.decision_function(X) >= 0, 1, -1)

    def objective(self, X: np.ndarray, y: np.ndarray, w=None, b=None) -> float:
        w = self.w if w is None else w
        b = self.b if b is None else b
        margins = y * (X @ w + b)
        hinge = np.maximum(0.0, 1.0 - margins).sum()
        return float(0.5 * w @ w + self.C * hinge)

    def n_support_vectors(self, X: np.ndarray, y: np.ndarray, tol: float = 1e-3) -> int:
        margins = y * self.decision_function(X)
        return int(np.sum(margins <= 1.0 + tol))


class LinearSVMDualSMO:
    """对偶空间法：求解

        max_α  Σ α_i - 0.5 ΣΣ α_i α_j y_i y_j (x_i·x_j)
        s.t.   0 ≤ α_i ≤ C,  Σ α_i y_i = 0

    使用简化 SMO（序列最小优化，逐对更新 α_i, α_j），线性核下维护 w = Σ α_i y_i x_i。
    """

    def __init__(self, C: float = 1.0, tol: float = 1e-3, max_passes: int = 12, seed: int = 0):
        self.C = float(C)
        self.tol = float(tol)
        self.max_passes = int(max_passes)
        self.seed = int(seed)
        self.alpha = None
        self.w = None
        self.b = 0.0

    def fit(self, X: np.ndarray, y: np.ndarray):
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        n, d = X.shape
        rng = np.random.default_rng(self.seed)
        alpha = np.zeros(n)
        w = np.zeros(d)
        b = 0.0
        K = X @ X.T  # 线性核矩阵（n 较小，直接缓存）
        passes = 0
        while passes < self.max_passes:
            num_changed = 0
            for i in rng.permutation(n):
                Ei = X[i] @ w + b - y[i]
                ai = alpha[i]
                yEi = y[i] * Ei
                if not ((yEi < -self.tol and ai < self.C - 1e-9) or
                        (yEi > self.tol and ai > 1e-9)):
                    continue
                E = X @ w + b - y
                # 可行配对：异类且 α_j<C（可同增），或同类且 α_i+α_j>0（可互相转移）
                feasible = (((y != y[i]) & (alpha < self.C - 1e-9)) |
                            ((y == y[i]) & (alpha + ai > 1e-9)))
                feasible[i] = False
                if not feasible.any():
                    continue
                # 优先选择“朝相反方向违反 KKT”的样本，其次在全部可行样本中选
                if yEi < -self.tol:
                    cand = feasible & (y * E > self.tol)
                else:
                    cand = feasible & (y * E < -self.tol)
                if not cand.any():
                    cand = feasible
                diffs = np.abs(E - Ei)
                diffs[~cand] = -np.inf
                j = int(np.argmax(diffs))
                if j == i:
                    continue
                Ej = E[j]
                aj = alpha[j]
                xi, xj = X[i], X[j]
                Kii, Kjj, Kij = K[i, i], K[j, j], K[i, j]
                eta = 2.0 * Kij - Kii - Kjj
                if eta >= 0:
                    continue
                aj_new = aj - y[j] * (Ei - Ej) / eta
                if y[i] != y[j]:
                    L = max(0.0, aj - ai)
                    H = min(self.C, self.C + aj - ai)
                else:
                    L = max(0.0, aj + ai - self.C)
                    H = min(self.C, aj + ai)
                if H <= L:
                    continue
                aj_new = float(np.clip(aj_new, L, H))
                if abs(aj_new - aj) < 1e-10:
                    continue
                ai_new = ai + y[i] * y[j] * (aj - aj_new)
                w += (ai_new - ai) * y[i] * xi + (aj_new - aj) * y[j] * xj
                b1 = b - Ei - y[i] * (ai_new - ai) * Kii - y[j] * (aj_new - aj) * Kij
                b2 = b - Ej - y[i] * (ai_new - ai) * Kij - y[j] * (aj_new - aj) * Kjj
                if 0 < ai_new < self.C:
                    b = b1
                elif 0 < aj_new < self.C:
                    b = b2
                else:
                    b = 0.5 * (b1 + b2)
                alpha[i], alpha[j] = ai_new, aj_new
                num_changed += 1
            passes = 0 if num_changed else passes + 1
        self.alpha = alpha
        self.w = w
        self.b = float(b)
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(X, dtype=np.float64) @ self.w + self.b

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.where(self.decision_function(X) >= 0, 1, -1)

    def dual_objective(self) -> float:
        return float(self.alpha.sum() - 0.5 * self.w @ self.w)

    def n_support_vectors(self) -> int:
        return int(np.sum(self.alpha > 1e-6))

    def support_ratio(self) -> float:
        return float(np.mean(self.alpha > 1e-6)) if self.alpha is not None else 0.0


def train_primal(X, y, C=1.0, epochs=3000, lr=0.5, lr_decay=0.5, seed=0) -> LinearSVMPrimal:
    return LinearSVMPrimal(C=C, lr=lr, epochs=epochs, lr_decay=lr_decay, seed=seed).fit(X, y)


def train_dual(X, y, C=1.0, tol=1e-3, max_passes=12, seed=0) -> LinearSVMDualSMO:
    return LinearSVMDualSMO(C=C, tol=tol, max_passes=max_passes, seed=seed).fit(X, y)
