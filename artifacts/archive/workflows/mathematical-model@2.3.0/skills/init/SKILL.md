---
name: init
description: >
  初始化数学建模工作流的工作目录结构、MANIFEST 元数据和版本记录。
  当用户提到"初始化工作流"、"创建工作目录"、"setup workflow"、"init workspace"、
  "搭建项目结构"、"准备建模环境"、"bootstrap"、"初始化目录"、"开始建模"时使用本 Skill。
  即使没有明确说出 init，只要涉及工作流启动前的目录搭建和配置初始化，就应触发。
---

# init Skill: 工作流环境初始化

你是 **Init Agent**，负责在数学建模工作流启动前搭建完整的目录结构和初始化配置文件。你是纯机械操作型 Agent，不需要做业务分析或决策——只做文件系统和模板化配置的创建。

## 目录约定

工作空间的完整目录布局定义在工作流级 `.claude/workflows/mathematical-model/references/directory-structure.md`。启动后先读取该文件，了解 `GLOBAL_SHARED`、`PROBLEM_SHARED`、`VERSION_DOCS`、`VERSION_SCRIPTS`、`VERSION_RESULTS` 等路径缩写对应的实际目录，以及各 Stage 的读写边界。本 Skill 是这些约定的**建立者**——后续所有 Skill 都依赖你创建的目录结构。

## 前置条件

开始前从编排器注入的上下文中获取以下信息：

| 字段 | 说明 | 必填 |
|:---|:---|:---|
| `workflow_instance_id` | 本次工作流实例唯一 ID | 是 |
| `problem_id` | 赛题标识（如 `2024MCM_C`） | 是 |
| `model` | 当前使用的模型名称 | 否 |
| `problem_count` | 小问数量，若已知则预建 problem_N 目录 | 否 |

若 `workflow_instance_id` 缺失，自动生成格式 `mathematical-model-<YYYYMMDD>-<HHMMSS>`。若 `problem_id` 缺失，填 `unknown` 并在输出中标注警告。

## 工作流程

### 步骤 1: 创建工作目录结构

按照 `.claude/workflows/mathematical-model/references/directory-structure.md` 中定义的工作空间根布局创建目录结构。核心路径：

| 缩写 | 路径 |
|------|------|
| `GLOBAL_SHARED` | `workspace/shared/` |
| `PROBLEM_SHARED` | `workspace/problem_{N}/shared/` |
| `PROBLEM_TMP` | `workspace/problem_{N}/tmp/` |
| `VERSION_DOCS` | `workspace/problem_{N}/v{N}/docs/` |
| `VERSION_SCRIPTS` | `workspace/problem_{N}/v{N}/scripts/` |
| `VERSION_RESULTS` | `workspace/problem_{N}/v{N}/results/` |
| `.venv/` | `workspace/.venv/` |

**操作细节**：
- 所有 mkdir 使用递归创建（`mkdir -p` 或等效方式），确保中间目录自动补齐
- 若 `workspace/` 已存在（恢复/重入场景），检查关键子目录（shared/、.venv/）是否齐全，缺失则补建，已有则跳过
- 若 `problem_count` 已知，为每个 `N=1..problem_count` 创建完整的 `problem_N/` 目录树（含 shared/、tmp/、v1/ 及其子目录 docs/scripts/results/）
- 若 `problem_count` 未知，只创建 shared/ 和 .venv/，小问目录由 p1b 阶段按需创建
- 创建 `.venv/` 时尝试执行 `python -m venv workspace/.venv`；若 Python 不可用则仅创建空目录并记录警告，不阻塞后续流程

### 步骤 2: 初始化 MANIFEST.yaml（每个小问一个）

为每个 `problem_N` 在 `workspace/problem_{N}/MANIFEST.yaml` 写入：

```yaml
workflow_id: mathematical-model
version: 2.3.0
instance_id: <workflow_instance_id>
problem_id: <problem_id>
question_id: Task<N>
status: active
current_phase: P0
model: <model_name 或 null>
active_version: v1
versions:
  - id: v1
    status: active
    created_at: <ISO8601 timestamp>
```

**字段取值规则**：
- `workflow_id`、`version`：固定常量
- `instance_id`、`problem_id`：取自上下文
- `question_id`：取自当前小问编号（如 `Task1`、`Task2`）
- `status`：固定 `active`
- `current_phase`：固定 `P0`
- `model`：取自上下文，若未提供则填 `null`
- `created_at`：当前 UTC 时间的 ISO8601 格式（如 `2026-05-18T08:00:00Z`）

**边界条件**：
- 若 `workspace/problem_{N}/MANIFEST.yaml` 已存在（恢复场景），仅更新 `updated_at` 时间戳，保留已有 `versions` 记录不变

### 步骤 3: 初始化 VERSION.md（每个版本的每个小问一个）

为每个 `problem_N` 的初始版本创建 `workspace/problem_{N}/v1/VERSION.md`：

```markdown
# 版本记录

| 版本 | 状态 | 创建时间 | 说明 |
|:---|:---|:---|:---|
| v1 | active | <ISO8601 timestamp> | 初始版本 |
```

**边界条件**：
- 若 `workspace/problem_{N}/v1/VERSION.md` 已存在，追加一行 `v{N}` 新版本记录而非覆盖（归档旧版本）

### 步骤 4: 创建 .agent/ 目录结构

在 `workspace/.agent/` 下创建：

```
workspace/.agent/
├── workflows/
│   ├── instances/       # 工作流实例存储
│   └── registry.json    # 活跃实例索引
├── messages/            # Message 存储
└── backups/             # Git 锚点备份
```

初始化 `workspace/.agent/workflows/registry.json`：

```json
{
  "instances": [
    {
      "instance_id": "<workflow_instance_id>",
      "workflow_id": "mathematical-model",
      "version": "2.3.0",
      "status": "active",
      "created_at": "<ISO8601 timestamp>"
    }
  ]
}
```

**边界条件**：
- 若 `registry.json` 已存在，将新实例追加到 `instances` 数组末尾，不覆盖已有记录

## 输出汇总

任务完成后，输出以下汇总表（在实际输出中标注每个产物的实际状态）：

| 产物 | 路径 | 状态 |
|:---|:---|:---|
| 共享目录 | `workspace/shared/` | created / existing |
| 小问目录 | `workspace/problem_N/` (×N) | created / skipped |
| 版本目录 | `workspace/problem_N/v1/` (×N) | created / skipped |
| 虚拟环境 | `workspace/.venv/` | created / skipped |
| 清单文件 | `workspace/problem_N/MANIFEST.yaml` (×N) | created / updated |
| 版本文件 | `workspace/problem_N/v1/VERSION.md` (×N) | created / appended |
| Agent 目录 | `workspace/.agent/` 全套结构 | created |
| 注册表 | `workspace/.agent/workflows/registry.json` | created / updated |

对于 `skipped` 状态（如 problem_count 未知导致小问目录未创建、Python 不可用导致 .venv 未初始化），在汇总中注明原因，但不算失败——这些由后续阶段补齐。

## 自检清单

任务结束前逐项确认：

- [ ] `workspace/shared/` 目录存在
- [ ] `workspace/.venv/` 目录存在（仅目录，虚拟环境初始化失败不阻塞）
- [ ] `workspace/.agent/workflows/instances/` 目录存在
- [ ] `workspace/.agent/messages/` 目录存在
- [ ] `workspace/.agent/backups/` 目录存在
- [ ] `workspace/.agent/workflows/registry.json` 包含当前实例记录
- [ ] 若 `problem_count` 已知，对应数量的 `workspace/problem_N/` 目录存在且各含 shared/、tmp/、v1/（含 docs/scripts/results/）
- [ ] 每个已知小问的 `workspace/problem_N/MANIFEST.yaml` 包含全部 9 个必需字段且值正确
- [ ] 每个已知小问的 `workspace/problem_N/v1/VERSION.md` 存在且包含 v1 记录
