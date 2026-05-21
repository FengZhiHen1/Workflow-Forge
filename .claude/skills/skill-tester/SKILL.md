---
name: skill-tester
description: >
  工作流 Skill 功能审计器。读取 WORKFLOW.yaml 自动发现所有 Skill，生成对抗测试用例，
  在隔离 worktree 中实际运行每个 Skill，验证其功能正确性（能跑吗）、降级行为、边界防御。
  当用户提到"测试 Skill"、"审查 Skill 功能"、"Skill 能不能跑"、"skill test"、
  "验证 Skill"、"Skill 质量"、"对抗测试 Skill"时使用本 Skill。
  也用于 workflow-designer 产出 Skill 后做功能验收。
  **与 skill-reviewer 不同**：skill-reviewer 审查 SKILL.md 的写作质量，本 Skill 审查 Skill 的实际运行行为。
---

# Skill Tester

你是 **Skill 功能审计器**。你的任务是实际运行 Skill，验证它能否正确完成声称的任务——不是读 SKILL.md 评价写得好不好，而是给它输入、收它输出、判断对不对。

## 与已有审计的边界

| 工具 | 审什么 | 怎么审 |
|------|--------|--------|
| `workflow-auditor` | YAML 状态机 + wfctl 行为 | 符号推演 + 沙箱调用 |
| `skill-reviewer` | SKILL.md 写作质量 | AI 独立评审 |
| **本 Skill** | **Skill 运行行为** | **worktree 隔离执行 + 对抗输入 + AI 评估** |

## 审计架构

```
Phase 0: Skill 发现
  读取 WORKFLOW.yaml → 提取所有 skill_id → 读 SKILL.md 理解能力声明
  ↓
Phase 1: 用例生成（AI，并行）
  为每个 Skill 自动生成 3-5 个对抗用例 + 链式用例
  ↓
Phase 2: 执行（Agent worktree，并行）
  每个用例 → spawn SubAgent → 给输入 → 收输出
  ↓
Phase 3: 评估（脚本 + AI）
  机械检查（存在性/幻觉/格式） + AI 语义评审
  ↓
输出: 审计报告 → workshop/audit-reports/<workflow_id>-skill-audit.md
```

---

## Phase 0：Skill 发现

### Step 0.1：提取 Skill 列表

读取 WORKFLOW.yaml，提取所有业务 stage（非 s00/s99）的 `skill_id`。去重。

### Step 0.2：子工作流发现

递归检查 WORKFLOW.yaml 中所有含 `workflow` 字段的 stage。对每个子工作流引用（`<id>@<ver>`），
在 `artifacts/workflows/` 或 `--workflows-dir` 下查找对应的 WORKFLOW.yaml，提取其中的 `skill_id`。

### Step 0.3：确认测试范围

向用户展示发现的 Skill 清单，用 AskUserQuestion 确认范围：

```
发现 N 个 Skill（含 M 个子工作流 Skill）。

测试范围？
- 仅主工作流（N-M 个 Skill）
- 主工作流 + 直接子工作流（一级嵌套）
- 全部（含嵌套子工作流，最多 3 层深）
```

如果用户未响应或 SKILL.md 要求全自动运行，默认选择"主工作流 + 直接子工作流"。

### Step 0.4：读取 Skill 能力声明

对每个 skill_id，定位并快读 SKILL.md：
- **身份与任务**（第一段）——这个 Skill 声称自己能做什么？
- **工作流程**——分几步？每步的输入/输出是什么？
- **输出格式**——产出什么文件？什么格式？
- **降级策略**——输入缺失时怎么办？

### Step 0.5：构建 Skill 依赖图

从 WORKFLOW.yaml 的 edges 推导 Skill 之间的数据流关系。标记哪些 Skill 有上下游关系，为链式测试做准备。子工作流内的 Skill 依赖图也一并构建。

---

## Phase 1：用例生成

### Step 1.1：为每个 Skill 生成基准 + 对抗用例

基于 Phase 0 的能力理解，对每个 Skill 生成用例。

**必选**（每个 Skill）：
- HAPPY（1 个）
- EMPTY（1 个）
- MALFORMED（1 个）
- **如果 Skill 含 AskUserQuestion** → CONFIRM-ALL-PASS + CONFIRM-ALL-REJECT（2 个）

**可选**（按 Skill 特征选择 1-3 个）：
- AMBIGUOUS / INJECTION / OVERFLOW / CROSS-REF

生成规则详见 `references/attack-cases.md`。

**用例保存路径**：`.tmp/skill-tester-<ts>/cases/<skill_id>/case-<N>.yaml`

### Step 1.2：生成链式用例

对有上下游关系的 Skill 对，生成 1-2 个链式用例：上游 Skill 的真实输出作为下游 Skill 的输入。

### Step 1.3：并行调度

所有 Skill 的用例生成可并行进行——无依赖关系。

---

## Phase 2：执行

### Step 2.1：按批次优先级调度

用例**必须**按以下批次顺序执行。同批次内全部并行，跨批次必须等上一批全部完成后才开始。

| 批次 | 用例模式 | 并行度 | 说明 |
|------|---------|--------|------|
| **Batch 1** | EMPTY + MALFORMED | 全部并行 | 零交互、零真实数据。worktree 文件最简单（空目录或故意损坏的文件）。**必须 100% 覆盖所有 Skill** |
| **Batch 2** | HAPPY | 全部并行 | 需要完整的上游输入文件。Batch 1 通过后再跑，避免在降级都没验证的情况下测快乐路径 |
| **Batch 3** | CONFIRM (ALL-PASS + ALL-REJECT) | 全部并行 | 需先完成 SKILL.md 打补丁（Step 2.1c）。确认点多的 Skill 优先 |
| **Batch 4** | CHAIN | 串行依赖 | 依赖上游 HAPPY 产出，等 Batch 2 全部完成后才能开始 |
| **Batch 5** | Optional (AMBIGUOUS / INJECTION / OVERFLOW / CROSS-REF) | 按需并行 | 仅在 Batch 1-3 全部通过且仍有时间预算时执行 |

**为什么 EMPTY 和 MALFORMED 必须在第一批**：二者都不需要真实上下文，是最容易自动化的用例。如果连空输入和损坏输入都处理不好，HAPPY 路径的验证没有意义。

### Step 2.1b：构造启动 Prompt

**非 CONFIRM 用例**（EMPTY / MALFORMED / HAPPY / Optional）：

```
你是本次测试的 SubAgent。你的 SKILL.md 已作为指令注入。

═══ 测试上下文 ═══
<用例描述和输入说明>

重要：这是自动化测试环境。如果 Skill 在此场景下发起 AskUserQuestion，说明 Skill
设计有问题（非交互场景不应有确认依赖）——请在 report 中标注此异常。
```

**CONFIRM 用例**（CONFIRM-ALL-PASS / CONFIRM-ALL-REJECT）：

必须先完成 Step 2.1c（SKILL.md 打补丁），使用打补丁后的 SKILL.md 作为指令源。启动 prompt 中只保留兜底规则：

```
你是本次测试的 SubAgent。你的 SKILL.md 已按测试剧本打过补丁——确认步骤已替换为内联答案。

═══ 兜底应答规则 ═══
如果仍然遇到未打补丁的确认点（不应发生，但以防万一）：
- CONFIRM-ALL-PASS 模式 → 默认选第一个选项，继续执行
- CONFIRM-ALL-REJECT 模式 → 默认选最后一个带"放弃/拒绝/否"语义的选项
- 在最终 report 中必须注明：
  "自动确认: 共发起 N 次确认，全部按脚本应答。应答记录: [question → answer, ...]"
- 如果 script 中没有任何预设答案能匹配当前问题 → 使用上述默认行为，并在 report 中标注"未预设的确认"

═══ 测试上下文 ═══
<用例描述和输入说明>
```

**通用规则**（所有用例）：

- **输入注入**：用例的 `input.worktree_files` 在 worktree 中创建对应的文件结构。**在 spawn SubAgent 之前必须验证文件已正确写入 worktree**——写入失败则标记为基础设施问题（INFRA_ERROR），不计入 Skill 的 pass/fail。
- **超时控制**：每个用例 `timeout_seconds`（默认 600s）。

### Step 2.1c：CONFIRM 用例 —— SKILL.md 打补丁

CONFIRM 用例的核心挑战：Skill 自身的 SKILL.md 要求调用 AskUserQuestion，但测试环境无人应答。**纯 prompt 劝服不可靠**——SubAgent 面对 SKILL.md 的具体指令和测试 harness 的通用指令冲突时，SKILL.md 往往胜出。

解决方案：**在 spawn SubAgent 之前，修改 Skill 的 SKILL.md，把确认步骤替换为内联答案**，从源头消除 AskUserQuestion 调用。

**打补丁流程**：

```
对每个 CONFIRM 用例：
  1. 复制原始 SKILL.md → worktree 中的 .tmp/patched-skills/<skill_id>/SKILL.md
  2. 读取 confirm_script，逐条取出 question_hint
  3. 在 SKILL.md 中搜索包含 question_hint 关键词的段落
     - 搜索策略：模糊匹配（question_hint 中的关键词全部出现即可）
     - 搜索范围：SKILL.md 全文
  4. 对每个匹配到的确认段落，替换为：
     """
     > **[自动化测试模式]** 此步骤原本需要向用户确认「{question_hint}」。
     > 测试预设答案：**{answer}**
     >
     > 使用此答案继续执行。在你的最终 report 中记录：`自动确认: [{question_hint} → {answer}]`
     > 禁止在此步骤调用 AskUserQuestion——答案已在上方给出。
     """
  5. 修改 SKILL.md frontmatter 的 name 字段为 `<原名>-patched-<case_id>`
     （防止 SubAgent 混淆原始 Skill 和测试版）
  6. 记录补丁结果：
     - 全部 question_hint 匹配成功 → 继续 spawn
     - 有 question_hint 在 SKILL.md 中找不到匹配段落 → ⚠️ warning:
       "confirm_script 中的 '{question_hint}' 在 SKILL.md 中未找到对应确认段落。
        该确认点将依赖兜底 prompt 规则处理。"
```

**打补丁后 spawn**：SubAgent 使用的 SKILL.md 路径指向 worktree 中的 `.tmp/patched-skills/<skill_id>/SKILL.md`，而非原始 Skill 路径。

**打补丁失败时的降级**：如果 SKILL.md 无法解析或打补丁过程出错，标记为 `INFRA_ERROR`，不纳入 Skill 评分。使用原始 SKILL.md + 强化兜底 prompt（明确禁止 AskUserQuestion）作为最后的 fallback。

### Step 2.2：收集中间产出

SubAgent 完成后，从 worktree 中收集：
- 产出文件列表
- SubAgent 的 report / checkpoint_summary
- 进程状态（是否 crash、是否超时）

---

## Phase 3：评估

### Step 3.1：机械检查（脚本）

对每个用例的产出运行机械检查。检查清单见 `references/attack-cases.md` §判定脚本。

机械检查输出 `mechanical_check.json` 到用例目录旁：
```json
{
  "case_id": "...",
  "checks": [
    {"check": "output_exists", "passed": true, "detail": "..."},
    {"check": "no_hallucinated_paths", "passed": false, "detail": "引用 /fake/path.md 不存在"},
    {"check": "token_budget", "passed": false, "budget": 80000, "actual": 51022, "detail": "未超"},
    {"check": "time_budget", "passed": true, "budget_seconds": 900, "actual": 816, "detail": "未超"},
    {"check": "no_ask_user_question", "passed": true, "detail": "CONFIRM 用例：SubAgent 未调用 AskUserQuestion，补丁生效"},
    {"check": "confirm_recorded", "passed": true, "detail": "report 包含自动确认记录，3 次确认全部应答"},
    {"check": "worktree_files_deployed", "passed": true, "detail": "3/3 文件已写入 worktree"}
  ],
  "crash_detected": false,
  "timeout_triggered": false,
  "infra_error": false,
  "extrapolation": {
    "entity_count": 8,
    "token_per_entity": 6378,
    "time_per_entity_seconds": 102,
    "projected_20_entities_tokens": 137550,
    "warning": "预计 20 模块时 137K tokens，超出建议预算"
  }
}
```

**新增检查项说明**：

| 检查项 | 适用用例 | 判定逻辑 |
|--------|---------|---------|
| `no_ask_user_question` | CONFIRM 用例必检 | 检查 SubAgent 的 tool_calls 中是否出现 `AskUserQuestion`。出现 → FAIL（补丁未生效）。非 CONFIRM 用例中如果出现也标记 warning |
| `confirm_recorded` | CONFIRM 用例必检 | SubAgent 的 report 是否包含 `自动确认:` 记录，且次数与 confirm_script 长度一致 |
| `worktree_files_deployed` | 所有用例 | spawn 前验证 `worktree_files` 是否成功写入。未成功 → `infra_error: true`，不计入 Skill 评分 |
| `infra_error` | 所有用例 | 基础设施故障标记。为 true 时该用例结果从 Skill 的 pass/fail 统计中排除 |

### Step 3.2：AI 语义评审（并行）

对每个用例的产出，以独立评审者视角评估：

| 维度 | 检查内容 |
|------|---------|
| 任务完成度 | 是否产出 SKILL.md 中声称的输出？格式是否正确？ |
| 逻辑合理性 | 输出是否从输入推导而来（不是凭空编造）？ |
| 降级质量 | 遇到错误/缺失输入时的行为是否合理？ |
| **降级类型** | **显性**（SKILL.md 有显式"若缺失→报 ERROR"条款）vs **隐性**（靠自然控制流碰巧停住）。两者都 pass，但隐性降级标注 info："建议增加显式错误处理条款" |
| 幻觉检测 | 是否引用了不存在的数据、路径、Skill？ |
| **确认合规**（CONFIRM 用例） | report 中是否注明了自动确认次数和选择？拒绝路径下是否优雅终止（非 crash/半成品）？ |
| **预算合规** | 机械层标记的超预算 finding 是否合理？外推风险评估是否需要升级？ |

### Step 3.3：CONFIRM 专项检查

对每个 CONFIRM 用例额外检查，**按优先级排序**：

```markdown
0. **【最高优先级】检查 SubAgent 是否调用了 AskUserQuestion 工具**
   - 检查 SubAgent 的 tool_calls 记录中是否出现 AskUserQuestion
   - 出现 → CRITICAL FAIL: "CONFIRM 用例补丁未生效——SubAgent 调用了 AskUserQuestion（应有 N 次确认被打补丁）。需检查 SKILL.md 打补丁流程是否遗漏了确认点。"
   - 未出现 → 补丁生效，继续后续检查

1. 检查 SubAgent 的 report 是否包含 "自动确认: 共发起 N 次确认"
   - 不包含 → warning: "CONFIRM 用例未记录确认应答"
   - 包含但 N=0 → warning: "确认记录为空——Skill 可能未走确认流程"
2. 检查 N 是否与 confirm_script 长度一致
   - 不一致 → warning: "确认次数异常（预期 M 次，实际 N 次）。可能原因：(a) 补丁遗漏了某个确认点，(b) Skill 在动态条件下产生了新确认"
3. CONFIRM-ALL-REJECT 检查：
   - SubAgent 是否优雅终止（DONE 或 ERROR，而非 crash/超时）
   - report 是否说明拒绝原因
   - 拒绝后是否产出了半成品文件（不应有）→ 如有，warning: "拒绝路径下仍产出了文件"
4. 如果 Skill 发起了 confirm_script 中未预设的确认（兜底规则触发）：
   - warning: "Skill 发起了未预期的确认: <question>。该确认点未在 confirm_script 中声明，可能需要在 SKILL.md 打补丁步骤中补充覆盖。"
5. **补丁覆盖率回查**：将 confirm_script 的 question_hint 列表与 SKILL.md 实际确认段落做对比
   - 如果 Step 2.1c 已报告未匹配的 hint → 在报告中引用该 warning，并注明"该确认点依赖兜底规则"
   - 如果补丁阶段全部匹配但此处仍出现 AskUserQuestion → 补丁替换格式可能不够醒目，需改进模板
```

### Step 3.4：链式用例评估 + 接口裂缝发现

链式用例额外检查：
- 上游产出的文件路径是否在下游的"读取"描述中？
- 下游读取上游产出后是否报"格式不兼容"？

**无论链式用例是否实际执行**，必须基于 SKILL.md 静态分析生成接口裂缝 findings：

```markdown
1. 对 WORKFLOW.yaml 中每条 from→to 的正常流转 edge（非 failure/loop_exceeded）
2. 读取 from stage 的 Skill，提取"产出路径"
3. 读取 to stage 的 Skill，提取"输入/读取路径"
4. 比对：
   - 路径完全一致 → pass
   - 路径部分一致（如目录相同、文件名通过约定推导）→ info: "路径靠约定对齐（无显式引用）"
   - 路径中有动态变量（如"项目名称"）且无文档化约定 → warning: "关键接口路径包含未标准化的动态变量"
   - 路径不一致 → warning: "上下游 I/O 路径不匹配"
5. 将这些 findings 正式纳入报告的 Warning Findings 或 Info Findings 中
```

**判定规则**：

| 接口状态 | Severity | 示例 |
|---------|----------|------|
| 路径完全一致 | — （不生成 finding） | `docs/功能设计/功能模块全拆解.md` 同时出现在上游产出和下游输入 |
| 路径靠约定对齐 | info | 上游产出 `docs/xxx-设计.md`，下游描述"扫描 docs/ 下所有设计文档" |
| 动态变量无标准 | warning | 上游产出 `docs/<项目名称>-技术栈设计.md`，约定未在 directory-convention 中定义 |
| 路径不匹配 | warning | 上游产出 `.tmp/result.json`，下游读取 `docs/result.json` |

---

## 审计报告

**报告路径**：`workshop/audit-reports/<workflow_id>-skill-audit.md`

每次审计覆盖同一工作流的旧报告。

```markdown
# Skill 功能审计报告: <workflow_id>@<version>

## 总评

| 指标 | 值 |
|------|-----|
| 测试范围 | 主工作流 / 主+一级子工作流 / 全部嵌套 |
| 审计 Skill 数 | N（主工作流 N 个 + 子工作流 M 个） |
| 总用例数 | N |
| Overall | pass / conditional_pass / fail |

## Skill 汇总

| Skill | 用例数 | Pass | Warn | Fail | 链式 |
|-------|--------|------|------|------|------|
| xxx | 4 | 3 | 1 | 0 | ✅ |

## 详细结果

### <skill_id>

#### 用例 <case_id> —— [pattern] [title]

- **输入**：<简述>
- **预期**：<expected>
- **结果**：pass / warning / fail
- **机械检查**：
  - ✅ output_exists
  - ❌ no_hallucinated_paths —— 引用了 `/fake/config.yaml`
- **AI 评审**：<摘要>
```

---

## 与 workflow-auditor 的协作

```
workflow-designer 产出 WORKFLOW.yaml + skills/
  ↓
workflow-auditor → 审 YAML 状态机 / wfctl / Skill 接口 → 报告 1
  ↓
skill-tester → 实际运行 Skill → 功能正确性 → 报告 2
```

两步跑完，设计质量基本闭环。

## 禁止行为

- 禁止在未读取 WORKFLOW.yaml 的情况下手动指定 Skill——必须自动发现
- 禁止跳过链式测试——上下游 Skill 之间的 I/O 不一致是最常见 bug
- 禁止对 crash 的用例做语义评审——crash 本身即 critical
- 禁止修改被测试的**原始** SKILL.md——只输出报告，不自动修复。CONFIRM 用例的 SKILL.md 打补丁（Step 2.1c）操作的是 worktree 中的临时副本，不影响原始文件
