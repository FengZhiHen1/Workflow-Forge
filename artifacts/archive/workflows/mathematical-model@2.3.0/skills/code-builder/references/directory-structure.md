# Reference：code-builder 路径使用规范

> 基础目录布局（`GLOBAL_SHARED`、`VERSION_DOCS`、`VERSION_SCRIPTS`、`VERSION_RESULTS`、`.venv/` 等）定义在工作流级 `../../references/directory-structure.md`。
> 本文档补充 code-builder 特有的路径使用规则和可视化命名约定。

## 路径使用规则

- **代码中的路径必须使用 `__file__` 或绝对路径推导，严禁基于 `os.getcwd()` 的相对路径拼接**。正确做法：
  ```python
  SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
  VERSION_ROOT = os.path.dirname(SCRIPT_DIR)  # v{N}/
  RESULTS_DIR = os.path.join(VERSION_ROOT, "results")
  ```
- **运行脚本时的工作目录（cwd）具有不确定性**。任何依赖 cwd 的路径写法（如 `"../results"`、`"./data"`）都是错误的。
- **`VERSION_XXX` 等标识符仅用于文档描述和文件路径登记，不得直接作为代码中的路径字符串**。禁止 `os.makedirs("VERSION_RESULTS/...")`。

## 可视化命名约定

| 属性 | 要求 |
|------|------|
| 分辨率 | ≥ 300 dpi |
| 文件命名 | `fig_{NN}_{description}.png` |
| 坐标轴标签字号 | ≥ 10pt |
| 图例字号 | ≥ 9pt |
