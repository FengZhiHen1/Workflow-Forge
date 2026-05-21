#!/usr/bin/env python3
"""
相关性分析与多重共线性初判脚本模板
用法：复制到 tmp/ 后，修改 TARGET_FIELDS 和 DATA_PATH
输出：相关性矩阵 CSV、热力图 PNG、高相关变量对列表
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ============ 用户配置区 ============
DATA_PATH = "workspace/shared/data/attachment.csv"  # 修改为实际数据路径
TARGET_FIELDS = ["field1", "field2", "field3"]       # 修改为实际字段名
OUTPUT_PREFIX = "tmp/YYYYMMDD_HHMMSS_corr"           # 修改为带时间戳的前缀
# =====================================


def load_data(path: str, fields: list) -> pd.DataFrame:
    """加载数据并提取目标字段。"""
    df = pd.read_csv(path)
    available = [f for f in fields if f in df.columns]
    missing = [f for f in fields if f not in df.columns]
    if missing:
        print(f"[WARN] 字段缺失: {missing}")
    return df[available]


def correlation_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """计算 Pearson 和 Spearman 相关性矩阵。"""
    numeric_df = df.select_dtypes(include=[np.number])
    pearson = numeric_df.corr(method="pearson")
    spearman = numeric_df.corr(method="spearman")
    return pearson, spearman


def find_high_corr_pairs(corr_matrix: pd.DataFrame, threshold: float = 0.8) -> list:
    """找出相关系数绝对值超过 threshold 的变量对（排除对角线）。"""
    pairs = []
    cols = corr_matrix.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            val = corr_matrix.iloc[i, j]
            if abs(val) >= threshold:
                pairs.append({
                    "var1": cols[i],
                    "var2": cols[j],
                    "correlation": round(val, 4),
                    "abs_corr": round(abs(val), 4)
                })
    return sorted(pairs, key=lambda x: x["abs_corr"], reverse=True)


def vif_approximation(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算近似 VIF（方差膨胀因子）。
    注意：这是快速初判，严格 VIF 需在建模阶段用 statsmodels 计算。
    """
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import r2_score

    numeric_df = df.select_dtypes(include=[np.number]).dropna()
    if numeric_df.empty or len(numeric_df.columns) < 2:
        return pd.DataFrame()

    vif_records = []
    for col in numeric_df.columns:
        X = numeric_df.drop(columns=[col])
        y = numeric_df[col]
        if len(X.columns) == 0:
            continue
        model = LinearRegression().fit(X, y)
        r2 = r2_score(y, model.predict(X))
        vif = 1 / (1 - r2 + 1e-10)  # 避免除零
        vif_records.append({"variable": col, "approx_vif": round(vif, 2)})

    vif_df = pd.DataFrame(vif_records).sort_values("approx_vif", ascending=False)
    return vif_df


def plot_heatmap(corr_matrix: pd.DataFrame, title: str, output_path: str):
    """绘制相关性热力图。"""
    plt.figure(figsize=(max(6, len(corr_matrix) * 0.8), max(5, len(corr_matrix) * 0.7)))
    mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)  # 只显示下三角
    sns.heatmap(
        corr_matrix,
        mask=mask,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        center=0,
        square=True,
        linewidths=0.5,
        cbar_kws={"shrink": 0.8}
    )
    plt.title(title)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] 热力图已保存: {output_path}")


def main():
    print("=" * 50)
    print("相关性分析与多重共线性初判")
    print("=" * 50)

    # 1. 加载数据
    df = load_data(DATA_PATH, TARGET_FIELDS)
    print(f"[INFO] 加载数据: {df.shape[0]} 行 × {df.shape[1]} 列")

    # 2. 相关性矩阵
    pearson, spearman = correlation_matrix(df)
    pearson_path = f"{OUTPUT_PREFIX}_pearson.csv"
    spearman_path = f"{OUTPUT_PREFIX}_spearman.csv"
    pearson.to_csv(pearson_path)
    spearman.to_csv(spearman_path)
    print(f"[INFO] Pearson 矩阵: {pearson_path}")
    print(f"[INFO] Spearman 矩阵: {spearman_path}")

    # 3. 高相关变量对
    high_corr = find_high_corr_pairs(pearson, threshold=0.8)
    if high_corr:
        print(f"\n[WARN] 发现 {len(high_corr)} 对高相关变量 (|r| >= 0.8):")
        for p in high_corr:
            print(f"    {p['var1']} <-> {p['var2']}: r = {p['correlation']}")
    else:
        print("\n[INFO] 未发现 |r| >= 0.8 的高相关变量对")

    # 4. 近似 VIF
    vif_df = vif_approximation(df)
    if not vif_df.empty:
        vif_path = f"{OUTPUT_PREFIX}_vif.csv"
        vif_df.to_csv(vif_path, index=False)
        print(f"\n[INFO] 近似 VIF 表: {vif_path}")
        high_vif = vif_df[vif_df["approx_vif"] > 5]
        if not high_vif.empty:
            print(f"[WARN] 以下变量近似 VIF > 5，存在多重共线性风险:")
            print(high_vif.to_string(index=False))
        else:
            print("[INFO] 所有变量近似 VIF <= 5，无明显共线性")

    # 5. 热力图
    plot_heatmap(pearson, "Pearson Correlation Matrix", f"{OUTPUT_PREFIX}_heatmap.png")

    print("\n[INFO] 分析完成")


if __name__ == "__main__":
    main()
