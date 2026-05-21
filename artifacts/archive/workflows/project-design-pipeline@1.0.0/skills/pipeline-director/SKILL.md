---
name: pipeline-director
description: >
  项目设计流水线的控制中枢 Skill。负责循环控制、进度汇报、同步检查和流程引导。
  当 Workflow 需要确认是否继续下一循环、检查项目级同步状态、或汇报整体进度时，**必须优先使用本 Skill**。
  适用场景：(1) 模块循环入口/出口的确认；(2) 项目级设计同步检查；(3) 工作流整体进度汇报。
---

# Pipeline Director

项目设计流水线的轻量级控制中枢，不执行业务逻辑，仅负责流程控制和用户确认。

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/<skill_id>/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/<skill_id>/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`。
- `skill_id` 与自身 `skill_id` 不一致：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

### 4. 降级熔断

- **方案级降级**：禁止自主执行。上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**：可自主执行，但必须在 `report` 中说明。

## 工作流上下文

本 Skill 是工作流 `project-design-pipeline` 中的控制型 Stage 执行器。

**对应 Stage**：
- `s06b-project-sync-check`：项目级同步检查
- `s20-next-module-confirm`：下一模块确认

**上游 Stage**：
- `s06-dependency-analysis`（module-dependency-analyzer）→ 输出模块依赖关系分析
- `s19-spec-internal-design`（module-spec-writer）→ 输出模块规格文档

**下游 Stage**：
- `s07-select-module`（module-intent-writer）← 循环回跳

## 执行规范

### s06b-project-sync-check 模式

当 `stage_id` 为 `s06b-project-sync-check` 时：

1. 扫描 `docs/功能设计/_sync-issues.md`（如存在）
2. 汇报当前项目级设计状态：
   - 技术栈设计文档路径和最后修改时间
   - 功能模块全拆解文档状态
   - 待同步问题清单（如有）
3. 上报 `PENDING_CONFIRM`：
   - `confirm_questions`: ["项目级设计已就绪，是否进入模块设计循环？"]
   - 选项包含："进入模块设计 (Recommended)" / "先同步项目级设计"

### s20-next-module-confirm 模式

当 `stage_id` 为 `s20-next-module-confirm` 时：

1. 统计当前已完成的模块数量（扫描 `docs/功能设计/` 下的意图文档和规格文档）
2. 列出剩余未处理模块（基于功能模块全拆解表）
3. 汇报进度摘要：
   ```
   已完成 X / Y 个模块：
   - [列表]
   
   剩余模块：
   - [列表]
   ```
4. 上报 `PENDING_CONFIRM`：
   - `confirm_questions`: ["是否继续处理下一个模块？"]
   - 选项包含："继续处理下一个 (Recommended)" / "结束工作流"

## 禁止行为

- 禁止执行业务分析、文档生成等不属于流程控制的任务
- 禁止修改任何设计文档内容
- 禁止跳过用户确认直接上报 DONE

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误，根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

### 确认点上报（Confirmation Point）

本 Skill 对应 stage 的 `confirmation_point=true`。完成任务后：

1. **不要直接上报 `DONE`**
2. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`
3. 设置 `confirm_questions: ["你的确认问题"]`
4. 调用 `write_message.py` 上报
5. 终止执行，等待编排器处理用户确认

用户确认后，编排器会自动将本 stage 标记为 DONE，并走 `condition: confirmed` 的 edge 解锁下游。

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "pipeline-director",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["control"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
