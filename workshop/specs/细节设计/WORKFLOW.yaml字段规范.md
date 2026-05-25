# WORKFLOW.yaml 字段规范 v3.0.0

---

## 一、顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_version` | `string` | 是 | 固定值 `"3.0.0"` |
| `workflow_id` | `string` | 是 | 工作流标识，与目录名 `<workflow_id>` 严格一致，kebab-case |
| `version` | `string` | 是 | 语义化版本，与目录名 `@<version>` 严格一致 |
| `max_parallel_agents` | `integer` | 是 | 全局并发上限，≥ 1 |
| `anchor_prefix` | `string` | 否 | git 锚点前缀，默认 `"wf"` |

已移除的顶层结构：`concurrency_rules`、`conflict_resolution`、`git_anchors`、`model_tiers`、`default_model_tier`。

---

## 二、stages

### 2.1 基础字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stage_id` | `string` | 是 | 全局唯一，kebab-case |
| `name` | `string` | 是 | 展示用名称 |
| `mandatory` | `boolean` | 是 | `true` 时用户不可跳过 |
| `confirmation_point` | `boolean` | 是 | `true` 时该 stage 完成后触发 AskUserQuestion |
| `retry` | `integer` | 否 | 失败后重试次数，`0` 不重试（默认），`2` 失败后再执行 2 次
| `timeout_seconds` | `integer` | 否 | stage 超时秒数。YAML 为默认值，主 Agent 可在运行时根据上下文动态调整。超时后由宿主平台终止 SubAgent，主 Agent 收到通知后调用 `next`，wfctl 将 stage 置为 ERROR 走 retry 分支 |
| `model` | `string` | 否 | 模型档位（`light` / `standard` / `heavy`），主 Agent 根据平台模型映射表解析为具体模型名 |

### 2.2 执行目标（互斥）

每个 stage 必须且只能指定以下之一：

| 字段 | 类型 | 说明 |
|------|------|------|
| `skill_id` | `string` | 指向 `artifacts/skills/<id>/` 或本工作流 `skills/` 下的 Skill。可钉住版本（`<id>@<ver>`），不写版本则浮动最新 |
| `workflow` | `string` | 指向 `artifacts/workflows/<id>@<ver>/`，声明子工作流 |

**虚拟 stage**（`s00-workflow-start`、`s99-workflow-end`）：两者均不写，`name` 取固定值。虚拟 stage 豁免 `mandatory` / `confirmation_point` / `retry` 字段校验，wfctl 内部处理。

### 2.3 并发

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `exclusive` | `boolean` | 否 | `true` 时该 stage 执行期间禁止其他 stage 并行。优先级高于 `max_parallel_agents`——有 exclusive RUNNING 时即使未达并发上限也不新增 spawn |
| `parallel` | `object` | 否 | 声明本 stage 可并行拆分 |

#### `parallel` 子字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | `string` | 是 | 上游 stage_id。wfctl 检测到此声明后，在 `source` stage 的 spawn action 中标 `requires_parallel_targets: true`，由主 Agent 注入提示词要求。`source` 的 Skill 不感知工作流协议 |
| `max_instances` | `integer` | 否 | 最大并行实例数 |

---

## 三、edges

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `from` | `string` | 是 | 上游 stage_id |
| `to` | `string` | 是 | 下游 stage_id |
| `condition` | `string` | 是 | 枚举见 3.1 |
| `max_loop` | `integer` | 条件必填 | `condition=failure` 或 `condition=confirmed`（且 `from == to`，即中继确认回指自身）时必填。循环次数达到上限后走 `loop_exceeded` edge |
| `choice` | `string` | 否 | `condition=confirmed` 或 `condition=rejected` 时可选。值须与 SubAgent `confirm_questions` 中标注的选项值严格一致。wfctl 通过字符串匹配选择对应 edge。同 `from` 下的多条同 condition edge 当 `choice` 互斥；无 `choice` 的 edge 作为兜底 |
| `cascade_reset_until` | `string` | 否 | 级联重置的上限 stage_id。仅用于回边（`to` 在拓扑序上位于 `from` 之前或自身）。当 confirmed/rejected edge 触发回跳时，`rollback` 命令或 `TransitionPolicy.compute_cascade_reset()` 只重置到该 stage 为止（含），不继续向上游扩散。该 stage 必须是从 `from` 可达的祖先或 `from` 自身 |
| `aggregation` | `string` | 否 | `all`（默认）/ `any`。多实例时解锁下游的条件。`any` 解锁下游时自动取消其余未完成实例。仅适用于互斥替代方案（如多方案评估，任一通过即可），不适用于互补拆分 |

### 3.1 condition 枚举

| 值 | 触发场景 |
|----|---------|
| `always` | stage 完成后无条件流转 |
| `success` | SubAgent 上报 DONE |
| `failure` | SubAgent 上报 ERROR |
| `confirmed` | confirmation_point 被用户确认。有 `choice` 值时按字符串匹配选择对应 edge，无 `choice` 时匹配所有未标 `choice` 的 confirmed edge |
| `rejected` | confirmation_point 被用户拒绝 |
| `loop_exceeded` | 循环次数达到 `max_loop` |

---

## 四、完整示例

### 4.1 基础示例（终局确认 + 失败回跳）

```yaml
schema_version: "3.0.0"
workflow_id: "math-model"
version: "2.1.0"
max_parallel_agents: 6
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"

  - stage_id: s01
    name: "选题分析"
    skill_id: topic-analyst
    mandatory: true
    confirmation_point: true
    retry: 2
    model: standard

  - stage_id: s02
    name: "模块拆解"
    skill_id: module-breakdown
    mandatory: true
    confirmation_point: false

  - stage_id: s03
    name: "逐模块设计"
    parallel:
      source: s02
      max_instances: 10
    workflow: module-design@1.0.0
    exclusive: true

  - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  - from: s00-workflow-start
    to: s01
    condition: always

  - from: s01
    to: s02
    condition: confirmed
    choice: "通过"

  - from: s01
    to: s01
    condition: rejected
    choice: "重做"
    max_loop: 3

  - from: s01
    to: s99-workflow-end
    condition: rejected
    choice: "放弃"

  - from: s01
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s02
    to: s03
    condition: always

  - from: s03
    to: s99-workflow-end
    condition: success
    aggregation: all
```

### 4.2 中继确认示例（多轮交互式确认）

s02（方案设计）内部可能需要多轮用户确认才能最终定稿：

- 用户选"继续完善"→ stage 回到 PENDING，重新 spawn，SubAgent 在原 worktree 中继续
- 用户选"通过"→ stage → DONE，进入下游 s03
- 用户选"放弃"→ 直接终止
- 最多允许 5 轮循环，超限后走 `loop_exceeded` 终止

```yaml
stages:
  - stage_id: s02
    name: "方案设计"
    skill_id: design-architect
    mandatory: true
    confirmation_point: true

  - stage_id: s03
    name: "代码实现"
    skill_id: code-generator
    mandatory: true

  - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  # 中继确认：回指自身，继续设计
  - from: s02
    to: s02
    condition: confirmed
    choice: "继续完善"
    max_loop: 5

  # 终局确认：指向下游
  - from: s02
    to: s03
    condition: confirmed
    choice: "通过"

  # 放弃
  - from: s02
    to: s99-workflow-end
    condition: rejected
    choice: "放弃"

  # 循环超限
  - from: s02
    to: s99-workflow-end
    condition: loop_exceeded
```
