---
name: module-lifecycle-blindtest
description: >
  盲测执行与失败分类。运行对抗性测试，辅助分类失败类型（实现漏洞/测试缺陷/契约矛盾），
  生成信息隔离的失败摘要和测试缺陷报告，执行回归检查和收敛停滞检测，
  输出分支信号（all-pass/impl-bug/test-defect/regression/max-rounds/stagnation）供 WORKFLOW edges 路由。
  使用场景：作为 module-lifecycle 工作流的 Stage orch-blindtest 被编排器调度执行，
  在测试代码就绪后接管盲测运行和失败分类。
  核心工作方式：全自动的脚本驱动流程——运行测试、分类失败、生成隔离摘要、验证隔离合规、回归检查，
  不涉及用户确认。
  每次调用输出失败摘要（failure-summary-round-{N}.md）、测试缺陷报告（test-defects-round-{N}.md，如适用）
  和分类信号到消息上报。
  必须优先使用本 skill 当编排器需要执行盲测、对抗性测试结果判定、或失败分类分流时。
---

# 模块生命周期盲测执行器

你是 `module-lifecycle` 工作流中 `orch-blindtest` Stage 的执行器。
你的核心职责：在信息隔离屏障下运行对抗性测试，自动分类失败根因，
生成符合隔离要求的失败摘要，并输出分支信号供 WORKFLOW edges 路由。

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-blindtest/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-blindtest/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）

> **零侵入原则**：若本 Skill 无专用契约，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-blindtest`）不一致：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id module-lifecycle-blindtest
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（算法变更、精度降低、功能裁剪）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（分批计算、降采样、稀疏矩阵）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中 Stage `orch-blindtest` 的执行器。

**上游 Stage**：
- `testgen-create`（Skill `module-lifecycle-test-generator`）：交付测试代码文件和测试清单
- `exec-fix`（Skill `module-lifecycle-impl-executor`）：修复后的实现代码，触发重新盲测
- `testgen-fix`（Skill `module-lifecycle-test-generator`）：修正后的测试代码，触发重新盲测

**下游 Stage**（由 WORKFLOW edges 根据本 Skill 产出的分类信号自动路由）：
- **all-pass** → `orch-report`（全部通过，对抗循环终止）
- **max-rounds** → `orch-report`（达到最大轮次上限）
- **stagnation** → `orch-report`（收敛停滞）
- **impl-bug** → `exec-fix`（实现漏洞，进入修复）
- **test-defect** → `testgen-fix`（测试缺陷或契约矛盾，进入测试修正）
- **regression** → `orch-contract`（检测到退化，暂停并重新仲裁）

**上游产物读取**：`upstream_files` 将包含测试代码文件路径、测试清单路径、green-seeking-report.json，以及（从第 2 轮起）上轮盲测产物路径。

---

## 核心执行流程

以下 8 个步骤按顺序执行，不可跳过。本 Skill 的 `confirmation_point = false`，完成后直接上报 `DONE` 并输出分类信号。

### Step 1：测试文件就绪检查

验证上游交付的测试代码文件可运行：

1. **文件存在性**：确认 `upstream_files` 中的测试代码文件在文件系统中实际存在。
2. **语法可解析性**：根据技术栈运行语法检查：
   - Python：`python -m py_compile <test_file>`
   - TypeScript：`npx tsc --noEmit <test_file>`
   - Go：`go vet <test_file>`
3. **导入路径正确性**：验证测试代码能否成功导入被测模块。
   - Python：`python -c "import <test_module>"` 或等效检查
   - TypeScript/JavaScript：检查模块解析路径
   - Go：检查包导入路径

若任何一个检查失败，跳过测试运行，直接进入 Step 3 的分类流程（该错误将被分类为测试缺陷）。

### Step 2：运行对抗性测试

根据项目技术栈自适应选择测试运行器，将完整输出重定向到文件：

```bash
# Python (pytest)
pytest {test_file_path} -v --tb=short > /tmp/pytest-output.txt 2>&1

# TypeScript (vitest)
npx vitest run {test_file_path} > /tmp/pytest-output.txt 2>&1

# Go
cd {module_dir} && go test -v ./... > /tmp/pytest-output.txt 2>&1
```

**技术栈推断优先级**：
1. 检查项目根目录的 `pyproject.toml`、`package.json`、`go.mod`
2. 检查 `upstream_files` 中测试文件的扩展名
3. 检查 `stage_direction` 中是否显式指定

**环境就绪**：
- Python 项目：确保 `pytest` 可在项目环境中调用
- TypeScript 项目：确保 `vitest` 或 `jest` 已安装
- Go 项目：确保 `go` 命令可用，模块依赖已下载

提取测试总数、通过数、失败数作为后续摘要的元数据。

### Step 3：失败分类

首先运行自动分类脚本：

```bash
python scripts/classify_failures.py \
    --test-output /tmp/pytest-output.txt \
    --framework {pytest|jest|go} \
    --contract {contract_expectations_path} \
    --signatures {function_signatures_path} \
    --output /tmp/classification.json
```

脚本将每个失败用例标注为以下四类之一：
- `implementation_bug`：错误指向被测函数的行为缺陷（如参数校验缺失、边界未处理）
- `test_bug`：语法错误、导入错误、断言逻辑自相矛盾
- `contract_contradiction`：测试期望与落地规范/契约清单矛盾
- `uncertain`：无法自动判定

**人工复核要求**（不可跳过）：
- 所有 `confidence < 0.8` 的条目必须人工审查 reason 和原始错误消息
- 所有 `uncertain` 条目必须逐条人工判定
- 复核完成后，将 `uncertain` 条目重新分类为 `implementation_bug` 或 `test_bug`
- 若无法人工判定，保守分类为 `test_bug`（宁可让测试者复查，不可漏掉实现漏洞）

### Step 4：分支信号判定

根据分类结果确定下游路由信号（存入 `metadata.classification_signal`）：

| 场景 | 信号值 | 去向 |
|:---|:---|:---|
| 全部测试通过（失败数 == 0） | `all-pass` | `orch-report` |
| 有失败，且当前轮次 == 最大轮次上限 | `max-rounds` | `orch-report` |
| 有失败，但全部失败被分类为测试缺陷/契约矛盾 | `test-defect` | `testgen-fix` |
| 有失败，且至少一个失败被分类为实现漏洞 | `impl-bug` | `exec-fix` |
| 混合（既有 impl-bug 又有 test-defect） | `impl-bug`（实现漏洞优先） | `exec-fix`，同时生成缺陷报告供后续修正 |

**退化检测**（仅从第 2 轮起适用）：
- 加载上一轮的失败集合（从 `upstream_files` 中的上一轮分类 JSON）
- 检查是否有上一轮通过的用例在本轮失败
- 若检测到退化：信号覆盖为 `regression`，上报时在 `report` 中详细说明退化用例

**收敛停滞检测**（仅从第 2 轮起适用）：
- 比较本轮失败集合与上一轮失败集合
- 若完全一致（无改善也无新增）：信号设为 `stagnation`

### Step 5：生成失败摘要（实现漏洞路径）

仅在信号为 `impl-bug` 或 `max-rounds` 且存在实现漏洞时执行：

```bash
python scripts/generate_failure_summary.py \
    --test-output /tmp/pytest-output.txt \
    --framework {framework} \
    --contract {contract_expectations_path} \
    --signatures {function_signatures_path} \
    --round {N} \
    --max-rounds {max_rounds} \
    --output {module_code_dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

生成 `failure-summary-round-{N}.md`，格式遵循 `references/failure-summary-format.md`。

### Step 6：验证信息隔离合规（阻断级）

对 Step 5 生成的失败摘要执行隔离验证：

```bash
python scripts/validate_failure_summary.py \
    {module_code_dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

**验证检查清单**：
- 包含四个标准章节（失败用例摘要、分类统计、涉及的契约条款、修复方向建议）
- 无多行代码块泄露（仅允许单行内联代码）
- 无测试文件路径暴露（如 `test_*.py`、`*.test.ts`）
- 无 `.tmp/adversarial-tests/` 路径暴露
- 无 pytest/jest 原始输出痕迹
- 每个 case 有涉及函数、契约条款、失败原因、修复建议
- case ID 连续
- 修复建议不泄露具体输入值或暗示查看测试

**若验证失败**：
1. 分析失败原因，修正 `failure-summary-round-{N}.md` 中的违规内容
2. 重新运行 `validate_failure_summary.py`
3. 若连续 3 次验证失败：上报 `ERROR`，`report` 中说明无法生成合规摘要的根因

此步骤为阻断级门控：验证未通过前，摘要不可流入下游。

### Step 7：生成测试缺陷报告（测试缺陷路径）

仅在信号为 `test-defect` 或混合场景下存在测试缺陷时执行。

生成 `test-defects-round-{N}.md` 至 `{module_code_dir}/.tmp/adversarial-tests/{module_id}/`：

```markdown
## 测试缺陷报告（第 {N} 轮）

#### [缺陷-001] {测试函数名}
- **契约条款**：{§N.N}
- **缺陷类型**：{语法错误 / 导入错误 / 断言逻辑矛盾 / 契约矛盾}
- **期望行为**：{根据契约，测试应该如何断言}
- **修复方向**：{具体修复建议，不含代码}
```

**关键约束**（与失败摘要同等严格）：
- 不得包含测试代码片段
- 不得包含具体输入值
- 不得包含测试文件路径
- 只描述"问题是什么"和"期望怎么修"

### Step 8：分类信号输出与上报

构造 message 草稿 JSON，写入 `.tmp/<workflow_instance_id>/`：

**必填字段**（按通用输出契约）：
- `status`：`"DONE"`（`confirmation_point = false`，无用户确认环节）
- `report`：执行摘要（纯文本，无 Markdown 标题语法）
- `upstream_files`：本次实际读取的上游文件路径
- `modified_files`：已覆盖的文件路径
- `draft_files`：临时草稿路径
- `output_files`：新增文件路径（failure-summary-round-{N}.md / test-defects-round-{N}.md）
- `checkpoint_summary`：上下文摘要
- `confirm_required`：`false`
- `confirm_questions`：`[]`

**扩展字段**（存入 `metadata`）：
- `classification_signal`：分支信号值（`all-pass` / `impl-bug` / `test-defect` / `regression` / `max-rounds` / `stagnation`）
- `round`：当前轮次编号
- `convergence_status`：收敛状态（`improving` / `stalled` / `regressed`）
- `test_stats`：`{ "total": N, "passed": N, "failed": N }`
- `classification_counts`：`{ "implementation_bug": N, "test_bug": N, "contract_contradiction": N }`

**收敛状态判定逻辑**：

```
首轮 → improving
连续两轮失败集合完全一致 → stalled
上轮通过的用例本轮失败 → regressed
本轮失败数 < 上轮失败数 → improving
失败数相同但内容不同 → improving
```

调用 `write_message.py` 落盘后，终止执行。编排器将读取 `classification_signal` 并通过 WORKFLOW edges 将流水线路由到正确下游。

---

## 信息隔离铁律

本 Skill 承担对抗性流水线的隔离屏障职责。以下规则不可妥协：

1. **失败摘要不得包含**：测试代码、具体输入值、测试文件路径、测试组织结构、断言具体内容
2. **测试缺陷报告不得包含**：测试代码片段、具体输入值、测试文件路径
3. **validate_failure_summary.py 失败 = 阻断**：不可绕过
4. **所有产物必须位于 `.tmp/` 下**：不可写入项目源代码目录

**信息边界对照**：

| 允许出现在摘要中 | 禁止出现在摘要中 |
|:---|:---|
| 错误类型（TypeError, ValueError 等） | 完整测试代码 |
| 涉及的函数名 | 具体输入值（如 `"abc"`, `-1`, `99999`） |
| 涉及的参数名 | 测试文件路径 |
| 参数期望类型（如 `int (>=1)`） | 测试用例组织结构 |
| 违反的契约条款编号 | 断言的具体内容 |
| 失败原因一句话描述 | 其他测试的通过/失败细节 |
| 修复建议（如"添加参数非空校验"） | 任何暗示查看测试的指导 |

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id module-lifecycle-blindtest`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。本 Skill 的 `confirmation_point = false`，`confirm_required` 设为 `false`，`confirm_questions` 为 `[]`。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。
6. `metadata.classification_signal` 是 WORKFLOW edges 路由的关键字段，必须准确填写。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-blindtest",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional",
  "stage_id": "orch-blindtest",
  "confirmation_point": false,
  "stage_type": "script_call"
}
```
