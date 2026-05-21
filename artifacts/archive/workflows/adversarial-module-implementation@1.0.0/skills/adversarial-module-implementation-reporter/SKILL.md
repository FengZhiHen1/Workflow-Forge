---
name: adversarial-module-implementation-reporter
description: >
  对抗性验证最终报告生成与验收技能。收集全流程证据文件，运行契约一致性与隔离合规审计脚本，
  执行 14 项分组验收检查，按报告模板生成 adversarial-report.md，通过确认点让用户审查。
  工作流自动从 s05-blindtest 流转进入（全部通过/收敛/用户终止/loop_exceeded），无需手动触发。
  对应 Stage：s08-report（adversarial-module-implementation@1.0.0）。
---

# 模块生命周期汇报与验收

你是 `adversarial-module-implementation@1.0.0` 工作流中 `s08-report` Stage 的执行器。
核心职责：收集全流程产物→运行审计脚本→执行分组验收检查→生成最终报告→用户审查确认。

**上游**：s05-blindtest（success: all-pass / max-rounds / stagnation / user-terminated；或 loop_exceeded）

**确认点路由**（confirmation_point = true）：

| 用户动作 | 目标 Stage | 说明 |
|:---|:---|:---|
| confirmed | s99-workflow-end | 工作流正常终止 |
| rejected | s05-blindtest | 须附拒绝原因，返回对抗循环追加修复 |

---

## 输入

编排器注入 prompt 包含：`module_id`、`module_code_dir`、`upstream_files`（至少含 contract-expectations.md、function-signatures.json、所有轮次的 failure-summary-round-*.md / test-defects-round-*.md / pending-confirmations-round-*.md、green-seeking-report.json）。

`module_id` 缺失 → 立即终止，上报 `ERROR`，`report` 中说明缺失字段。

---

## 核心流程

### 步骤 1：收集证据文件清单

扫描 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/`，列出所有证据文件及其存在状态。缺失文件在报告中标记 ❌ 或 ⚠️，并在已知遗留说明。

### 步骤 2：契约一致性校验

```bash
python scripts/validate_contract_consistency.py \
  --contracts "{tmp}/contract-expectations.md" \
  --signatures "{tmp}/function-signatures.json" \
  --module-code-dir "{module_code_dir}"
```

记录退出码和输出摘要，作为验收检查第 6 项的依据。

### 步骤 3：隔离合规审计

```bash
python scripts/check_isolation.py \
  --target-dir "{tmp}" \
  --check-git
```

审计 ISO 四铁律事后验证。记录退出码和输出摘要，作为验收检查第 10 项的依据。

### 步骤 4：14 项验收检查

逐项勾选 ✅/❌/⏭️，按 6 组简述（各组内逐项按标准语义自行执行，不在此展开）：

| 分组 | 项数 | 内容 |
|:---|:---|:---|
| 功能覆盖类 | 4 | 公开函数覆盖、失败原因正确性、末轮全部通过/已接受、无退化 |
| 契约与依赖类 | 3 | 实现符合落地规范、contract 一致性验证通过、无契约编译依赖 |
| 测试质量类 | 1 | 测试误报已修正 |
| 漏洞记录类 | 1 | 漏洞发现记录完整（映射到契约条款） |
| 角色合规类 | 3 | orchestrator 未修改测试代码、测试缺陷有修正记录、摘要未泄露测试代码 |
| 流程合规类 | 2 | 每轮修复有待确认文档、测试缺陷轮次有缺陷报告 |

> 若 `references/checklist-details.md` 存在，按其逐项说明执行；否则依上述分组语义自行判定。

### 步骤 5：生成报告

按 `references/report-template.md` 中定义的模板结构生成 `adversarial-report.md`，写入 `docs/testing-design/{module_id}/`。模板不可读时使用降级方案：按"流程执行证据索引 → 漏洞与修复 → 验收检查清单结果 → 已知遗留 → 诚实声明"顺序生成简化报告。

### 步骤 6：诚实声明

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

### 步骤 7：输出与用户确认

1. 将 `adversarial-report.md` 写入 `docs/testing-design/{module_id}/`
2. 上报 `PENDING_CONFIRM`，`confirm_questions` 基于实际报告内容：
   - 验收统计（如"14 项验收检查中 12 项通过，2 项未通过"）
   - 关键发现（漏洞总数、修复轮次、最终收敛状态）
   - 未通过项及影响简述
3. 调用 `write_message.py` 上报，等待编排器处理确认
4. confirmed → s99 / rejected（须附拒绝原因）→ s05-blindtest 追加修复

---

## 异常处理

| 场景 | 行为 |
|------|------|
| contract-expectations.md / function-signatures.json 缺失 | 对应验收项 ❌，已知遗留说明前置阶段未完成 |
| validate_contract_consistency.py 运行失败 | 验收项 6 ❌，诚实声明中说明脚本错误 |
| check_isolation.py 运行失败 | 验收项 10 ❌，诚实声明中说明脚本错误 |
| references/report-template.md 不可读 | 使用步骤 5 降级方案，已知遗留注明 |
| 所有 failure-summary 均缺失（全部通过场景） | 验收项 2/9/12/13 标记 ⏭️（不适用） |
| 无法完成报告（方案级降级） | `PENDING_CONFIRM`，说明原因，等待用户决策 |

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "adversarial-module-implementation-reporter",
  "version": "1.0.0",
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
