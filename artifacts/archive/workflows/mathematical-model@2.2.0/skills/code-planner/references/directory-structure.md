# Reference：目录结构与路径规范（唯一权威来源）

本 Skill 涉及的全部目录与文件路径如下表所示，后文所有引用均以此为准。

## 全局目录（整道题级别）

| 标识 | 路径 | 说明 |
|:---|:---|:---|
| `GLOBAL_SHARED` | `workspace/shared/` | 整道题共享资产：选题分析、赛题原文、原始附件 |
| `GLOBAL_DATA` | `workspace/shared/data/` | 整道题原始数据附件 |

## 小问目录（每个小问独立）

| 标识 | 路径 | 说明 |
|:---|:---|:---|
| `PROBLEM_ROOT` | `workspace/problem_{N}/` | 第 N 小问的工作根目录，`N` 为小问编号 |
| `PROBLEM_SHARED` | `workspace/problem_{N}/shared/` | 本小问共享资产：小问分析、数据侦察摘要 |
| `PROBLEM_TMP` | `workspace/problem_{N}/tmp/` | 本小问临时文件 |

## 版本目录（每个版本独立，位于 `PROBLEM_ROOT` 下）

| 标识 | 路径 | 说明 |
|:---|:---|:---|
| `VERSION_ROOT` | `v{N}/` | 版本根目录（相对于 `PROBLEM_ROOT`） |
| `VERSION_DOCS` | `v{N}/docs/` | 版本文档产出 |
| `VERSION_SCRIPTS` | `v{N}/scripts/` | 版本脚本产出 |
| `VERSION_RESULTS` | `v{N}/results/` | 版本结果产出 |
| `VERSION_MD` | `v{N}/VERSION.md` | 版本状态文件 + 阶段历史（两者合一） |

## 元数据文件

| 标识 | 路径 | 说明 |
|:---|:---|:---|
| `MANIFEST` | `workspace/problem_{N}/manifest.yaml` | 小问级状态机，由 workflow-director **独占维护** |

## 路径使用规则

- 所有路径均相对于**工作区根目录**（即 `workspace/` 所在目录）。
- `shared/` 单独出现时存在歧义，必须根据上下文区分是 `GLOBAL_SHARED` 还是 `PROBLEM_SHARED`。
- **严禁**将任何产出写入未在上方表格中登记的目录。
- **`VERSION_XXX` 等标识符仅用于文档描述和文件路径登记，不得直接作为代码中的路径字符串**。例如禁止在 Python 代码中写 `os.makedirs("VERSION_RESULTS/...")`。
- **代码中的路径必须使用 `__file__` 或绝对路径推导，严禁基于 `os.getcwd()` 的相对路径拼接**。正确做法：
  ```python
  SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
  VERSION_ROOT = os.path.dirname(SCRIPT_DIR)  # v{N}/
  RESULTS_DIR = os.path.join(VERSION_ROOT, "results")
  ```
- **运行脚本时的工作目录（cwd）由 workflow-director 调度决定，具有不确定性**。任何依赖 cwd 的路径写法（如 `"../results"`、`"./data"`）都是错误的。
