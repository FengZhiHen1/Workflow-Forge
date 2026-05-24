# 子工作流设计指南

> 供 workflow-designer 在设计 Stage 结构时参考。涵盖子工作流判定、Stage 拆分、parallel 约束和条件路由。

## 子工作流

当 Stage 的 `workflow` 字段指向另一个 WORKFLOW.yaml 时，该 Stage 在执行时会创建独立的子工作流实例。

| 模式 | YAML 字段 | 何时使用 |
|------|----------|---------|
| **子工作流** | `workflow: <id>@<ver>` | 子任务本身是多 Stage 流程，有独立确认点，可独立重试 |
| **parallel 扇出** | `parallel: {source: ...}` | 同一 Skill 逻辑在 N 个独立目标上执行。⚠️ 若 source stage 同时设了确认点，有特殊约束，见下方「parallel 扇出 + 确认点」 |
| **多 Stage 串行** | 多个 `skill_id` Stage | 不同 Skill 按顺序接力 |
| **SUCCESS choice 路由** | `choice` 字段用于 `success` 边 | SubAgent 自主判定路径，无需确认点。见下方「条件路由」节 |

**核心判断标准**：如果 Stage 内部还要分好几步、还要用户确认——那就该用子工作流而不是单个 Skill。

**设计约束**：嵌套深度上限 3 层。父 Stage 状态 = 子实例汇总状态。

**子工作流感知义务**（设计/优化时）：
- 分析已有工作流：检测 `workflow` 字段 → 读取子工作流 → 纳入分析报告
- 优化已有工作流：父工作流改了，必须检查子工作流是否有同步优化空间
- 设计新工作流：判定需要子工作流后，designer 输出子工作流骨架 WORKFLOW.yaml
- 增量更新：修改父工作流 `workflow` 引用版本时，同步检查子工作流

## Stage 拆分原则

一个 stage 对工作流系统是**状态跃迁的原子单元**（启动→执行→上报→DAG 决定下一步），对 SubAgent 是**封闭任务**（收 prompt→干活→上报）。拆分只有在带来的架构收益大于上下文损失时才有意义。

**收益（满足任一可考虑拆分）**：

| # | 收益 | 说明 |
|---|------|------|
| 1 | **换 Skill** | 两个阶段需要不同专业能力。硬边界，不拆不行 |
| 2 | **DAG 路由点** | 中间结果决定下游走哪条路。路由发生在阶段末尾 → 那就是 stage 边界 |
| 3 | **用户显式暂停/回退点** | 用户说"这一步做完我要看一下，可能回退重来" |
| 4 | **并行** | 不同分支可以同时走 |
| 5 | **故障隔离** | 这一段容易出错，出错后只想重试这一段 |

**损失**：

| # | 损失 | 说明 |
|---|------|------|
| 1 | **上下文断裂** | 下一个 SubAgent 冷启动，仅靠 `checkpoint_summary` 和文件重建理解，前一个 SubAgent 的推理上下文全部丢失 |
| 2 | **延迟** | 多一次 wfctl next 往返 |

**决策方法**：列出具体收益项，一项都列不出来就别拆。

**常见反模式**：同一 Skill、无 DAG 路由、无并行机会、无用户暂停需求、上下游共享同一推理上下文，却拆成两个 stage。结果是 SubAgent 无法感知 stage 边界——它在第一个 stage 里把第二个 stage 的活也干了，在错误的 stage 上报错误的状态。`existing-artifact-detector` 是典型案例：阶段 A（扫描）和阶段 B（路径判定）是同一分析的上下游，SubAgent 自然从 A 流到 B。

**"确认点"本身不是拆分理由**——relay 确认完全可以在单 stage 内部完成多轮交互（AWAITING_CONFIRM → 用户答 → continue → 继续工作），不需要 stage 边界。

## parallel 扇出 + 确认点 设计约束

当 stage 同时满足以下两个条件时，存在一条**强制性设计约束**：

1. 本 stage 设有 `confirmation_point: true`
2. 存在下游 stage 通过 `parallel: {source: <本 stage>}` 引用本 stage 的输出

**约束：该 stage 的确认点不可使用终局确认（`to` 指向下游 stage）。必须使用中继确认（`to` 指向自身，自循环），确保 SubAgent 在用户确认后能继续执行并产出 `parallel_targets`。**

```yaml
# 错误：终局确认 — SubAgent 在确认后直接关闭，无法产出 parallel_targets
- from: s07-dispatch
  to: s08-fanout
  condition: confirmed
  choice: "确认调度"

# 正确：中继确认 + success 边 — SubAgent 确认后继续，产出 targets 再 DONE
- from: s07-dispatch
  to: s08-fanout
  condition: success                         # ← DONE(success) 才解锁下游
- from: s07-dispatch
  to: s07-dispatch
  condition: confirmed
  choice: "确认调度"
  max_loop: 3                                # ← 自循环，SubAgent 继续执行
```

**原因**：终局确认直接关闭 stage（AWAITING_CONFIRM → DONE），SubAgent 不会再获得控制权。此时若消息中未包含 `parallel_targets`，下游并行拆分无法执行。中继确认让 stage 回到 PENDING → SubAgent 通过 `continue` 继续 → 处理用户选择 → 产出 `parallel_targets` → 上报 DONE(success) → `success` 边解锁下游并行 stage。

**运行时防护**：`wfctl confirm` 会在终局确认阶段检测此违规——若 stage 的 `requires_parallel_targets` 已持久化为 `true` 而消息中无 `parallel_targets`，将拒绝关闭并返回 `PARALLEL_TARGETS_REQUIRED` 错误。但不应依赖运行时拦截——设计阶段就应避免。

## 条件路由：success + choice vs confirmed + choice

当 stage 的下游有多条互斥路径需要根据运行时判定来路由时，有两种互不依赖的机制：

| | `success` + `choice` | `confirmed` + `choice` |
|---|---|---|
| **谁决定** | SubAgent 自主判断 | 用户确认选择 |
| **需要 confirmation_point** | 否 | 是 |
| **SubAgent 行为** | 分析 → 选定 choice → `DONE --choice "xxx"` | 分析 → `AWAITING_CONFIRM` → 用户确认 → continue → `DONE` |
| **典型场景** | 检测结果明确，SubAgent 能独立判定路径（如：扫描到代码→走逆向工程；无代码→走全新设计） | 用户做主观决策（如：方案选择、范围确认、是否需要留档） |
| **安全网** | `valid_routing_choices` 校验 choice 合法性，非法则置 ERROR | `valid_choices` 编排器层面兜底 + confirm 拦截 |

**核心原则：不要为了获得条件路由而设立确认点。** 如果 SubAgent 自己就能判断该走哪条路，用 `success + choice`，不设 `confirmation_point`。设了确认点反而多一轮无意义交互。

**YAML 示例——SubAgent 自主路由（无需确认点）**：

```yaml
- stage_id: s02-analyze
  name: "分析与路由判定"
  skill_id: path-analyzer
  confirmation_point: false               # ← 无需确认

edges:
  - from: s02-analyze
    to: s03-full-design
    condition: success
    choice: "full_design"                  # ← SubAgent 的 --choice 匹配此处
  - from: s02-analyze
    to: s04-reverse-engineer
    condition: success
    choice: "code_only"
  - from: s02-analyze
    to: s99-workflow-end
    condition: failure
```

**决策流程**：

```
SubAgent 能自主决定走哪条路？
  ├─ 是 → success + choice，不设 confirmation_point
  └─ 否，需要用户判断 → confirmed + choice + confirmation_point: true
```

**注意**：若全部 SUCCESS 边都没有 `choice`，则 `valid_routing_choices` 为空列表——SubAgent 无需传 `--choice`，DONE 后所有 SUCCESS 边视为同时满足（OR 语义激活下游）。
