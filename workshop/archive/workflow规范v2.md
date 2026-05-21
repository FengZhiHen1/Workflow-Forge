# **Workflow 规范 v2.0.0**

---

## 1. 文件体系

```
.claude/
├── workflows/                          # 工作流 Reference 定义
│   └── <workflow_id>@<version>/        # 每个工作流一个目录
│       ├── WORKFLOW.md                 # 人类可读：名称、概览、Mermaid 流程图
│       └── WORKFLOW.yaml               # 机器规范：stages、edges、并发规则
├── skills/
│   └── <skill_id>/
│       ├── skill.md
│       └── references/
├── contracts/
│   └── common.md
└── scripts/
    ├── write_message.py
    ├── validate_instance.py
    └── calc_ref_hash.py

.agent/
├── workflows/
│   ├── instances/
│   │   └── <instance_id>.json          # Instance 状态机
│   └── registry.json                    # 活跃实例索引
├── messages/
│   └── YYYY-MM-DD/
│       └── <message_id>.json
└── backups/
    └── <instance_id>/
        └── <timestamp>.tar.gz
```

---

## 2. Reference 工作流规范

### 2.1 目录路径

`.claude/workflows/<workflow_id>@<version>/`

一个工作流对应一个目录，目录名即 `<workflow_id>@<version>`，内部固定包含两个文件，可选包含一个目录：
- `WORKFLOW.md` —— 面向人类和 Agent 理解
- `WORKFLOW.yaml` —— 面向脚本精确解析
- `references/` —— 可选：工作流级共享参考（目录规范、输出模板、数据字典等），供本工作流内多个 Skill 共同读取

### 2.2 职责分离

| 文件/目录 | 读者 | 内容 | 约束 |
|-----------|------|------|------|
| `WORKFLOW.md` | 人类开发者、AI Agent | 工作流名称、目标、概览、Mermaid 流程图、各 stage 的自然语言描述 | 机器**不依赖**此文件做决策 |
| `WORKFLOW.yaml` | 编排器脚本、校验工具 | 完整的机器规范（stages、edges、并发规则、冲突仲裁） | 是调度和校验的**唯一权威** |
| `references/` | SubAgent | 工作流级共享参考：目录规范、输出模板、数据字典、格式约定等 | 可选目录；本工作流内多个 Skill 按需读取 |

**一致性规则**：`WORKFLOW.md` 与 `WORKFLOW.yaml` 中同一字段（如 `workflow_id`、`version`、`stage_id`）出现冲突时，以 `WORKFLOW.yaml` 为准。

### 2.3 WORKFLOW.md 模板

完整模板见 `reference/templates/WORKFLOW.template.md`。

该模板包含：
- 工作流名称和概览（目标、并发上限、适用场景）
- Mermaid 流程图
- 每个 Stage 的自然语言说明（目的、输入、输出、对应 Skill、注意事项）

### 2.4 WORKFLOW.yaml 完整模板

完整模板见 `reference/templates/WORKFLOW.template.yaml`。

该模板包含：
- `schema_version`、`workflow_id`、`version`、`description`
- `stages`：stage_id、skill_id、mandatory、confirmation_point、retry_policy
- `edges`：from/to/condition/max_loop/loop_counter_stage
- `concurrency_rules`、`conflict_resolution`、`git_anchors`

### 2.5 字段约束

| 字段 | 类型 | 约束 |
|------|------|------|
| `workflow_id` | `string` | 与目录名 `<workflow_id>` 严格一致 |
| `version` | `string` | 语义化版本，与目录名 `@<version>` 严格一致 |
| `model_tiers` | `string[]` | 本工作流可用的抽象模型档位列表（如 `["light", "standard", "heavy"]`），平台无关 |
| `default_model_tier` | `string` | 默认模型档位；`stages[].model_tier` 省略时继承此值；必须存在于 `model_tiers` 中 |
| `stages[].stage_id` | `string` | 全局唯一，kebab-case |
| `stages[].skill_id` | `string` | 必须存在于 `.claude/skills/` |
| `stages[].model_tier` | `string` | 抽象模型档位（如 `light`, `standard`, `heavy`），必须存在于本工作流 `model_tiers` 列表中；省略时继承 `default_model_tier` |
| `stages[].mandatory` | `boolean` | `true` 时不可被用户跳过 |
| `stages[].confirmation_point` | `boolean` | `true` 时该 stage 结束后必须触发 AskUserQuestion |
| `stages[].retry_policy.max_attempts` | `integer` | ≥ 1，默认 1（不重试） |
| `stages[].retry_policy.on` | `string[]` | 触发重试的条件：`timeout`, `error` |
| `edges[].from` / `to` | `string` | 必须存在于 `stages[].stage_id` |
| `edges[].condition` | `string` | 枚举：`always`, `success`, `failure`, `confirmed`, `rejected`, `loop_exceeded` |
| `edges[].max_loop` | `integer` | `condition=failure/success` 回跳时必填，≥ 1 |
| `edges[].loop_counter_stage` | `string` | 记录循环次数的 stage，通常等于 `from` |
| `concurrency_rules.allowed_parallel_stages` | `string[][]` | 每组内的 stage 可并发执行 |
| `git_anchors.enabled` | `boolean` | 是否自动打 git tag 锚点 |
| `git_anchors.preserve_paths` | `string[]` | 回退时不随 git 回退的目录 |

### 2.6 Edge 条件语义

| 条件 | 触发场景 |
|------|---------|
| `always` | stage 完成后无条件流转 |
| `success` | SubAgent 上报 `status=DONE` |
| `failure` | SubAgent 上报 `status=ERROR` 或测试未通过等 |
| `confirmed` | `confirmation_point=true` 且用户选择"确认" |
| `rejected` | `confirmation_point=true` 且用户选择"拒绝/跳过" |
| `loop_exceeded` | 循环次数达到 `max_loop` |

---

## 3. Workflow 实例状态机规范

### 3.1 文件路径
`.agent/workflows/instances/<instance_id>.json`

### 3.2 字段定义

完整模板见 `reference/templates/instance.template.json`。

### 3.3 关键字段约束

| 字段 | 类型 | 约束 |
|------|------|------|
| `status` | `enum` | `PLANNING` / `EXECUTING` / `SUSPENDED` / `COMPLETED` / `FAILED` / `CANCELLED` |
| `reference.snapshot_hash` | `string` | 绑定 Reference 时 `WORKFLOW.yaml` 内容的 SHA256 哈希，防止漂移 |
| `stages[].status` | `enum` | `PENDING` / `RUNNING` / `BLOCKED` / `DONE` / `ERROR` / `SKIPPED` / `CANCELLED` / `SUPERSEDED` |
| `stages[].output_message_id` | `string` | 当前生效的 message，可为 null |
| `stages[].history_message_ids` | `string[]` | 该 stage 所有历史 message（含被回退的），按时间顺序 |
| `stages[].git_anchor_tag` | `string` | 该 stage 开始前的 git tag，回退锚点 |
| `stages[].loop_counter` | `integer` | 当前循环次数，从 0 开始 |
| `stages[].attempt_count` | `integer` | 当前 stage 重试次数，从 0 开始 |
| `stages[].blocked_by_confirm` | `boolean` | 是否因等待确认而阻塞 |
| `pending_confirmations` | `string[]` | 当前 `PENDING_CONFIRM` 的 message_id 列表 |
| `deviation_log` | `object[]` | 偏差记录，见 3.4 |

### 3.4 偏差记录（Deviation Log）

偏差记录条目格式详见 `reference/templates/instance.template.json` 中的 `deviation_log` 示例。

**type 枚举**：`USER_OVERRIDE` / `USER_ROLLBACK` / `SKILL_FAILURE` / `TIMEOUT` / `RESOURCE_CONFLICT` / `MANUAL_ADJUSTMENT` / `LOOP_EXCEEDED`

---

## 4. Registry 注册表规范

完整模板见 `reference/templates/registry.template.json`。

---

## 5. Git 锚点与回退机制

### 5.1 锚点创建

每个 stage 的 SubAgent **开始执行前**，编排器自动执行：

```bash
git tag -a wf-<instance_id>-<stage_id>-<message_id>-pre -m "Anchor before stage <stage_id> of <instance_id>"
```

### 5.2 回退操作

当需要回退到 stage `s_target` 时：

1. **保存当前 `.agent/` 状态**：
   ```bash
   cp -r .agent /tmp/.agent-backup-<instance_id>
   ```

2. **回退业务代码**（保留 `.agent/`）：
   ```bash
   git checkout wf-<instance_id>-<s_target>-<msg>-pre -- .
   ```

3. **恢复 `.agent/`**：
   ```bash
   rm -rf .agent && mv /tmp/.agent-backup-<instance_id> .agent
   ```

4. **重置 Instance 状态机**：
   - `s_target` 及之后所有 stage 的 `status` 重置为 `PENDING`；
   - `s_target` 的 `output_message_id` 置 null，`history_message_ids` 保留；
   - 依赖 `s_target` 的并发活跃 stage 标记为 `CANCELLED`；
   - `current_stage` 指向 `s_target`。

5. **生成新 message**：SubAgent 重新执行时创建全新 message，旧 message 保留在 `history_message_ids` 中。

### 5.3 用户驱动回退

编排器在每次 AskUserQuestion 的选项中**固定提供**：
- `{"label": "确认继续", ...}`
- `{"label": "回退到上一阶段重新执行", ...}`
- `{"label": "取消工作流", ...}`

此外，编排器持续监听用户自然语言。若识别出回退意图（如"回到上一步"、"重新做 s2"）：
1. 提取目标 stage（如未明确，默认为 `current_stage` 的前置 stage）；
2. 通过 AskUserQuestion 向用户确认："检测到回退意图，是否回退到 stage `<target>`？"；
3. 用户严格确认后，执行 5.2 的回退操作。

---

## 6. 状态流转

### 6.1 Stage 状态流转

```
PENDING → RUNNING → DONE
   ↓        ↓        ↓
BLOCKED  ERROR    SKIPPED
   ↑        ↓
   └──── CANCELLED
```

- **PENDING → RUNNING**：前置 `depends_on` 全部 `DONE`，无资源冲突，编排器调度。
- **RUNNING → BLOCKED**：SubAgent 上报 `PENDING_CONFIRM`。
- **BLOCKED → RUNNING**：用户确认，编排器恢复 SubAgent。
- **RUNNING → DONE**：SubAgent 上报 `DONE`。
- **任意 → CANCELLED**：回退时强制取消依赖该 stage 的并发任务。
- **任意 → SUPERSEDED**：回退后，原 `DONE` 的 stage 被新执行覆盖，旧记录标记为 `SUPERSEDED`。

### 6.2 Instance 状态流转

```
PLANNING → EXECUTING → COMPLETED
              ↓            ↓
          SUSPENDED      FAILED
              ↓
          CANCELLED
```

- **SUSPENDED**：存在未处理的 `PENDING_CONFIRM`、用户指令冲突等待仲裁、或回退确认等待中。

---

## 7. 循环机制

### 7.1 Stage 重试（Skill 内部失败）

在 stage 定义中配置，不体现在 `edges`：
```yaml
retry_policy:
  max_attempts: 3
  on: [timeout, error]
```

编排器在 SubAgent 上报 `ERROR` 且 `attempt_count < max_attempts` 时，将 stage 重置为 `PENDING`（`attempt_count += 1`），由下一轮调度重新启动新 SubAgent。

### 7.2 Workflow 循环（图跳转）

在 `edges` 中配置：
```yaml
- from: s3_test
  to: s2_refactor
  condition: failure
  max_loop: 3
  loop_counter_stage: s3_test
```

每次触发该 edge 时，`loop_counter_stage` 对应的 `loop_counter` +1。达到 `max_loop` 后，改走 `condition: loop_exceeded` 的 edge。

### 7.3 Skill 内部循环

不暴露给 Workflow 层，由 Skill 自行处理，对外只上报一次 `DONE` 或 `ERROR`。

---

## 8. `validate_instance.py` 规范

### 8.1 路径
`.claude/scripts/validate_instance.py`

### 8.2 调用接口
```bash
python .claude/scripts/validate_instance.py \
  --instance <instance_id> \
  [--strict]  # 严格模式：检查 git tag 存在性
```

### 8.3 校验项

| 类别 | 校验内容 |
|------|---------|
| **语法** | JSON 格式合法，字段类型匹配 |
| **引用完整性** | `reference.workflow_id@version` 目录存在于 `.claude/workflows/`，且目录内包含 `WORKFLOW.md` 和 `WORKFLOW.yaml` |
| **版本一致性** | `reference.snapshot_hash` 与当前 `.claude/workflows/<id>@<ver>/WORKFLOW.yaml` 内容哈希匹配 |
| **双文件一致性** | `WORKFLOW.md` 中的 `workflow_id`、`version` 若出现，与 `WORKFLOW.yaml` 一致（警告级别，非致命） |
| **Stage 合法性** | 所有 `stages[].stage_id` 存在于绑定的 Reference `WORKFLOW.yaml` 中 |
| **Message 存在性** | `output_message_id` 和 `history_message_ids` 指向的 message 文件存在于 `.agent/messages/` |
| **Git 锚点** | `status=DONE` 或 `RUNNING` 的 stage，`git_anchor_tag` 必须在 `git tag -l` 中存在 |
| **状态流转** | 不允许 `DONE` → `PENDING` 除非 `metadata.rolled_back_at` 存在 |
| **循环计数器** | `loop_counter` ≤ Reference 中对应 edge 的 `max_loop` |
| **并发一致性** | `active_agents` 数量与 `status=RUNNING` 的 stage 数量一致 |

### 8.4 返回
- **成功**：stdout `{"valid": true}`，退出码 `0`
- **失败**：stdout `{"valid": false, "errors": [...]}`，退出码 `1`

---

## 9. 编排器调度核心逻辑（伪代码）

```python
while True:
    # 1. 扫描 Registry 发现活跃实例
    for instance in registry.active_instances:
        # 2. 读取 Instance
        state = load_instance(instance.instance_id)
        
        # 3. 校验（安全网）
        if not validate_instance(state):
            mark_failed(instance)
            continue
        
        # 4. 处理确认
        for msg_id in state.pending_confirmations:
            msg = load_message(msg_id)
            if msg.status == "PENDING_CONFIRM":
                ask_user_question(msg)  # 映射为 AskUserQuestion 格式
                update_instance_status(state, msg_id, "AWAITING_USER")
        
        # 5. 处理用户意图（自然语言回退识别）
        if detect_rollback_intent(user_input):
            target = extract_target_stage(user_input, state)
            ask_user_question_rollback_confirm(target)
        
        # 6. 调度就绪 stage
        for stage in state.stages:
            if stage.status == "PENDING" and dependencies_satisfied(stage):
                if stage.git_anchor_tag is None:
                    create_git_anchor(state.instance_id, stage)
                agent_id = spawn_subagent(stage.skill_id, stage)
                update_instance_stage(state, stage.stage_id, "RUNNING", agent_id)
        
        # 7. 处理完成 message
        for stage in state.stages:
            if stage.status == "RUNNING":
                msg = load_message(stage.output_message_id)
                if msg.status == "DONE":
                    update_instance_stage(state, stage.stage_id, "DONE")
                    unlock_downstream(state)
                elif msg.status == "ERROR":
                    handle_error(state, stage, msg)
                elif msg.status == "PENDING_CONFIRM":
                    add_pending_confirmation(state, msg.message_id)
        
        # 8. 保存 Instance
        save_instance(state)
    
    sleep(POLL_INTERVAL)
```

---

## 10. 与 Message 规范的衔接

- Instance 的 `stages[].output_message_id` 必须指向 `.agent/messages/` 下存在的 message。
- `pending_confirmations` 由编排器根据 Message 池中 `status=PENDING_CONFIRM` 且 `workflow_instance_id` 匹配本实例的 message 动态维护。
- 回退时生成新 message，旧 message 保留，`history_message_ids` 追加。
- 编排器轮询时：Registry → Instance → Message，三层级联读取。

---

## 11. 双文件一致性约束（新增）

`WORKFLOW.md` 与 `WORKFLOW.yaml` 应保持一致，但机器以 `WORKFLOW.yaml` 为唯一权威。

| 检查项 | 级别 | 说明 |
|--------|------|------|
| `workflow_id` 一致 | 警告 | md 中的 id 若与 yaml 不符，提示但不阻塞 |
| `version` 一致 | 警告 | 同上 |
| `stage_id` 列表一致 | 警告 | md 中描述的 stages 应与 yaml 中的 `stages[].stage_id` 对应 |
| Mermaid 节点与 stage_id 对应 | 建议 | 流程图中的节点名建议与 `stage_id` 一致，便于人工核对 |

推荐做法：工作流作者先写 `WORKFLOW.yaml` 确定机器规范，再基于它撰写 `WORKFLOW.md`。
