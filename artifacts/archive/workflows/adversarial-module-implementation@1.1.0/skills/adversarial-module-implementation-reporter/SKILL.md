---
name: adversarial-module-implementation-reporter
description: >
  对抗性验证最终报告生成与验收技能。收集全流程证据文件，执行最终全量测试运行，
  运行契约一致性与隔离合规审计脚本，执行 16 项分组验收检查，
  生成 adversarial-report.md、TESTING.md 和 IMPLEMENTATION_NOTES.md，
  通过确认点让用户审查并选择「接受并结束」或「追加一轮修复」。
  触发场景：s05-blindtest 以 all-pass / max-rounds / stagnation / user-terminated / loop_exceeded 状态进入 s08-report 时自动执行。
  对应 Stage：s08-report（adversarial-module-implementation@1.1.0）。
---

# 模块生命周期汇报与验收

你是 `adversarial-module-implementation@1.1.0` 工作流中 `s08-report` Stage 的执行器。
核心职责：收集全流程产物 → 最终全量测试运行 → 运行审计脚本 → 执行分组验收检查 →
生成最终报告 + TESTING.md + IMPLEMENTATION_NOTES.md → 用户审查确认。

**上游**：s05-blindtest（success: all-pass / max-rounds / stagnation / user-terminated；或 loop_exceeded）

**确认点路由**（confirmation_point = true）：

**CD-001：报告验收确认**

展示内容：
- 最终运行状态（passed / failed / partial）及统计
- 16 项验收检查结果摘要（通过 / 未通过 / 不适用）
- 关键发现（漏洞总数、修复轮次、收敛状态）

| 选项 | 用户动作 | 目标 Stage | 说明 |
|:---|:---|:---|:---|
| A — ✅ 接受并结束 | confirmed | s99-workflow-end | 工作流正常终止 |
| B — 🔄 追加一轮修复 | rejected | s05-blindtest | 须附拒绝原因，返回对抗循环追加修复 |

---

## 输入

编排器注入 prompt 包含：`module_id`、`module_code_dir`、`upstream_files`（至少含 contract-expectations.md、function-signatures.json、所有轮次的 failure-summary-round-*.md / test-defects-round-*.md / pending-confirmations-round-*.md、green-seeking-report.json）。

`module_id` 缺失 → 立即终止，上报 `ERROR`，`report` 中说明缺失字段。

---

## 核心流程

### Step 0：最终全量测试运行

在生成报告前，运行一次当前实现代码与测试代码的完整测试套件，结果写入报告「最终运行状态」字段，并作为验收检查第 15 项依据。

**技术栈自适应**（按以下优先级探测）：

| 技术栈 | 探测标志 | 运行命令 |
|:---|:---|:---|
| Python | `pytest` 可用 或 `tests/` 目录含 `test_*.py` | `pytest tests/` 或 `pytest {module_code_dir}/tests/` |
| JavaScript/TypeScript | `package.json` 存在 | `npx vitest run`（优先）或 `npm test` |
| Go | `*.go` 文件存在 | `go test ./...` |

**结果格式**：

```json
{
  "passed": <int>,
  "failed": <int>,
  "total": <int>,
  "status": "passed|failed|partial"
}
```

- `status` 判定：`failed === 0` → `"passed"`；`passed === 0` → `"failed"`；否则 → `"partial"`。
- 若测试命令不可用、无测试文件或运行报错，`status` 记为 `"failed"`，`total=0`，在已知遗留中说明原因。

### Step 1：收集证据文件清单

扫描 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/`，列出所有证据文件及其存在状态。缺失文件在报告中标记 ❌ 或 ⚠️，并在已知遗留中说明。

### Step 2：契约一致性校验

```bash
python scripts/validate_contract_consistency.py \
  --contracts "{tmp}/contract-expectations.md" \
  --signatures "{tmp}/function-signatures.json" \
  --module-code-dir "{module_code_dir}"
```

记录退出码和输出摘要，作为验收检查第 6 项的依据。

### Step 3：隔离合规审计

```bash
python scripts/check_isolation.py \
  --target-dir "{tmp}" \
  --check-git
```

审计 ISO 四铁律事后验证。记录退出码和输出摘要，作为验收检查第 10 项的依据。

### Step 4：16 项验收检查

逐项勾选 ✅/❌/⏭️，按 6 组简述：

| 分组 | 项数 | 内容 |
|:---|:---|:---|
| 功能覆盖类 | 4 | 公开函数覆盖、失败原因正确性、末轮全部通过/已接受、无退化 |
| 契约与依赖类 | 3 | 实现符合落地规范、contract 一致性验证通过、无契约编译依赖 |
| 测试质量类 | 2 | 测试误报已修正、**最终全量测试运行状态**（新增，依据 Step 0） |
| 漏洞记录类 | 1 | 漏洞发现记录完整（映射到契约条款） |
| 角色合规类 | 3 | orchestrator 未修改测试代码、测试缺陷有修正记录、摘要未泄露测试代码 |
| 流程合规类 | 3 | 每轮修复有待确认文档、测试缺陷轮次有缺陷报告、**TESTING.md 与 IMPLEMENTATION_NOTES.md 已生成**（新增） |

> 若 `references/checklist-details.md` 存在，按其逐项说明执行；否则依上述分组语义自行判定。

### Step 5a：生成 adversarial-report.md

按 `references/report-template.md` 中定义的模板结构生成 `adversarial-report.md`，写入 `docs/testing-design/{module_id}/`。模板不可读时使用降级方案：按「流程执行证据索引 → 漏洞与修复 → 最终运行状态 → 验收检查清单结果 → 已知遗留 → 诚实声明」顺序生成简化报告。

报告必须包含以下字段：
- 流程执行证据索引
- 漏洞与修复统计（轮次、通过/失败数、修复数）
- **最终运行状态**（Step 0 结果 JSON）
- 16 项验收检查清单结果
- 已知遗留
- 诚实声明

### Step 5b：生成 TESTING.md

- **路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/TESTING.md`
- **内容来源**：从 pending-confirmations.md、各轮 pending-confirmations-round-*.md 及各轮测试运行记录中整理
- **必须包含**：
  - 运行命令说明（如 `pytest tests/`、`npx vitest run`、`go test ./...`）
  - 环境要求（语言运行时版本、依赖安装命令、环境变量）
  - 已知限制（未覆盖场景、环境依赖、需要外部服务等）
- 若 `references/testing-notes-template.md` 存在，按模板填充；否则按上述结构生成。

### Step 5c：生成 IMPLEMENTATION_NOTES.md

- **路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/IMPLEMENTATION_NOTES.md`
- **内容来源**：从 pending-confirmations.md 及各轮 pending-confirmations-round-*.md 中整理中低风险条目
- **必须包含**：
  - 保守假设摘要（实现中基于合理推断但未在契约中显式规定的假设）
  - 契约未覆盖盲区说明（边界条件、异常路径、未定义行为等）
  - 实现决策理由（为何选择当前实现方式而非其他方案）
- 若 `references/implementation-notes-template.md` 存在，按模板填充；否则按上述结构生成。

### Step 6：诚实声明

在报告末尾写入 7 项诚实声明表，每项 ✅/❌/⏭️，引用可验证证据文件：

| # | 声明 | 证据 |
|---|------|------|
| 1 | 实现代码严格按落地规范编写，未参考对抗性测试代码 | 实现源码 + 时间戳 |
| 2 | 对抗性测试严格按接口契约生成，未读取实现源码 | failure-summary 信息隔离检查 |
| 3 | 失败摘要仅含错误类型和契约条款，未暴露测试代码/具体输入值 | failure-summary 信息隔离检查 |
| 4 | 所有测试误报已修正并排除在修复流程之外 | test-defects + SubAgent 修正记录 |
| 5 | 信息隔离规则在全部迭代轮次中被遵守 | 以上 1-4 项全部通过 |
| 6 | orchestrator 未直接修改任何测试代码文件 | check_isolation.py 审计结果 |
| 7 | 所有测试缺陷通过 SubAgent 修正，非 orchestrator 直接处理 | SubAgent 调用记录 + test-defects |

无法通过的项在已知遗留中说明原因和影响。

### Step 7：输出与用户确认

1. 将 `adversarial-report.md` 写入 `docs/testing-design/{module_id}/`
2. 将 `TESTING.md` 写入 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/TESTING.md`
3. 将 `IMPLEMENTATION_NOTES.md` 写入 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/IMPLEMENTATION_NOTES.md`
4. 上报 `PENDING_CONFIRM`，`confirm_questions` 基于实际报告内容：
   - 最终运行状态（passed / failed / partial）
   - 16 项验收检查统计（如「16 项中 14 项通过，1 项未通过，1 项不适用」）
   - 关键发现（漏洞总数、修复轮次、最终收敛状态）
   - 未通过项及影响简述
   - **明确展示双选项**：
     - **选项 A**：✅ 接受并结束 → 选择 confirmed → 进入 s99-workflow-end
     - **选项 B**：🔄 追加一轮修复 → 选择 rejected（须附拒绝原因）→ 进入 s05-blindtest 追加修复
5. 调用 `write_message.py` 上报，等待编排器处理确认

---

## 异常处理

| 场景 | 行为 |
|------|------|
| contract-expectations.md / function-signatures.json 缺失 | 对应验收项 ❌，已知遗留说明前置阶段未完成 |
| validate_contract_consistency.py 运行失败 | 验收项 6 ❌，诚实声明中说明脚本错误 |
| check_isolation.py 运行失败 | 验收项 10 ❌，诚实声明中说明脚本错误 |
| references/report-template.md 不可读 | 使用 Step 5a 降级方案，已知遗留注明 |
| 所有 failure-summary 均缺失（全部通过场景） | 验收项 2/9/12/13 标记 ⏭️（不适用） |
| 最终全量测试运行失败或命令不可用 | 验收项 15 ❌，已知遗留说明原因 |
| TESTING.md / IMPLEMENTATION_NOTES.md 生成失败 | 验收项 16 ❌，已知遗留注明失败原因 |
| 无法完成报告（方案级降级） | `PENDING_CONFIRM`，说明原因，等待用户决策 |

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "adversarial-module-implementation-reporter",
  "version": "1.1.0",
  "stage_id": "s08-report",
  "confirmation_point": true,
  "stage_type": "script_call",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false
}
```
