---
name: adversarial-module-implementation-init
description: >
  对抗性模块实现流水线（adversarial-module-implementation@1.1.0）入口阶段——环境就绪与契约冻结。
  负责环境检查、强化契约提取（含模糊边界显式仲裁）、设计仲裁、冻结 contract-expectations.md、
  执行计划预览与用户双决策确认。当工作流调度 s01-init 阶段、需要初始化模块对抗验证流水线、
  或从 s05-blindtest 通过 recontract 分支回流重新冻结契约时，必须优先使用本 Skill。
  确认点精简为双核心决策：契约完整性确认 + 执行计划可接受性。
---

# 对抗性模块实现流水线 — 初始化与契约冻结

## 定位

你是工作流 `adversarial-module-implementation@1.1.0` 中 Stage `s01-init` 的执行器。核心任务是：**从设计文档中提取并冻结接口契约，为后续信息隔离的对抗验证奠定基础。**

上游 `s00-workflow-start` 无业务输入；下游 `s02-impl` 依赖本阶段冻结的 `contract-expectations.md` 与用户确认。你可能被 `s05-blindtest` 通过 `branch_target="recontract"` 重新调用，进入"重新冻结"模式。

**职责边界**：只负责检查、提取与仲裁，不实现代码，不生成测试，不调度 SubAgent。

---

## 输入

从编排器注入的上下文中获取：

| 字段 | 说明 | 必填 |
|------|------|:--:|
| `module_id` | 目标模块编号（如 M01） | 是 |
| `module_code_dir` | 模块代码目录路径 | 是 |
| `workflow_ref_dir` | 工作流共享资源目录 | 否 |
| `branch_context` | 回流上下文（recontract 时携带） | 否 |

`module_id` 缺失 → 立即终止，上报 `ERROR`。

---

## 执行步骤

### Step 0：Recontract 模式检测（入口分支）

进入时优先检测是否存在已冻结的契约文件：

```
contract_path = {module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md
```

**若文件存在且头部包含 `> **冻结时间`**：**

进入 **「重新冻结」模式**：
1. 读取已有的 `contract-expectations.md`
2. 读取 `branch_context` 中携带的 `conflict_items`（s05-blindtest 报告的契约矛盾点列表）
3. 对每条矛盾条目，在设计文档中重新定位其来源章节，执行针对性仲裁
4. 仅更新矛盾条目，保留其余已确认契约的编号、描述和来源
5. 追加「重新冻结记录」到文件头部（重新冻结时间、本轮更新条目编号、矛盾来源）
6. **跳过 Step 1（环境检查）和 Step 2（完整契约提取）**，直接进入 Step 5（执行计划预览）和 Step 6（确认点）

**若文件不存在：** 正常执行完整流程（Step 1 → Step 6）。

---

### Step 1：环境就绪检查（Preflight）

环境检查结果折叠为只读信息，不占用用户决策注意力。

**1.1 Python 版本检查**：确认当前运行环境 Python >= 3.8。不满足 → `ERROR` 阻断。

**1.2 脚本完整性验证**：
```bash
python {workflow_ref_dir}/scripts/preflight_check.py --module-id {module_id}
```
退出码 0（通过）→ 继续；退出码 1（警告）→ 继续但记录到环境摘要；退出码 2（阻断）→ `ERROR`。

**1.3 设计文档定位**：搜索 `docs/功能设计/` 下匹配 `{module_id}` 的设计文档，四级优先级：

| 优先级 | 文件模式 | 说明 |
|:---:|------|------|
| P0 | `{group}/{module_id}-*/{module_id}-*-落地规范.md` | 独立落地规范（首选） |
| P0 | `{group}/{module_id}-*/{module_id}-*-设计文档.md` | 独立设计文档 |
| P1 | `{group}/{module_id}-*/{module_id}-*-功能设计文档.md` | 旧版单文件 |
| P2 | `{module_id}-总设计文档.md` | 总设计文档 |
| P3 | 其他路径 | 兜底搜索 |

至少定位到一份 P0 或 P1 文档，否则 → `ERROR`。

**1.4 环境摘要记录**：将 Python 版本、脚本检查结果、定位到的文档路径汇总为内部只读摘要，供后续步骤参考，不对外展示。

---

### Step 2：契约提取

按优先级读取设计产物（详细解析算法见 `{workflow_ref_dir}/references/contract-extractor.md`，或本 Skill 目录下的 `references/contract-extractor.md`）：

| 优先级 | 来源 | 提取内容 |
|:---:|------|------|
| P0 | 落地规范「输入/输出类型定义」 | 参数类型、必填/可选、bounds、默认值、枚举 |
| P0 | 落地规范「异常处理」 | 异常类型、触发条件 |
| P1 | 落地规范「状态机」 | 状态转换约束、前置条件 |
| P1 | 设计文档「接口契约」 | 业务层面输入约束、边界定义 |
| P2 | 项目结构文档 | 命名规范、模块边界 |

P0 文件全部缺失 → `ERROR`。P1/P2 缺失 → 记录警告，继续使用可用文件。

核心流程（5 步，详见 contract-extractor.md）：
1. 解析「输入/输出类型定义」章节 → 每个类型的字段列表
2. 将类型定义映射为函数参数契约
3. 解析「异常处理」章节 → 异常契约列表
4. 解析「状态机」章节 → 状态约束 + 前置条件
5. 组装契约条目（**A 系列**参数约束 + **B 系列**状态约束），生成破坏性输入矩阵

---

### Step 3：设计文档冲突仲裁 + 模糊边界显式纳入

**3.1 多文档冲突仲裁**

当多份文档对同一接口的描述不一致时：

1. **仲裁规则**：P0（落地规范）> P1（设计文档）> P2（项目结构文档）
2. **冲突记录**：将每个冲突写入 `conflict_log`：
   ```
   | 冲突维度 | P0/P1/P2 各自声明 | 裁决结果 | 备注 |
   ```

**3.2 模糊边界显式纳入契约（核心强化）**

设计文档中**所有模糊或不确定的边界描述**必须在仲裁阶段显式确定，不允许以「约束未声明」搁置：

| 原文中的模糊表述 | 仲裁动作 | 契约中必须显式写为 |
|:---|:---|:---|
| "通常不为空" / "一般不为空" / "默认非空" | 仲裁确定 | "禁止 null" 或 "允许 null，默认值为 X" |
| "可能为 null" / "可为空" | 仲裁确定 | "允许 null" 并声明默认值，或 "禁止 null" |
| "长度不超过大约 N" / "建议长度 X" | 仲裁确定 | 明确 bounds.max = N（或具体值） |
| "视情况而定" / "具体视场景" | 追溯设计文档中的分支条件 | 每个分支显式列出约束 |
| "未定义行为" / "行为未指定" | 仲裁确定 | 声明抛出异常类型，或声明返回特定值 |
| "仅支持部分格式" / "尽量兼容" | 列出明确支持的格式集合 | bounds.allowed_values 或 regex |
| 无明确异常类型的错误场景 | 按技术栈惯例确定异常类型 | 显式声明异常类名 |

**仲裁原则**：
- 优先以 P0 落地规范为准；P0 未覆盖的，以 P1 设计文档为准；两者皆模糊的，按最严格安全假设确定（禁止 null > 允许 null，抛出异常 > 静默返回）。
- 每条仲裁结果必须记录裁决依据（来源章节或假设理由）。
- **禁止**将模糊边界简单标记为「约束未声明」。

**3.3 未覆盖场景处理**

仅当某字段/参数**完全未在任何来源文档中出现**时，方可标记为「约束未声明」。即便如此，仍需做出保守假设并显式写入契约：
- 必填参数 → 至少标注 "non-empty" 约束
- 可选参数 → 标注 "无约束声明，允许任何值"

---

### Step 4：生成并冻结 contract-expectations.md

**产物路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`

**格式要求**：见 contract-extractor.md 的「输出格式」章节。

**冻结前验证**：
```bash
python {workflow_ref_dir}/scripts/validate_contract_expectations.py \
    {contract_path} \
    --function-signatures {function_signatures_path}
```

验证内容包括：结构完整性、编号格式、覆盖完整性（每个公开函数至少一条条目）、破坏性输入明确性、模糊边界显式性（检查无「通常」「可能」「视情况」等模糊词汇残留）。

验证失败 → 修正后重试，最多 3 次。连续 3 次失败 → `ERROR`。
验证通过 → 更新冻结时间为当前时间戳，标记文件为冻结状态。**冻结后不得擅自修改**。

---

### Step 5：构建执行计划预览

向用户展示以下信息，结构分为「只读摘要」和「待确认决策」两部分：

**【只读摘要】（无需决策，仅提供上下文）**
- 模块信息：`module_id`、模块名称、设计文档来源
- 环境检查结果：Python 版本、脚本检查状态（✅/⚠️）
- 设计文档冲突摘要：冲突数量及仲裁结果简述（如无冲突则显示"无冲突"）
- 模糊边界仲裁摘要：本轮显式仲裁的条目数量

**【待确认决策】**
- 契约统计：公开函数数量、A 系列参数约束条目数、B 系列状态约束条目数
- 执行计划预览：预计 Stage 数 8、SubAgent 调度 4 次、对抗循环 2-3 轮

---

### Step 6：确认点上报

`confirmation_point=true`，完成任务后上报 `PENDING_CONFIRM`，**不得直接上报 `DONE`**。

确认问题精简为 **2 个核心决策**，一次性列入 `confirm_questions`：

| 编号 | 问题 | 触发条件 |
|:---|------|------|
| CD-001 | 「契约完整性确认」：以上契约覆盖 {N} 个公开函数、{M} 条参数约束、{K} 条状态约束。是否有遗漏或错误？ | 始终触发 |
| CD-002 | 「执行计划可接受性」：预计 Stage 数 8、SubAgent 调度 4 次、对抗循环 2-3 轮。是否按此计划执行？ | 始终触发 |

其中 `{N}`、`{M}`、`{K}` 替换为实际统计数字。

**确认后行为**：
- 用户确认 → 编排器标记 DONE，`contract-expectations.md` 作为冻结产物传递下游 `s02-impl`
- 用户拒绝/要求修改 → 编排器恢复本 Stage，根据反馈修改后重新上报（自循环最多 2 次）
- 超过 2 次 → 走 `loop_exceeded` → `s99-workflow-end`

---

## Recontract 重新冻结模式详细流程

当从 `s05-blindtest` 通过 `branch_target="recontract"` 回流时：

1. **检测已有契约**：读取 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`
2. **提取矛盾点**：从 `branch_context` 获取 `recontract_reason`（矛盾描述）和 `affected_contract_ids`（受影响条目编号列表）
3. **增量更新**：
   - 对 `affected_contract_ids` 中的每条契约，回到设计文档重新定位来源
   - 执行 Step 3 的仲裁流程（含模糊边界显式纳入）
   - 更新契约条目的描述、约束和破坏性输入
   - 保留未受影响条目的编号和描述（确保下游引用稳定）
4. **重新冻结**：
   - 更新文件头部的「冻结时间」为当前时间戳
   - 追加「重新冻结记录」段落：记录重新冻结时间、本轮更新条目、recontract 来源（s05-blindtest 第 X 轮）
5. **精简确认**：
   - CD-001 问题调整为：「契约已重新仲裁，更新 {X} 条条目。其余 {Y} 条未变更。是否有遗漏或错误？」
   - CD-002 保持不变

---

## 输出

| 产物 | 路径 | 下游使用者 |
|------|------|----------|
| `contract-expectations.md` | `{module_code_dir}/.tmp/adversarial-tests/{module_id}/` | s02-impl, s04-testgen, s05-blindtest, s08-report |
| `conflict_log` | inline 在 contract-expectations.md 的「冲突记录」章节 | s05-blindtest（recontract 时引用） |
| 执行计划预览 | inline 在 confirm_questions 中 | 用户审查 |

---

## 错误处理

| 场景 | 处理 |
|------|------|
| `module_id` 缺失 | `ERROR` 阻断 |
| Python < 3.8 | `ERROR` 阻断 |
| preflight_check.py 退出码 2 | `ERROR` 阻断 |
| 设计文档全部未定位到 | `ERROR` 阻断 |
| P0 落地规范缺失 | `ERROR` 阻断 |
| P1/P2 文件部分缺失 | 记录警告，继续 |
| 验证连续 3 次失败 | `ERROR` 阻断 |
| recontract 时原契约文件缺失 | 降级为正常模式（完整提取） |
| recontract 时 branch_context 缺失 | 读取现有契约，提示用户补充矛盾信息；若无法定位则按完整重新提取处理 |

---

## 禁止行为

- 禁止自行假设未声明的约束（必须显式仲裁确定或上报确认）
- 禁止将模糊边界简单标记为「约束未声明」而不做显式仲裁
- 禁止修改任何上游设计文档（落地规范、设计文档、契约文件）
- 禁止在 `PENDING_CONFIRM` 阶段上报 `DONE`
- 禁止查阅或读取实现代码
- 禁止编造不存在的约束或异常条件
- 禁止跳过验证直接冻结
- 禁止在未获取用户确认前释放下游
- 禁止调用 `AskUserQuestion` 工具
- 禁止调度内部 SubAgent

---

## 资源引用

| 资源 | 类型 | 路径 | 角色 |
|------|------|------|------|
| contract-extractor.md | references | `references/contract-extractor.md` / `{workflow_ref_dir}/references/contract-extractor.md` | 详细解析算法（建立者） |
| subagent-prompts.md | references | `references/subagent-prompts.md` / `{workflow_ref_dir}/references/subagent-prompts.md` | 下游 SubAgent prompt 模板（建立者） |
| preflight_check.py | scripts | `{workflow_ref_dir}/scripts/preflight_check.py` | 使用者 |
| validate_contract_expectations.py | scripts | `{workflow_ref_dir}/scripts/validate_contract_expectations.py` | 使用者 |

> **建立者职责**：本 Skill 负责建立 `contract-extractor.md` 和 `subagent-prompts.md`。首次执行完整流程时，若工作流级 references/ 目录不存在这两个文件，Skill 应将本 Skill 目录下的副本复制到 `{workflow_ref_dir}/references/` 供下游复用。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "adversarial-module-implementation-init",
  "version": "1.1.0",
  "workflow_id": "adversarial-module-implementation",
  "stage_id": "s01-init",
  "confirmation_point": true,
  "task_modes": ["core", "recontract"],
  "autonomous_degradation": true
}
```
