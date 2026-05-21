# Reference：输出文件模板与命名规范

本文件定义 code-implementer 的全部输出文件模板。Skill 主体中仅保留文件清单与引用说明，**详细模板内容以此文件为准**。

---

## 文件组织方式

```
VERSION_SCRIPTS/
├── main_[model].py              # 主运行脚本（支持通过参数切换方案）
├── evaluate_[model].py          # 验证脚本（对照验证标准做阈值判定）
├── sensitivity_analysis.py      # 敏感性分析（对接 math-modeler 敏感性矩阵）
├── test_toy_model.py            # 单元测试（对接 math-modeler Toy Model）
├── requirements.txt             # 依赖清单（含版本号，由 uv 锁定）
└── utils/                       # 共享工具模块
    ├── __init__.py
    ├── data_loader.py           # 数据加载（含单位换算、缺失值处理）
    ├── metrics.py               # 评估指标（RMSE/MAE/R²/MAPE/...）
    └── plotting.py              # 统一可视化风格（论文级图表）

VERSION_RESULTS/
├── exp_01_baseline/             # 方案 A 结果
│   ├── csv/
│   └── png/
├── exp_02_advanced/             # 方案 B 结果
│   ├── csv/
│   └── png/
└── comparison/                  # 跨方案对比
    ├── csv/
    └── png/
```

---

## 模板一：主运行脚本（`main_[model].py`）

```python
"""
[模型名] 主运行脚本

基于 math-modeler 的公式推导实现，支持多方案切换。
对应文档：
- P3-符号体系与假设_[model].md
- P3-公式推导_问题建立_[model].md
- P3-公式推导_求解算法_[model].md
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd

# 将 utils 加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.data_loader import load_data
from utils.plotting import setup_plot_style, save_figure


def core_algorithm(data, params, scheme="baseline"):
    """
    核心算法实现。

    Parameters
    ----------
    data : np.ndarray
        输入数据，形状为 (n_samples, n_features)
    params : dict
        模型参数字典
    scheme : str
        方案标识："baseline" 或 "advanced"

    Returns
    -------
    result : dict
        包含预测值、损失、迭代历史等
    """
    # 公式 (2.1): 初始化
    # ...

    # 公式 (2.2): 核心迭代
    # ...

    # 公式 (2.3): 输出计算
    # ...

    return result


# 基于脚本位置推导目录（与 cwd 无关）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VERSION_ROOT = os.path.dirname(_SCRIPT_DIR)
_DEFAULT_RESULTS = os.path.join(_VERSION_ROOT, "results", "exp_01_baseline")
_DEFAULT_DATA = os.path.join(_VERSION_ROOT, "data", "input.csv")


def main():
    parser = argparse.ArgumentParser(description="[模型名] 主运行脚本")
    parser.add_argument(
        "--scheme",
        type=str,
        default="baseline",
        choices=["baseline", "advanced"],
        help="选择运行方案",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=_DEFAULT_RESULTS,
        help="结果输出目录",
    )
    parser.add_argument(
        "--data-path",
        type=str,
        default=_DEFAULT_DATA,
        help="输入数据路径",
    )
    args = parser.parse_args()

    # 创建输出目录
    os.makedirs(os.path.join(args.output_dir, "csv"), exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "png"), exist_ok=True)

    # 加载数据（含单位换算、缺失值处理）
    data = load_data(args.data_path)

    # 运行核心算法
    result = core_algorithm(data, params={}, scheme=args.scheme)

    # 保存结果
    pd.DataFrame(result["predictions"]).to_csv(
        os.path.join(args.output_dir, "csv", "predictions.csv"), index=False
    )

    # 生成可视化
    setup_plot_style()
    # ... 绘图代码 ...
    save_figure(os.path.join(args.output_dir, "png", "fig_01_key_result.png"))

    print(f"运行完成，结果保存至 {args.output_dir}")


if __name__ == "__main__":
    main()
```

---

## 模板二：验证脚本（`evaluate_[model].py`）

```python
"""
[模型名] 验证脚本

对照 P3-验证标准_[model].md 中的阈值做通过/失败判定。
输出：validation_report.md + 残差可视化
"""

import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.metrics import rmse, mae, r2_score, mape
from utils.plotting import setup_plot_style, save_figure

# 验证标准阈值（从 P3-验证标准提取）
THRESHOLDS = {
    "R2": {"acceptable": 0.70, "excellent": 0.90},
    "RMSE": {"acceptable": 0.05, "excellent": 0.02},
    # ... 其他指标
}


def evaluate(predictions, ground_truth, output_dir):
    """
    执行验证并生成报告。

    Returns
    -------
    report : dict
        包含各指标计算值、阈值、状态
    """
    metrics = {
        "R2": r2_score(ground_truth, predictions),
        "RMSE": rmse(ground_truth, predictions),
        "MAE": mae(ground_truth, predictions),
        "MAPE": mape(ground_truth, predictions),
    }

    report_lines = ["## 验证结果\n"]
    report_lines.append(
        "| 指标 | 计算值 | 可接受阈值 | 优秀阈值 | 状态 |"
    )
    report_lines.append("|:---|:---|:---|:---|:---|")

    all_pass = True
    for name, value in metrics.items():
        thresh = THRESHOLDS.get(name, {})
        acc = thresh.get("acceptable")
        exc = thresh.get("excellent")

        if acc is not None:
            # 注意：R2 越大越好，RMSE 越小越好
            if name in ["R2"]:
                status = "PASS" if value >= acc else "FAIL"
                if value >= exc:
                    status += " ⭐"
            else:
                status = "PASS" if value <= acc else "FAIL"
                if value <= exc:
                    status += " ⭐"
        else:
            status = "N/A"

        if "FAIL" in status:
            all_pass = False

        report_lines.append(
            f"| {name} | {value:.4f} | {acc} | {exc} | {status} |"
        )

    report_lines.append(f"\n**综合判定**: {'全部通过' if all_pass else '存在未通过项'}")

    # 残差分析
    residuals = ground_truth - predictions
    report_lines.append("\n## 残差分析\n")

    # Shapiro-Wilk 检验（简化示例）
    from scipy import stats
    sw_stat, sw_p = stats.shapiro(residuals[:5000])  # 限制样本量
    report_lines.append(
        f"- Shapiro-Wilk 检验: p = {sw_p:.4f} ({'通过' if sw_p > 0.05 else '未通过'})"
    )

    # 保存报告
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "validation_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    # 生成残差可视化
    setup_plot_style()
    # QQ 图、直方图、散点图 ...
    save_figure(os.path.join(output_dir, "fig_residual_qq.png"))

    return {"metrics": metrics, "all_pass": all_pass}


if __name__ == "__main__":
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _VERSION_ROOT = os.path.dirname(_SCRIPT_DIR)
    _RESULTS_DIR = os.path.join(_VERSION_ROOT, "results", "exp_01_baseline")
    _DATA_DIR = os.path.join(_VERSION_ROOT, "data")

    preds = pd.read_csv(os.path.join(_RESULTS_DIR, "csv", "predictions.csv")).values.flatten()
    truth = pd.read_csv(os.path.join(_DATA_DIR, "ground_truth.csv")).values.flatten()
    evaluate(preds, truth, _RESULTS_DIR)
```

---

## 模板三：敏感性分析脚本（`sensitivity_analysis.py`）

```python
"""
敏感性分析脚本

基于 P3-误差与敏感性分析_[model].md 中的敏感参数列表，
执行参数扰动并输出敏感性矩阵和可视化。
"""

import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main_model import core_algorithm  # 从主脚本导入核心函数
from utils.plotting import setup_plot_style, save_figure

# 敏感参数列表（从 math-modeler 提取）
SENSITIVE_PARAMS = {
    "alpha": {"baseline": 0.5, "perturb": 0.10},   # 扰动 ±10%
    "beta": {"baseline": 1.0, "perturb": 0.10},
    # ...
}


def run_sensitivity(data, params_config, output_dir):
    """
    对每个敏感参数执行扰动分析。

    Returns
    -------
    df : pd.DataFrame
        敏感性矩阵
    """
    results = []
    baseline_result = core_algorithm(data, {k: v["baseline"] for k, v in params_config.items()})
    baseline_output = baseline_result["objective_value"]  # 或关键输出指标

    for param_name, config in params_config.items():
        base = config["baseline"]
        delta = config["perturb"] * base

        for direction, sign in [("low", -1), ("high", +1)]:
            perturbed = base + sign * delta
            perturbed_params = {k: v["baseline"] for k, v in params_config.items()}
            perturbed_params[param_name] = perturbed

            result = core_algorithm(data, perturbed_params)
            output_val = result["objective_value"]

            rel_change = (output_val - baseline_output) / baseline_output if baseline_output != 0 else np.nan

            results.append({
                "parameter": param_name,
                "direction": direction,
                "perturbed_value": perturbed,
                "output_value": output_val,
                "relative_change": rel_change,
            })

    df = pd.DataFrame(results)

    # 保存矩阵
    os.makedirs(f"{output_dir}/csv", exist_ok=True)
    df.to_csv(f"{output_dir}/csv/sensitivity_matrix.csv", index=False)

    # 生成龙卷风图
    setup_plot_style()
    # ... 绘图代码 ...
    save_figure(f"{output_dir}/png/fig_sensitivity_tornado.png")

    return df


if __name__ == "__main__":
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _VERSION_ROOT = os.path.dirname(_SCRIPT_DIR)
    _DATA_PATH = os.path.join(_VERSION_ROOT, "data", "input.csv")
    _RESULTS_DIR = os.path.join(_VERSION_ROOT, "results", "comparison")

    data = np.loadtxt(_DATA_PATH, delimiter=",")
    run_sensitivity(data, SENSITIVE_PARAMS, _RESULTS_DIR)
```

---

## 模板四：单元测试（`test_toy_model.py`）

```python
"""
单元测试：Toy Model 验证

基于 P3-公式推导_问题建立_[model].md 中的极简特例手算结果，
验证核心函数的正确性。
"""

import sys
import os
import pytest
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main_model import core_algorithm


class TestToyModel:
    """Toy Model 测试：n=2 的极简特例。"""

    def test_toy_model_basic(self):
        """公式 (2.1)-(2.3) 的联合验证。"""
        data = np.array([[1.0, 2.0], [3.0, 4.0]])
        params = {"alpha": 0.5, "beta": 1.0}

        result = core_algorithm(data, params)

        # 依据 P3-公式推导_问题建立 中的手算结果
        expected = 3.14159
        assert result["objective_value"] == pytest.approx(expected, rel=1e-3)

    def test_boundary_zero_input(self):
        """边界条件：零输入。"""
        data = np.zeros((2, 2))
        params = {"alpha": 0.5, "beta": 1.0}

        result = core_algorithm(data, params)

        # 不应抛出异常，且输出应为 0 或符合理论预期
        assert np.isfinite(result["objective_value"])

    def test_division_protection(self):
        """除零保护：输入包含接近零的值。"""
        data = np.array([[1e-12, 2.0], [3.0, 4.0]])
        params = {"alpha": 0.5, "beta": 1.0}

        # 不应抛出 RuntimeWarning 或 ZeroDivisionError
        result = core_algorithm(data, params)
        assert np.isfinite(result["objective_value"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

## 模板五：共享工具模块

### `utils/data_loader.py`

```python
"""
数据加载工具

对接 P3-符号体系与假设 中的数据映射表，
处理单位换算、缺失值、数据类型转换。
"""

import pandas as pd
import numpy as np


def load_data(filepath, mapping_config=None):
    """
    加载数据并应用预处理。

    Parameters
    ----------
    filepath : str
        数据文件路径
    mapping_config : dict, optional
        数据映射配置（字段名、单位换算、预处理要求）

    Returns
    -------
    data : pd.DataFrame or np.ndarray
        处理后的数据
    """
    df = pd.read_csv(filepath)

    # 缺失值处理
    df = df.fillna(df.median(numeric_only=True))

    # 单位换算（示例：km -> m）
    if mapping_config and "unit_conversions" in mapping_config:
        for col, factor in mapping_config["unit_conversions"].items():
            df[col] = df[col] * factor

    return df
```

### `utils/metrics.py`

```python
"""
评估指标工具

对接 P3-验证标准 中的指标定义。
"""

import numpy as np


def rmse(y_true, y_pred):
    return np.sqrt(np.mean((y_true - y_pred) ** 2))


def mae(y_true, y_pred):
    return np.mean(np.abs(y_true - y_pred))


def r2_score(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    return 1 - ss_res / ss_tot if ss_tot != 0 else 0.0


def mape(y_true, y_pred):
    return np.mean(np.abs((y_true - y_pred) / (y_true + 1e-10))) * 100
```

### `utils/plotting.py`

```python
"""
统一可视化风格

论文级图表：300 dpi、规范字号、统一配色。
"""

import matplotlib.pyplot as plt
import matplotlib

# 设置论文级默认样式
plt.rcParams.update({
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "legend.fontsize": 9,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "figure.figsize": (3.35, 2.5),  # 单栏 8.5cm 宽度
})


def setup_plot_style():
    """应用统一绘图样式。"""
    matplotlib.rcdefaults()
    plt.rcParams.update({
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 12,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
    })


def save_figure(filepath, figsize=None):
    """
    保存图表。

    Parameters
    ----------
    filepath : str
        保存路径，建议命名格式：fig_{NN}_{description}.png
    figsize : tuple, optional
        自定义尺寸，单位英寸。单栏 (3.35, 2.5)，双栏 (6.69, 4.0)
    """
    if figsize:
        plt.gcf().set_size_inches(figsize)
    plt.tight_layout()
    plt.savefig(filepath, bbox_inches="tight", facecolor="white")
    plt.close()
```
