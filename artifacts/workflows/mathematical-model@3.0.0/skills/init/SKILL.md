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
- 若 `problem_count` 未知，只创建 shared/ 和 .venv/，小问目录由后续阶段按需创建
- 创建 `.venv/` 时尝试执行 `python -m venv workspace/.venv`；若 Python 不可用则仅创建空目录并记录警告，不阻塞后续流程

### 步骤 2: 初始化 MANIFEST.yaml（每个小问一个）

为每个 `problem_N` 在 `workspace/problem_{N}/MANIFEST.yaml` 写入：

```yaml
problem_id: <N>
problem_name: "<赛题简称> - 第<N>问"
created_at: "<ISO8601 timestamp>"
active_version: v1

versions:
  v1:
    status: active
    phase: P0
    model: null
    model_full: null
    docs: []
    scripts: []
    results: []
    pending_deps: []
    block_reason: null
    pending_agents: []
    frozen_at: null
    created_at: "<ISO8601 timestamp>"
    abandoned_at: null

shared_assets: []
risk_list: []
adversarial_round: 0
p4_adversarial_history: []
pending_adversarial_actions: []
time_gates:
  T18h_passed: false
  T42h_passed: false
  T60h_passed: false
  T72h_passed: false
```

**字段取值规则**：
- `problem_id`：当前小问编号（整数，如 `1`、`2`）
- `problem_name`：格式为 `"<赛题简称> - 第<N>问"`，若已知小问标题则追加括号说明（如 `"B题 大型展销会 - 第1问（固定小组）"`）
- `created_at`：当前时间的 ISO8601 格式（含时区，如 `2026-05-18T15:00:00+08:00`）
- `active_version`：固定 `v1`
- `versions.v1.status`：固定 `active`
- `versions.v1.phase`：固定为起始阶段
- `versions.v1.model` / `model_full`：初始为 `null`，各阶段回填
- `versions.v1.docs` / `scripts` / `results`：初始为空列表 `[]`，各阶段追加
- `versions.v1.created_at`：与顶层 `created_at` 一致
- `shared_assets`：初始为空列表 `[]`，后续由初始化流程补充赛题文件路径
- `time_gates`：四个时间门控全部初始化为 `false`

**边界条件**：
- 若 `workspace/problem_{N}/MANIFEST.yaml` 已存在（恢复场景），保留已有内容不变，跳过创建

### 步骤 3: 初始化 VERSION.md（每个版本的每个小问一个）

为每个 `problem_N` 的初始版本创建 `workspace/problem_{N}/v1/VERSION.md`：

```markdown
# VERSION v1

## 版本状态

- status: active
- phase: P0
- model: null
- model_full: null
- created_at: <ISO8601 timestamp>

## 阶段历史

- P0: <ISO8601 timestamp> — 初始化完成
```

**边界条件**：
- 若 `workspace/problem_{N}/v1/VERSION.md` 已存在，保留已有内容不变，跳过创建

## 输出汇总

任务完成后，输出以下汇总表（在实际输出中标注每个产物的实际状态）：

| 产物 | 路径 | 状态 |
|:---|:---|:---|
| 共享目录 | `workspace/shared/` | created / existing |
| 小问目录 | `workspace/problem_N/` (×N) | created / skipped |
| 版本目录 | `workspace/problem_N/v1/` (×N) | created / skipped |
| 虚拟环境 | `workspace/.venv/` | created / skipped |
| 清单文件 | `workspace/problem_N/MANIFEST.yaml` (×N) | created / skipped |
| 版本文件 | `workspace/problem_N/v1/VERSION.md` (×N) | created / skipped |

对于 `skipped` 状态（如 problem_count 未知导致小问目录未创建、Python 不可用导致 .venv 未初始化），在汇总中注明原因，但不算失败——这些由后续阶段补齐。

## 自检清单

任务结束前逐项确认：

- [ ] `workspace/shared/` 目录存在
- [ ] `workspace/.venv/` 目录存在（仅目录，虚拟环境初始化失败不阻塞）
- [ ] 若 `problem_count` 已知，对应数量的 `workspace/problem_N/` 目录存在且各含 shared/、tmp/、v1/（含 docs/scripts/results/）
- [ ] 每个已知小问的 `workspace/problem_N/MANIFEST.yaml` 包含全部必需字段且值正确
- [ ] 每个已知小问的 `workspace/problem_N/v1/VERSION.md` 存在且包含 v1 记录
