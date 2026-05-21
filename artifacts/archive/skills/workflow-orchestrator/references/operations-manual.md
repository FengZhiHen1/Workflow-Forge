# 编排器操作手册

> 本手册供编排器执行时查阅具体脚本调用方式。SKILL.md 中只保留决策逻辑和脚本名称映射，详细命令参数请查阅本手册。

## 最常用命令速查

```bash
# 修改 stage 状态（最高频）
python scripts/instance_manager.py update-stage --instance <id> --stage <sid> --status <新状态>

# 读取 SubAgent 上报的 Message
python scripts/message_manager.py read --message-id <id>

# 扫描 PENDING_CONFIRM 消息
python scripts/message_manager.py scan --instance <id> --status PENDING_CONFIRM

# 更新 Message 状态（确认回复后）
python scripts/message_manager.py update --message-id <id> --status CONFIRMED

# 校验 Instance（每次修改后必须调用）
python .claude/scripts/validate_instance.py --instance <id>

# 创建新实例
python scripts/instance_manager.py create --workflow <wid> --version <ver>

# 解析工作流
python scripts/resolve_workflow.py --query "<用户输入>"
```

---

## 初始化脚本

### `scripts/resolve_workflow.py`

**用途**：扫描 `.claude/workflows/`，按关键词匹配工作流。

```bash
python scripts/resolve_workflow.py --query "<用户输入>"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--query` | 是 | 用户输入的工作流关键词 |

---

### `scripts/instance_manager.py create`

**用途**：从 Reference 生成 Instance JSON。

```bash
python scripts/instance_manager.py create \
  --workflow <workflow_id> \
  --version <version> \
  --special-instructions "<用户补充指令>"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--workflow` | 是 | 工作流 ID |
| `--version` | 是 | 版本号 |
| `--special-instructions` | 否 | 用户补充指令 |

**输出示例**：
```json
{
  "success": true,
  "instance_id": "wf-refactor-20260510-001-a7f3",
  "path": ".agent/workflows/instances/wf-refactor-20260510-001-a7f3.json",
  "workflow_id": "refactor-pipeline",
  "version": "1.3.0",
  "total_stages": 4,
  "first_stage": "s1_analyze",
  "model_tiers": ["light", "standard", "heavy"],
  "default_model_tier": "standard",
  "stages": [
    {"stage_id": "s1_analyze", "model_tier": "standard", "resolved_model": null},
    {"stage_id": "s2_refactor", "model_tier": "heavy", "resolved_model": null},
    {"stage_id": "s3_test", "model_tier": "light", "resolved_model": null},
    {"stage_id": "s4_doc", "model_tier": "light", "resolved_model": null}
  ]
}
```

> `resolved_model` 在创建时为 `null`，由编排器在启动 SubAgent 前读取自身 `references/model-tiers.yaml`，按当前平台解析并回填到 Instance。

---

### `scripts/instance_manager.py set-create`

**用途**：基于同一工作流定义批量创建多个实例（Instance Set）。

```bash
python scripts/instance_manager.py set-create \
  --workflow <workflow_id> \
  --version <version> \
  --param-list '[{"target":"A"}, {"target":"B"}]' \
  [--completion-policy all|any] \
  [--confirmation-mode batch|stream|individual]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--workflow` | 是 | 工作流 ID |
| `--version` | 是 | 版本号 |
| `--param-list` | 是 | JSON 数组，每个元素生成一个实例 |
| `--completion-policy` | 否 | Set 完成策略：`all`（默认，全部完成）/ `any`（任一完成） |
| `--confirmation-mode` | 否 | 确认点聚合模式：`batch`（默认，汇总确认）/ `stream`（流水确认）/ `individual`（逐个确认） |

**输出示例**：
```json
{
  "success": true,
  "set_id": "set-project-design-20260514-001-a7f3",
  "instances": [
    {"instance_id": "wf-...", "params": {"target": "A"}},
    {"instance_id": "wf-...", "params": {"target": "B"}}
  ],
  "total_instances": 2
}
```

---

### `scripts/instance_manager.py set-status`

**用途**：查询 Instance Set 状态（省略 `--set-id` 则列出所有 Set）。

```bash
python scripts/instance_manager.py set-status --set-id <set_id>
```

**输出**：Set 聚合状态 + 各实例状态。

---

### `scripts/instance_manager.py set-cancel`

**用途**：取消 Set 内所有活跃实例。

```bash
python scripts/instance_manager.py set-cancel --set-id <set_id>
```

---

## 状态管理脚本

### `scripts/instance_manager.py update-stage`

**用途**：原子更新 stage 状态（含流转校验、重试计数、pending_confirmations 维护）。

```bash
python scripts/instance_manager.py update-stage \
  --instance <instance_id> \
  --stage <stage_id> \
  --status <new_status> \
  [--message-id <message_id>] \
  [--agent-id <逻辑agent_id>] \
  [--system-agent-id <系统agent_id>] \
  [--force]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | Stage ID |
| `--status` | 是 | 新状态：`RUNNING` / `DONE` / `ERROR` / `BLOCKED` / `PENDING` / `SKIPPED` / `CANCELLED` |
| `--message-id` | 否 | 关联的 Message ID |
| `--agent-id` | 否 | 逻辑 assigned_agent_id（编排器生成） |
| `--system-agent-id` | 否 | 系统返回的真实 agent_id |
| `--force` | 否 | 绕过状态流转校验 |

**特殊校验**：
- `ERROR → PENDING`：检查 `attempt_count < retry_policy.max_attempts`
- `DONE → PENDING`：要求 `metadata.rolled_back_at` 存在（或 `--force`）
- `BLOCKED → RUNNING`：自动解除 `pending_confirmations`

---

### `scripts/instance_manager.py skip-stage`

**用途**：将 stage 标记为 `SKIPPED`，并记录预检跳过元数据。

```bash
python scripts/instance_manager.py skip-stage \
  --instance <instance_id> \
  --stage <stage_id> \
  [--reason PREFLIGHT_DETECTION] \
  [--evidence "<判断依据>"] \
  [--user-confirmed] \
  [--force]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | Stage ID |
| `--reason` | 否 | 跳过原因，默认 `PREFLIGHT_DETECTION` |
| `--evidence` | 否 | 完成证据描述 |
| `--user-confirmed` | 否 | 用户是否显式确认 |
| `--force` | 否 | 绕过 `PENDING` 限制 |

**约束**：仅允许从 `PENDING` 跳过（除非 `--force`）。

---

### `scripts/instance_manager.py rollback`

**用途**：回退到指定 stage（备份 `.agent/`、重置状态机）。

```bash
python scripts/instance_manager.py rollback \
  --instance <instance_id> \
  --target-stage <stage_id>
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--target-stage` | 是 | 目标 Stage ID |

**脚本内部完成**：
1. 备份 `.agent/` 到 `.agent/backups/<instance_id>/<timestamp>/`
2. 重置状态机：target 及之后所有 stages 恢复为 `PENDING`
3. 并发活跃任务标记为 `CANCELLED`
4. 输出 Git 锚点 tag 供 checkout 使用

**输出示例**：
```json
{
  "success": true,
  "instance_id": "wf-xxx",
  "target_stage": "s2_refactor",
  "git_anchor_tag": "wf-xxx-s2_refactor-msg123-pre",
  "backup_path": ".agent/backups/wf-xxx/20260510-143000/",
  "instruction": "Run: git checkout wf-xxx-s2_refactor-msg123-pre -- ."
}
```

---

### `scripts/instance_manager.py restore-agent`

**用途**：从最近一次 rollback 的备份中恢复 `.agent/` 目录。

```bash
python scripts/instance_manager.py restore-agent \
  --instance <instance_id> \
  [--backup-path <路径>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--backup-path` | 否 | 覆盖备份路径（默认读取 instance metadata） |

---

### `scripts/instance_manager.py log-deviation`

**用途**：记录偏差日志。

```bash
python scripts/instance_manager.py log-deviation \
  --instance <instance_id> \
  --type <deviation_type> \
  --reason "<原因描述>" \
  [--user-confirmed] \
  [--original-stage <stage_id>] \
  [--impact-stages "s1,s2"] \
  [--resolution "<解决说明>"]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--type` | 是 | 偏差类型：`USER_OVERRIDE` / `USER_ROLLBACK` / `SKILL_FAILURE` / `TIMEOUT` / `RESOURCE_CONFLICT` / `MANUAL_ADJUSTMENT` / `LOOP_EXCEEDED` |
| `--reason` | 是 | 原因描述 |
| `--user-confirmed` | 否 | 用户是否确认 |
| `--original-stage` | 否 | 原始 stage |
| `--impact-stages` | 否 | 受影响的 stages，逗号分隔 |
| `--resolution` | 否 | 解决方案说明 |

---

## 消息管理脚本

### `scripts/message_manager.py`

**用途**：编排器操作 Message 的唯一入口。子命令：`read` / `scan` / `update` / `sync` / `upstream`。

**编排器禁止直接读写 `.agent/messages/`，必须通过本脚本。**

#### `read`

```bash
python scripts/message_manager.py read --message-id <message_id>
```

#### `scan`

```bash
python scripts/message_manager.py scan \
  --instance <instance_id> \
  --status <status>
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--status` | 是 | 扫描状态，如 `PENDING_CONFIRM` |

#### `update`

```bash
python scripts/message_manager.py update \
  --message-id <message_id> \
  --status <new_status> \
  [--confirm-responses '[true]']
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--message-id` | 是 | Message ID |
| `--status` | 是 | 新状态：`AWAITING_USER` / `CONFIRMED` 等 |
| `--confirm-responses` | 否 | 用户确认结果 JSON 数组 |

#### `sync`

```bash
python scripts/message_manager.py sync --instance <instance_id>
```

#### `upstream`

```bash
python scripts/message_manager.py upstream --message-id <message_id>
```

---

## 中断恢复脚本

### `scripts/sync_instance_state.py`

**用途**：编排器中断后扫描活跃实例，将 Instance 状态与已上报 Message 对齐。

```bash
python scripts/sync_instance_state.py [--dry-run]
```

**干运行模式**（先预览不一致项）：
```bash
python scripts/sync_instance_state.py --dry-run
```

**自动处理场景**：
- `RUNNING → DONE`
- `RUNNING → ERROR`
- `RUNNING → BLOCKED`
- `BLOCKED → PENDING`

**输出**：含 `running_without_message` 孤儿任务列表。

---

### `scripts/collect_running_agents.py`

**用途**：收集所有活跃实例中 `status=RUNNING` 的 stage 的 agent 信息。

```bash
python scripts/collect_running_agents.py
```

**输出**：`agents` 列表（含 `system_agent_id`），供编排器查询平台存活状态。

---

## 辅助工具脚本

### `scripts/collect_upstream_context.py`

**用途**：为指定 stage 收集直接前置依赖的 Message 产出摘要。

```bash
python scripts/collect_upstream_context.py \
  --instance <instance_id> \
  --stage <stage_id> \
  --max-report-length 500
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--stage` | 是 | Stage ID |
| `--max-report-length` | 否 | 摘要最大长度，默认 500 |

---

### `scripts/generate_agent_id.py`

**用途**：为 Stage 生成全局唯一的逻辑 `agent_id`。

```bash
python scripts/generate_agent_id.py --stage <stage_id> --instance <instance_id>
```

**格式**：`{stage_id}-{YYYYMMDD}-{HHMMSS}-{4位hex}`

---

### `scripts/find_message_by_agent.py`

**用途**：按 `agent_id`（逻辑或系统编号）反向查找 Message。

```bash
python scripts/find_message_by_agent.py \
  --agent-id <assigned_agent_id> \
  --instance <instance_id>
```

---

### `scripts/generate_report.py`

**用途**：生成工作流运行状态报告。支持按 Instance 过滤或按 Instance Set 聚合。

```bash
python scripts/generate_report.py [--instance <instance_id>] [--set-id <set_id>]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 否 | 过滤到特定实例 |
| `--set-id` | 否 | 聚合特定 Set 的实例状态，输出附加 Set 级汇总 |

---

## 基础设施脚本

### `.claude/scripts/validate_instance.py`

**用途**：Instance 状态机校验器。**每次修改 Instance 后必须调用。**

```bash
python .claude/scripts/validate_instance.py --instance <instance_id> [--strict]
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `--instance` | 是 | 实例 ID |
| `--strict` | 否 | 严格模式：检查 git tag 存在性 |

**返回**：
- 成功：`{"valid": true}`，退出码 `0`
- 失败：`{"valid": false, "errors": [...]}`，退出码 `1`

**校验项**：语法、引用完整性、版本一致性、Stage 合法性、Message 存在性、Git 锚点、状态流转、循环计数器、并发一致性。

> **强制规则**：校验失败则立即停止执行，读取 errors 修正，最多重试 3 次。

---

### `.claude/scripts/write_message.py`

**用途**：SubAgent 上报 Message 的原子写入脚本。

---

### `.claude/scripts/calc_ref_hash.py`

**用途**：计算 `WORKFLOW.yaml` 的 `snapshot_hash`。

---

## 废弃脚本

以下脚本位于 `scripts/deprecated/`，不再被 workflow-orchestrator 调用：

- `scheduler.py`
- `route_edges.py`
- `check_terminal.py`
- `perform_rollback.py`
- `registry_manager.py`
- `git_anchor.py`

> `registry_manager.py` 维护的 `registry.json` 机制已被淘汰，编排器直接扫描 `instances/*.json` 管理实例。
