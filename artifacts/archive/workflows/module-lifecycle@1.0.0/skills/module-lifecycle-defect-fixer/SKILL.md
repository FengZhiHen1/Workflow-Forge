---
name: module-lifecycle-defect-fixer
description: >
  根据用户描述的缺陷/问题进行根因分析、提出修复方案，在用户确认后执行代码修复，
  并同步修正存在缺陷的设计文档。覆盖实现 bug 和设计缺陷两类问题。
  触发场景：(1) 用户报告 bug、错误、异常行为，附带了错误信息/traceback/日志；
  (2) 用户引用审查报告中的具体问题条目；
  (3) 用户口头描述某个功能"不对"、"有问题"、"返回值错误"等；
  (4) 用户要求修复测试失败、linter 报错、运行时崩溃等问题；
  (5) 用户提及"修 bug"、"修一下"、"缺陷"、"fix this"、"修复"等关键词。
  核心工作方式：先诊断根因、后提案确认、再动手修复，是模块生命周期中缺陷修复环节的唯一执行器。
  必须优先使用本 skill 当用户要求修复缺陷、修 bug、或排查代码问题时。
---

# 模块生命周期缺陷修复器

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-defect-fixer/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-defect-fixer/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）

> **零侵入原则**：若本 Skill 无专用契约，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与 `module-lifecycle-defect-fixer` 不一致：立即终止，上报 `ERROR`。
- `stage_id` 不在 `["fix-diagnose", "fix-proposal", "fix-execute", "fix-report"]` 中：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（修复策略变更、工具替换、设计文档大改）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（减少测试覆盖、简化自检步骤）：可自主执行，但必须在 `report` 中说明具体措施和影响。

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中 Group 3（缺陷修复）的执行器，覆盖四个 Stage：

| Stage | 名称 | 类型 | 确认点 |
|-------|------|------|--------|
| `fix-diagnose` | 缺陷诊断与根因分析 | analysis | 条件确认 |
| `fix-proposal` | 修复方案确认 | communication | 强制确认（内部循环最多 5 次） |
| `fix-execute` | 执行修复 | generation | 无 |
| `fix-report` | 修复结果汇报 | communication | 无 |

**Stage 流转**：`fix-diagnose` → `fix-proposal` → `fix-execute` → `fix-report`

**上游**：由编排器根据工作流上下文注入缺陷输入（用户报告、审查条目、traceback 等）。

**下游**：`fix-report` 完成后，编排器将本 Group 标记为完成，流程进入下一阶段。

---

## Stage: fix-diagnose（缺陷诊断与根因分析）

**确认规则**：条件确认。仅当无法定位到具体代码或文档时上报 `PENDING_CONFIRM`（AQ-007），要求用户补充信息。若成功完成根因分析，直接上报 `DONE`。

### 1. 解析缺陷输入

根据输入类型切入：

- **报错信息 / traceback**：从 traceback 中提取文件路径、行号、异常类型，直接读取出错文件的相关代码，分析调用链理解触发条件。
- **审查报告条目**（来自 module-implementation-review）：解析条目中的文件路径、行号、问题描述，直接定位到对应代码位置，将审查报告中的问题描述作为已知结论使用。
- **口头描述**（"XX 功能返回值不对"）：从描述中提取关键实体（函数名、模块名、数据结构），grep 搜索相关代码，阅读核心逻辑，对比预期行为与当前实现。

### 2. 定位设计文档

按以下优先级查找：
1. 优先查找项目中的 `功能模块全拆解.md`（由 module-breakdown-designer 生成），根据缺陷涉及的功能自动匹配最相关的模块编号
2. 根据模块编号找到对应的功能设计文档（在 `docs/功能设计/`、`docs/specs/` 等目录下）
3. 若项目无拆解文档，直接搜索 `docs/` 下文件名或内容与缺陷关键词相关的设计文档
4. 读取设计文档中与该缺陷相关的章节（核心逻辑步骤、异常处理、边界条件等）

### 3. 无法定位时的处理

若仅凭用户描述无法定位到具体代码或文档（搜不到相关文件、traceback 不完整、口头描述过于模糊），上报 `PENDING_CONFIRM`：

```json
{
  "status": "PENDING_CONFIRM",
  "confirm_questions": [
    "请提供更多信息以定位缺陷：文件路径、复现步骤、或更详细的错误日志"
  ],
  "report": "无法仅凭当前描述定位到具体代码或设计文档，需要补充信息。"
}
```

等待编排器恢复后，根据用户补充的信息继续诊断。

### 4. 根因分析

定位到相关代码后，对照设计文档（如有）判断缺陷性质：

| 缺陷性质 | 定义 | 设计文档处理 |
|:---|:---|:---|
| **实现 bug** | 代码行为与设计文档不一致（逻辑错误、边界遗漏、类型误用等） | 设计文档正确，不需要改 |
| **设计缺陷** | 设计文档本身的逻辑/约束/接口定义有误，代码按错误设计实现 | 设计文档需要同步修正 |

**分析原则**：
- 用 3-5 句话呈现根因：问题出在哪、为什么会发生、影响范围
- 不输出长篇排查日志，只给结论和关键证据（文件路径、行号、关键变量值）
- 若根因无法确定，诚实说明不确定性，标注为"待确认"并在 report 中列出需用户补充的信息

### 5. 上报

成功完成根因分析后，上报 `DONE`：

```json
{
  "status": "DONE",
  "report": "## 根因分析结果\n\n**缺陷性质**：{实现bug/设计缺陷}\n**根因**：{2-3句话}\n**关键证据**：{文件路径:行号, 变量/条件}\n**影响范围**：{一句话}"
}
```

---

## Stage: fix-proposal（修复方案确认）

**确认规则**：强制确认。完成任务后必须上报 `PENDING_CONFIRM`，不可直接 `DONE`。Workflow 通过 rejected edge 处理方案被驳回时的内部循环（最多 5 次）。当用户表述模糊时，再次上报 `PENDING_CONFIRM` 要求澄清（AQ-009）。

### 1. 读取上游产物

从 `upstream_files` 或上一阶段输出中读取 fix-diagnose 的根因分析结果（缺陷性质、根因描述、关键证据）。若上游产物不可用，上报 `ERROR` 说明缺失内容。

### 2. 制定修复方案

根据根因分析结果提出修复方案，使用以下格式：

```
## 根因
{2-3 句话说明根本原因}

## 修复方案
{具体的修改计划，包括：}
- 修改文件：{路径}
- 修改内容：{简述改动内容}
- {若涉及设计文档} 设计文档同步：{文档路径 + 修改内容}

## 影响范围
{一句话说明是否影响其他模块，若有破坏性变更明确标注"⚠️ 破坏性变更"}

## 测试建议
{若适合补充回归测试} 建议补充测试：{测试场景描述}
{若不合适} 不适合自动化测试，原因：{简述}
```

**方案设计原则**：
- 区分缺陷性质：实现 bug 不改设计文档，设计缺陷才改
- 精准修复：只修改与缺陷直接相关的代码，不趁机重构无关代码
- 跨多模块时：逐一分析每个受影响模块，在方案中合并呈现
- 破坏性变更：在方案中明确标注"⚠️ 破坏性变更"，说明影响面和迁移路径

### 3. 上报确认

将完整方案填入 `report`，上报 `PENDING_CONFIRM`：

```json
{
  "status": "PENDING_CONFIRM",
  "confirm_questions": [
    "以上修复方案是否可行？请确认、否定、或提出修正意见。"
  ],
  "report": "{完整的修复方案（根因 + 修复方案 + 影响范围 + 测试建议）}"
}
```

### 4. 处理用户反馈（内部循环由 Workflow 驱动）

编排器恢复后，根据 `metadata.confirm_responses` 处理：

- **用户明确同意**（"可以"、"OK"、"就这样修"、"同意"）：在 report 中记录确认结果，上报 `DONE`，方案进入 fix-execute。
- **用户不同意**：根据用户反馈调整方案，重新呈现，再次上报 `PENDING_CONFIRM`（Workflow 的 rejected edge 计数 +1，最多 5 次）。
- **用户提出修正意见**：修改方案，重新呈现，确认是否满足预期。
- **用户表述模糊**（"应该可以吧"、"你看着办"）：上报 `PENDING_CONFIRM`，`confirm_questions` 设置为 `["请明确确认是否按此方案修复？"]`（AQ-009）。

即使用户选择的方案不是最优解，也按用户决定执行（但可在 report 中温和提醒风险）。

---

## Stage: fix-execute（执行修复）

**确认规则**：无需确认。直接执行修复，完成后上报 `DONE`。

### 1. 读取上游产物

从 `upstream_files` 或上一阶段输出中读取 fix-proposal 的已确认修复方案。若上游产物不可用，上报 `ERROR` 说明缺失内容。

### 2. 执行修复（严格按顺序）

1. **修复代码**：按确认的方案修改代码文件。
2. **同步设计文档**（仅设计缺陷）：如果根因是设计文档错误，同步修正设计文档中的相关章节（逻辑步骤、异常策略、边界条件、验收测试等）。若修正涉及多个文档，逐个修改。
3. **补充回归测试**（如适用）：如果 bug 适合用自动化测试覆盖（纯逻辑错误、边界遗漏、异常处理缺失等），补充针对该场景的测试用例。不适合自动化测试的场景包括：环境相关 bug、UI 渲染问题、竞态条件等难以自动化验证的情况。

### 3. 自检

修复后逐项检查：
- 修复是否完整覆盖了根因
- 是否引入了新的问题（如未使用的 import、破坏的接口）
- 若涉及设计文档修改，代码与文档是否一致

### 4. 边界情况

- **设计文档不存在**：仍进行代码修复，在 report 中注明"未找到相关设计文档，未进行文档同步"
- **修复引入破坏性变更**：已在 fix-proposal 阶段标注，此处按方案执行，在 report 中再次提醒

### 5. 上报

```json
{
  "status": "DONE",
  "report": "## 修复执行完成\n\n**修改文件**：{文件列表 + 关键改动摘要}\n**设计文档同步**：{文件列表，如无则写"无"}\n**测试补充**：{测试文件，如无则写"不适合补充"}\n**自检结果**：{通过/有注意事项}"
}
```

---

## Stage: fix-report（修复结果汇报）

**确认规则**：无需确认。生成汇总报告后上报 `DONE`，结束本 Skill 的全部工作。

### 1. 汇总产出

汇总 fix-diagnose、fix-proposal、fix-execute 三个阶段的关键信息：根因、方案、实际修改内容。

### 2. 生成报告

使用以下模板：

```
## 缺陷修复完成

### 根因
{一句话}

### 修改内容
- 代码：{文件列表 + 关键改动}
- 设计文档：{文件列表 + 关键改动，如无则写"无"}
- 测试：{补充的测试，如无则写"不适合补充"}

### 建议
{如有后续注意事项，一句话说明；无则省略}
```

### 3. 上报

```json
{
  "status": "DONE",
  "report": "{完整的修复汇报（根因 + 修改内容 + 建议）}"
}
```

---

## 关键原则

- **先诊断、后动手**：不要在没搞清楚根因的情况下直接修改代码。fix-diagnose 和 fix-proposal 是必要的前置。
- **区分缺陷性质**：实现 bug 不改设计文档，设计缺陷才改。不确定时标注为"待确认"。
- **精准修复**：只修改与缺陷直接相关的代码，不趁机重构无关代码。
- **尊重用户决策**：用户是最终的决策者，方案必须经用户确认才能执行。
- **证据驱动**：根因分析给出具体证据（行号、变量值、调用链），不说"可能是 XX 的问题"。

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-defect-fixer",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
