# 工作流设计模式库

> 供 designer / designer-fast 参考的常见模式。设计时从模式中选取最接近的作为起点，再根据具体需求调整。

---

## 模式一：顺序审批流（Sequential Approval）

### 特征
多个 Stage 串联执行，关键 Stage 设置 ``，用户逐个确认后推进。

### 适用场景
- 文档审核流水线（初稿 → 审核 → 定稿）
- 方案设计（需求分析 → 方案设计 → 实现）
- 任何需要"逐步把关"的流程

### YAML 模板

```yaml
schema_version: "3.0.0"
workflow_id: "sequential-approval"
version: "1.0.0"
max_parallel_agents: 3
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"

  - stage_id: s01-analysis
    name: "需求分析"
    skill_id: requirement-analyst
    mandatory: true
    - stage_id: s02-design
    name: "方案设计"
    skill_id: design-architect
    mandatory: true
    - stage_id: s03-implementation
    name: "代码实现"
    skill_id: code-generator
    mandatory: true
    - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  - from: s00-workflow-start
    to: s01-analysis
    condition: always

  - from: s01-analysis
    to: s02-design
    condition: success + choice
    choice: "通过"

  - from: s01-analysis
    to: s01-analysis
    condition: success + choice
    choice: "继续完善"
    max_loop: 3

  - from: s01-analysis
    to: s99-workflow-end
    condition: success + choice
    choice: "放弃"

  - from: s01-analysis
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s02-design
    to: s03-implementation
    condition: success + choice
    choice: "通过"

  - from: s02-design
    to: s02-design
    condition: success + choice
    choice: "继续完善"
    max_loop: 5

  - from: s02-design
    to: s99-workflow-end
    condition: success + choice
    choice: "放弃"

  - from: s02-design
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s03-implementation
    to: s99-workflow-end
    condition: success
```

### 设计要点
- 每个 `` 的 Stage 必须有 `confirmed` / `rejected` / `loop_exceeded` 出边
- confirm + continue（回指自身）适用于"需要多轮打磨"的 Stage
- 非确认 Stage（如 s03）只有 `success` / `failure` 出边

### 常见反模式
- ❌ 确认点过多（>50% Stage）→ 流程拖沓
- ❌ 缺少 `loop_exceeded` → 无限循环风险
- ❌ 非确认 Stage 有 `confirmed` 出边 → condition 与 confirmation_point 不匹配

---

## 模式二：并行分支流（Parallel Branch）

### 特征
上游 Stage 产出多个独立任务，下游 Stage 并行执行，最后结果聚合。

### 适用场景
- 多文件并行处理（批量分析、批量转换）
- 多方案并行评估（A/B/C 方案同时设计）
- 模块级并行开发

### YAML 模板

```yaml
schema_version: "3.0.0"
workflow_id: "parallel-branch"
version: "1.0.0"
max_parallel_agents: 6
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"

  - stage_id: s01-decompose
    name: "任务拆解"
    skill_id: task-decomposer
    mandatory: true
    - stage_id: s02-parallel-process
    name: "并行处理"
    skill_id: batch-processor
    mandatory: true
    parallel:
      source: s01-decompose
      max_instances: 10

  - stage_id: s03-aggregate
    name: "结果聚合"
    skill_id: result-aggregator
    mandatory: true
    - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  - from: s00-workflow-start
    to: s01-decompose
    condition: always

  - from: s01-decompose
    to: s02-parallel-process
    condition: success + choice
    choice: "通过"

  - from: s01-decompose
    to: s01-decompose
    condition: success + choice
    choice: "重新拆解"
    max_loop: 3

  - from: s01-decompose
    to: s99-workflow-end
    condition: success + choice
    choice: "放弃"

  - from: s01-decompose
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s02-parallel-process
    to: s03-aggregate
    condition: success
    aggregation: all

  - from: s03-aggregate
    to: s99-workflow-end
    condition: success + choice
    choice: "通过"

  - from: s03-aggregate
    to: s03-aggregate
    condition: success + choice
    choice: "重新聚合"
    max_loop: 3
```

### 设计要点
- `parallel.source` 指向拆解 Stage，wfctl 会自动分发
- `aggregation: all` 表示所有并行实例完成才解锁下游
- `aggregation: any` 表示任一完成即解锁（适用于"多方案选一"）
- 并行 Stage 的 `skill_id` 通常相同（同一 Skill 处理不同输入）

### 常见反模式
- ❌ `aggregation: any` 用于互补拆分（应使用 `all`）
- ❌ 并行实例数超过 `max_parallel_agents` 限制
- ❌ 并行 Stage 设置 `exclusive: true`（与并行语义冲突）

---

## 模式三：迭代打磨流（Iterative Refinement）

### 特征
单个 Stage 设置 ``，用户可选择"继续完善"回指自身，多轮迭代后定稿。

### 适用场景
- 创意生成（方案设计、文案撰写、架构设计）
- 精细化调整（UI设计、配置调优）
- 任何需要"试-反馈-改"循环的任务

### YAML 模板

```yaml
schema_version: "3.0.0"
workflow_id: "iterative-refinement"
version: "1.0.0"
max_parallel_agents: 3
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"

  - stage_id: s01-draft
    name: "初稿生成"
    skill_id: draft-writer
    mandatory: true
    - stage_id: s02-refine
    name: "迭代打磨"
    skill_id: refinement-editor
    mandatory: true
    - stage_id: s03-finalize
    name: "终稿确认"
    skill_id: final-reviewer
    mandatory: true
    - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  - from: s00-workflow-start
    to: s01-draft
    condition: always

  - from: s01-draft
    to: s02-refine
    condition: success

  - from: s02-refine
    to: s02-refine
    condition: success + choice
    choice: "继续完善"
    max_loop: 5

  - from: s02-refine
    to: s03-finalize
    condition: success + choice
    choice: "进入终稿"

  - from: s02-refine
    to: s99-workflow-end
    condition: success + choice
    choice: "放弃"

  - from: s02-refine
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s03-finalize
    to: s99-workflow-end
    condition: success + choice
    choice: "通过"

  - from: s03-finalize
    to: s02-refine
    condition: success + choice
    choice: "返回打磨"
    max_loop: 3
```

### 设计要点
- 初稿 Stage（s01）通常不设确认点，快速产出第一版
- 打磨 Stage（s02）是核心迭代点，max_loop 建议 3-5
- 终稿确认（s03）提供"返回打磨"选项，形成大循环
- 两个循环都要配 `loop_exceeded` 出口

### 常见反模式
- ❌ 在初稿 Stage 就设确认点 → 用户还没看到内容就要决策
- ❌ `max_loop` 过大（>10）→ 用户失去耐心
- ❌ 缺少终稿确认 → 迭代可能无限进行

---

## 模式四：条件路由流（Conditional Routing）

### 特征
根据上游 Stage 的输出或用户选择，路由到不同的下游分支。

### 适用场景
- 多方案评估后选择（方案A/B/C）
- 根据输入类型自动路由（文档/PDF/图片分别处理）
- 质量门控（通过→继续，不通过→返回修改）

### YAML 模板

```yaml
schema_version: "3.0.0"
workflow_id: "conditional-routing"
version: "1.0.0"
max_parallel_agents: 4
anchor_prefix: "wf"

stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"

  - stage_id: s01-evaluate
    name: "方案评估"
    skill_id: solution-evaluator
    mandatory: true
    - stage_id: s02-route-a
    name: "执行方案A"
    skill_id: executor-a
    mandatory: true
    - stage_id: s02-route-b
    name: "执行方案B"
    skill_id: executor-b
    mandatory: true
    - stage_id: s03-merge
    name: "结果合并"
    skill_id: result-merger
    mandatory: true
    - stage_id: s99-workflow-end
    name: "工作流终止"

edges:
  - from: s00-workflow-start
    to: s01-evaluate
    condition: always

  - from: s01-evaluate
    to: s02-route-a
    condition: success + choice
    choice: "方案A"

  - from: s01-evaluate
    to: s02-route-b
    condition: success + choice
    choice: "方案B"

  - from: s01-evaluate
    to: s01-evaluate
    condition: success + choice
    choice: "重新评估"
    max_loop: 3

  - from: s01-evaluate
    to: s99-workflow-end
    condition: success + choice
    choice: "放弃"

  - from: s01-evaluate
    to: s99-workflow-end
    condition: loop_exceeded

  - from: s02-route-a
    to: s03-merge
    condition: success

  - from: s02-route-b
    to: s03-merge
    condition: success

  - from: s03-merge
    to: s99-workflow-end
    condition: success
```

### 设计要点
- 使用 `choice` 区分同 condition 的多条出边
- 分支 Stage（s02-route-a/b）的下游必须汇聚到同一 Stage（s03-merge），避免工作流过早终止
- 汇聚 Stage 通常不设确认点（分支结果已在前序确认）

### 常见反模式
- ❌ 分支后没有汇聚 → 工作流在任意分支完成时就结束，其他分支可能还在跑
- ❌ `choice` 值重复 → wfctl 无法正确路由
- ❌ 缺少兜底 success edge（无 choice）→ routing_choice 不匹配时工作流卡住

---

## 模式组合指南

实际工作流通常是多种模式的组合：

```
顺序审批 + 迭代打磨：
  需求分析(确认) → 方案设计(确认+迭代) → 实现(无确认) → 验收(确认)

并行分支 + 顺序审批：
  任务拆解(确认) → [并行处理A/B/C] → 聚合(确认) → 报告生成

条件路由 + 迭代打磨：
  评估(确认+路由) → 方案A/B执行 → 合并 → 打磨(迭代) → 终稿
```

designer 在选择模式时应：
1. 识别用户工作流中最主要的模式特征
2. 以该模式为模板生成基础结构
3. 根据具体需求调整 Stage 数量、确认点位置、循环次数
4. 组合多种模式时，确保汇聚点清晰（分支必须有合流）
