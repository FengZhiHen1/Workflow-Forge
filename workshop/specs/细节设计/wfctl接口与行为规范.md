# wfctl 接口与行为规范

---

## 一、角色定位

| 角色 | 本质 | 行为边界 |
|------|------|---------|
| **主 Agent** | 智能决策者 | 理解意图、匹配工作流、驱动循环、呈现确认、把握全局进度 |
| **wfctl** | 机械调度程序 | 纯 Python，无状态，无 AI，输入确定则输出确定 |
| **SubAgent** | 执行者 | 在隔离 worktree 中干活，通过 wfctl 写入 Message 上报 |

wfctl 不接触用户，不启动 SubAgent，不做语义判断，不持有内存状态。

---

## 二、命令体系

| 命令 | 职责 | 调用时机 |
|------|------|---------|
| `resolve` | 无参数时扫描可用工作流（ID、版本、描述、标签）；`--workflow <id>@<ver>` 时解析单个 WORKFLOW.yaml（stage 列表、并发声明、确认点位置、输入 schema） | 主 Agent 需了解工作流选项或锁定目标后查看详情 |
| `create` | 生成 Instance JSON，分配实例 worktree，写入身份元数据，打初始锚点 | 主 Agent 确认工作流后 |
| `next` | **调度核心**：读 instance 状态，消费消息池，更新 stage 状态，处理 ERROR 分支，计算并返回下一步批量 actions | 每次循环的默认调用 |
| `sync` | 仅消费消息池、更新 stage 状态，**不计算 next**（诊断用） | 开发者查询进度或主 Agent 轻量查看时 |
| `confirm` | 将指定 AWAITING_CONFIRM 的 stage 解锁，恢复流转 | 用户回复确认后 |
| `rollback` | 重建实例 worktree 到指定 stage 锚点（`git checkout anchor-s2`），下游 stage 产物被 git 自然清除，其状态重置为 PENDING。**级联清理**被回退 stage 及所有下游 stage 关联的 `consumed_message_ids`。旧状态保留在 git reflog 中可恢复 | 用户要求回退或失败策略触发时 |
| `status` | 无参数：扫描 `.agent/instances/`，返回项目全局状态（活跃实例、阻塞点、最近完成/失败）。`--instance <id>`：返回指定实例完整摘要（含子工作流透传） | 开发者询问进度 / 主 Agent 获取视野时 |
| `deviate` | 向日志追加一条 deviation 记录 | 主 Agent 检测到非标行为时 |
| `terminate` | 取消 ACTIVE 实例，清理全部 worktree，实例标记 FAILED | 用户要求取消或主 Agent 判断需终止时 |

### `status` 返回结构

**项目级**（`wfctl status`，无参数）：

```json
{
  "active_instances": [
    {
      "instance_id": "20260517-001",
      "workflow_id": "math-model",
      "status": "ACTIVE",
      "stages_done": 3,
      "stages_total": 10,
      "blocked_by": [
        {"stage_id": "s04", "state": "AWAITING_CONFIRM", "summary": "确认建模方案"}
      ]
    }
  ],
  "recent_completed": ["20260516-003", "20260516-002"],
  "recent_failed": ["20260516-001"]
}
```

派生视图——每次扫描 `.agent/instances/` 实时聚合，无持久文件。`recent_*` 最多 5 条 ID。

**实例级**（`wfctl status --instance <id>`）：

```json
{
  "instance_id": "20260517-001",
  "goal": "为 M01-M05 模块编写落地规范",
  "status": "ACTIVE",
  "stages_summary": {
    "total": 10,
    "pending": 2,
    "running": 3,
    "awaiting_confirm": 1,
    "done": 4,
    "error": 0,
    "conflict": 0
  },
  "active_worktrees": [".tmp/worktrees/instance-20260517-001"],
  "conflict_worktrees": [],
  "stages": [
    {
      "stage_id": "s03",
      "status": "RUNNING",
      "child_instance": {
        "instance_id": "child-001",
        "status": "ACTIVE",
        "stages_summary": {"done": 1, "running": 0, "awaiting_confirm": 1, "pending": 2},
        "blocked_stages": [
          {"stage_id": "s02", "status": "AWAITING_CONFIRM", "output_message_id": "msg-child-005"}
        ]
      }
    },
    {
      "stage_id": "s04",
      "status": "AWAITING_CONFIRM",
      "output_message_id": "msg-014",
      "confirm_questions": ["确认建模方案？"]
    },
    {
      "stage_id": "s05",
      "status": "PENDING",
      "waiting_for": ["s04"]
    }
  ]
}
```

`stages` 列出所有非 `DONE` 的 stage（含 RUNNING、AWAITING_CONFIRM、PENDING、ERROR、CONFLICT），按状态和 stage_id 排序。每个 stage 携带其状态的诊断级联信息：

| 字段 | 出现条件 |
|------|---------|
| `output_message_id` | stage 有产出消息时——开发者可直接打开对应 message 文件 |
| `waiting_for` | PENDING stage——列出其依赖的上游 stage_id |
| `confirm_questions` | AWAITING_CONFIRM——待确认的问题 |
| `attempt_count` | ERROR——当前重试次数 |
| `child_instance` | 关联子工作流的 RUNNING stage——透传子实例状态快照和阻塞点 |

子工作流递归——`child_instance` 内可再含 `stages` 的 `child_instance`，深度 ≤ 3。

`stages_summary` 和 `stages` 的区分：`stages_summary` 是轻量计数（不展开），`stages` 是阻塞/异常 stage 的详情（提供排查入口）。

---

## 三、`resolve` —— 工作流发现

- `resolve`（无参数调用）：扫描 `artifacts/workflows/` 下所有 `WORKFLOW.md` 的 YAML 头（name、description、tags），返回可用工作流清单
- `resolve --workflow <id>@<ver>`：解析单个 WORKFLOW.yaml 的完整结构

`resolve` 是纯信息查询命令，不涉及任何确认门控。

---

## 四、`next` —— 调度核心

### 核心流程

```
next --instance <id>
  │
  ├─ 1. 读 instance.json
  ├─ 1.5 读取 .agent/running_agents.json（项目级文件），按 instance_id 过滤当前实例的存活 SubAgent 列表
  ├─ 2. 扫描消息池 .agent/instances/<instance_id>/messages/
  ├─ 3. 消费未处理消息，更新对应 stage 状态
  ├─ 3.5 并发 stage 合并：多个 stage 同时 DONE 时按 stage_id 字典序依次合入实例 worktree
  ├─ 4. 检查 parallel 拆分（见下方）
  ├─ 5. 检查子工作流完成（见下方）
  ├─ 6. 计算就绪 stage
  ├─ 6.5 同 Skill 延续检测：对每个就绪 stage，在映射表中查同 skill_id 的条目
  │     └─ 命中 → {action: "continue", ...}（不创建新 SubAgent），更新该条目 stage_id
  │     └─ 未命中 → {action: "spawn", ...}（正常新建）
  ├─ 7. 分配 worktree（见第十三节）—— spawn 和 continue 的 worktree 分配逻辑一致
  ├─ 8. 检测实例终态（COMPLETED / FAILED）→ 自动清理全部 worktree
  ├─ 9. 返回批量 action 数组
```

### parallel 拆分

当 `next` 发现某 stage 声明了 `parallel` 且其上游已完成时，读取上游 Message 中的 `parallel_targets` 数组，为该 stage 创建 N 个 stage 实例，各自分配独立 worktree。

wfctl 只做乘法——"YAML 声明了 parallel + 上游提供了 N 个 target → 创建 N 个实例"。不涉及语义判断。

### 子工作流处理

当 stage 的 `workflow` 字段引用了另一个 WORKFLOW.yaml 时，`next` 为该 stage 创建嵌套的 Instance。父实例中该 stage 的状态 = 子实例的汇总状态。子实例全部 DONE → 父 stage DONE，解锁下游。

### ERROR 触发

Stage 进入 ERROR 的两种路径：

| 路径 | 触发方 | 机制 |
|------|--------|------|
| SubAgent 自报 | SubAgent | 调用 `wfctl message write --status ERROR` |
| 超时 | 宿主平台 | `timeout_seconds` 到期，平台终止 SubAgent 并通知主 Agent。主 Agent 调用 `next`，wfctl 发现该 stage 的 SubAgent 已终止且无 DONE/ERROR 消息，自动写入 ERROR |

### ERROR 分支处理

当 `next` 发现 stage 状态为 ERROR 时，按以下优先级判定：

```
1. attempt_count < retry_policy.max_attempts？
   → {action: "retry", stage_id: "s3", attempt: 2}

2. 重试耗尽，存在 condition=failure 的 edge，且 loop_counter < max_loop？
   → {action: "spawn", stage_id: "s2", reason: "failure-edge"}

3. 重试耗尽，failure edge 也耗尽（loop_counter >= max_loop），存在 loop_exceeded edge？
   → {action: "spawn", stage_id: "emergency", reason: "loop-exceeded"}

4. 重试耗尽，无可用 handler？
   → {action: "terminate", status: "FAILED", reason: "..."}
```

全部为确定性规则，wfctl 无需语义判断即可处理。

### action 结构

```json
{action: "spawn" | "continue" | "retry" | "await" | "confirm" | "conflict" | "merge_to_main" | "terminate",
 stage_id: "...",
 skill_id: "...",
 worktree: "...",
 model: "standard",                    # stage 声明的模型档位，主 Agent 解析为具体模型名后传入 Agent()
 reason: "...",
 requires_parallel_targets: false,     # 仅 parallel 下游标注
 conflict_files: [],                   # 仅 conflict 类型
 system_agent_id: null,                # continue action 时必填——指向已有的 SubAgent
 context: {...}}
```

### `spawn` 与 `continue`

| | `spawn` | `continue` |
|------|---------|-----------|
| 触发条件 | 就绪 stage 的 skill_id 在映射表中**未命中** | 就绪 stage 的 skill_id 在映射表中**命中** |
| `system_agent_id` | null（主 Agent 启动后回填并写入 .agent/running_agents.json） | 已有 SubAgent 的平台 ID |
| `worktree` | 正常分配（见 §十三） | 正常分配（同 spawn） |
| `context` | 含上游摘要、task 描述 | 含上游摘要、task 描述 + 上一步用户确认结果 |
| 主 Agent 行为 | 调用 `Agent()` 启动新 SubAgent → 写入 .agent/running_agents.json | 向已有 SubAgent 发送继续消息（切换 worktree + 注入 task） |
| stage 状态变更 | PENDING → RUNNING | PENDING → RUNNING（由 next 在生成 action 时写入） |
| `system_agent_id` 写入 | 主 Agent 启动后回填到 instance.json | `next` 从映射表复制到 stage 记录 |

**映射表**（`.agent/running_agents.json`）：项目级唯一文件，格式 `[{skill_id, system_agent_id, stage_id, instance_id}]`。编排器在 spawn 成功后写入新条目（按 system_agent_id 去重），continue 时 `next` 自动更新 stage_id。`next` 每次调用自动读取并按 instance_id 过滤。

**`continue` action 的 worktree 分配**：与 `spawn` 完全一致——单 Stage 就绪用实例 worktree，多 Stage 并发用独立 stage worktree（基于实例 worktree HEAD 创建）。已有 SubAgent 收到 continue 消息后切换到新分配的 worktree 继续工作，自然读到最新文件状态。

- `conflict`：合并冲突。`worktree` 指向保留冲突状态的 stage worktree，`conflict_files` 列出冲突文件，`source_stage` 为产出冲突变更的 stage。主 Agent 收到后启动 `conflict-resolver` 全局 Skill 在实例 worktree 中消解冲突。冲突未解时 `next` 每轮返回同一 conflict action 直到消解。
- `merge_to_main`：实例所有 stage DONE 后，主 Agent 调用 wfctl 将实例 worktree 合入主仓库。无 stage_id。wfctl 执行合并：无冲突静默完成，打最终锚点；有冲突返回 conflict action，主 Agent 启动 conflict-resolver 在**主仓库**中消解。不设确认点。
- `requires_parallel_targets: true` 时，主 Agent 在 SubAgent 的 prompt 中注入"请产出 `parallel_targets`"。Skill 不感知工作流协议，仅响应注入要求。
- `context`：传递给 SubAgent 的结构化上下文（上游摘要、平行拆分目标等），由主 Agent 拼入 prompt。

### 确认点聚合

`next` 发现多个 stage 处于 `AWAITING_CONFIRM` 时，合并为一个 `confirm` action：

```json
{action: "confirm",
 pending: [
   {stage_id: "s03", questions: ["确认建模方案？"]},
   {stage_id: "s04", questions: ["架构选型为微服务？"]}
 ]}
```

`questions` 来自 SubAgent 上报的 Message 字段 `confirm_questions`。wfctl 从消息中提取，组装到 confirm action 中。

wfctl 不做聚合决策——返回当前时刻的 pending 快照。主 Agent 从中选取 stage，逐一向用户呈现，逐个调用 `confirm --stage <stage_id>`。每确认一个后再次调用 `next`，已确认的 stage 被移除，新增的确认请求自然出现在下一轮。

### exclusive 调度

- 有 `RUNNING` 且 `exclusive: true` 的 stage → `next` 本轮不返回任何新 `spawn`。
- 多个就绪 stage 中含 exclusive → 正常 FIFO，不特殊照顾。exclusive 完成后，下一轮 `next` 自然捡起等待的 stage。
- 不需要 `deferred` action——等待的 stage 依然是 `PENDING`。

### rejected 默认行为

`confirmation_point: true` 被用户拒绝，且 YAML 中无对应 `rejected` edge → Instance → `FAILED`，原因记录 deviation 日志。不静默终止。

### 子工作流父感知

父实例 `next` 发现 stage 关联子工作流时，读取子 instance JSON（`.agent/instances/<parent_id>/children/<child_id>/instance.json`）获取汇总状态：
- 子全部 DONE → 父 stage DONE
- 子进入 FAILED（终态）→ 父 stage ERROR
- 子 ACTIVE（含内部 retry/回跳等过渡态）→ 父 stage 保持 RUNNING

只有终态传播到父状态机，过渡态不透传——避免子实例一次 retry 就引发父级状态振荡。

**子状态透传**：`status` 命令返回父实例摘要时，对关联子工作流的 RUNNING stage，附 `child_instance` 字段暴露子实例内部状态快照（总 stage 数、各状态计数）。开发者无需翻子 instance JSON 即可感知子工作流内部进度与异常。状态机不消费此信息，纯可观测性。

---

## 五、`confirm` —— 确认/拒绝

```
confirm --instance <id> --stage <stage_id> --choice <string> [--feedback "..."]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | 目标 stage_id，必须是 `AWAITING_CONFIRM` 状态 |
| `--choice` | 是 | 用户选择的选项值。须与 YAML edges 中对应 `choice` 字段一致 |
| `--feedback` | 否 | 用户反馈文本，`rejected` 时建议提供，注入回 SubAgent 的 prompt 供重做参考 |

`--choice` 的值来源：SubAgent 在 `confirm_questions` 中标注各选项的值，主 Agent 将其呈现给用户，用户选择后主 Agent 将对应值传入 `--choice`。主 Agent 不做语义判断——只做传话。

### 行为

**choice 匹配到 confirmed edge**：
1. 校验 stage 当前状态为 `AWAITING_CONFIRM`
2. 在 YAML 中查找 `from=stage_id`、`condition=confirmed`、`choice` 值匹配的 edge
3. 找到 → stage 状态 → `DONE`，匹配的 edge 在随后的 `next` 中解锁对应下游
4. 未找到 → 报错 `{status: "error", reason: "unknown choice: <value>"}`
5. 写入 timeline（`awaiting_confirm→done`，标注 `confirmed_by: user`，`choice: <value>`）
6. 返回 `{status: "ok", stage_id: "s03", new_status: "DONE", matched: "方案A-微服务"}`

**choice 匹配到 rejected edge 或无 edge 的兜底**：
1. 在 YAML 中查找 `from=stage_id`、`condition=rejected`、`choice` 值匹配的 edge
2. **有匹配的 rejected edge**：stage 状态 → `PENDING`，`attempt_count` 归零。主 Agent 随后调用 `next` 时走对应 edge
3. **无匹配的 rejected edge**：Instance 状态 → `FAILED`，记录 deviation 日志
4. `--feedback` 文本写入 Message 关联字段，供 SubAgent 重做时参考
5. 写入 timeline（`awaiting_confirm→pending`，标注 `rejected_by: user`，`choice: <value>`）
6. 返回 `{status: "ok", stage_id: "s03", new_status: "PENDING"}` 或 `{status: "instance_failed", ...}`

### 与 `next` confirm action 的关系

`next` 返回的 confirm action 是**快照**——当前所有 `AWAITING_CONFIRM` 的 stage 列表。主 Agent 从中选取 stage，逐一向用户呈现确认问题，逐个调用 `confirm`。确认完一批后再次 `next`，剩余 pending 自然出现在下一轮快照中。

---

## 六、`rollback` —— 回退

```
rollback --instance <id> --stage <stage_id>
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | 回退目标 stage_id，必须是已完成（有锚点）的 stage |

### 行为

1. 校验 `<stage_id>` 存在且有对应锚点（`wf-<instance_id>-<stage_id>`）
2. 确定受影响的下游 stage：从 `<stage_id>` 出发，沿 edges（排除 `condition=failure` 和 `loop_exceeded`）BFS 遍历，收集所有可达 stage（含 parallel 拆出的所有 stage 实例）。**同 skill 延续链**：如果受影响的 stage 携带 `continued_to` 字段，沿链追踪，一并纳入复位范围
3. 重建实例 worktree 到目标锚点
4. 移除受影响 stage 的锚点 tags
5. 重置受影响 stage 状态为 `PENDING`，清零 `attempt_count`、`loop_counter`、`system_agent_id`、`continued_to`
6. 级联清理受影响 stage 的 `consumed_message_ids`
7. 写入 timeline
8. 返回 `{status: "ok", reset_stages: ["s04", "s05"], worktree: ".tmp/worktrees/instance-<id>/"}`

### 不可回退

| 情况 | 返回 |
|------|------|
| 目标 stage 无锚点 | `{status: "error", reason: "no anchor for stage <id>"}` |
| 实例 worktree 已合入主仓库 | `{status: "error", reason: "already merged to main"}` |

回退的完整 git 操作和清理细节见 `worktree与git锚点规范` §六。

---

## 七、`terminate` —— 取消实例

```
terminate --instance <id>
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |

### 行为

1. 校验实例状态为 `ACTIVE`（终态实例不可重复终止）
2. 实例状态 → `FAILED`，记录终止原因为 `user_terminated`
3. 清理该实例所有关联 stage worktree 和实例 worktree（`git worktree remove --force`）
4. 写入 deviation 日志（`type: USER_TERMINATED`）
5. 写入 timeline（`active→failed`，标注 `terminated_by: user`）
6. 返回 `{status: "ok", terminated_instance: "<id>", cleaned_worktrees: [...]}`

### 不可终止

| 情况 | 返回 |
|------|------|
| 实例已是终态（COMPLETED / FAILED） | `{status: "error", reason: "instance already in terminal state: <status>"}` |
| 实例不存在 | `{status: "error", reason: "instance not found: <id>"}` |

---

## 八、无状态契约

```
每次调用 = 冷启动
输入 → 读盘（Instance JSON / WORKFLOW.yaml / 消息池）→ 计算 → 输出 JSON → 写盘 → 退出
```

wfctl 不守护、不监听、不持有内存状态。主 Agent 负责在适当时机驱动 wfctl。

### 并发安全

- 主 Agent 对同一实例**必须串行**调用 wfctl（不并发启动两个 `next`）。
- wfctl 内部通过锁文件实现跨平台互斥：`.lock` 内写入 `pid:timestamp`，调用者检查 pid 是否存活，死则抢锁。不依赖 `fcntl.flock`，Windows/Unix 通用。

---

## 九、主 Agent ↔ wfctl 协作协议（循环模型）

```
[用户指令 / 平台通知(SubAgent 完成) / 用户确认]
              ↓
[主 Agent 理解上下文，组装 wfctl 输入]
              ↓
[调用 wfctl <cmd> --input <json> → 接收 <output json>]
              ↓
[主 Agent 解析 output 中的 actions，执行物理操作]
              ↓
[循环回到顶部]
```

wfctl 永远只返回"下一步该做什么"，不做任何物理副作用。

---

## 十、身份与消息投递机制

| 环节 | 机制 |
|------|------|
| 身份写入 | 主 Agent 调用 `create` 时，wfctl 在实例 worktree 固定位置写入身份元数据文件（instance_id、stage_id、project_root、消息投递路径） |
| 身份读取 | wfctl 提供 `identity` 子命令，SubAgent 启动后调用 `wfctl identity` 获取自身身份参数 |
| 契约约束 | 通用契约硬禁令：SubAgent 必须在执行任务前通过 `wfctl identity` 获取身份参数，禁止凭记忆或假设构造 |
| 消息写入 | SubAgent 调用 `wfctl message write`，wfctl 内部将消息追加写入原仓库 `.agent/instances/<instance_id>/messages/`，不经过 worktree 隔离 |

---

## 十一、消息池与状态机解耦

| 层面 | 规则 |
|------|------|
| SubAgent 侧 | 只通过 `wfctl message write` 追加消息到消息池，**绝不触碰 instance 状态文件**。SubAgent 不自行上报 `modified_files` |
| wfctl 注入 | 消费消息时通过 `git status --porcelain` 获取变更列表，注入 `modified_files`，同时做保护区校验 |
| 状态消费 | 由主 Agent 驱动 `next` 时统一处理。instance.json 记录 `consumed_message_ids` 列表。`next` 扫描消息池时跳过已消费的消息，对新消息幂等更新 stage 状态后追加到已消费列表。消息文件只增不删，崩溃可重放 |
| 并发安全 | 多个并发 stage 分别写入不同消息文件，天然隔离；状态更新在同一 `next` 调用中串行化，辅以文件锁兜底 |
| 目录结构 | `.agent/instances/<instance_id>/messages/<message_id>.json` |

---

## 十二、触发模型

| 触发源 | 行为 |
|--------|------|
| 用户发出指令 | 主 Agent 解析意图，必要时 `resolve` → `create` → 进入 `next` 循环 |
| SubAgent 完成 | 依赖宿主平台能力（进程退出通知），主 Agent 被动感知后调用 `next` |
| 用户回复确认 | 主 Agent 调用 `confirm` 注入确认，再驱动 `next` |

wfctl 无推送能力，不主动产生任何事件。不依赖定时轮询。

---

## 十三、批量指令、worktree 分配与两级合并

- `next` 返回批量 action 数组，主 Agent 按并发规则批量启动 SubAgent。

### worktree 分配

wfctl 在 `next` 中判断并分配 worktree，全部在 `.tmp/worktrees/` 下：

- **单 stage 就绪**：SubAgent 直接在实例 worktree（`.tmp/worktrees/instance-<id>/`）中执行，不额外拆分，stage 提交直接挂在实例 worktree 上。
- **多 stage 并发就绪**：每个 stage 分配独立的 stage 级 worktree（`.tmp/worktrees/stage-<id>-<s_id>/`），基于实例 worktree HEAD 创建，完成后 fetch 合并回实例 worktree。
- **parallel 拆分出的多个 stage 实例**：每个实例分配独立 worktree（`.tmp/worktrees/stage-<id>-<s_id>#<n>/`）。

### 两级合并与冲突处理

**非并发 stage**：直接在实例 worktree 中工作并提交，无需合并。

**并发 stage**：各自在独立 stage worktree 中工作，完成后由 wfctl `next` 将临时分支合并回实例 worktree：

```
SubAgent 上报 DONE
  → wfctl next 消费消息，尝试 stage worktree → 实例 worktree 合并
    → 无冲突：合并，清 stage worktree，stage → DONE
    → 有冲突：保留 stage worktree，stage → CONFLICT，返回 {action: "conflict", ...}
      → 主 Agent 启动 conflict-resolver SubAgent（全局 Skill）
        → stage CONFLICT → RUNNING
        → conflict-resolver 在冲突的实例 worktree 中消解冲突
          → 自动消解：上报 DONE
          → 语义冲突：上报 AWAITING_CONFIRM，向开发者呈现冲突选项
        → 主 Agent 调 next，wfctl 重试合并
          → 仍有冲突：stage → CONFLICT，返回 conflict action（循环）
          → 无冲突：合并，清 stage worktree，stage → DONE
```

conflict-resolver 是全局 Skill，放在 `artifacts/skills/conflict-resolver/`。它不感知工作流协议——它只做一件事：消解 git 合并冲突。简单冲突（相邻行、空白差异、相同文件不同区域）自动合并；语义冲突（同一行被两个 stage 改写）通过 `confirm_questions` 追问开发者，由用户裁决。

主 Agent 启动 conflict-resolver 时注入的 prompt 包含：冲突文件列表、冲突所在的实例 worktree 路径、产出冲突变更的 stage 信息。

- 实例 worktree → 主仓库：实例所有 stage DONE 后，`next` 返回 `{action: "merge_to_main", ...}`，主 Agent 直接将实例 worktree 合入主仓库。主仓库无新提交时静默合并；有新提交导致冲突时主 Agent 辅助用户解决，不静默覆盖。

---

## 十四、权限与隔离

| 层级 | 机制 |
|------|------|
| worktree 隔离 | SubAgent 运行中的物理屏障 |
| 身份校验 | `wfctl identity` + 契约强制读取，防止越权上报 |
| 消息池隔离 | 按 instance_id 分目录，防止跨实例污染 |
| git 锚点可逆 | 任何时刻可从锚点回退 |
