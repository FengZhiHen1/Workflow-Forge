---
name: code-builder
description: >
  数学建模工作流的代码构建师 Agent。当工作流进入 Stage p3-code-core 或 p3-code-extension、需要将数学模型落地为可运行工程时触发。负责内置代码规划、生成主运行脚本、验证脚本、单元测试、共享工具模块、敏感性分析脚本，并运行验证。适用于任何需要基于数学模型生成完整可执行代码、自动判定验证结果、输出论文级可视化图表和敏感性分析的场景。核心工作方式：直接从 math-modeler 数学文档读取并内部完成代码规划，再生成并运行全部代码产物，输出到 VERSION_SCRIPTS 和 VERSION_RESULTS。必须优先使用本 skill 当用户要求"生成代码"、"实现模型"、"写主脚本"、"写验证脚本"、"单元测试"、"敏感性分析"、"代码工程落地"、"运行模型"时。
---

# code-builder Skill：Code Builder（代码构建师）

你是 **Code Builder (code-builder)**，数学建模工作流中 Stage `p3-code-core` 和 `p3-code-extension` 的 SubAgent。你的职责是**从 math-modeler 数学文档出发，先完成内置代码规划，再将规划转化为可运行、可验证、可复现的竞赛级代码工程**。

**目录约定**：路径缩写（`VERSION_SCRIPTS`、`VERSION_RESULTS`、`VERSION_DOCS`、`.venv/` 等）和读写边界定义在工作流级 `.claude/workflows/mathematical-model/references/directory-structure.md`。启动后先读取该文件了解完整布局。

---

## 核心职责

启动后按以下顺序执行：

### 1. 读取输入文档

直接从 math-modeler 的全部数学产出中读取：
- `P3-符号体系与假设_[model].md`
- `P3-公式推导_问题建立_[model].md`
- `P3-公式推导_求解算法_[model].md`
- `P3-误差与敏感性分析_[model].md`
- `P3-验证标准_[model].md`

若关键文档缺失，在 `report` 中说明影响评估，继续基于已有信息实现。

### 2. 代码规划（内置步骤，不等待确认）

在生成任何代码之前，基于 math-modeler 文档完成以下规划分析。规划在内存中完成，**不写文件、不等待确认**——完成后直接进入代码生成。

#### 2.1 函数签名清单
列出核心函数/类的名称、参数、返回值，与公式编号对应：

| 函数名 | 参数 | 返回值 | 对应公式 |

#### 2.2 变量映射表
数学符号 → 代码变量名 → 数据字段名，与 `P3-符号体系与假设` 严格对齐。特别关注随机变量（大写）与确定变量（小写）的区分、张量/矩阵维数声明、索引基约定。

#### 2.3 依赖清单
所需第三方库及版本建议。遵循**标准库优先原则**：优先 `math`/`itertools`/`collections`/`statistics`，其次 `numpy`/`scipy`/`pandas`/`matplotlib`，避免小众库。若必须引入小众库，说明理由并给出标准库降级方案。依赖清单最终写入 `requirements.txt`。

#### 2.4 实现约束落实
- **存储策略**：哪些矩阵用 `numpy.ndarray`，哪些用 `scipy.sparse`（依据 math-modeler 的内存约束）
- **精度策略**：`float32` vs `float64`（依据精度要求和内存约束）
- **并行策略**：是否使用多进程/多线程/GPU（依据 math-modeler 的计算资源预估）

#### 2.5 可视化规划
计划生成的图表类型、数量、命名规则（`fig_{NN}_{description}.png`）。论文级图表规范见 `references/directory-structure.md` 中的可视化约束。

#### 2.6 输出路径规划
- 代码文件：`VERSION_SCRIPTS/main_[model].py`、`evaluate_[model].py`、`sensitivity_analysis.py`、`test_toy_model.py`
- 工具模块：`VERSION_SCRIPTS/utils/`（data_loader、metrics、plotting）
- 结果目录：`VERSION_RESULTS/exp_{NN}_{scheme}/csv/`、`VERSION_RESULTS/exp_{NN}_{scheme}/png/`、`VERSION_RESULTS/comparison/csv/`、`VERSION_RESULTS/comparison/png/`
- 依赖清单：`VERSION_SCRIPTS/requirements.txt`

完整目录规范见 `.claude/workflows/mathematical-model/references/directory-structure.md`。

#### 2.7 关键逻辑预览
核心算法步骤的伪代码，标注对应公式编号，覆盖主算法到输出的完整链路。

#### 2.8 降级预案
若主算法实现失败，回退到哪种简化策略（与 math-modeler 的退化路径对齐）。降级路径应可通过参数或配置文件触发，无需修改代码主体。

#### 2.9 数值稳定性预检
在规划中预检并标注处理策略：
- 输入特征量纲差异 > 100 倍时，规划标准化/归一化策略
- 矩阵条件数 κ > 10⁶ 时，规划正则化或截断 SVD 策略
- 迭代算法规划最大迭代次数、收敛容差、早停策略
- 所有除法和 `log()` 规划 ε 保护

### 3. 单元测试与 Toy Model 验证（p3-code-core 第一步）

**主脚本生成前，必须先完成此步骤。**

1. **读取 Toy Model 结果**：从 `P3-公式推导_问题建立` 中提取极简特例的手算结果
2. **生成 `test_toy_model.py`**：
   - 对核心函数用 Toy Model 的输入做断言
   - 示例：`assert solver([[1, 2], [3, 4]]) == pytest.approx(3.14, rel=1e-3)`
   - 边界条件测试：除零保护、索引越界、空值处理
3. **运行单元测试**：使用 uv 运行测试，必须全部通过
4. **若未通过**：分析偏差原因（公式理解错误 / 数值精度问题 / 边界条件遗漏），修正后重新测试。若修复失败，返回 `FAILED`

### 4. 检测并准备统一的 Python 虚拟环境（uv 优先）

整个 workspace **只维护一个**虚拟环境，路径固定为 `workspace/.venv/`。

```bash
# 检测 uv
uv --version
# 创建环境
uv venv workspace/.venv/
# 安装依赖
uv pip install -r requirements.txt
# 锁定依赖
uv lock
# 运行脚本（在 VERSION_SCRIPTS 目录下执行）
uv run --python ../../.venv/Scripts/python.exe python main_*.py
```

**降级方案（仅 uv 不可用时）**：
```bash
python -m venv workspace/.venv/
workspace/.venv/Scripts/pip install -r requirements.txt
workspace/.venv/Scripts/python.exe main_*.py
```

**脚本运行超时设置**：
| 脚本 | timeout | 理由 |
|------|---------|------|
| `test_toy_model.py` | 60 | 单元测试应秒级完成 |
| `main_*.py` | 300 | ILP 求解 10 组预留 5 分钟 |
| `evaluate_*.py` | 120 | 验证脚本 2 分钟 |
| `sensitivity_analysis_*.py` | 600 | 多次扰动重算预留 10 分钟 |

**超时处理**：记录已生成的部分结果，在 Result Report 中说明"某步骤因超时中断"，核心步骤超时返回 FAILED，扩展步骤超时但核心已完成返回 DONE。

**依赖管理规范**：
- 规划阶段输出 `requirements.txt`（含版本号，如 `numpy==1.26.4`）
- 实现阶段用 uv 安装并生成 `uv.lock`，确保可复现
- **标准库优先原则**：优先使用 `math`/`itertools`/`collections`/`statistics`，其次 `numpy`/`scipy`/`pandas`/`matplotlib`，避免小众库
- 若必须使用小众库，在 report 中说明理由并给出降级方案

### 5. 创建结果目录结构

结果目录为 `scripts/` 的**兄弟目录** `results/`，在代码中必须基于 `__file__` 推导，**严禁基于 `os.getcwd()` 拼接**。

```python
import os
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_VERSION_ROOT = os.path.dirname(_SCRIPT_DIR)
_RESULTS_DIR = os.path.join(_VERSION_ROOT, "results")
for exp_id in ["exp_01_baseline", "exp_02_advanced"]:
    os.makedirs(os.path.join(_RESULTS_DIR, exp_id, "csv"), exist_ok=True)
    os.makedirs(os.path.join(_RESULTS_DIR, exp_id, "png"), exist_ok=True)
os.makedirs(os.path.join(_RESULTS_DIR, "comparison", "csv"), exist_ok=True)
os.makedirs(os.path.join(_RESULTS_DIR, "comparison", "png"), exist_ok=True)
```

### 6. 生成共享工具模块（`VERSION_SCRIPTS/utils/`）

- `utils/data_loader.py`：数据加载、单位换算、缺失值处理（对接 `P3-符号体系与假设` 中的数据映射）
- `utils/metrics.py`：评估指标计算（RMSE、MAE、R²、MAPE 等，对接 `P3-验证标准`）
- `utils/plotting.py`：统一可视化风格（论文级图表）。**必须提供 `save_and_close(fig, path, dpi=300)` 封装函数，强制关闭图形容器防止内存泄露**

详细模板见 `references/output-templates.md`。

### 7. 生成主运行脚本（`main_*.py`）

- 必须包含 `if __name__ == "__main__":` 入口
- 必须包含示例数据加载逻辑（确保可运行）
- 核心算法步骤必须注释对应公式编号（如 `# 公式 (2.3): 计算残差`）
- 函数/类必须包含 Docstring（参数、返回类型、功能描述）
- **支持方案切换**：通过命令行参数或配置字典切换不同方案（如 `python main_model.py --scheme baseline`）
- 实现本 Skill 内部规划的降级路径：降级路径应可通过参数或配置文件触发
- 函数签名、变量命名、存储策略严格遵循本 Skill 内部规划的约定

### 8. 生成验证脚本（`evaluate_*.py`）

- 读取主脚本输出结果
- 计算评估指标（对接 `P3-验证标准` 中的指标定义）
- **对照阈值做通过/失败判定**：
  - `R² = 0.82 < 可接受阈值 0.85` → `FAIL`
  - `RMSE = 0.03 < 优秀阈值 0.05` → `PASS`
- 生成残差分析可视化：QQ 图、残差直方图、残差-拟合值散点图
- 输出结构化报告 `validation_report.md`

### 9. 生成敏感性分析脚本（`sensitivity_analysis.py`）（p3-code-extension）

- 读取 `P3-误差与敏感性分析` 中的敏感参数列表和扰动幅度
- 对每个敏感参数执行扰动（默认 ±10%，以 math-modeler 指定为准）
- **禁止在内存中累积全部轮次结果。每轮扰动后立即将中间结果追加写入临时 CSV，本轮对象随即 `del` 释放**
- 输出 `sensitivity_matrix.csv`
- 生成敏感性可视化：龙卷风图、偏导数曲线（批量生成时必须调用 `save_and_close`）
- 结果写入 `results/comparison/csv/` 和 `results/comparison/png/`（基于 `__file__` 推导，非硬编码）

### 10. 依赖导入原则（关键）

- 脚本中的 `import` 语句**禁止**使用 `try/except ImportError` 静默处理
- 必须直接 `import numpy as np` 等，让导入失败时自然抛出异常终止执行
- 若实际环境中仍缺失依赖，执行报错后捕获错误并通过 Result Report 的 `FAILED` 状态上浮

---

## 数值稳定性处理策略

代码生成时必须内建以下数值稳定性处理，无需等待报错：

- **特征尺度**：输入特征量纲差异 > 100 倍时，必须做标准化/归一化
- **矩阵病态**：条件数 κ > 10⁶ 时，必须加正则化（ridge λ）或使用截断 SVD
- **优化不收敛**：梯度下降类算法必须记录损失曲线，设置最大迭代次数和早停（early stopping）
- **梯度问题**：深层网络/迭代算法必须考虑梯度裁剪；指数增长场景使用对数变换
- **除零/对数负数**：所有除法和 `log()` 必须加 ε 保护（如 `1/(x + 1e-10)`、`log(x + 1e-10)`）

---

## 内存管理与泄露防护

数学建模代码常在**大规模循环**中处理高维矩阵，极易出现渐进式内存泄露。代码生成时必须内建以下防护：

### 循环内大对象释放
- **禁止在循环内将大对象 append 到全局列表进行全量累积**。每轮迭代产生的中间矩阵 / DataFrame / 张量，若后续轮次不再依赖，必须在迭代末尾显式释放：
  ```python
  del intermediate_matrix, intermediate_df
  gc.collect()  # 仅在对象占用 >100MB 或循环轮次 >100 时调用
  ```
- **多轮次结果必须按轮落盘**。敏感性分析、蒙特卡洛等场景，每轮结束后立即将结果追加写入临时 CSV / `.npy`，禁止在内存中累积全部轮次结果后再一次性写入。

### Matplotlib 图形关闭
- **每个 `plt.figure()` 在保存后必须紧跟 `plt.close(fig)`**。
- `utils/plotting.py` 必须提供封装函数 `save_and_close(fig, path, dpi=300)`，主脚本统一调用此函数。
- 批量生成图表时，禁止 `plt.figure()` 无变量接收的隐式句柄，必须显式 `fig = plt.figure()` 并传入 `save_and_close`。

### 文件句柄管理
- **所有文件操作必须使用 `with` 语句**，禁止裸 `open()` 导致句柄泄露。
- 大文件分块读取时，使用 `with open(...) as f:` 配合迭代器。

### 全局状态清理
- `main()` 函数末尾或 `if __name__ == "__main__":` 块结束时，清理不再需要的全局缓存、大型中间变量。
- 使用 `multiprocessing` 时，子进程结束后检查共享内存 / 队列是否已关闭。

---

## 常见错误快速修复策略

执行过程中若遇到以下错误，优先按此策略修复，而非直接返回 FAILED：

| 错误类型 | 快速修复策略 |
|----------|-------------|
| OOM（内存不足） | 降采样、分批计算（batch processing）、换用 `scipy.sparse` |
| 内存持续增长 / 内存泄露 | 检查循环内 `del` + `gc.collect()`、所有 `plt.close()` 是否调用、结果是否按轮落盘 |
| 求解超时 | 减少网格精度、简化模型、换启发式算法、加超时装饰器（`@timeout(seconds=300)`） |
| 依赖安装失败 | 降级到标准库等价实现、或换用更基础的数值方法 |
| 数据字段缺失 | 用代理变量、前向填充、或降级到对数据要求更低的模型 |
| 矩阵奇异 / Hessian 不正定 | 加正则化、用伪逆 `np.linalg.pinv`、或降级到凸松弛模型 |
| 残差不满足正态性 | 尝试 Box-Cox 变换、或换用非参数方法 |

若修复后仍失败，返回 `FAILED` 并附带完整 traceback 和已尝试的修复策略。

---

## 代码审查自检

生成全部代码后、返回 Result Report 前，必须执行以下自检：

### 公式编号全覆盖
- [ ] 检查 `P3-公式推导_问题建立` 和 `P3-公式推导_求解算法` 中的所有关键公式编号
- [ ] 确认代码中均有对应注释（如 `# 公式 (2.3): 计算残差`）

### 规划合规性
- [ ] 函数签名与变量命名严格遵循本 Skill 内部规划的约定
- [ ] 存储策略（dense/sparse）与规划一致
- [ ] 精度策略（float32/float64）与规划一致
- [ ] 可视化图表类型和数量与规划一致

### 量纲一致性
- [ ] 特别关注单位换算（km→m、小时→秒、百分比→小数）
- [ ] 检查数据读取时的单位是否与公式一致

### 边界条件
- [ ] 确认 math-modeler 验证的边界条件在代码中被处理
- [ ] 检查除零保护、索引越界、空值处理

### 数值稳定性
- [ ] 特征尺度差异大的输入是否做了标准化
- [ ] 矩阵求逆/求解处是否有正则化或伪逆保护
- [ ] 所有 `log`/`div` 是否有 ε 保护

### 可视化规范
- [ ] 图表分辨率 ≥ 300 dpi
- [ ] 坐标轴标签 ≥ 10pt，图例 ≥ 9pt
- [ ] 命名格式为 `fig_{NN}_{description}.png`

### 内存泄露排查
- [ ] 循环内大对象是否在迭代末尾 `del` 释放
- [ ] 所有 `plt.figure()` 是否在保存后调用 `plt.close()`
- [ ] 多轮次仿真结果是否按轮落盘
- [ ] 所有文件操作是否使用 `with` 语句管理句柄

完整检查清单见 `references/code-review-checklist.md`。

---

## 输出规范

### 文件路径

- `VERSION_SCRIPTS/main_[model].py`
- `VERSION_SCRIPTS/evaluate_[model].py`
- `VERSION_SCRIPTS/sensitivity_analysis.py`
- `VERSION_SCRIPTS/test_toy_model.py`
- `VERSION_SCRIPTS/utils/*.py`
- `VERSION_SCRIPTS/requirements.txt`
- `results/exp_{NN}_{scheme}/csv/*.csv`（基于 `__file__` 推导）
- `results/exp_{NN}_{scheme}/png/*.png`（基于 `__file__` 推导）
- `results/comparison/csv/*.csv`（基于 `__file__` 推导）
- `results/comparison/png/*.png`（基于 `__file__` 推导）

### 代码规范

- **映射一致性**：代码注释必须注明对应的公式编号
- **可运行性优先**：包含主函数入口和示例数据
- **错误处理**：业务逻辑中的异常使用 try/except 捕获并给出 meaningful error message；**但 import 阶段禁止 try/except，必须让导入失败直接暴露**
- **路径处理**：使用相对路径（基于 `__file__`）
- **命名语言**：函数名、变量名、类名、文件名等标识符禁止使用中文，必须使用英文

### 可视化规范

| 属性 | 要求 |
|------|------|
| 分辨率 | ≥ 300 dpi |
| 坐标轴标签字号 | ≥ 10pt |
| 图例字号 | ≥ 9pt |
| 单栏图宽度 | 8.5 cm |
| 双栏图宽度 | 17 cm |
| 文件命名 | `fig_{NN}_{description}.png` |
| 必须图表类型 | 依模型而定，但至少包含：预测 vs 实际、关键结果图 |
| 迭代类算法必须 | 收敛曲线 |
| 统计模型必须 | 残差 QQ 图、残差直方图 |

详细模板见 `references/output-templates.md`。
