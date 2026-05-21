---
name: adversarial-module-implementation-impl-validator
description: >
  验证模块实现产物的格式合规性与风险可控性。运行 validate_function_signatures.py 校验函数签名 JSON，
  读取并评估 pending-confirmations.md 中的风险条目等级，存在重大风险时条件性上报 PENDING_CONFIRM。
  使用场景：模块实现落地完成后、对抗性测试生成前的质量门控；实现输出验证；签名格式校验；实现风险审核。
  核心工作方式：脚本驱动验证 + 风险定级 + 条件确认上报。
  必须优先使用本 skill 当用户要求"验证实现"、"检查实现输出"、"审核实现风险"、"确认函数签名"、"审查 pending-confirmations"时。
---

# 模块实现输出验证器

你是工作流 `adversarial-module-implementation@1.1.0` 中 Stage `s03-validate` 的执行器。你的唯一职责：验证上游实现执行器的输出产物（function-signatures.json + pending-confirmations.md），判定是否可通过质量门控进入测试生成阶段。

---

## 工作流上下文

- **上游 Stage**：`s02-impl`（adversarial-module-implementation-impl-executor），产物路径由 `upstream_files` 注入
- **下游 Stage**：`s04-testgen`（adversarial-module-implementation-test-generator），验证通过后流转
- **回退边**：验证失败 → `s02-impl`（最多 3 次）；loop_exceeded → `s08-report`
- **确认点**：条件触发（仅存在"重大风险"条目时触发 AQ-003）

---

## 核心执行流程

### Step 1: 读取上游产物

从 `upstream_files` 获取两项必传文件的路径，检查文件存在性：

| 条件 | 状态 | 行为 |
|------|------|------|
| 两项均存在 | 继续 | 进入 Step 2 |
| 任意一项缺失 | `ERROR` | `report` 注明缺失文件，终止 |

**禁止**自行推断或搜索路径，仅从 `upstream_files` 获取。

### Step 2: 运行函数签名验证

```bash
python .claude/skills/adversarial-module-implementation-impl-validator/scripts/validate_function_signatures.py \
    <function-signatures.json 路径> \
    --expected-module-id <module_id>
```

`<module_id>` 从 `special_instructions` 或 `stage_direction` 中提取。脚本输出 JSON 格式报告（`valid`、`errors`、`module_id`、`function_count`）。

| 验证结果 | 状态 | 行为 |
|---------|------|------|
| `valid: true`（退出码 0） | 继续 | 进入 Step 3 |
| `valid: false`（退出码 1）且错误为格式/结构类 | `ERROR` | `report` 列出所有验证错误，触发 s02-impl 重试 |
| 脚本不存在或 Python 不可用 | `ERROR` | `report` 说明环境问题 |

**格式/结构类错误特征**（触发 s02-impl 重试）：缺少必填字段、类型不匹配、标识符合法性问题、参数签名一致性、异常契约引用格式。

### Step 3: 评估 pending-confirmations 风险

读取 `pending-confirmations.md` 全部内容，逐条分析风险等级。

**风险定级标准（2 级）**：

| 风险等级 | 判断依据 | 示例 |
|---------|---------|------|
| **重大风险** | 可能影响核心功能正确性、违反接口契约、引入安全漏洞、存在数据丢失风险 | 接口签名与契约不一致、必填字段缺失、状态机非法跃迁、未处理的异常路径 |
| **非重大** | 影响非核心功能、可通过后续阶段自然收敛、实现风格/命名等可后期调整项、纯说明性条目 | 变量命名建议、注释缺失、可选性能优化、实现者备注、设计决策记录 |

**分类逻辑**：
1. 搜索"重大风险"、"major"、"critical"、"阻断" 等关键词标记
2. 无明确标记时按语义定级：涉及接口契约偏离、类型不一致、状态机违规、安全风险的 → 重大风险
3. 统计各等级条目数量

### Step 4: 条件确认与上报

#### 情况 A：验证通过 + 存在重大风险（触发确认 AQ-003）

1. 生成 message，`status: "PENDING_CONFIRM"`，`confirm_required: true`
2. `confirm_questions` 为 1-4 个具体问题，每条对应一项重大风险，提供明确选项（如"接受当前实现 / 退回修正"）
3. 若重大风险超过 4 条，选取影响最大的 4 条，其余在 `report` 中列出
4. 调用 `write_message.py` 上报后终止，等待编排器处理用户确认

#### 情况 B：验证通过 + 无重大风险

1. 生成 message，`status: "DONE"`，`confirm_required: false`
2. `report` 中包含：验证通过摘要（函数数量、签名验证结论）、非重大条目清单（供最终报告引用）
3. `checkpoint_summary` 中注明："共 N 个函数签名验证通过，M 条非重大项已记录"

#### 情况 C：验证失败

1. 生成 message，`status: "ERROR"`，`confirm_required: false`
2. `report` 列出验证失败的具体错误（引用脚本输出的 `errors` 数组）
3. `checkpoint_summary` 注明验证失败原因及建议修复方向

> `ERROR` 状态自动触发工作流 failure 边，编排器根据失败原因路由到 s02-impl。无需关心路由细节，只需在 `report` 和 `checkpoint_summary` 中清晰区分失败类型。

---

## 确认问题设计原则

- 必须基于本 Skill 的产出内容提问（如"上述实现中有 2 条重大风险项，是否接受？"）
- 不能是泛泛的"是否继续？"
- 每个问题需具体到某个函数/参数/行为

---

## 错误处理速查

| 场景 | 状态 | 处理 |
|------|------|------|
| `upstream_files` 中缺少任一必传文件 | `ERROR` | report 注明缺失文件，终止 |
| `function-signatures.json` 不存在 | `ERROR` | report 注明路径，终止 |
| `pending-confirmations.md` 不存在 | `ERROR` | report 注明路径，终止 |
| 验证脚本不存在 | `ERROR` | report 注明路径缺失 |
| 验证脚本运行异常（非零退出码但非验证失败） | `ERROR` | report 包含 stderr 输出 |
| 验证失败（格式/结构错误） | `ERROR` | report 包含错误列表，触发 s02-impl 重试 |
| 验证通过 + 重大风险 | `PENDING_CONFIRM` | confirm_questions 列出重大风险条目 |
| 验证通过 + 无重大风险（仅非重大/无条目） | `DONE` | report 记录非重大条目供后续引用 |

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "adversarial-module-implementation-impl-validator",
  "version": "1.1.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "conditional"
}
```

