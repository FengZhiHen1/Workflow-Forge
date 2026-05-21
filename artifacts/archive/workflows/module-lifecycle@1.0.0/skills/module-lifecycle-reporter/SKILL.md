---
name: module-lifecycle-reporter
description: >
  对抗性验证完成汇报与验收技能。生成最终对抗性验证报告，执行 14 项验收检查清单，
  运行角色隔离合规审计和契约一致性验证脚本，产出包含诚实声明的最终报告。
  使用场景：对抗循环终止后（全部通过、达到最大轮次或收敛停滞），
  工作流自动从 orch-blindtest 流转进入本阶段，无需用户手动触发。
  核心工作方式：收集全流程证据文件，运行校验脚本，逐项勾选验收清单，
  按报告模板生成 adversarial-report.md 并输出到 docs/testing-design/{module_id}/。
  必须优先使用本 skill 当编排器判定对抗循环收敛且需要生成最终验收报告时。
---

# 模块生命周期汇报与验收 Skill

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-reporter/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-reporter/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `module_id`：目标模块标识符
- `module_code_dir`：模块代码目录路径
- `upstream_files`：上游 Stage 产物路径列表，至少包含：
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/function-signatures.json`
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-*.md`（全轮次）
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/test-defects-round-*.md`（如适用）
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/pending-confirmations-round-*.md`（全轮次）
  - `{module_code_dir}/.tmp/adversarial-tests/{module_id}/green-seeking-report.json`
- `workflow_ref_dir`, `workflow_refs`（可选）：工作流级共享参考目录和文件列表
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id` 不一致：立即终止，上报 `ERROR`。
- `module_id` 缺失：立即终止，上报 `ERROR`，说明无法定位报告输出路径。

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

- **方案级降级**（验收清单项无法执行、关键证据文件缺失导致无法完成报告）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（部分轮次文件缺失但仍可生成不完整报告）：可自主执行，但必须在报告的已知遗留章节中详细说明缺失内容和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中的 Stage `orch-report` 的执行器。

**上游 Stage**：`orch-blindtest`（来自 Skill `module-lifecycle-blindtest`）
- 流转条件：`all-pass`（全部通过）、`max-rounds`（达到最大轮次上限 3 轮）、`stagnation`（收敛停滞）
- 本 Skill 启动时，`upstream_files` 将包含盲测阶段产出的所有证据文件

**下游 Stage（可选）**：
- `testw-prep`（进入 Skill `module-lifecycle-test-writer`）：对抗循环完成后，用户可选择触发正式验收测试编写
- `review-identify`（进入 Skill `module-lifecycle-reviewer`）：对抗循环完成后，用户可选择触发模块审查

本 Skill 的产物 `adversarial-report.md` 将作为下游 Skill 的输入参考。

---

## 核心执行逻辑

### 步骤 1：收集证据文件清单

扫描 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/` 目录，列出所有存在的证据文件。预期文件集合：

| 阶段 | 预期文件 | 必要性 |
|:---|:---|:---|
| Phase 1.1 契约提取 | `contract-expectations.md` | 强制 |
| Phase 2 实现 | `function-signatures.json` | 强制 |
| Phase 2 待确认 | `pending-confirmations.md` | 条件（如实现阶段有待确认事项） |
| Phase 3 测试生成 | `test_{module_id}.adversarial.*` | 强制 |
| Phase 3 测试清单 | `{module_id}.adversarial.test.list.md` | 强制 |
| Phase 3 自检 | `green-seeking-report.json` | 强制 |
| Phase 4 失败摘要 | `failure-summary-round-{N}.md` | 条件（盲测有失败时） |
| Phase 4 测试缺陷 | `test-defects-round-{N}.md` | 条件（判定为测试缺陷的轮次） |
| Phase 4.5 修正记录 | SubAgent 调用记录 | 条件（有测试缺陷修正时） |
| Phase 5 修复说明 | `pending-confirmations-round-{N}.md` | 条件（有修复轮次时） |

缺失文件在报告中标记为 ❌ 或 ⚠️，并在已知遗留章节说明原因。

### 步骤 2：运行契约一致性校验脚本

```bash
python scripts/validate_contract_consistency.py \
  --contracts "{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md" \
  --signatures "{module_code_dir}/.tmp/adversarial-tests/{module_id}/function-signatures.json" \
  --module-code-dir "{module_code_dir}"
```

此脚本验证实现代码中对外暴露的接口类型与契约文件定义的接口类型是否一致。记录退出码和输出摘要。

### 步骤 3：运行角色隔离合规审计脚本

```bash
python scripts/check_isolation.py \
  --target-dir "{module_code_dir}/.tmp/adversarial-tests/{module_id}" \
  --check-git
```

此脚本审计 orchestrator 是否违规直接修改了测试代码文件。记录退出码和输出摘要。

### 步骤 4：执行 14 项验收检查清单

逐项检查以下 14 个项目，每个项目输出 ✅（通过）、❌（未通过）或 ⏭️（不适用）：

#### 功能覆盖类（4 项）

1. **公开函数覆盖**：每个公开函数都有对抗性测试覆盖。验证方式：比对 `function-signatures.json` 中的函数列表与 `*.adversarial.test.list.md` 中的测试目标列表。
2. **失败原因正确性**：每轮失败用例经过"失败原因正确性"验证。验证方式：检查 `failure-summary-round-*.md` 中每个 case 的失败原因描述是否与修复动作匹配。
3. **最后一轮全部通过或用户已接受未修复项**：验证方式：检查最后一轮盲测结果，若未全部通过，检查 `pending-confirmations-round-*.md` 中是否有用户接受记录。
4. **无退化发生**：验证方式：逐轮对比盲测结果，确认没有"上轮通过、本轮失败"的退化用例（退化发生时工作流应从 orch-blindtest 走 regression 边回 orch-contract，若本阶段被触发则说明退化已处理或用户接受）。

#### 契约与依赖类（3 项）

5. **实现代码符合落地规范**：验证方式：对照落地规范和项目结构文档检查实现代码的接口契约、异常定义、类型签名。
6. **外部接口类型通过 validate_contract_consistency.py 验证**：验证方式：步骤 2 的脚本运行结果，退出码为 0 且无未解决的差异报告。
7. **实现代码未对契约文件产生编译依赖**：验证方式：检查实现代码的 import/include 语句，确认不包含对 `contract-expectations.md`、`function-signatures.json` 或 `.tmp/adversarial-tests/` 下任何文件的引用。

#### 测试质量类（1 项）

8. **所有测试误报已修正**：验证方式：检查所有 `test-defects-round-*.md` 中记录的缺陷是否都有对应的 SubAgent 修正记录，且修正后测试通过。

#### 漏洞记录类（1 项）

9. **漏洞发现记录完整**：每个发现的漏洞都有对应记录，且映射到具体契约条款编号。验证方式：检查 `failure-summary-round-*.md` 中每个 case 是否包含契约条款引用。

#### 角色合规类（3 项）

10. **orchestrator 未直接修改测试代码文件**：验证方式：步骤 3 的 `check_isolation.py --check-git` 审计结果。
11. **所有测试缺陷有对应文档和修正记录**：验证方式：每个判定为测试缺陷的轮次，存在 `test-defects-round-{N}.md` 且包含 SubAgent 修正记录。
12. **失败摘要未泄露测试代码**：验证方式：抽查 `failure-summary-round-*.md`，确认不含测试代码片段、具体输入值、测试文件路径。

#### 流程合规类（2 项）

13. **每轮修复有对应的待确认文档**：验证方式：每个修复轮次（Phase 5）存在 `pending-confirmations-round-{N}.md`。
14. **判定为测试缺陷的轮次存在缺陷报告**：验证方式：`test-defects-round-{N}.md` 文件存在且内容非空。

### 步骤 5：生成最终报告

按照以下模板结构生成 `adversarial-report.md`，写入 `docs/testing-design/{module_id}/`。

**完整模板结构（在 `reference/skills/module-implementation-orchestrator/references/report-template.md` 中定义）**：

```markdown
# 功能模块落地完成：{module_name}（对抗性验证模式）

## 涉及技术栈
{技术栈描述}

## 代码组织依据
{项目结构文档引用}

## 修改文件范围
- 新增：{新增文件列表}
- 修改：{修改文件列表}
- 未改动（可复用）：{未改动列表}

## 对抗性验证记录

| 轮次 | 总用例 | 通过 | 跳过 | 失败 | 收敛状态 |
|:---|:---|:---|:---|:---|:---|
| {N} | ... | ... | ... | ... | ... |

## 流程执行证据索引

| 阶段 | 证据文件 | 状态 | 说明 |
|:---|:---|:---|:---|
| ... | ... | ✅/❌/⏭️ | ... |

证据文件缺失的说明：
- ...

## 发现的漏洞与修复

### 实现漏洞（经 Phase 5 SubAgent 修复）
1. **[漏洞类型]** 函数 `xxx` ...
   - 修复：...
   - 涉及契约：§N.M
   - 修复轮次：Round N
   - 待确认事项：...

### 测试缺陷（经 Phase 3 SubAgent 修正）
1. **[缺陷类型]** ...
   - 修正：...
   - 修正轮次：Round N
   - 测试缺陷报告：`test-defects-round-N.md`
   - SubAgent 修正记录：{存在/缺失}

## 验收检查清单结果

| # | 检查项 | 结果 | 说明 |
|:---|:---|:---|:---|
| 1 | 每个公开函数都有对抗性测试覆盖 | ✅/❌ | ... |
| 2 | 每轮失败用例经过失败原因正确性验证 | ✅/❌ | ... |
| ... | ... | ... | ... |
| 14 | 判定为测试缺陷的轮次存在缺陷报告 | ✅/❌ | ... |

**未勾选项说明**：{逐项解释未通过的原因和影响}

## 模块作用简述
{1-2 句话描述模块功能}

## 已知遗留
- {未修复项及其原因}
- {跳过项及其原因}
- {证据文件缺失的影响评估}

## 对抗性测试位置
`.tmp/adversarial-tests/{module_id}/`
（可运行对应测试框架复现）

## 建议后续操作
- 调用 module-lifecycle-test-writer 生成正式验收测试
- 将发现的漏洞模式纳入后续模块的落地规范
```

### 步骤 6：诚实声明

在报告末尾写入诚实声明表。声明内容必须引用可验证的证据文件路径。

```markdown
## 诚实声明

| # | 声明 | 验证方式 | 证据文件 | 结果 |
|:---|:---|:---|:---|:---|
| 1 | 实现代码严格按落地规范和项目结构设计文档编写，未参考任何对抗性测试代码。 | 代码审查 + 实现代码时间戳早于测试代码 | 实现源码文件 | ✅/❌ |
| 2 | 对抗性测试严格按接口契约生成，未读取实现源码。 | validate_failure_summary.py 信息隔离检查 | failure-summary-round-*.md | ✅/❌ |
| 3 | 失败摘要仅包含错误类型和契约条款，未向实现者暴露测试代码或具体输入值。 | validate_failure_summary.py 信息隔离检查 | failure-summary-round-*.md | ✅/❌ |
| 4 | 所有测试误报已修正并排除在修复流程之外。 | test-defects-round-*.md 存在 + SubAgent 修正记录 | test-defects-round-*.md | ✅/❌ |
| 5 | 信息隔离规则在全部迭代轮次中被遵守。 | 以上 1-4 项全部通过 | 以上全部证据 | ✅/❌ |
| 6 | orchestrator 未在 Phase 4 直接修改任何测试代码文件。 | test-defects-round-*.md 存在（判定为测试缺陷时必须有） | test-defects-round-*.md | ✅/❌ |
| 7 | 所有测试缺陷均通过 Phase 3 SubAgent 修正，非 orchestrator 直接处理。 | SubAgent 调用记录 + test-defects-round-*.md 存在 | test-defects-round-*.md | ✅/❌ |

**无法勾选？** 说明流程未完整执行。未勾选项须在"已知遗留"中说明原因和影响。
```

诚实声明中的每一项都必须有对应的可验证证据支撑。如果某项无法通过（标记 ❌），说明原因并在已知遗留中给出影响评估。

### 步骤 7：输出与提交

1. 将完整的 `adversarial-report.md` 写入 `docs/testing-design/{module_id}/adversarial-report.md`。
2. 确保输出目录存在，若不存在则创建。
3. 生成 message 草稿 JSON，设置 `status: "DONE"`，`report` 中包含：
   - 报告路径
   - 验收清单通过/未通过统计（如：12/14 通过，2 项未通过并在诚实声明中标注）
   - 关键发现摘要（发现的漏洞总数、修复轮次、最终收敛状态）
4. 调用 `write_message.py` 上报 DONE。

---

## 异常处理

### 关键证据文件缺失

如果 `contract-expectations.md` 或 `function-signatures.json` 缺失：
- 验收清单第 1、6、7 项自动标记为 ❌
- 在报告的"已知遗留"中说明：流程前置阶段未完成，无法生成完整报告

如果所有 `failure-summary-round-*.md` 均缺失（全部通过的场景）：
- 验收清单第 2、9、12、13 项标记为 ⏭️（不适用，因无失败用例）
- 验收清单第 5 项仍按正常方式检查

### 脚本运行失败

如果 `validate_contract_consistency.py` 或 `check_isolation.py` 运行出错（非零退出码且非"无违规"语义）：
- 对应验收清单项标记为 ❌
- 在诚实声明中说明脚本错误原因
- 如果脚本不可执行或路径错误，上报 `PENDING_CONFIRM`（方案级降级，需用户决策）

### 报告模板缺失

如果 `reference/skills/module-implementation-orchestrator/references/report-template.md` 不可读：
- 使用本 SKILL.md 步骤 5 中内嵌的模板结构生成报告
- 在报告的"已知遗留"中注明使用了降级模板

---

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

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-reporter",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["planning", "core", "extension"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
