# Agent 协议样板

> 本文件为工作流级共享规范。所有 `project-design-pipeline@2.0.0` 的 Skill 均需遵守。
> Skill 在 SKILL.md 前置要求中通过一行引用本文件，无需在自身重复描述。

---

## 1. 契约读取义务

作为工作流 Stage 执行器，收到任务后必须依次读取：

1. 通用契约（优先 Skill 专用 `references/contract-input.md` / `references/contract-output.md`，缺失则读取 `contracts/` 目录下的通用契约）
2. 编排器注入的 `workflow_refs` 中列出的文件（如有）
3. `references/agent-protocol.md`（本文件）
4. `references/directory-convention.md`（全局目录结构约定）

> **零侵入原则**：若 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底。无需因此上报 ERROR。

---

## 2. 输入接收与校验

从编排器注入的 context 中提取以下字段：

**必填字段**（缺失任意一项，立即终止并上报 `ERROR`）：
- `workflow_instance_id`：工作流实例 ID
- `agent_id`：当前 Agent 标识
- `skill_id`：当前 Skill 标识
- `stage_id`：当前执行的 Stage 标识

**可选字段**：
- `upstream_files`：上游产物路径列表
- `upstream_message_ids`：上游消息 ID 列表
- `workflow_ref_dir`：工作流参考文件目录
- `workflow_refs`：需要读取的工作流级参考文件列表
- `special_instructions`：编排器注入的特殊指令
- `stage_direction`：Stage 级工作方向指令（优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项 → 立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身声明不一致 → 立即终止，上报 `ERROR`。

---

## 3. 消息上报

完成后必须通过标准脚本上报。脚本路径由编排器注入（通常为 `<workflow_ref_dir>/scripts/write_message.py`）：

```bash
python <scripts_dir>/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

**禁止直接手写 JSON 到 `.agent/messages/` 目录。**

**message 草稿 JSON 结构**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | 是 | `PENDING_CONFIRM` / `DONE` / `ERROR` |
| `report` | string | 是 | 面向用户的阶段工作报告摘要（中文） |
| `confirm_questions` | string[] | status=PENDING_CONFIRM 时 | 1-4 条具体可回答的问题 |
| `artifacts` | string[] | 否 | 产出文件路径列表 |

**重试规则**：
- 若脚本返回非零退出码，根据 stderr 修正后重新调用。
- 连续失败 3 次 → 将 `status` 改为 `ERROR`，`report` 说明校验失败详情，终止执行。
- `message_id` 由脚本自动生成，无需手动提供。

**终止前**：最终回答必须包含脚本返回的 message 文件路径。

---

## 4. 降级熔断规则

| 降级类型 | 示例 | 规则 |
|----------|------|------|
| **方案级** | 算法变更、精度降低、功能裁剪 | **禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM` 等待用户确认。 |
| **资源级** | 分批计算、降采样、稀疏矩阵 | 可自主执行，但必须在 `report` 中说明具体措施和影响。 |

---

## 5. 确认点行为规范

当 Stage 的 `confirmation_point=true` 时（参考 WORKFLOW.yaml 对应 Stage 定义）：

1. 完成阶段任务后，**不要直接上报 `DONE`**。
2. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`。
3. 设置 `confirm_questions`（1-4 个，必须具体、可回答、覆盖用户可能的关注点）：
   - 每条问题应让用户能直接判断"是否满足要求"。
   - 若有多项待确认内容，一次性全部列出，不要分多次终止。
4. 调用 `write_message.py` 上报。
5. 终止执行，等待编排器处理用户确认。

用户确认后，编排器自动将 Stage 标记为 DONE，并按 WORKFLOW.yaml 的 `condition: confirmed` 流转到下游 Stage。
用户拒绝则在当前 Stage 循环修订（受 WORKFLOW.yaml 的 max_loop 约束）。
