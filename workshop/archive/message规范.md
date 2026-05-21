# **Message 文件完整规范 v1.1.0**

---

## 1. 概述

Message 文件是编排器（Orchestrator）与执行器（SubAgent）之间**唯一的官方通信协议**。所有执行器必须通过统一的 Python 写入脚本（`write_message.py`）落盘，禁止直接手写或绕过脚本写入。

- **格式**：JSON（`.json`）
- **编码**：UTF-8，无 BOM
- **命名**：`<message_id>.json`
- **存储**：`.agent/messages/YYYY-MM-DD/<message_id>.json`

---

## 2. 字段定义

| 字段 | 类型 | 必填 | 来源 | 约束与说明 |
|------|------|------|------|-----------|
| `schema_version` | `string` | 是 | 脚本注入 | 固定值 `"1.1.0"`。用于未来协议升级识别。 |
| `message_id` | `string` | 是 | **脚本生成** | 格式：`YYYYMMDD-序号-4位随机后缀`（如 `20260509-003-a7f3`）。父 Agent **不注入**，由脚本基于目录扫描自动分配序号。 |
| `workflow_instance_id` | `string` | 是 | **父 Agent 注入** | 编排器在启动 SubAgent 的 prompt 中显式传递。SubAgent **不得自行生成**。 |
| `agent_id` | `string` | 是 | **父 Agent 注入** | 同上。用于热恢复时定位平台级 SubAgent 实例。 |
| `skill_id` | `string` | 是 | **父 Agent 注入** | 同上。必须存在于 `.claude/skills/` 目录下。 |
| `status` | `string` | 是 | SubAgent 提供 | 封闭枚举，见第 3 节。 |
| `timestamp` | `string` | 是 | 脚本注入 | ISO 8601 格式，带时区偏移（如 `2026-05-09T11:00:00+08:00`）。 |
| `upstream_files` | `string[]` | 是 | SubAgent 提供 | 本次任务读取的上游文件路径。空数组为 `[]`，禁止 `null`。路径标准化，见第 4 节。 |
| `modified_files` | `string[]` | 是 | SubAgent 提供 | 本次任务**已覆盖**的真实文件路径（代码 worktree 修改或已确认的文档）。空数组为 `[]`。 |
| `draft_files` | `string[]` | 是 | SubAgent 提供 | 存放于 `.tmp/` 下的文档草稿路径。`status=DONE` 时若草稿已转正，应清空并移入 `modified_files`。空数组为 `[]`。 |
| `output_files` | `string[]` | 是 | SubAgent 提供 | 本次**新增**的文件路径（区别于 `modified_files` 的覆盖）。空数组为 `[]`。 |
| `report` | `string` | 是 | SubAgent 提供 | 非空字符串。单行或多行纯文本，禁止 Markdown 标题语法（`#`），避免编排器解析干扰。 |
| `confirm_required` | `boolean` | 是 | SubAgent 提供 | `true` 时，触发编排器阻塞并调用提问工具。 |
| `confirm_questions` | `string[]` | 条件必填 | SubAgent 提供 | `confirm_required=true` 时必须存在且长度 ≥ 1 且 ≤ 4（匹配 AskUserQuestion 工具上限），每个元素非空；`confirm_required=false` 时为 `[]`。 |
| `checkpoint_summary` | `string` | 是 | SubAgent 提供 | 非空。建议三段式："已完成：…；待处理：…；关键上下文：…"。用于冷启动重建。 |
| `tmp_dir` | `string` | 是 | 脚本生成 | 本 message 的过程产物目录。格式：`.tmp/<workflow_instance_id>/<message_id>/`，以 `/` 结尾。 |
| `metadata` | `object` | 否 | 混合 | 扩展字段。SubAgent 可写入 `parent_message_id`、`attempt_count`、`token_usage`；编排器可追加 `confirm_responses`、`confirmed_at`、`confirmed_by`、`edit_history`。 |

---

## 3. 状态枚举（Status）

| 值 | 含义 | 谁写入 | 流转条件 |
|----|------|--------|---------|
| `RUNNING` | 执行器已启动，正在处理中 | SubAgent（初始上报） | 冷启动后首次上报 |
| `PENDING_CONFIRM` | 执行器遇到确认点，已终止等待用户确认 | SubAgent | 执行器完成阶段分析，需人类决策 |
| `AWAITING_USER` | 编排器已代表执行器向用户发起提问，等待用户响应 | **编排器** | 调用 AskUserQuestion 后标记，防止重复提问 |
| `CONFIRMED` | 用户已确认，编排器已更新此状态，执行器可恢复继续 | **编排器** | 用户通过提问工具返回响应后 |
| `DONE` | 阶段任务完成，产物已沉淀 | SubAgent | 执行器完成最终产出并上报 |
| `ERROR` | 执行器遇到不可恢复错误，或脚本校验失败 | SubAgent 或 脚本 | 异常终止 |
| `CANCELLED` | 用户或编排器主动取消 | **编排器** | 用户拒绝确认或编排器主动中断 |

**状态流转**：
- `RUNNING` → `PENDING_CONFIRM` → `AWAITING_USER` → `CONFIRMED` → `RUNNING`（恢复）→ `DONE`
- `PENDING_CONFIRM` 只能由 SubAgent 写入；`AWAITING_USER`、`CONFIRMED`、`CANCELLED` 只能由编排器写入。

---

## 4. 路径规范

所有文件路径字段必须遵循：

1. **相对路径**：以项目根目录为基准，**不以 `/` 开头**。
2. **无冗余**：禁止 `./`、`../`、`//`。
3. **统一分隔符**：使用 `/`。
4. **目录结尾**：仅 `tmp_dir` 以 `/` 结尾，其余文件路径不以 `/` 结尾。

---

## 5. Python 写入脚本规范

### 5.1 脚本路径
`.agent/scripts/write_message.py`

### 5.2 调用接口

```bash
python .agent/scripts/write_message.py \
  --input <草稿_JSON路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

### 5.3 脚本行为

1. **读取草稿**：从 `--input` 读取 SubAgent 生成的 JSON。
2. **注入字段**：
   - `schema_version`: `"1.1.0"`
   - `message_id`: 按 `YYYYMMDD-序号-4位随机` 生成。序号基于 `.agent/messages/YYYY-MM-DD/` 目录扫描。
   - `timestamp`: 当前时间 ISO 8601 带时区。
   - `tmp_dir`: `.tmp/<workflow_instance_id>/<message_id>/`
3. **Schema 校验**（失败则拒绝落盘）：
   - 字段类型匹配；
   - `status` 在封闭枚举内；
   - `skill_id` 与 `--skill-id` 参数一致；
   - `workflow_instance_id` 与 `--workflow` 参数一致；
   - `agent_id` 与 `--agent-id` 参数一致；
   - `confirm_questions` 长度 ∈ [1, 4]（当 `confirm_required=true`）；
   - 所有路径符合第 4 节规范。
4. **原子写入**：
   - 先写入 `.agent/messages/YYYY-MM-DD/<message_id>.json.tmp`；
   - 校验通过后，`os.replace()` 重命名为最终路径。
5. **返回**：
   - **成功**：stdout 输出最终文件路径，退出码 `0`；
   - **失败**：stderr 输出结构化错误 JSON，退出码非 `0`。

### 5.4 错误输出格式（stderr）

错误输出格式详见 `reference/templates/message-error.template.json`。

---

## 6. 编排器提问规范（AskUserQuestion 映射）

当编排器轮询发现 `status=PENDING_CONFIRM` 的 message 时，必须将其转换为标准的 AskUserQuestion 工具调用。

### 6.1 映射规则

| Message 字段 | AskUserQuestion 字段 | 映射方式 |
|-------------|---------------------|---------|
| `confirm_questions[i]` | `questions[i].question` | 原文填入 |
| `workflow_instance_id` + `skill_id` + `agent_id` | `questions[i].header` | 统一格式：`[wf-001] Skill: refactor-module \| Agent: agent-refactor-01` |
| `report` + `checkpoint_summary` | `questions[i].options[*].preview` | 编排器提取关键信息生成预览卡片，供用户切换选项时参考 |
| 固定选项 | `questions[i].options` | 每个确认问题默认提供 3 个选项，见下表 |
| `false` | `questions[i].multiSelect` | 确认问题均为单选 |

**默认选项模板**：

| label | description |
|-------|-------------|
| 确认执行 | 同意执行此操作，SubAgent 将继续推进 |
| 拒绝/跳过 | 暂不执行此操作，SubAgent 将绕过该步骤 |
| 需要修改 | 提供补充指示，SubAgent 将根据新指令调整 |

若 `confirm_questions` 长度为 1 且问题本质为二元确认（是/否），选项可简化为：
- `{"label": "确认", "description": "是"}`
- `{"label": "拒绝", "description": "否"}`

### 6.2 提问调用示例

提问调用格式详见 `reference/templates/ask-user-question.template.json`。

### 6.3 编排器处理流程

1. **冻结实例**：在 Workflow 实例状态机中将对应 stage 标记为 `BLOCKED`。
2. **状态标记**：将 message 文件的 `status` 原子修改为 `AWAITING_USER`，追加 `metadata.awaiting_since` 时间戳。
3. **调用提问工具**：使用平台原生 AskUserQuestion 工具（Claude Code / Kimi Code 均支持）向用户展示映射后的 questions。
4. **等待响应**：编排器阻塞在该工作流实例，直到用户返回。
5. **结果回写**：用户响应后，编排器原子修改原 message 文件：
   - `status` → `CONFIRMED`
   - `metadata.confirm_responses` → 用户选择的选项索引或布尔数组
   - `metadata.confirmed_at` → ISO 8601 时间戳
   - `metadata.confirmed_by` → `"user"`
   - `metadata.edit_history` → 追加修改记录（含时间、修改字段、原因）
6. **恢复执行器**：通过平台恢复机制唤醒对应 `agent_id` 的 SubAgent，将确认结果传入 prompt。

---

## 7. 恢复机制规范

### 7.1 Kimi Code

- **方式**：通过 `Agent` 工具的 `resume` 参数，传入已存在的 `agent_id`。
- **上下文**：SubAgent 实例的完整 Soul 上下文自动恢复，包括历史工具调用和推理链。
- **输入**：编排器在恢复 prompt 中提供 `message_id`、用户确认结果、`checkpoint_summary`。

### 7.2 Claude Code

- **方式**：通过 `SendMessage` 工具，向指定 `agent_id` 的 SubAgent 发送消息。
- **上下文**：SubAgent 的完整 transcript 恢复，保留全部历史。
- **输入**：同上，编排器在 message 中携带确认结果和恢复指令。

### 7.3 降级方案

若平台恢复机制不可用（如 Claude Code 未开启 Agent Teams），编排器：
1. 创建**新 SubAgent**（新 `agent_id`）；
2. 在启动 prompt 中注入：原 `message_id`、`checkpoint_summary`、`metadata.confirm_responses`、上游文件路径；
3. 新 SubAgent 基于文件系统状态和摘要重建上下文，继续履约。

---

## 8. 编排器修改规则（Message 文件可变性）

Message 文件遵循**有限可变性**原则：

- **SubAgent**：仅创建 message，禁止修改已存在的 message 文件。
- **脚本**：仅执行原子写入，禁止覆盖已存在的 `<message_id>.json`。
- **编排器**：唯一被允许修改 message 的场景是处理 `PENDING_CONFIRM` → `CONFIRMED` 的推进。允许修改的字段白名单：
  - `status`（`PENDING_CONFIRM` → `AWAITING_USER` → `CONFIRMED` 或 `CANCELLED`）
  - `metadata.confirm_responses`
  - `metadata.confirmed_at`
  - `metadata.confirmed_by`
  - `metadata.awaiting_since`
  - `metadata.edit_history`
- **修改方式**：必须使用原子重写（先写 `.tmp` 再 `os.replace`），禁止原地编辑。
- **审计要求**：每次修改必须在 `metadata.edit_history` 中追加记录：

审计记录格式详见 `reference/templates/message-edit-history.template.json`。

---

## 9. SubAgent 侧契约（Skill Prompt 规范段落）

每个 Skill 的系统提示词中必须包含以下段落：

> **Message 上报契约**
> 
> 1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
> 2. 当你完成阶段任务或需要用户确认时：
>    - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
>    - 调用 `python .agent/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
>    - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
>    - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
> 3. `message_id` 由脚本自动生成，你无需提供。
> 4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
> 5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

---

## 10. 完整示例

### 10.1 SubAgent 生成的草稿（input）

草稿模板详见 `reference/templates/message-draft.template.json`。

### 10.2 脚本注入后的最终文件

完整模板详见 `reference/templates/message.template.json`。

### 10.3 编排器确认后的字段变化

编排器将消息池文件的 `status` 改为 `CONFIRMED`，并在 `metadata` 中追加 `confirm_responses`、`confirmed_at`、`confirmed_by` 和 `edit_history`。同一文件格式见 `reference/templates/message.template.json`。

---

## 11. 版本升级策略

未来若需调整字段或协议：
1. 升级 `schema_version`（如 `"1.2.0"`）；
2. 脚本必须向后兼容读取旧版本 message；
3. 新写入的 message 必须使用新版本 schema；
4. 编排器轮询时根据 `schema_version` 选择解析策略。