---
name: adversarial-module-implementation-blindtest
description: >
  盲测执行与失败分类中枢（v1.1.0）。运行对抗性测试，辅助分类失败类型（实现漏洞/测试缺陷/契约矛盾），
  生成信息隔离的失败摘要与测试缺陷报告，执行回归/收敛检测，输出 branch_target 信号供 WORKFLOW edges 路由。
  每轮输出进度摘要，支持用户提前终止或契约矛盾时返回重新仲裁（recontract）。对应 Stage：s05-blindtest（adversarial-module-implementation@1.1.0）。
---

# 模块生命周期盲测执行器

你是 `adversarial-module-implementation@1.1.0` 工作流中 `s05-blindtest` Stage 的执行器。
核心职责：在信息隔离屏障下运行对抗性测试，自动分类失败根因，生成隔离摘要，输出分支信号路由至下游。

**上游 Stage**：s04-testgen（测试代码交付）、s06-fix（修复后实现代码）、s07-testfix（修正后测试代码）

**下游路由**（由 `branch_target` 字段决定，编排器据此选择 WORKFLOW edge）：

| `branch_target` | 信号值 | 目标 Stage | 触发条件 |
|:---|:---|:---|:---|
| `success` | `all-pass` | s08-report | 全部测试通过 |
| `success` | `max-rounds` | s08-report | 达到最大轮次上限 |
| `success` | `stagnation` | s08-report | 收敛停滞 |
| `success` | `user-terminated` | s08-report | 用户提前终止 |
| `fix_impl` | `impl-bug` | s06-fix | 存在实现漏洞 |
| `fix_impl` | `regression` | s06-fix | 检测到退化 |
| `fix_test` | `test-defect` | s07-testfix | 仅测试缺陷/契约矛盾（默认） |
| `recontract` | `recontract` | s01-init | 用户选择重新仲裁契约 |

---

## 核心执行流程

以下 8 步按序执行，不可跳过。本 Skill 的 `confirmation_point = false`，流程全自动运行。

### Step 1：测试文件就绪检查

验证 `upstream_files` 中测试代码文件的运行就绪状态：

1. **文件存在性**：确认 `upstream_files` 中的测试文件在文件系统中实际存在
2. **语法可解析性**：按技术栈运行语法检查 —— Python `py_compile` / TypeScript `tsc --noEmit` / Go `go vet`
3. **导入路径正确性**：验证测试代码能否成功导入被测模块

任一检查失败 → 跳过测试运行，直接进入 Step 3（该失败归类为测试缺陷）。

### Step 2：运行对抗性测试

根据项目技术栈自适应选择运行器，完整输出重定向到文件：

```bash
# Python
pytest {test_file} -v --tb=short > /tmp/pytest-output.txt 2>&1
# TypeScript
npx vitest run {test_file} > /tmp/pytest-output.txt 2>&1
# Go
cd {module_dir} && go test -v ./... > /tmp/pytest-output.txt 2>&1
```

技术栈推断优先级：检查 `pyproject.toml` / `package.json` / `go.mod` → 测试文件扩展名 → `stage_direction` 显式指定。

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

分类标签：`implementation_bug` / `test_bug` / `contract_contradiction` / `uncertain`

**人工复核**（不可跳过）：
- `confidence < 0.8` 的条目 → 逐条人工审查 reason 和原始错误消息
- `uncertain` 条目 → 逐条人工判定，重新分类为 `implementation_bug` 或 `test_bug`
- 无法人工判定 → 保守分类为 `test_bug`（宁可让测试者复查，不可漏掉实现漏洞）

### Step 4：分支信号判定

根据分类结果确定默认 `branch_target` 和信号值：

| 场景 | branch_target | 信号值 |
|:---|:---|:---|
| 全部测试通过（失败数 == 0） | `success` | `all-pass` |
| 有失败，当前轮次 == 最大轮次上限 | `success` | `max-rounds` |
| 全部失败被分类为测试缺陷/契约矛盾 | `fix_test` | `test-defect` |
| 至少一个失败被分类为实现漏洞 | `fix_impl` | `impl-bug` |
| 混合（既有 impl-bug 又有 test-defect） | `fix_impl` | `impl-bug`（实现漏洞优先，同时生成缺陷报告） |

**退化检测**（第 2 轮起）：
- 加载上一轮分类 JSON，检查上一轮通过的用例是否在本轮失败
- 若检测到退化 → 信号覆盖为 `regression`，`branch_target = fix_impl`

**收敛停滞检测**（第 2 轮起）：
- 比较本轮失败集合与上一轮失败集合
- 若完全一致（无改善也无新增） → 信号设为 `stagnation`，`branch_target = success`

> **契约矛盾默认路径**：`contract_contradiction` 分类默认归入 `fix_test`，流转至 s07-testfix。用户可在 Step 8 选择覆盖为 `recontract`。

### Step 5：生成失败摘要（impl-bug / max-rounds 路径）

仅在 `branch_target = fix_impl` 或 `max-rounds` 且存在实现漏洞时执行：

```bash
python scripts/generate_failure_summary.py \
    --test-output /tmp/pytest-output.txt --framework {framework} \
    --contract {contract_expectations_path} --signatures {function_signatures_path} \
    --round {N} --max-rounds {max_rounds} \
    --output {dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

格式遵循 `references/failure-summary-format.md`，产物仅供 s06-fix 的 SubAgent 读取。

### Step 6：验证信息隔离合规（阻断级门控）

对 Step 5 生成的失败摘要执行隔离验证：

```bash
python scripts/validate_failure_summary.py \
    {dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

验证检查清单：
- 包含四个标准章节（失败用例摘要、分类统计、涉及的契约条款、修复方向建议）
- 无多行代码块泄露（仅允许单行内联代码）
- 无测试文件路径暴露（如 `test_*.py`、`*.test.ts`）
- 无 `.tmp/adversarial-tests/` 路径暴露
- 无 pytest/jest 原始输出痕迹
- 每个 case 有涉及函数、契约条款、失败原因、修复建议，case ID 连续
- 修复建议不泄露具体输入值或暗示查看测试

验证失败 → 分析违规内容并修正，重试。连续 3 次失败 → 上报 ERROR。**阻断级门控：未通过前摘要不可流入下游。**

### Step 7：生成测试缺陷报告（test-defect 路径）

仅在 `branch_target = fix_test` 或混合场景存在测试缺陷时执行。
生成 `test-defects-round-{N}.md`：

```
## 测试缺陷报告（第 {N} 轮）
#### [缺陷-001] {测试函数名}
- 契约条款：{§N.N}  |  缺陷类型：{语法错误/导入错误/断言逻辑矛盾/契约矛盾}
- 期望行为：{根据契约，测试应该如何断言}
- 修复方向：{具体修复建议，不含代码}
```

关键约束（与失败摘要同等严格）：
- 不得包含测试代码片段
- 不得包含具体输入值
- 不得包含测试文件路径
- 只描述"问题是什么"和"期望怎么修"

### Step 8：进度输出、提前终止、recontract 选项与信号输出

**每轮进度摘要**（每轮结束时输出，不阻塞流程）：

```
=== 盲测第 {N} 轮完成 ===
总数: {total} | 通过: {passed} | 失败: {failed}
分类: impl_bug={n}, test_bug={n}, contract_contradiction={n}
判定方向: {branch_target} → {target_stage}
收敛状态: {improving|stalled|regressed}
```

**可选操作提示**（根据当前状态在进度摘要后追加）：

| 状态条件 | 追加提示 |
|:---|:---|
| 存在 `contract_contradiction` | `【可选】检测到契约矛盾，是否返回重新仲裁契约？选择后将携带矛盾条款清单回流至 s01-init。` |
| 非最终轮次且 `failed > 0` | `【可选】跳过剩余修复，直接生成报告。` |

**用户提前终止**：若用户选择"跳过剩余修复，直接生成报告" → 信号覆盖为 `user-terminated`，`branch_target = success`，直接流转至 s08-report。

**契约重仲裁（recontract）**：若用户选择"返回重新仲裁契约" → 信号覆盖为 `recontract`，`branch_target = recontract`，流转至 s01-init。此时必须在 `branch_context` 中携带 `conflict_items`：

```json
{
  "conflict_items": [
    {
      "contract_clause": "§2.3",
      "function": "process_order",
      "description": "契约声明参数不可为null，但测试发现传入null时期望行为未定义"
    }
  ]
}
```

`conflict_items` 生成规则：
- 从 `contract_contradiction` 分类的失败中提取，每条矛盾一个条目
- `contract_clause`：违反的契约条款编号（如 `§3.1`）
- `function`：涉及的函数名
- `description`：矛盾描述，仅说明契约声明与观测到的行为冲突，不得包含测试代码、具体输入值、测试文件路径

**输出 message 必含字段**：
- `status`：`"DONE"`
- `branch_target`：`"success"` / `"fix_impl"` / `"fix_test"` / `"recontract"`
- `classification_signal`：具体信号值
- `round`：当前轮次编号
- `convergence_status`：收敛状态
- `test_stats`：`{ total, passed, failed }`
- `classification_counts`：`{ implementation_bug, test_bug, contract_contradiction }`
- `output_files`：生成的文件路径列表（failure-summary-round-{N}.md / test-defects-round-{N}.md）
- `branch_context`：当 `branch_target = recontract` 时必填，格式见上文；其他情况可省略

**收敛状态判定**：
- 首轮 → `improving`
- 连续两轮失败集合完全一致 → `stalled`
- 上轮通过的用例本轮失败 → `regressed`
- 本轮失败数 < 上轮失败数 → `improving`
- 失败数相同但内容不同 → `improving`

---

## 信息隔离铁律（不可妥协）

1. **失败摘要不得包含**：测试代码、具体输入值、测试文件路径、测试组织结构、断言具体内容
2. **测试缺陷报告不得包含**：测试代码片段、具体输入值、测试文件路径
3. **recontract 的 `branch_context.conflict_items` 不得包含**：测试代码、具体输入值、测试文件路径
4. **validate_failure_summary.py 失败 = 阻断**：不可绕过
5. **所有产物必须位于 `.tmp/` 下**：不可写入项目源代码目录

### 信息边界对照表

| 允许出现在摘要中 | 禁止出现在摘要中 |
|:---|:---|
| 错误类型（TypeError, ValueError 等） | 完整测试代码 |
| 涉及的函数名 | 具体输入值（如 `"abc"`, `-1`, `99999`） |
| 涉及的参数名 | 测试文件路径（如 `test_*.py`, `*.test.ts`） |
| 参数期望类型（如 `int (>=1)`） | 测试用例组织结构 |
| 违反的契约条款编号 | 断言的具体内容 |
| 失败原因一句话描述 | 其他测试的通过/失败细节 |
| 修复建议（如"添加参数非空校验"） | 任何暗示查看测试的指导 |

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "adversarial-module-implementation-blindtest",
  "version": "1.1.0",
  "stage_id": "s05-blindtest",
  "confirmation_point": false,
  "stage_type": "script_call",
  "task_modes": ["core"],
  "autonomous_degradation": false
}
```
