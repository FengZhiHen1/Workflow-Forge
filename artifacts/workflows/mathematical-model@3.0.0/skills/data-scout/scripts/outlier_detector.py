#!/usr/bin/env python3
"""
异常值检测与分布可视化脚本模板
用法：复制到 tmp/ 后，修改 TARGET_FIELDS 和 DATA_PATH
输出：异常值标记列表、箱线图、Q-Q 图、分布直方图
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from pathlib import Path

# ============ 用户配置区 ============
DATA_PATH = "workspace/shared/data/attachment.csv"
TARGET_FIELDS = ["field1", "field2", "field3"]
OUTPUT_PREFIX = "tmp/YYYYMMDD_HHMMSS_outlier"
IQR_K = 1.5          # IQR 系数，常规用 1.5，极端用 3.0
ZSCORE_THRESHOLD = 3  # Z-score 阈值
# =====================================


def load_data(path: str, fields: list) -> pd.DataFrame:
    df = pd.read_csv(path)
    available = [f for f in fields if f in df.columns]
    missing = [f for f in fields if f not in df.columns]
    if missing:
        print(f"[WARN] 字段缺失: {missing}")
    return df[available]


def detect_outliers_iqr(series: pd.Series, k: float = 1.5) -> pd.Series:
    """使用 IQR 方法检测异常值。"""
    Q1 = series.quantile(0.25)
    Q3 = series.quantile(0.75)
    IQR = Q3 - Q1
    lower = Q1 - k * IQR
    upper = Q3 + k * IQR
    return (series < lower) | (series > upper)


def detect_outliers_zscore(series: pd.Series, threshold: float = 3) -> pd.Series:
    """使用 Z-score 方法检测异常值。"""
    z = np.abs(stats.zscore(series, nan_policy="omit"))
    return z > threshold


def detect_outliers_all(df: pd.DataFrame, fields: list, iqr_k: float = 1.5, z_threshold: float = 3) -> dict:
    """
    对多个字段执行异常值检测。
    返回每个字段的异常值摘要，包括检测方法、异常数量、异常值范围。
    """
    results = {}
    for col in fields:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if not np.issubdtype(series.dtype, np.number):
            print(f"[SKIP] {col} 为非数值类型，跳过异常值检测")
            continue

        outlier_iqr = detect_outliers_iqr(series, k=iqr_k)
        outlier_z = detect_outliers_zscore(series, threshold=z_threshold)
        outlier_union = outlier_iqr | outlier_z

        outlier_values = series[outlier_union]
        normal_values = series[~outlier_union]

        results[col] = {
            "总记录数": len(series),
            "IQR 异常数": int(outlier_iqr.sum()),
            "Z-score 异常数": int(outlier_z.sum()),
            "联合异常数": int(outlier_union.sum()),
            "异常率": f"{outlier_union.mean():.2%}",
            "异常值范围": f"[{outlier_values.min():.4f}, {outlier_values.max():.4f}]" if len(outlier_values) > 0 else "N/A",
            "正常值范围": f"[{normal_values.min():.4f}, {normal_values.max():.4f}]" if len(normal_values) > 0 else "N/A",
            "均值_vs_中位数差异": f"均值={series.mean():.4f}, 中位数={series.median():.4f}, 差异={abs(series.mean()-series.median()):.4f}",
        }

        # 保存异常值明细
        if len(outlier_values) > 0:
            outlier_df = pd.DataFrame({
                "index": outlier_values.index,
                "value": outlier_values.values,
                "method": np.where(outlier_iqr[outlier_union], "IQR", np.where(outlier_z[outlier_union], "Z-score", "both"))
            })
            outlier_df.to_csv(f"{OUTPUT_PREFIX}_{col}_outliers.csv", index=False)

    return results


def plot_boxplots(df: pd.DataFrame, fields: list, output_path: str):
    """绘制箱线图。"""
    numeric_fields = [f for f in fields if f in df.columns and np.issubdtype(df[f].dtype, np.number)]
    if not numeric_fields:
        print("[SKIP] 无数值字段可绘制箱线图")
        return

    n = len(numeric_fields)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4, rows * 4))
    if n == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    for i, col in enumerate(numeric_fields):
        sns.boxplot(y=df[col].dropna(), ax=axes[i])
        axes[i].set_title(col)
        axes[i].set_ylabel("")

    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] 箱线图已保存: {output_path}")


def plot_histograms(df: pd.DataFrame, fields: list, output_path: str):
    """绘制分布直方图 + KDE。"""
    numeric_fields = [f for f in fields if f in df.columns and np.issubdtype(df[f].dtype, np.number)]
    if not numeric_fields:
        print("[SKIP] 无数值字段可绘制直方图")
        return

    n = len(numeric_fields)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4, rows * 3.5))
    if n == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    for i, col in enumerate(numeric_fields):
        data = df[col].dropna()
        sns.histplot(data, kde=True, ax=axes[i], stat="density")
        axes[i].set_title(f"{col} (skew={data.skew():.2f}, kurt={data.kurtosis():.2f})")
        axes[i].set_xlabel("")

    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] 分布直方图已保存: {output_path}")


def plot_qq(df: pd.DataFrame, fields: list, output_path: str):
    """绘制 Q-Q 图。"""
    numeric_fields = [f for f in fields if f in df.columns and np.issubdtype(df[f].dtype, np.number)]
    if not numeric_fields:
        print("[SKIP] 无数值字段可绘制 Q-Q 图")
        return

    n = len(numeric_fields)
    cols = min(3, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 4, rows * 4))
    if n == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    for i, col in enumerate(numeric_fields):
        data = df[col].dropna()
        stats.probplot(data, dist="norm", plot=axes[i])
        axes[i].set_title(f"Q-Q Plot: {col}")

    for j in range(i + 1, len(axes)):
        axes[j].set_visible(False)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] Q-Q 图已保存: {output_path}")


def main():
    print("=" * 50)
    print("异常值检测与分布可视化")
    print("=" * 50)

    df = load_data(DATA_PATH, TARGET_FIELDS)
    print(f"[INFO] 加载数据: {df.shape[0]} 行 × {df.shape[1]} 列")

    # 1. 异常值检测
    results = detect_outliers_all(df, TARGET_FIELDS, iqr_k=IQR_K, z_threshold=ZSCORE_THRESHOLD)
    print("\n[INFO] 异常值检测摘要:")
    for col, info in results.items():
        print(f"\n  [{col}]")
        for k, v in info.items():
            print(f"    {k}: {v}")

    # 2. 保存摘要
    summary_df = pd.DataFrame(results).T
    summary_path = f"{OUTPUT_PREFIX}_summary.csv"
    summary_df.to_csv(summary_path)
    print(f"\n[INFO] 异常值摘要已保存: {summary_path}")

    # 3. 可视化
    plot_boxplots(df, TARGET_FIELDS, f"{OUTPUT_PREFIX}_boxplot.png")
    plot_histograms(df, TARGET_FIELDS, f"{OUTPUT_PREFIX}_histogram.png")
    plot_qq(df, TARGET_FIELDS, f"{OUTPUT_PREFIX}_qqplot.png")

    print("\n[INFO] 分析完成")


if __name__ == "__main__":
    main()
