# 线性支持向量机作业（a~e 五个独立脚本）

本目录对应作业《线性支持向量机》：用线性 SVM 做**水天分割**并检测**水天线**，
并比较"原空间法"与"对偶空间法"两种求解方式。

## 一、文件说明

| 题目 | 启动文件 | 内容 |
|---|---|---|
| **a** | `a_svm_segmentation.py` | 自主设计的线性 SVM（自实现简化 SMO 对偶解法）→ 水天分割，并在分割结果上检测水天线、鲁棒拟合直线 |
| **b** | `b_primal_svm.py` | **原空间法**：hinge 损失 + L2 正则的次梯度下降求解，做水天分割 |
| **c** | `c_dual_svm.py` | **对偶空间经典解法**：用 `scipy.optimize.minimize`（SLSQP）求解对偶 QP（带箱约束与等式约束），不可用时回退自实现 SMO |
| **d** | `d_compare.py` | 量化对比原空间法与对偶空间法：目标值、对偶间隙、支持向量数、训练耗时、判决一致率、权重余弦、水天线 MAE 等 |
| **e** | `e_waterline_accuracy.py` | 如何评估水天线检测准确性：参考真值、MAE/RMSE/±2px 内点率、检测率、分割 IoU、鲁棒性 |
| 公共模块 | `common.py` / `svm.py` | 图像与特征、伪标签、水天线提取、绘图；两种 SVM 的从零实现 |

## 二、环境要求

```bat
py -3.12 -c "import numpy, PIL, matplotlib, scipy, pandas"
```

依赖：`numpy`、`Pillow`、`matplotlib`、`scipy`（对偶 QP 求解）、`pandas`（可选）。
OpenCV 不是必需依赖：本方案用 scipy 的 SLSQP 求解对偶问题，不依赖 `cv2.ml`。

## 三、准备图片

把作业给定图片放到本目录，命名为 **`image.jpg`**（也支持 `image.png` / `boat.jpg`）。
目录里的 `demo_boat.png` 是从课件幻灯片裁出的演示图（仅用于先跑通流程）。

也可以运行时指定：

```bat
py -3.12 a_svm_segmentation.py --image D:\path\to\your.jpg
```

## 四、运行方式（每个脚本都可单独启动）

```bat
cd /d "E:\Code\Graduate-course-code\Principles and Applications of Pattern Recognition\作业\线性支持向量机"

py -3.12 a_svm_segmentation.py
py -3.12 b_primal_svm.py
py -3.12 c_dual_svm.py
py -3.12 d_compare.py
py -3.12 e_waterline_accuracy.py
```

常用参数：

```bat
py -3.12 a_svm_segmentation.py --C 1.0 --max-per-class 400
py -3.12 b_primal_svm.py --C 1.0 --epochs 400 --lr 0.5
py -3.12 c_dual_svm.py --C 1.0
```

## 五、方法要点

1. **训练样本从哪来**：没有逐像素人工标注时，用空间先验生成伪标签——
   图像上部 30% 视为天空（+1）、下部 30% 视为水面（−1），中间 40% 不参与训练。
   每类最多采样 400 个像素，特征为归一化 RGB（3 维）。
2. **a 的"自设计算法"**：自实现简化 SMO——每次选取违反 KKT 条件最严重的样本对 (i, j)，
   在 0≤α≤C、Σαy=0 约束下解析更新 α，线性核下维护 w = Σαᵢyᵢxᵢ。
3. **b 的原空间法**：直接优化 `0.5||w||² + C·Σmax(0, 1−yᵢ(w·xᵢ+b))`，批量次梯度下降
   （默认 3000 轮、学习率 0.5 并按 `lr/(1+0.5·epoch)` 衰减），收敛后原空间目标值与对偶最优相差可忽略。
4. **c 的对偶空间法**：求解 `max Σα − 0.5ΣΣαᵢαⱼyᵢyⱼ(xᵢ·xⱼ)`，s.t. `0≤αᵢ≤C, Σαᵢyᵢ=0`；
   用 scipy 的 SLSQP 直接求解该 QP（通用凸优化器），由 `w = Σαᵢyᵢxᵢ` 与自由支持向量恢复 `b`。
   d 脚本中两种解法的对偶间隙实测为 0，权重余弦相似度 0.9999。
5. **水天线检测**：对分割掩码逐列寻找"天空→水面"的转换行；中值平滑后用鲁棒最小二乘拟合直线
   （迭代剔除离群列），得到水天线的直线参数 y = ax + b。
6. **分割效果评估（d/e）**：以伪标签为基准计算 accuracy/P/R/F1；以独立梯度检测器为参考真值
   计算水天线的 MAE、RMSE、±2px 内点率、检测率，以及参考分割的 IoU。

## 六、输出结果（`results/` 目录）

| 文件 | 含义 |
|---|---|
| `a_svm_segmentation.png` | a 的分割叠加图（红：逐列水天线，黄：拟合直线）|
| `a_waterline_scatter.png` | a 的逐列边界散点 + 拟合直线 |
| `b_primal_segmentation.png` / `b_primal_loss.png` | b 的分割结果与收敛曲线 |
| `c_dual_segmentation.png` / `c_dual_metrics.png` | c 的分割结果与指标柱状图 |
| `d_compare.csv` / `d_compare_bars.png` / `d_waterline_compare.png` | d 的量化对比表与图 |
| `e_waterline_metrics.csv` / `e_waterline_compare.png` / `e_error_hist.png` | e 的准确性评估表与图 |
| `*_metrics.json` / `*_waterline.npz` / `*_label_map.npy` | 原始指标与中间数据 |

## 七、作业 e 的答题要点（脚本已实现，可直接引用）

评估水天线检测准确性应从**误差、覆盖率、分割一致性、鲁棒性**四方面进行：

1. **逐列误差**：对每一列比较检测边界与参考真值的竖直距离，统计 MAE、RMSE、偏差；
2. **容差内点率**：误差在 ±2px、±3px 以内的列占比（工程上常用 ±2px 作为"检测正确"）；
3. **检测率/覆盖率**：成功输出边界且位于合理范围的列数占比，并单独报告遮挡列；
4. **分割一致性**：以水天线为界生成天空/水面参考掩码，计算与 SVM 分割的像素准确率、IoU；
5. **鲁棒性**：在模糊、噪声、光照变化下重复检测，观察误差变化；
6. **真值来源**：正式评估应由人工逐列标注（或标注若干控制点后拟合）作为真值，
   本脚本无人工标注时采用独立的"竖直梯度最大"检测器作为参考，并明确说明这一替代。

> 注意：参考真值仅用于课程演示流程；若需严格结论，请用人工标注替换
> `common.gradient_waterline_reference` 的返回值。
