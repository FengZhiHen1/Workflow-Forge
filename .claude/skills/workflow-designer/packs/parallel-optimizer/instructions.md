# Parallel Optimizer Pack — 并发优化

> 当工作流中存在可并行执行的 Stage、需要设计 parallel 扇出、或优化并发效率时，加载此包。

## parallel 声明

```yaml
parallel:
  source: s07-dispatch
  max_instances: 10
```

`parallel` 表示：本 Stage 的 Skill 逻辑将在 N 个独立目标上执行，每个目标一个独立 SubAgent 实例。

## parallel 扇出 + 确认点 的强制约束

当 stage 同时满足以下两个条件时，存在一条**强制性设计约束**：

1. 本 stage 设有 ``
2. 存在下游 stage 通过 `parallel: {source: <本 stage>}` 引用本 stage 的输出

**约束：该 stage 的确认点不可使用DONE 上报（`to` 指向下游 stage）。必须使用confirm + continue（`to` 指向自身，自循环），确保 SubAgent 在用户确认后能继续执行并产出 `parallel_targets`。**

```yaml
# 错误：DONE 上报 — SubAgent 在确认后直接关闭，无法产出 parallel_targets
- from: s07-dispatch
  to: s08-fanout
  condition: success + choice
  choice: "确认调度"

# 正确：confirm + continue + success 边
- from: s07-dispatch
  to: s08-fanout
  condition: success                         # ← DONE(success) 才解锁下游
- from: s07-dispatch
  to: s07-dispatch
  condition: success + choice
  choice: "确认调度"
  max_loop: 3                                # ← 自循环，SubAgent 继续执行
```

**原因**：DONE 上报直接关闭 stage（AWAITING_CONFIRM → DONE），SubAgent 不会再获得控制权。此时若消息中未包含 `parallel_targets`，下游并行拆分无法执行。confirm + continue让 stage 回到 PENDING → SubAgent 通过 `continue` 继续 → 处理用户选择 → 产出 `parallel_targets` → 上报 DONE(success) → `success` 边解锁下游并行 stage。

**运行时防护**：`wfctl confirm` 会在DONE 上报阶段检测此违规——若 stage 的 `requires_parallel_targets` 已持久化为 `true` 而消息中无 `parallel_targets`，将拒绝关闭并返回 `PARALLEL_TARGETS_REQUIRED` 错误。但不应依赖运行时拦截——设计阶段就应避免。

## 并发效率检查清单

- `max_parallel_agents` ≥ 最大的 `parallel.max_instances`
- 无并行 Stage 时，`max_parallel_agents` 应 ≤ 3（建议）
- `parallel` 与 `exclusive` 不能同时存在
- 聚合策略：`aggregation: any` 只适用于互斥替代方案；`aggregation: all` 是默认

## 瓶颈识别

- 找出最长的串行路径（关键路径）
- 评估哪些 Stage 可以并行化但未并行化
- 检查是否有不必要的 `exclusive: true` 阻塞并发
