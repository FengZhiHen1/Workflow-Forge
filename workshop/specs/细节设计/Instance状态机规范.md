# Instance 状态机规范 v3.0.0

---

## 一、目录结构

```
.agent/instances/<instance_id>/
├── instance.json          # 状态机
├── messages/              # 消息池
│   └── <message_id>.json
├── logs/
│   ├── deviation.jsonl    # 偏差记录
│   ├── stage_history.jsonl# stage 历史 message
│   └── timeline.jsonl     # 时间戳
└── children/
    └── <child_id>/        # 子工作流实例（同上结构）
```

---

## 二、instance.json schema

```json
{
  "schema_version": "3.0.0",
  "instance_id": "20260517-001",
  "workflow_id": "math-model",
  "version": "2.1.0",
  "goal": "为 M01-M05 模块编写完整的落地规范",
  "status": "ACTIVE",
  "parent_instance_id": null,
  "consumed_message_ids": ["msg-001"],
  "stages": [
    {
      "stage_id": "s01",
      "stage_instance_id": "s01",
      "status": "DONE",
      "agent_id": "agent-s01-001",
      "system_agent_id": "sys-abcd",
      "output_message_id": "msg-001",
      "loop_counter": 0,
      "attempt_count": 0,
      "model": "standard",
      "child_instance_id": null,
      "fan_out_target": null
    }
  ]
}
```

---

## 三、顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_version` | `string` | 是 | `"3.0.0"` |
| `instance_id` | `string` | 是 | 实例唯一标识，含时间戳，如 `20260517-001` |
| `workflow_id` | `string` | 是 | 绑定工作流 ID |
| `version` | `string` | 是 | 绑定工作流版本 |
| `goal` | `string` | 是 | 实例目标声明，随实例创建时确定。语义锚点，防意图漂移。wfctl 不消费，通过 `status` 命令对外暴露 |
| `status` | `enum` | 是 | `ACTIVE` / `COMPLETED` / `FAILED` |
| `parent_instance_id` | `string` | 否 | 父实例 ID，子工作流时有值 |
| `consumed_message_ids` | `string[]` | 是 | 已消费的消息 ID 列表，幂等保护。`next` 扫描消息池时跳过 |

---

## 四、status 枚举与流转

### Instance

```
ACTIVE → PAUSED     用户或主 Agent 调用 pause 命令
ACTIVE → COMPLETED  所有 stage 终态（DONE/ERROR 且无可用 handler）
ACTIVE → FAILED     ERROR 无可用 handler 且无 failure edge
PAUSED → ACTIVE     用户或主 Agent 调用 resume 命令
```

### Stage

```
PENDING → RUNNING → DONE
              ↓
         AWAITING_CONFIRM ── confirm → PENDING + continue
              ↓              confirm ──→ PENDING + continue（loop_counter++，loop_counter ≥ max_loop 时走 loop_exceeded）
              ↓
         (已废弃) rejected → PENDING
              ↓
         (已废弃) rejected 无匹配 → Instance FAILED
              ↓
            ERROR → PENDING（retry）/ DONE（耗尽）
              ↓
         CONFLICT → RUNNING（conflict-resolver 接手）
              ↓
         CONFLICT →（冲突已解决，next 重试合并成功）→ DONE
```

| 状态 | 含义 |
|------|------|
| `PENDING` | 等待依赖满足后调度 |
| `RUNNING` | SubAgent 执行中 |
| `AWAITING_CONFIRM` | 等待用户确认，阻塞下游但 stage 未完成。用户确认后：edge 指向下游 → DONE（终局确认）；edge 指向自身 → PENDING（中继确认，loop_counter++，重新 spawn 继续执行） |
| `DONE` | 完成 |
| `ERROR` | 出错，待 retry 或终止 |
| `CONFLICT` | stage worktree 合入实例 worktree 时冲突，等待 conflict-resolver 接手。阻塞下游，不解锁 |
| `ERROR` | 出错，待 retry 或终止 |

---

## 五、stages[] 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stage_id` | `string` | 是 | YAML 中的 stage 标识 |
| `stage_instance_id` | `string` | 是 | 实例级标识。单实例时 = `stage_id`；parallel 时为 `{stage_id}#{n}` |
| `status` | `enum` | 是 | 五态 |
| `agent_id` | `string` | 否 | wfctl 生成的逻辑 Agent ID，启动前写入 |
| `system_agent_id` | `string` | 否 | 平台原生 Agent ID，启动后主 Agent 回填 |
| `output_message_id` | `string` | 否 | 该 stage 产出的消息 ID |
| `loop_counter` | `integer` | 是 | stage 级回跳计数，默认 0。confirm 循环或 failure edge 重定向时递增 |
| `attempt_count` | `integer` | 是 | stage 级重试计数，默认 0 |
| `model` | `string` | 否 | 模型档位，由主 Agent 查平台模型映射表解析为具体模型名后写入 |
| `child_instance_id` | `string` | 否 | 子工作流实例 ID |
| `fan_out_target` | `object` | 否 | parallel 拆分目标 `{id, label, context}` |
| `system_agent_id` | `string` | 否 | 平台原生 Agent ID。同 skill 跨 Stage 延续时，此字段直接复制到下游 Stage，不生成新 ID |
| `continued_to` | `string` | 否 | 实例被保留并延续到的下游 stage_id。仅 `AWAITING_CONFIRM → confirm → continue` 时写入 |
| `requires_parallel_targets` | `boolean` | 否 | `true` 时该 stage 的 Skill 需要产出 `parallel_targets`。`next` 在 spawn action 中标注 `requires_parallel_targets: true`，由主 Agent 注入提示词要求。`confirm` 前校验该 stage 是否已产出 `parallel_targets`（通过 `output_message_id` 关联的消息检查），未产出则置 ERROR |
| `pending_choice` | `string` | 否 | 最近一次 confirm 的用户选择，供 continue prompt 注入。`confirm` 命令由 `TransitionPolicy.on_confirm()` 写入，通过 continue prompt 传递给 SubAgent |
| `routing_choice` | `string` | 否 | SubAgent 上报 DONE 时携带的选择值。`MessageConsumerProcessor` 从消息提取并写入，用于 SUCCESS edge 的 choice 匹配。多条 SUCCESS edge 且设置了 `choice` 时必填 |
| `confirmed` | ~~`boolean`~~ | — | ~~已移除~~。旧 legacy 字段，新架构不再使用。`StageState` dataclass 中不存在该字段， instance.json 中如有出现将被忽略 |

---

## 六、同实例跨 Stage 延续

### 概述

当多个连续或非连续的 Stage 使用同一个 `skill_id` 时，`next` 通过 `.agent/running_agents.json` 映射表检测命中，生成 `continue` action（而非 `spawn`），让同一个 SubAgent 实例跨 Stage 继续执行。

### 映射表

`runtime/agent/manager.py` 中的 `RunningAgentManager` 统一管理。

`.agent/running_agents.json`（项目级唯一文件），格式 `[{skill_id, system_agent_id, stage_id, instance_id}]`。
- 编排器在 `spawn` 成功后写入新条目（按 `system_agent_id` 去重）
- `continue` 时 `next` 自动更新对应条目的 `stage_id`
- `next` 每次调用自动读取并按 `instance_id` 过滤
- SubAgent 崩溃/超时后编排器移除对应条目

### 检测逻辑（在 `next` 中）

```
1.5 读取 .agent/running_agents.json → 按 instance_id 过滤
...
6.5 对每个就绪 stage：
    if skill_id 在过滤后的映射表中命中：
      → {action: "continue", system_agent_id: "<已有的>", stage_id, worktree, ...}
      → stage → RUNNING，system_agent_id 从映射表复制到 stage 记录
      → 上游 stage（该 skill_id 的前一个 stage）写入 continued_to
      → 更新映射表文件中该条目的 stage_id
    else：
      → {action: "spawn", ...}  // 首次出现或非并行，正常新建
```

### continue action 的行为

主 Agent 收到 `continue` action 后：
1. **不**调用 `Agent()` 创建新 SubAgent
2. 向 `system_agent_id` 对应的已有 SubAgent 发送继续消息，注入：
   - 新的 worktree 路径（基于最新 HEAD 分配，SubAgent 切换过去后 `git pull` 或 checkout 即可拿到最新文件）
   - 当前 Stage 的 task 描述（从 WORKFLOW.yaml 的 `name` 字段提取，不暴露 `stage_id`）
   - 上一个 Stage 的用户确认结果（`choice` 值 + `feedback`，如有）
3. SubAgent 从上次 `AskUserQuestion` 返回点恢复，自然继续执行下一步骤

### 排除规则

| 条件 | 行为 |
|------|------|
| `parallel` 拆分出的多个 stage 实例 | 各有独立 SubAgent，不参与映射表 |
| confirm + continue 循环 | 同一 Stage 内循环，SubAgent 继续执行，不参与映射表 |
| SubAgent 崩溃/超时 | 主 Agent 从映射表移除，`next` 中未命中 → 正常 `spawn` 重建 |
| 下游为 `workflow`（子工作流） | 子工作流有独立实例，不参与父级映射表 |
| 用户 rollback | 级联清理受影响 stage 的 `system_agent_id`，映射表自然失效 |

### SubAgent 视角

SubAgent 不知道跨 Stage 延续的存在。它按 SKILL.md 的步骤序列执行，每步结束时 AskUserQuestion，拿到回答后继续下一步。Step 的边界和 Stage 的边界是独立的两层——Skill 定义前者的节奏，WORKFLOW.yaml 定义后者的锚点。

---

## 七、日志文件

### deviation.jsonl

```json
{"timestamp":"...","type":"USER_OVERRIDE","reason":"...","stage_id":"s03","user_confirmed":true}
```

### stage_history.jsonl

```json
{"stage_id":"s01","message_id":"msg-001","status":"DONE","timestamp":"..."}
```

### timeline.jsonl

覆盖全部状态流转边：

```json
{"stage_id":"s01","event":"scheduled","timestamp":"...","agent_id":"agent-001"}
{"stage_id":"s01","event":"running→awaiting_confirm","timestamp":"...","message_id":"msg-001"}
{"stage_id":"s01","event":"awaiting_confirm→running","timestamp":"...","confirmed_by":"user"}
{"stage_id":"s01","event":"running→done","timestamp":"...","message_id":"msg-001"}
{"stage_id":"s01","event":"running→error","timestamp":"...","message_id":"msg-001","reason":"..."}
{"stage_id":"s01","event":"error→pending","timestamp":"...","attempt":2}
{"stage_id":"s01","event":"running→conflict","timestamp":"...","conflict_files":["src/a.py"],"source_stage":"s01"}
{"stage_id":"s01","event":"conflict→running","timestamp":"...","resolver":"conflict-resolver"}
{"stage_id":"s01","event":"conflict→done","timestamp":"...","merged_by":"conflict-resolver"}
```

### deviation.jsonl

审计日志。主 Agent 通过 `wfctl deviate` 命令写入，不直接触碰文件。wfctl 不消费 deviation 做调度决策——它是事后回溯的依据，不是调度的输入。与 stage_history 的区别：deviation 记录非标准行为（用户覆盖、强制跳过、回退等），history 记录正常流转。

日志仅追加，不参与 wfctl 的调度计算。

---

## 八、子工作流

父 stage 声明 `workflow: <child>@<ver>` 时，wfctl 在 `children/<child_id>/` 下创建子实例。父 stage 记录 `child_instance_id`。

父 `next` 读取子 instance.json 汇总状态：
- 全部 DONE → 父 stage DONE
- 子实例 `FAILED` → 父 stage ERROR（终态）
- 子实例有 ERROR 但 `ACTIVE`（仍在 retry/回跳）→ 父 stage 保持 RUNNING

只有子实例进入终态 FAILED 时父 stage 才标记 ERROR。过渡态（子实例内部 retry/回跳/AWAITING_CONFIRM）不透传到父状态机——避免状态振荡。子实例过渡态期间的异常通过 `status` 命令的 `child_instance` 字段暴露（可观测性层面），不参与调度计算。

**嵌套深度上限**：子工作流最多嵌套 3 层。`create` 时检测父实例的嵌套深度，超过上限拒绝创建并返回错误。
