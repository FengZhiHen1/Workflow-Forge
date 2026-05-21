#!/usr/bin/env python3
"""
缺失模式分析脚本模板
用法：复制到 tmp/ 后，修改 TARGET_FIELDS 和 DATA_PATH
输出：缺失率表格、缺失模式可视化、MCAR/MAR/MNAR 初判
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ============ 用户配置区 ============
DATA_PATH = "workspace/shared/data/attachment.csv"
TARGET_FIELDS = ["field1", "field2", "field3"]
OUTPUT_PREFIX = "tmp/YYYYMMDD_HHMMSS_missing"
# =====================================


def load_data(path: str, fields: list) -> pd.DataFrame:
    df = pd.read_csv(path)
    available = [f for f in fields if f in df.columns]
    missing = [f for f in fields if f not in df.columns]
    if missing:
        print(f"[WARN] 字段缺失: {missing}")
    return df[available]


def missing_rate_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """计算各字段缺失率并分级。"""
    total = len(df)
    records = []
    for col in df.columns:
        missing_count = df[col].isna().sum()
        missing_rate = missing_count / total
        if missing_rate < 0.05:
            level = "低"
        elif missing_rate < 0.30:
            level = "中"
        else:
            level = "高"
        records.append({
            "字段": col,
            "总记录数": total,
            "缺失数": missing_count,
            "缺失率": f"{missing_rate:.2%}",
            "缺失率数值": round(missing_rate, 4),
            "分级": level
        })
    return pd.DataFrame(records)


def missing_pattern_analysis(df: pd.DataFrame) -> dict:
    """
    缺失模式初判。
    注意：MCAR/MAR/MNAR 的严格检验需要 Little's MCAR test 或敏感性分析，
    这里仅做启发式初判。
    """
    results = {}
    missing_matrix = df.isna()

    # 1. 完全缺失行
    complete_missing_rows = missing_matrix.all(axis=1).sum()
    results["完全缺失行数"] = int(complete_missing_rows)

    # 2. 缺失字段共现模式（最常见的几种缺失组合）
    pattern_counts = missing_matrix.value_counts().head(5)
    results["常见缺失模式"] = pattern_counts.to_dict()

    # 3. 启发式 MCAR 初判：若缺失与观测值无系统关联，倾向于 MCAR
    # 简单做法：检查缺失字段与其他完整字段是否有显著分布差异
    mcar_hints = []
    for col in df.columns:
        if df[col].isna().sum() == 0:
            continue
        for other_col in df.columns:
            if col == other_col or df[other_col].isna().sum() > 0:
                continue
            # 比较 other_col 在 col 缺失 vs 不缺失时的均值差异
            group_missing = df[df[col].isna()][other_col].dropna()
            group_not_missing = df[df[col].notna()][other_col].dropna()
            if len(group_missing) > 0 and len(group_not_missing) > 0:
                mean_diff = abs(group_missing.mean() - group_not_missing.mean())
                pooled_std = np.sqrt(
                    (group_missing.var() + group_not_missing.var()) / 2 + 1e-10
                )
                cohens_d = mean_diff / pooled_std
                if cohens_d > 0.5:  # 中等效应量
                    mcar_hints.append(
                        f"{col} 的缺失与 {other_col} 的分布存在关联 (Cohen's d={cohens_d:.2f})，倾向于 MAR/MNAR"
                    )
    results["MCAR 初判"] = mcar_hints if mcar_hints else ["未发现明显的缺失-观测关联，可能接近 MCAR"]

    return results


def plot_missing_pattern(df: pd.DataFrame, output_path: str):
    """绘制缺失模式热力图。"""
    plt.figure(figsize=(max(8, len(df.columns) * 0.6), max(6, len(df) * 0.02)))
    sns.heatmap(df.isna(), cbar=True, yticklabels=False, cmap="viridis_r")
    plt.title("Missing Value Pattern (Yellow = Missing)")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] 缺失模式图已保存: {output_path}")


def plot_missing_bar(df: pd.DataFrame, output_path: str):
    """绘制各字段缺失率柱状图。"""
    missing_rates = df.isna().mean().sort_values(ascending=True)
    plt.figure(figsize=(max(8, len(df.columns) * 0.5), 5))
    colors = ["#2ecc71" if r < 0.05 else "#f39c12" if r < 0.30 else "#e74c3c" for r in missing_rates]
    bars = plt.barh(missing_rates.index, missing_rates.values, color=colors)
    plt.axvline(x=0.05, color="gray", linestyle="--", alpha=0.7, label="5% (低/中分界)")
    plt.axvline(x=0.30, color="gray", linestyle="--", alpha=0.7, label="30% (中/高分界)")
    plt.xlabel("Missing Rate")
    plt.title("Missing Rate by Field")
    plt.legend(loc="lower right")
    plt.xlim(0, max(missing_rates.max() * 1.1, 0.35))
    for bar, val in zip(bars, missing_rates.values):
        plt.text(val + 0.01, bar.get_y() + bar.get_height() / 2, f"{val:.1%}", va="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[INFO] 缺失率柱状图已保存: {output_path}")


def main():
    print("=" * 50)
    print("缺失模式分析")
    print("=" * 50)

    df = load_data(DATA_PATH, TARGET_FIELDS)
    print(f"[INFO] 加载数据: {df.shape[0]} 行 × {df.shape[1]} 列")

    # 1. 缺失率分析
    rate_df = missing_rate_analysis(df)
    rate_path = f"{OUTPUT_PREFIX}_rate.csv"
    rate_df.to_csv(rate_path, index=False)
    print(f"\n[INFO] 缺失率表格:\n{rate_df.to_string(index=False)}\n")

    # 2. 缺失模式分析
    pattern_results = missing_pattern_analysis(df)
    print("[INFO] 缺失模式分析:")
    for k, v in pattern_results.items():
        print(f"  {k}: {v}")

    # 3. 可视化
    plot_missing_pattern(df, f"{OUTPUT_PREFIX}_pattern.png")
    plot_missing_bar(df, f"{OUTPUT_PREFIX}_bar.png")

    print("\n[INFO] 分析完成")


if __name__ == "__main__":
    main()
