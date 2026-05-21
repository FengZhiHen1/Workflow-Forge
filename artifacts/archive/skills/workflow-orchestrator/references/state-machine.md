# 状态流转规则

## Stage 状态流转

```
PENDING → RUNNING → DONE
   ↓        ↓        ↓
BLOCKED  ERROR    SKIPPED
   ↑        ↓
   └──── CANCELLED
```

| 转换 | 触发条件 | 备注 |
|------|---------|------|
| `PENDING → RUNNING` | 依赖全部 DONE，无阻塞，编排器调度 | 创建 git_anchor_tag，启动 SubAgent |
| `PENDING → SKIPPED` | 用户覆盖跳过非 mandatory stage | 需记录 deviation_log |
| `RUNNING → BLOCKED` | SubAgent 上报 `PENDING_CONFIRM` | 编排器调用 AskUserQuestion |
| `BLOCKED → PENDING` | 用户确认后 | `blocked_by_confirm = false`，等待调度 |
| `RUNNING → DONE` | SubAgent 上报 `DONE` | 解锁下游 stage |
| `RUNNING → ERROR` | SubAgent 上报 `ERROR` | 检查 retry_policy |
| `ERROR → PENDING` | 重试次数未耗尽，编排器重新调度 | `attempt_count += 1` |
| `任意 → CANCELLED` | 用户取消或回退时强制取消 | 取消依赖该 stage 的并发任务 |
| `DONE → SUPERSEDED` | 回退后重新执行，旧记录被覆盖 | 保留在 history_message_ids 中 |

## Instance 状态流转

```
PLANNING → EXECUTING → COMPLETED
              ↓            ↓
          SUSPENDED      FAILED
              ↓
          CANCELLED
```

| 转换 | 触发条件 |
|------|---------|
| `PLANNING → EXECUTING` | 第一个 stage 被调度为 RUNNING |
| `EXECUTING → SUSPENDED` | 存在未处理的 PENDING_CONFIRM |
| `SUSPENDED → EXECUTING` | 所有确认已处理，继续调度 |
| `EXECUTING → COMPLETED` | 所有 stages DONE/SKIPPED |
| `EXECUTING → FAILED` | stage ERROR 且重试耗尽，无 error_handler |
| `任意 → CANCELLED` | 用户明确取消 |

## Workflow 循环（Edges 回跳）

```
s3_test --failure--> s2_refactor  (max_loop: 3)
  |
  +-- loop_exceeded --> s_error_handler
```

- 每次触发 failure edge 时，`loop_counter_stage` 对应的 `loop_counter` +1
- 达到 `max_loop` 后，改走 `loop_exceeded` edge
- `loop_counter` 与 `attempt_count` 是独立的概念：
  - `attempt_count`：同一 stage 的重试次数（由 retry_policy 控制）
  - `loop_counter`：Workflow 层面的循环回跳次数（由 edges 控制）

## 降级熔断流转

| 场景 | Stage 行为 | Instance 行为 |
|------|-----------|--------------|
| 用户要求跳过 mandatory stage | 停止，不上报 DONE | 冻结为 SUSPENDED，AskUserQuestion |
| 用户要求跳过非 mandatory stage | 标记 SKIPPED | 记录 deviation_log，继续下游 |
| 方案级降级（算法变更等） | SubAgent 上报 PENDING_CONFIRM | 走正常确认流程 |
