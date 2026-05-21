---
name: adversarial-module-implementation-init
description: >
  对抗性模块实现流水线入口——环境就绪与契约冻结。
  负责环境检查、强化契约提取（含模糊边界显式仲裁）、契约冻结、
  执行计划预览与用户确认。支持 core / recontract / contract-update / from-reverse 四种模式。
---

# 对抗性模块实现流水线 — 初始化与契约冻结

## 定位

你是对抗性模块实现流水线的入口执行器。核心任务是：**从设计文档（或逆向工程草稿）中提取并冻结接口契约，为后续信息隔离的对抗验证奠定基础。**

**职责边界**：只负责检查、提取、仲裁与冻结，不实现代码，不生成测试。

---

## 模式选择

本 Skill 支持四种模式。进入时根据上下文中的 `mode` 参数判定当前模式。

### 模式判定逻辑

```
若 mode == "recontract"：
    → 进入 recontract 模式
  否则若 mode == "contract-update"：
  → 进入 contract-update 模式
否则若 mode == "from-reverse"：
  → 进入 from-reverse 模式
否则（mode == "core" 或未指定）：
  → 检查 {module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md 是否存在且已冻结
    → 存在且已冻结 → 提示用户已有契约，询问是否覆盖或改为 contract-update
    → 不存在 → 进入 core 模式
```

### core 模式

**场景**：首次初始化，不存在已冻结的 contract-expectations.md。

**流程**：环境检查 → 契约提取 → 模糊边界仲裁 → 契约冻结 → 执行计划预览 → 用户确认。

**确认选项**：["确认", "重做", "放弃"]

### contract-update 模式

**场景**：已存在冻结的 contract-expectations.md，且收到差异报告 JSON（变更的契约条目列表）。

**流程**：加载已有契约 → 解析差异报告 → 仅更新变更条目，保留未变更条目的编号 → 用户确认。

**确认选项**：["确认", "继续完善", "放弃"]

### from-reverse 模式

**场景**：收到逆向设计文档草稿（从代码反向推断的设计文档）。

**流程**：读取逆向草稿 → 转化为标准 contract-expectations.md → 应用模糊边界显式仲裁（从代码行为推断约束，对模糊点做保守假设） → 用户确认。

**确认选项**：["确认", "继续完善", "放弃"]

---

## 输入

从编排器注入的上下文中获取：

| 字段 | 说明 | 必填 | 适用模式 |
|------|------|:--:|------|
| `module_id` | 目标模块编号（如 M01） | 是 | 全部 |
| `module_code_dir` | 模块代码目录路径 | 是 | 全部 |
| `mode` | 运行模式：`core` / `contract-update` / `from-reverse` | 否（默认 core） | 全部 |
| `diff_report` | 差异报告 JSON（变更的契约条目列表） | contract-update 时必填 | contract-update |
| `reverse_draft_path` | 逆向设计文档草稿路径 | from-reverse 时必填 | from-reverse |

`module_id` 缺失 → 立即终止，报告错误。

---

## core 模式详细流程

### Step 1：环境就绪检查（Preflight）

环境检查结果折叠为只读信息，不占用用户决策注意力。

**1.1 Python 版本检查**：确认当前运行环境 Python >= 3.8。不满足 → 错误阻断。

**1.2 脚本完整性验证**：
```bash
python .claude/workflows/adversarial-module-implementation/scripts/preflight_check.py --module-id {module_id}
```
退出码 0（通过）→ 继续；退出码 1（警告）→ 继续但记录到环境摘要；退出码 2（阻断）→ 错误。

**1.3 设计文档定位**：搜索 `docs/功能设计/` 下匹配 `{module_id}` 的设计文档，四级优先级：

| 优先级 | 文件模式 | 说明 |
|:---:|------|------|
| P0 | `{group}/{module_id}-*/{module_id}-*-落地规范.md` | 独立落地规范（首选） |
| P0 | `{group}/{module_id}-*/{module_id}-*-设计文档.md` | 独立设计文档 |
| P1 | `{group}/{module_id}-*/{module_id}-*-功能设计文档.md` | 旧版单文件 |
| P2 | `{module_id}-总设计文档.md` | 总设计文档 |
| P3 | 其他路径 | 兜底搜索 |

至少定位到一份 P0 或 P1 文档，否则 → 错误。

**1.4 环境摘要记录**：将 Python 版本、脚本检查结果、定位到的文档路径汇总为内部只读摘要，供后续步骤参考，不对外展示。

---

### Step 2：契约提取

按优先级读取设计产物（详细解析算法见 `references/contract-extractor.md`）：

| 优先级 | 来源 | 提取内容 |
|:---:|------|------|
| P0 | 落地规范「输入/输出类型定义」 | 参数类型、必填/可选、bounds、默认值、枚举 |
| P0 | 落地规范「异常处理」 | 异常类型、触发条件 |
| P1 | 落地规范「状态机」 | 状态转换约束、前置条件 |
| P1 | 设计文档「接口契约」 | 业务层面输入约束、边界定义 |
| P2 | 项目结构文档 | 命名规范、模块边界 |

P0 文件全部缺失 → 错误。P1/P2 缺失 → 记录警告，继续使用可用文件。

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
python .claude/workflows/adversarial-module-implementation/scripts/validate_contract_expectations.py \
    {contract_path} \
    --function-signatures {function_signatures_path}
```

验证内容包括：结构完整性、编号格式、覆盖完整性（每个公开函数至少一条条目）、破坏性输入明确性、模糊边界显式性（检查无「通常」「可能」「视情况」等模糊词汇残留）。

验证失败 → 修正后重试，最多 3 次。连续 3 次失败 → 错误。
验证通过 → 更新冻结时间为当前时间戳，标记文件为冻结状态。**冻结后不得擅自修改**。

---

### Step 5：构建执行计划预览

向用户展示以下信息，结构分为「只读摘要」和「待确认决策」两部分：

**【只读摘要】（无需决策，仅提供上下文）**
- 模块信息：`module_id`、模块名称、设计文档来源
- 环境检查结果：Python 版本、脚本检查状态
- 设计文档冲突摘要：冲突数量及仲裁结果简述（如无冲突则显示"无冲突"）
- 模糊边界仲裁摘要：本轮显式仲裁的条目数量

**【待确认决策】**
- 契约统计：公开函数数量、A 系列参数约束条目数、B 系列状态约束条目数
- 执行计划预览：后续流程概览

---

### Step 6：确认点

使用 AskUserQuestion 请求用户确认。需确认的核心事项：

1. **契约完整性**：以上契约覆盖 {N} 个公开函数、{M} 条参数约束、{K} 条状态约束。是否有遗漏或错误？
2. **执行计划可接受性**：是否按此计划执行？

选项：["确认", "重做", "放弃"]

**确认后行为**：
- 用户选择「确认」→ 契约冻结生效，流程完成
- 用户选择「重做」→ 根据用户反馈修改契约或计划，修改后重新进入确认点（自循环最多 2 次）
- 用户选择「放弃」→ 终止流程
- 超过 2 次重做 → 终止流程

---

## recontract 模式详细流程

进入条件：从盲测回流（上下文中包含 branch_context 且已有冻结契约）。

### Step 1：检测已有契约 + 提取矛盾点

读取 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`，
验证文件头部包含 `> **冻结时间**` 标记。

从上下文的 `branch_context` 获取：
- `recontract_reason`：矛盾描述
- `affected_contract_ids`：受影响条目编号列表（如 ["A-003", "B-005"]）

若原契约文件缺失 → 降级为 core 模式。若 branch_context 缺失 → 读取现有契约，
提示用户补充矛盾信息；无法定位则按 core 模式处理。

### Step 2：定向更新矛盾条目

对 `affected_contract_ids` 中的每条契约：
1. 在设计文档中重新定位其来源章节
2. 执行 core 模式 Step 3 的仲裁流程（含模糊边界显式纳入）
3. 更新契约条目的描述、约束和破坏性输入
4. 保留未受影响条目的编号和描述（确保下游引用稳定）

### Step 3：重新冻结

1. 更新文件头部的「冻结时间」为当前时间戳
2. 追加「重新冻结记录」段落：记录重新冻结时间、本轮更新条目编号、recontract 来源
3. 运行契约验证脚本

### Step 4：精简确认点

调用 AskUserQuestion：
- 「契约已重新仲裁，更新 {X} 条条目。其余 {Y} 条未变更。是否有遗漏或错误？」
- 选项：**"确认"** / **"重做"** / **"放弃"**

> 确认点选项与 core 模式一致：["确认", "重做", "放弃"]。

---

## contract-update 模式详细流程

### Step 1：加载已有契约

读取 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`。

验证文件头部包含 `> **冻结时间**` 标记。若文件不存在或未冻结 → 降级为 core 模式并提示用户。

### Step 2：解析差异报告

从输入 `diff_report` 中获取变更条目列表。差异报告结构：

```json
{
  "added": [
    {
      "function": "函数名",
      "parameter": "参数名",
      "constraint_type": "约束类型",
      "destructive_input": "破坏性输入",
      "expected_behavior": "期望行为",
      "source": "来源章节"
    }
  ],
  "modified": [
    {
      "contract_id": "A12",
      "changes": {
        "constraint_type": "新的约束类型",
        "destructive_input": "新的破坏性输入",
        "expected_behavior": "新的期望行为"
      }
    }
  ],
  "deleted": ["A03", "B07"]
}
```

### Step 3：增量更新

按以下规则处理三类变更：

- **新增条目（added）**：分配新编号（接续现有 A/B 系列最大编号），按标准格式生成完整条目，来源标注为差异报告的来源字段。
- **修改条目（modified）**：根据 `contract_id` 定位已有条目，仅更新差异报告中指定的字段，**保持编号不变**（确保下游引用稳定）。其余字段保留原值。
- **删除条目（deleted）**：在对应条目上追加删除标记 `[已删除 — {当前时间戳}]`，保留编号占位和原始描述（供下游追溯）。不得物理删除行。
- **未变更条目**：完全保留原样，不做任何修改。

更新完成后更新文件头部的「冻结时间」为当前时间戳，并追加「增量更新记录」段落：

```markdown
> **增量更新记录**：
> - 更新时间：{ISO 8601 时间戳}
> - 新增条目：{added_ids 列表}
> - 修改条目：{modified_ids 列表}
> - 删除条目：{deleted_ids 列表}
```

### Step 4：确认点

使用 AskUserQuestion 请求用户确认：

「契约已增量更新，新增 {X} 条、修改 {Y} 条、删除 {Z} 条。其余 {W} 条未变更。是否有遗漏或错误？」

选项：["确认", "继续完善", "放弃"]

**确认后行为**：
- 用户选择「确认」→ 更新生效，流程完成
- 用户选择「继续完善」→ 根据用户反馈继续修改契约（自循环最多 2 次）
- 用户选择「放弃」→ 终止流程
- 超过 2 次继续完善 → 终止流程

---

## from-reverse 模式详细流程

### Step 1：读取逆向设计文档草稿

读取 `reverse_draft_path` 指定的逆向设计文档草稿。该文档由代码分析工具生成，包含从实现代码反向推断的函数签名、类型定义、异常处理、状态转换等信息。

验证草稿可解析性：
- 至少包含一个公开函数列表
- 函数列表中的每个条目至少有函数名和参数列表
- 不满足 → 错误阻断

### Step 2：转化为标准契约格式

将逆向设计文档草稿中的信息映射为标准 contract-expectations.md 格式：

**2.1 函数签名映射**：
- 从草稿中提取公开函数列表
- 为每个函数的每个参数生成 A 系列参数约束条目
- 参数类型来自草稿中的类型声明

**2.2 类型约束映射**：
- 从草稿中的类型定义/注解中提取 bounds 信息
- 若草稿包含文档注释（docstring），从中提取参数说明作为补充约束
- 若草稿包含类型检查代码（如 `isinstance` 调用），提取为显式类型约束

**2.3 异常映射**：
- 从草稿中的异常信息（`raise` 语句分析结果）生成异常契约条目
- 若草稿包含 `try/except` 分析，将 catch 的异常类型作为可能的触发条件

**2.4 状态约束映射**：
- 若草稿包含状态机分析结果，生成 B 系列状态约束条目
- 若草稿包含前置条件检查代码（如 `if state != ...` 守卫），提取为前置条件约束

### Step 3：模糊边界显式仲裁

逆向工程得出的约束往往存在模糊点（代码中未显式校验的参数、隐式依赖的运行时行为等）。对以下模糊点执行保守假设：

| 场景 | 仲裁策略 |
|:---|:---|
| 函数签名声明了参数类型但代码中无显式校验 | 假设期望类型校验，契约要求 `TypeError` on type mismatch |
| 参数在代码中被使用但未做 null 检查 | 假设允许 null，标注"代码中未做 null 防护，存在 NPE 风险" |
| 代码中有范围检查（如 `if len(x) > N`）但无文档化 | 提取为显式 bounds.max = N，来源标注"从代码行为推断" |
| 异常被 catch 但未重新抛出 | 标注"静默吞异常"，契约中标记为需人工关注的模糊点 |
| 隐式依赖外部状态（全局变量、环境变量、文件系统等） | 提取为前置条件约束，标注"从代码隐式依赖推断" |
| 参数在代码中被传递给下游但未校验 | 记录为"透传参数，约束继承自下游"，标记置信度为 low |

**仲裁原则**：
- 记录观察到的代码行为，不做超出代码证据的推测
- 标注不确定性（使用"推断""假设""可能"等词明确区分于从设计文档提取的确定约束）
- 优先假设需要防御而非宽容（保守安全立场）
- 每条仲裁结果必须记录裁决依据（代码位置或推断理由）

**仲裁记录格式**：

```markdown
| 编号 | 契约维度 | 代码行为观察 | 仲裁结果 | 置信度 | 仲裁依据 |
|:---|:---|:---|:---|:---|:---|
| A12 | `process_data` 参数 `config` 可空性 | 函数体内未做 null 检查，直接访问 config 属性 | 假设允许 null（高风险） | low | 代码行为推断，未找到显式 null 防护 |
```

### Step 4：生成 contract-expectations.md

按 core 模式 Step 4 的格式要求生成 contract-expectations.md，但在文件头部追加逆向工程来源标记：

```markdown
> **来源**：逆向工程推断（基于代码分析草稿 `{reverse_draft_path}`）
> **推断时间**：{ISO 8601 时间戳}
> **置信度说明**：本契约基于代码行为反向推断，非基于设计文档。标记为 low 置信度的条目需人工复核。
```

运行相同的冻结前验证（`.claude/workflows/adversarial-module-implementation/scripts/validate_contract_expectations.py`）。

### Step 5：确认点

使用 AskUserQuestion 请求用户确认：

「基于逆向工程推断的契约覆盖 {N} 个函数、{M} 条约束。其中有 {L} 条为低置信度条目（需人工复核）。是否有遗漏或错误？」

选项：["确认", "继续完善", "放弃"]

**确认后行为**：
- 用户选择「确认」→ 契约冻结生效（含低置信度标记），流程完成
- 用户选择「继续完善」→ 根据用户反馈修改或补充契约（自循环最多 2 次）
- 用户选择「放弃」→ 终止流程
- 超过 2 次继续完善 → 终止流程

---

## 输出

| 产物 | 路径 | 说明 |
|------|------|------|
| `contract-expectations.md` | `{module_code_dir}/.tmp/adversarial-tests/{module_id}/` | 冻结的契约期望清单 |
| `conflict_log` | inline 在 contract-expectations.md 的「冲突记录」章节 | 设计文档冲突记录 |
| `arbitration_log` | inline 在 contract-expectations.md 的「模糊边界仲裁记录」章节 | 边界仲裁记录（core 模式和 from-reverse 模式） |

---

## 错误处理

| 场景 | 处理 |
|------|------|
| `module_id` 缺失 | 错误阻断 |
| Python < 3.8 | 错误阻断 |
| preflight_check.py 退出码 2 | 错误阻断 |
| 设计文档全部未定位到 | 错误阻断 |
| P0 落地规范缺失 | 错误阻断 |
| P1/P2 文件部分缺失 | 记录警告，继续 |
| 验证连续 3 次失败 | 错误阻断 |
| contract-update 时原契约文件缺失或未冻结 | 降级为 core 模式 |
| contract-update 时 diff_report 缺失或格式错误 | 错误阻断 |
| from-reverse 时 reverse_draft_path 缺失或文件不存在 | 错误阻断 |
| from-reverse 时逆向草稿无法解析（无有效函数列表） | 错误阻断 |

---

## 禁止行为

- 禁止自行假设未声明的约束（必须显式仲裁确定或请求用户确认）
- 禁止将模糊边界简单标记为「约束未声明」而不做显式仲裁
- 禁止修改任何上游设计文档（落地规范、设计文档、契约文件）
- 禁止在 core 模式或 contract-update 模式下查阅或读取实现代码
- 禁止编造不存在的约束或异常条件
- 禁止跳过验证直接冻结
- 禁止在未获取用户确认前完成流程
- 禁止调度内部 SubAgent

---

## 资源引用

| 资源 | 类型 | 路径 | 角色 |
|------|------|------|------|
| contract-extractor.md | references | `references/contract-extractor.md` | 详细解析算法（Skill 自用 + 建立供下游复用） |
| subagent-prompts.md | references | `references/subagent-prompts.md` | 下游 SubAgent prompt 模板（建立者，供编排器使用） |
| preflight_check.py | scripts | `.claude/workflows/adversarial-module-implementation/scripts/preflight_check.py` | 使用者 |
| validate_contract_expectations.py | scripts | `.claude/workflows/adversarial-module-implementation/scripts/validate_contract_expectations.py` | 使用者 |

> **建立者职责**：本 Skill 负责建立 `contract-extractor.md` 和 `subagent-prompts.md`。首次执行时，若 `.claude/workflows/adversarial-module-implementation/references/` 目录不存在这两个文件，应将本 Skill 目录下 `references/` 中的副本复制到该路径供下游复用。
