# Reference：math-modeler 路径使用规范

> 基础目录布局（`GLOBAL_SHARED`、`PROBLEM_SHARED`、`VERSION_DOCS`、`VERSION_SCRIPTS`、`VERSION_RESULTS` 等）定义在工作流级 `.claude/workflows/mathematical-model/references/directory-structure.md`。
> 本文档补充 math-modeler 特有的路径规则和文件命名约定。

## 读写边界

| 目录 | 权限 | 说明 |
|------|------|------|
| `VERSION_DOCS` | 写入 | 数学建模文档产出（唯一写入目录） |
| `PROBLEM_SHARED` | 只读 | 读取小问分析和选型报告 |

## 文件拆分要求

每个小问产出 **3~5 个独立文件**，严禁合并为单个文件。文件跨引用通过一句话索引，不重复定义。

## [model] 命名来源优先级

1. 给定的命名约束（首选）
2. 从输入文件路径中解析模型简称（如 `P2-模型选型_方案01_聚合ILP.md` → `聚合ILP`）
3. 若以上均无法确定，使用 `unknown_model` 占位并在 `report` 中标记 `model_name_inferred: true`
