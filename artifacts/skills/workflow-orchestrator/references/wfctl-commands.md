# wfctl 命令参考

> 本文档是编排器调用 wfctl 的完整参考。所有命令通过 `python .claude/scripts/wfctl/main.py <command> [options]` 在项目根目录执行。下文示例中以 `wfctl` 简写表示该路径。

---

## 一、命令总览

| 命令 | 职责 | 何时调用 |
|------|------|---------|
| `resolve` | 扫描可用工作流 / 解析单个 WORKFLOW.yaml | 用户表达意图后 |
| `create` | 生成 Instance JSON，创建实例 worktree，打初始锚点。支持 `--clone` 克隆旧实例、`--fast-forward-to` 跳转到指定 stage | 用户确认工作流后 |
| `next` | 调度核心：消费消息 → 更新 stage → 计算就绪 → 返回 actions | 每次循环的默认调用 |
| `sync` | 仅消费消息、更新 stage，不计算 next（诊断用） | 中断恢复对齐状态 |
| `confirm` | 将 AWAITING_CONFIRM stage 解锁，恢复流转 | 用户回复确认后 |
| `rollback` | 回退实例 worktree 到指定 stage 锚点，重置下游 | 用户要求回退时 |
| `pause` | 暂停 ACTIVE 实例，重置 RUNNING stage → PENDING | 用户要求暂停时 |
| `resume` | 恢复 PAUSED 实例 → ACTIVE | 用户要求继续时 |
| `skip` | 跳过 stage 直接标记 DONE。`--force` 可跳过非 PENDING 状态（RUNNING / AWAITING_CONFIRM / ERROR） | 用户要求跳过已完成/不需要的 stage |
| `terminate` | 取消 ACTIVE/PAUSED 实例，清理 worktree 和 tag，标记 FAILED | 用户要求取消时 |
| `status` | 扫描实例状态（项目级 / 实例级） | 用户询问进度 / 中断恢复 |
| `deviate` | 追加 deviation 日志记录 | 检测到非标行为时 |
| `identity` | 读取当前 worktree 的身份元数据 | SubAgent 启动时自行获取 |
| `message` | 消息操作（write 子命令） | SubAgent 上报阶段结果 |
| `cleanup` | 清理僵尸实例、孤儿 worktree 和残留 tag，自动备份 | 手动清理残留状态时 |
| `restore` | 从归档恢复误删实例 | 需要恢复误删实例时 |

---

## 二、命令详情

### 2.1 resolve

**扫描可用工作流**：
```bash
wfctl resolve
```
返回：
```json
{
  "workflows": [
    {"id": "math-model", "version": "2.1.0", "description": "数学建模", "tags": ["建模", "数学"], "stages_count": 10}
  ]
}
```

**解析单个工作流**：
```bash
wfctl resolve --workflow <id>@<ver>
```
返回：
```json
{
  "workflow_id": "math-model",
  "version": "2.1.0",
  "max_parallel_agents": 6,
  "stages": [
    {"stage_id": "s01", "name": "选题分析", "skill_id": "topic-analyst",  "mandatory": true, "retry": 2, "model": "standard"}
  ],
  "edges": [
    {"from": "s01", "to": "s02", "condition": "success", "choice": "通过"}
  ]
}
```

### 2.2 create

**基本用法**：
```bash
wfctl create --workflow <id>@<ver> [--goal "用户目标描述"]
```
返回：
```json
{
  "status": "ok",
  "instance_id": "20260517-001",
  "workflow_id": "math-model",
  "version": "2.1.0",
  "worktree": ".tmp/worktrees/instance-20260517-001/"
}
```

创建前自动 `git worktree prune` 清理残留注册；失败时回滚（删 worktree、删 tag、删实例目录）。

**克隆旧实例**（`--clone`）：
```bash
wfctl create --workflow <id>@<ver> --clone <old_instance_id> [--goal "..."]
```
行为：
1. 新 worktree 基于旧实例 worktree 的 HEAD 创建，**完整继承所有 DONE stage 的文件产物**
2. 旧实例中 DONE 的 stage（含 parallel fan-out 多实例）在新实例中保持 DONE；其余重置为 PENDING
3. 只复制被继承 DONE stage 的输出消息，改写 `instance_id` 为新 ID
4. `consumed_message_ids` 初始化为已复制消息 ID，避免 `next` 重复消费
5. 旧实例若为非 FAILED 终态，自动标记 FAILED 并写入 `INSTANCE_CLONED` deviation

返回（含额外字段）：
```json
{
  "status": "ok",
  "instance_id": "20260519-002",
  "workflow_id": "math-model",
  "version": "2.1.0",
  "worktree": ".tmp/worktrees/instance-20260519-002/",
  "cloned_from": "20260519-001",
  "worktree_source": "old-head",
  "inherited_done_stages": ["p0-init", "p1a", "p1b"]
}
```
- `worktree_source`: `"old-head"`（正常继承）或 `"main-head"`（旧 worktree 已不存在，退化到主仓库 HEAD）
- `--clone` 与 `--fast-forward-to` 互斥

**快速跳转**（`--fast-forward-to`）：
```bash
wfctl create --workflow <id>@<ver> --fast-forward-to <stage_id> [--goal "..."]
```
行为：
1. 反向 BFS 沿 `incoming` edges 收集目标 stage 的所有拓扑前驱
2. 排除 `failure` / `loop_exceeded` 边（非常规到达路径）
3. 将收集到的祖先 stage 全部标记为 DONE，目标 stage 保持 PENDING
4. 创建后首个 `next` 直接进入目标 stage

返回（含额外字段）：
```json
{
  "status": "ok",
  "instance_id": "20260519-002",
  "workflow_id": "math-model",
  "version": "2.1.0",
  "worktree": ".tmp/worktrees/instance-20260519-002/",
  "fast_forwarded": ["p0-init", "p1a", "p1b"]
}
```
- 目标 stage 必须是有效的非虚拟 stage，否则报错
- 不依赖旧实例，worktree 从主仓库 HEAD 全新创建，无历史包袱

### 2.3 next

```bash
wfctl next --instance <id>
```

同 Skill 延续检测：`next` 自动读取 `.agent/running_agents.json`（项目级文件），按 `instance_id` 过滤后查表。编排器在 `spawn`/`continue` 后维护此文件。

返回：
```json
{
  "status": "ok",
  "instance_id": "20260517-001",
  "instance_status": "ACTIVE",
  "actions": [
    {
      "action": "spawn",
      "stage_id": "s01",
      "stage_instance_id": "s01",
      "skill_id": "topic-analyst",
      "worktree": ".tmp/worktrees/instance-20260517-001/",
      "requires_parallel_targets": false,
      
      "valid_routing_choices": [],
      "context": {
        "goal": "为 M01-M05 模块编写规范",
        "upstream_summaries": [],
        "parallel_target": null
      }
    }
  ]
}
```

**action 类型枚举**：

| action | 含义 | 编排器行为 |
|--------|------|-----------|
| `spawn` | 启动 SubAgent | 构造 prompt，`Agent(worktree=..., prompt=..., run_in_background=true)` |
| `continue` | 延续已有 SubAgent | 向 `system_agent_id` 发继续消息（含新 worktree+task），不创建新实例 |
| `retry` | 重试失败 stage | 同 spawn，attempt 计数已由 wfctl 递增 |
| `confirm` | 有待确认 stage | 逐个呈现 AskUserQuestion → `wfctl confirm` |
| `conflict` | 合并冲突 | 启动 conflict-resolver SubAgent 消解 |
| `merge_to_main` | 实例已合入主仓库 | 子实例自动；一级实例需先经 `__merge__` 确认 |
| `terminate` | 实例终态 | 报告用户，停止循环 |
| `await` | 无就绪 stage | 等待 SubAgent 完成通知 |

> **v3.1**：`next --instance <root>` 已递归处理整棵实例树（父→子→孙），子工作流的 spawn/continue/confirm 自动出现在父级 action 列表中。编排器**不再**需要单独调用 `wfctl next --instance <child_id>`。`child_next` action 类型已移除。

**各 action 的完整字段**：

spawn / retry：
```json
{
  "action": "spawn",
  "instance_id": "20260517-001",
  "stage_id": "s03",
  "stage_instance_id": "s03",
  "skill_id": "topic-analyst",
  "worktree": ".tmp/worktrees/instance-<id>/",
  "model": "standard",
  "requires_parallel_targets": false,
  
  "context": {
    "goal": "实例目标",
    "upstream_summaries": [{"stage_id": "s01", "checkpoint": "已完成..."}],
    "parallel_target": {"id": "7.1.1", "label": "数据预处理", "context": "清洗 raw_data.csv"}
  }
}
```

continue：
```json
{
  "action": "continue",
  "instance_id": "20260517-001",
  "stage_id": "s02",
  "skill_id": "design-tech-stack",
  "worktree": ".tmp/worktrees/instance-<id>/",
  "system_agent_id": "agent-001",
  "requires_parallel_targets": false,
  
  "context": {
    "goal": "实例目标",
    "upstream_summaries": [{"stage_id": "s01", "checkpoint": "已完成需求收集，用户确认方案A"}]
  }
}
```
- `system_agent_id`：已有 SubAgent 的平台 ID。编排器向此实例发送继续消息
- `worktree`、`context`：与 spawn 同字段，分配逻辑一致
- 编排器收到后**不**调用 `Agent()`，而是向 `system_agent_id` 发送继续消息（含新 worktree + task）

`model` 字段来自 stage 在 WORKFLOW.yaml 中声明的模型档位（`light` / `standard` / `heavy`）。
编排器读取 `references/model-mapping.yaml` 按当前平台解析为具体模型名后传入 `Agent(model=...)`。
若 action 无 `model` 字段，Agent 继承父级模型。

confirm：
```json
{
  "action": "confirm",
  "pending": [
    {
      "stage_id": "s02",
      "instance_id": "20260517-001",
      "questions": ["full_design：全新设计，从意图澄清开始", "code_only：存量代码逆向"]
    },
    {"stage_id": "s01-scheme-design", "instance_id": "child-001", "parent_stage_id": "p2-question-solution", "questions": ["确认方案设计？"]}
  ]
}
```
- `instance_id`：确认目标实例。与当前父实例不同时，编排器应对子实例调用 `wfctl confirm --instance <instance_id>`
- `parent_stage_id`：仅子实例条目出现，标记父工作流中对应的 stage
- `questions`：SubAgent 上报的原始选项（权威选项来源）。为 `null` 时编排器降级解析 questions

conflict：
```json
{
  "action": "conflict",
  "stage_id": "s03",
  "worktree": ".tmp/worktrees/stage-<id>-s03/",
  "conflict_files": ["src/a.py", "src/b.py"],
  "source_stage": "s03"
}
```

terminate：
```json
{
  "action": "terminate",
  "status": "COMPLETED",
  "reason": "所有 stage 已完成"
}
```

### 2.4 sync

```bash
wfctl sync --instance <id>
```
仅消费消息池、更新 stage 状态，不计算 next。返回状态变更摘要：
```json
{
  "status": "ok",
  "changes": [
    {"stage_id": "s01", "old_status": "RUNNING", "new_status": "DONE", "message_id": "msg-001"}
  ]
}
```

### 2.5 confirm

```bash
wfctl confirm --instance <id> --stage <stage_id> --choice "<选项值>" [--feedback "用户反馈"]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | 目标 stage_id，必须是 AWAITING_CONFIRM。特殊值 `__merge__` 处理合入确认 |
| `--choice` | 是 | 选项值，须与 YAML edges 中对应 choice 严格一致。`__merge__` 时：`yes` 允许合入，`no` 推迟 |
| `--feedback` | 否 | confirm 时可选填写，注入 continue prompt |

**`__merge__` 伪 stage**：一级实例全部 stage DONE 后，wfctl 自动在 `next` 中注入确认请求。编排器按标准 confirm 流程处理——调 `wfctl confirm --stage __merge__ --choice yes`。确认后下次 `next` 执行合入。

返回（confirm 永远返回 PENDING + continue）：
```json
{"status": "ok", "stage_id": "s03", "new_status": "PENDING", "matched": "方案A-微服务", "loop": 1}
```

**`--choice` 值来源**：SubAgent 在 `confirm_questions` 中预设选项值，编排器原样传入，不做修改。

### 2.6 rollback

```bash
wfctl rollback --instance <id> --stage <stage_id>
```
返回：
```json
{
  "status": "ok",
  "reset_stages": ["s04", "s05"],
  "worktree": ".tmp/worktrees/instance-20260517-001/"
}
```

不可回退的情况：
- 目标 stage 无锚点 → `{"status": "error", "reason": "no anchor for stage <id>"}`（正常 `next` 完成的 stage 均有锚点；若缺失则可能是 skip/clone 遗留问题）
- 已合入主仓库 → `{"status": "error", "reason": "already merged to main"}`

锚点覆盖规则：所有正常走完 `next` 流程的 DONE stage（含并行 fan-out 的每个 `stage_instance_id`）均由 `_auto_commit_done_stages` 自动打锚；virtual stage 由 `_resolve_virtual_stages` 打锚；skip/conflict-resolved 各自打锚。

### 2.7 pause

暂停活跃实例，将 RUNNING stage 重置为 PENDING。

```bash
wfctl pause --instance <id> [--reason "暂停原因"]
```
返回：
```json
{"status": "ok", "instance_id": "20260518-001", "reset_stages": ["p2-scheme-design"]}
```

执行操作：RUNNING → PENDING → 实例状态 → PAUSED → 写 deviation(INSTANCE_PAUSED) → 写 timeline。`next` 对 PAUSED 实例自动拒绝。不碰 AWAITING_CONFIRM / ERROR / CONFLICT 状态。

### 2.8 resume

恢复 PAUSED 实例为 ACTIVE。

```bash
wfctl resume --instance <id>
```
返回：
```json
{"status": "ok", "instance_id": "20260518-001"}
```

执行操作：实例状态 → ACTIVE → 写 deviation(INSTANCE_RESUMED) → 写 timeline。编排器随后调用 `wfctl next` 继续调度，被重置的 stage 重新 spawn。

### 2.9 skip

跳过指定 stage，直接标记 DONE。

```bash
wfctl skip --instance <id> --stage <stage_id> [--reason "跳过原因"] [--force]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | 目标 stage_id |
| `--reason` | 否 | 跳过原因，默认 "Manually skipped" |
| `--force` | 否 | 强制跳过非 PENDING 状态（RUNNING / AWAITING_CONFIRM / ERROR） |

返回：
```json
{"status": "ok", "stage_id": "p0-init", "old_status": "RUNNING", "forced": true, "reason": "..."}
```

执行操作：标 DONE → 清除 `started_at`（若为 RUNNING）→ 打锚点 → 写 timeline → 写 deviation → 保存。

**并行 stage 处理**：若 stage 已被 `_check_parallel` 拆分为多个实例（fan-out），`skip` 会查找**所有** `stage_id` 匹配的条目，逐一标记 DONE 并打锚点（按 `stage_instance_id`）。返回 `instances_skipped` 计数。

**可跳过状态**：

| 状态 | 无 --force | 有 --force | 说明 |
|------|-----------|-----------|------|
| PENDING | ✅ | ✅ | 正常跳过 |
| RUNNING | ❌ | ✅ | 清理 start_at，标记 DONE |
| AWAITING_CONFIRM | ❌ | ✅ | 跳过确认，直接 DONE |
| ERROR | ❌ | ✅ | 跳过重试，直接 DONE |
| DONE | ❌ | ❌ | 已是终态，拒绝 |
| CONFLICT | ❌ | ❌ | 应走冲突消解流程 |

**使用场景**：
- **正常 skip**：用户确认某 stage 已完成或不需要执行，将其直接标记 DONE
- **force skip**：恢复场景中，`next` 已将 stage 置为 RUNNING 但用户想跳过（如 p0-init 在实例 clone 后已被调度）
- **恢复工作流**：`create --clone` 后若某些 stage 被意外调度，可用 `skip --force` 补救
- deviation 类型：普通 skip 写 `STAGE_SKIPPED`，force skip 写 `STAGE_SKIPPED_FORCE`

### 2.10 terminate

```bash
wfctl terminate --instance <id> [--reason "终止原因"] [--force]
```

**安全确认**：一级实例未合入 main 时，无 `--force` 返回：
```json
{"status": "requires_confirmation", "instance_id": "...", "reason": "Root instance not merged to main. Use --force to terminate anyway."}
```
编排器用 `AskUserQuestion` 确认后加 `--force` 重试。

返回：
```json
{
  "status": "ok",
  "instance_id": "20260517-001",
  "reason": "User requested termination"
}
```

执行操作：创建备份分支 `wf-backup-{id}` → 归档实例目录到 `.agent/archive/` → 置实例 FAILED → 删 tag → 移除 worktree → 清理孤儿 worktree → 写 deviation。

不可终止的情况：
- 已是终态 → `{"status": "error", "reason": "instance already in terminal state: COMPLETED"}`
- 不存在 → `{"status": "error", "reason": "instance not found: <id>"}`

### 2.11 status

**项目级**（无参数）：
```bash
wfctl status
```
返回：
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
        {"stage_id": "s04", "status": "AWAITING_CONFIRM", "output_message_id": "msg-014"}
      ]
    }
  ],
  "recent_completed": ["20260516-003"],
  "recent_failed": ["20260516-001"]
}
```

**实例级**：
```bash
wfctl status --instance <id>
```
返回：
```json
{
  "instance_id": "20260517-001",
  "goal": "为 M01-M05 模块编写落地规范",
  "status": "ACTIVE",
  "stages_summary": {"total": 10, "pending": 2, "running": 3, "awaiting_confirm": 1, "done": 4, "error": 0, "conflict": 0},
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
    {"stage_id": "s04", "status": "AWAITING_CONFIRM", "output_message_id": "msg-014", "confirm_questions": ["确认建模方案？"]},
    {"stage_id": "s05", "status": "PENDING", "waiting_for": ["s04"]}
  ]
}
```

`stages` 仅列出非 DONE 的 stage。字段出现条件：

| 字段 | 出现条件 |
|------|---------|
| `output_message_id` | stage 有产出消息 |
| `waiting_for` | PENDING — 依赖的上游 stage_id 列表 |
| `confirm_questions` | AWAITING_CONFIRM — 待确认问题 |
| `attempt_count` | ERROR — 当前重试次数 |
| `child_instance` | 关联子工作流的 RUNNING stage |

### 2.12 deviate

```bash
wfctl deviate --instance <id> --type <type> --reason "描述" [--stage <id>] [--files ...]
```
返回：
```json
{"status": "ok"}
```

常用 type：`USER_OVERRIDE`、`USER_ROLLBACK`、`USER_TERMINATED`、`ACCESS_VIOLATION`。

### 2.13 identity

SubAgent 启动时调用，从当前目录向上查找 `.wfctl_identity.json`，读取身份元数据。

```bash
wfctl identity
```
返回：
```json
{
  "instance_id": "20260517-001",
  "stage_id": "p0-init",
  "stage_instance_id": "p0-init",
  "message_target_path": ".agent/instances/20260517-001/messages"
}
```

必须在 worktree 内部执行（`.wfctl_identity.json` 位于 worktree 根目录）。未找到身份文件时返回 `IDENTITY_MISMATCH`。

### 2.14 message

SubAgent 上报阶段结果。当前仅支持 `write` 子命令。

```bash
wfctl message write \
  --instance <id> \
  --stage <stage_id> \
  --status DONE|ERROR|AWAITING_CONFIRM \
  --report "摘要" \
  [--checkpoint "已完成：...；关键上下文：...；待处理：..."] \
  [--questions "问题1" "问题2"] \
  [--parallel-targets "t1:标签:上下文" "t2:标签:上下文"]
```

返回：
```json
{
  "status": "ok",
  "message_id": "msg-a1b2c3d4",
  "timestamp": "2026-05-18T12:00:00.000Z"
}
```

参数说明：
| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID（须与身份文件一致） |
| `--stage` | 是 | stage_id（须与身份文件一致） |
| `--status` | 是 | DONE / ERROR / AWAITING_CONFIRM |
| `--report` | 是 | 执行摘要 |
| `--checkpoint` | 否 | 三部分交接上下文 |
| `--questions` | 否 | AWAITING_CONFIRM 时的问题列表 |
| `--parallel-targets` | 否 | 并行拆分目标，格式 `id:标签:上下文` |

### 2.15 cleanup

清理僵尸实例目录、孤儿 worktree 和残留 anchor tag。

```bash
wfctl cleanup [--instance <id>] [--dry-run] [--force]
```

`--force`：强制清理一级未合入实例，跳过安全检查。

返回：
```json
{
  "status": "ok",
  "removed": [
    {"worktree": ".tmp/worktrees/instance-20260518-001", "reason": "orphan worktree", "action": "removed"},
    {"tag": "wf-20260518-001-s00-workflow-start", "reason": "stale anchor tag", "action": "deleted"}
  ],
  "skipped": [
    {"instance": "20260518-003", "reason": "root instance not merged, use --force to cleanup"}
  ],
  "dry_run": false
}
```

三级清理：
1. `git worktree prune` 清注册残留
2. 僵尸实例目录（ACTIVE 但无 worktree 且无运行中 stage，或终态残留）
3. 残留 anchor tag（无对应实例目录的）

删除前自动创建备份分支 + 归档实例目录到 `.agent/archive/`。一级未合入实例无 `--force` 时加入 `skipped` 而非删除。

`--dry-run` 仅列出不执行。`--instance <id>` 仅清理指定实例。

### 2.16 restore

从归档恢复误删的实例。

```bash
wfctl restore --instance <id>
```

返回：
```json
{"status": "ok", "instance_id": "20260518-003"}
```

执行操作：从 `.agent/archive/{id}/` 移回实例目录 → 从 `wf-backup-{id}` 分支重建 worktree → 重建 anchor tag。

不可恢复的情况：
- 归档不存在 → `{"status": "error", "code": "INSTANCE_NOT_FOUND"}`
- 目标实例已存在 → `{"status": "error", "code": "INSTANCE_EXISTS"}`
