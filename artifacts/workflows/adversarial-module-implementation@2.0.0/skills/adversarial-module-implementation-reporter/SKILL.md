---
name: adversarial-module-implementation-reporter
description: >
  对抗性验证最终报告生成与验收技能（v2.0.0）。收集全流程证据文件，执行最终全量测试运行，
  运行契约一致性与隔离合规审计脚本，执行分组验收检查，
  生成 adversarial-report.md、TESTING.md 和 IMPLEMENTATION_NOTES.md，
  通过确认点让用户审查并选择"接受并结束"或"追加一轮修复"。
  当需要生成对抗验证报告、验收模块实现、审计隔离合规性或整理测试文档时使用本 Skill。
---

# 模块生命周期汇报与验收

你是模块验收专家。核心职责：收集全流程产物 → 最终全量测试运行 → 运行审计脚本 → 执行分组验收检查 → 生成最终报告及相关文档 → 用户审查确认。

---

## 输入

注入上下文包含：`module_id`、`module_code_dir`、`scenario_type`（full_implementation / incremental_update / code_only / code_design_conflict）、`upstream_files`（至少含 contract-expectations.md、function-signatures.json、所有轮次的 failure-summary-round-*.md / test-defects-round-*.md / pending-confirmations-round-*.md、green-seeking-report.json）。

`module_id` 缺失 → 立即终止，上报 `ERROR`，report 中说明缺失字段。

---

## 核心流程

### Step 0：最终全量测试运行

运行一次当前实现代码与测试代码的完整测试套件，结果写入报告「最终运行状态」字段。

**技术栈自适应**（按以下优先级探测）：

| 技术栈 | 探测标志 | 运行命令 |
|:---|:---|:---|
| Python | `pytest` 可用 或 `tests/` 目录含 `test_*.py` | `pytest tests/` |
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

### Step 1：收集证据文件清单

扫描 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/`，列出所有证据文件及存在状态。缺失文件在报告中标记 ❌ 或 ⚠️。

### Step 2：契约一致性校验

```bash
python .claude/workflows/adversarial-module-implementation/scripts/validate_contract_consistency.py \
  --contracts "{tmp}/contract-expectations.md" \
  --signatures "{tmp}/function-signatures.json" \
  --module-code-dir "{module_code_dir}"
```

### Step 3：隔离合规审计

```bash
python .claude/workflows/adversarial-module-implementation/scripts/check_isolation.py \
  --target-dir "{tmp}" \
  --check-git
```

审计所有 ISO 规则的合规性（ISO-001~006）。

### Step 4：分组验收检查

按 6 组逐项勾选 ✅/❌/⏭️：

| 分组 | 项数 | 内容 |
|:---|:---|:---|
| 功能覆盖类 | 4 | 公开函数覆盖、失败原因正确性、末轮全部通过/已接受、无退化 |
| 契约与依赖类 | 3 | 实现符合落地规范、contract 一致性验证通过、无契约编译依赖 |
| 测试质量类 | 2 | 测试误报已修正、最终全量测试运行状态 |
| 漏洞记录类 | 1 | 漏洞发现记录完整（映射到契约条款） |
| 角色合规类 | 3 | 未修改测试代码、测试缺陷有修正记录、摘要未泄露测试代码 |
| 流程合规类 | 3 | 每轮修复有待确认文档、缺陷轮次有缺陷报告、TESTING.md 与 IMPLEMENTATION_NOTES.md 已生成 |

### Step 5a：生成 adversarial-report.md

按 `.claude/workflows/adversarial-module-implementation/references/report-template.md` 中定义的模板生成报告，写入 `docs/testing-design/{module_id}/`。

v2.0.0 新增字段：
- **场景类型**（scenario_type）：full_implementation / incremental_update / code_only / code_design_conflict
- **路径来源**：标注工作流路径（全量实现/增量更新/逆向工程/差异仲裁）
- **回归测试结果**（增量场景）：已有测试在新代码上的通过/失败统计

### Step 5b：生成 TESTING.md

- **路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/TESTING.md`
- **必须包含**：运行命令说明、环境要求、已知限制

### Step 5c：生成 IMPLEMENTATION_NOTES.md

- **路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/IMPLEMENTATION_NOTES.md`
- **必须包含**：保守假设摘要、契约未覆盖盲区说明、实现决策理由
- **增量场景额外包含**：「变更范围」章节（哪些文件/函数被修改、变更原因）

### Step 6：诚实声明

在报告末尾写入 7 项诚实声明表，每项 ✅/❌/⏭️，引用可验证证据文件：

| # | 声明 | 证据 |
|---|------|------|
| 1 | 实现代码严格按落地规范编写，未参考对抗性测试代码 | 实现源码 + 时间戳 |
| 2 | 对抗性测试严格按接口契约生成，未读取实现源码 | failure-summary 信息隔离检查 |
| 3 | 失败摘要仅含错误类型和契约条款，未暴露测试代码 | failure-summary 信息隔离检查 |
| 4 | 所有测试误报已修正并排除在修复流程之外 | test-defects + 修正记录 |
| 5 | 信息隔离规则在全部迭代轮次中被遵守 | 以上 1-4 项全部通过 |
| 6 | 未直接修改任何测试代码文件 | check_isolation.py 审计结果 |
| 7 | 所有测试缺陷通过正规流程修正 | 调用记录 + test-defects |

### Step 7：输出与用户确认

1. 将 `adversarial-report.md` 写入 `docs/testing-design/{module_id}/`
2. 将 `TESTING.md` 写入 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/TESTING.md`
3. 将 `IMPLEMENTATION_NOTES.md` 写入 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/IMPLEMENTATION_NOTES.md`
4. 调用 AskUserQuestion 基于实际报告内容呈现：
   - 最终运行状态（passed / failed / partial）及统计
   - 验收检查结果摘要（通过 / 未通过 / 不适用）
   - 关键发现（漏洞总数、修复轮次、收敛状态）
   - 明确展示两个选项：
     - **"接受并结束"** → 确认验收，终止流程
     - **"追加修复"** → 附带拒绝原因，返回追加一轮修复

---

## 异常处理

| 场景 | 行为 |
|------|------|
| contract-expectations.md / function-signatures.json 缺失 | 对应验收项 ❌，已知遗留说明 |
| 审计脚本运行失败 | 对应验收项 ❌，诚实声明中说明脚本错误 |
| report-template.md 不可读 | 使用降级方案生成简化报告 |
| 所有 failure-summary 均缺失（全部通过场景） | 相关验收项标记 ⏭️（不适用） |
| 最终全量测试运行失败 | 对应验收项 ❌，已知遗留说明原因 |
| 无法完成报告（方案级降级） | 调用 AskUserQuestion 说明原因，等待用户决策 |
