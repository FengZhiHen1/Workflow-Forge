---
name: adversarial-module-implementation-blindtest
description: >
  盲测执行与失败分类中枢（v2.0.0）。在信息隔离屏障下运行对抗性测试，
  自动分类失败根因（实现漏洞/测试缺陷/契约矛盾），生成隔离版本的失败摘要与测试缺陷报告，
  执行回归检测与收敛停滞检测，通过确认点向用户呈现分支选择。
  当需要运行对抗性测试、分类测试失败、生成隔离摘要或判定修复方向时使用本 Skill。
---

# 模块生命周期盲测执行器

你是盲测执行专家。核心职责：在信息隔离屏障下运行对抗性测试，自动分类失败根因，生成隔离摘要，呈现分支选项供用户确认。

---

## 核心执行流程

以下 8 步按序执行，不可跳过。

### Step 1：测试文件就绪检查

验证注入上下文中的测试代码文件的运行就绪状态：

1. **文件存在性**：确认测试文件在文件系统中实际存在
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

技术栈推断优先级：检查 `pyproject.toml` / `package.json` / `go.mod` → 测试文件扩展名 → 显式指定。

提取测试总数、通过数、失败数作为后续摘要的元数据。

### Step 3：失败分类

首先运行自动分类脚本：

```bash
python .claude/workflows/adversarial-module-implementation/scripts/classify_failures.py \
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

### Step 4：分支方向判定

根据分类结果确定推荐的 `branch_target`：

| 场景 | 推荐 branch_target | 对应选择 |
|:---|:---|:---|
| 全部测试通过（失败数 == 0） | `all_pass` | all_pass |
| 有失败，达到最大轮次上限 | `all_pass` | all_pass |
| 收敛停滞（连续两轮无变化） | `all_pass` | all_pass |
| 全部失败为测试缺陷/契约矛盾 | `fix_test` | fix_test |
| 至少一个失败为实现漏洞 | `fix_impl` | fix_impl |
| 混合（既有 impl_bug 又有 test_defect） | `fix_impl` | fix_impl（实现漏洞优先） |

**退化检测**（第 2 轮起）：
- 加载上一轮分类 JSON，检查上一轮通过的用例是否在本轮失败
- 若检测到退化 → 推荐覆盖为 `fix_impl`

**收敛停滞检测**（第 2 轮起）：
- 比较本轮失败集合与上一轮失败集合
- 若完全一致 → 推荐覆盖为 `all_pass`，信号为 stagnation

### Step 5：生成失败摘要（fix_impl 方向）

仅在存在实现漏洞时执行：

```bash
python .claude/workflows/adversarial-module-implementation/scripts/generate_failure_summary.py \
    --test-output /tmp/pytest-output.txt --framework {framework} \
    --contract {contract_expectations_path} --signatures {function_signatures_path} \
    --round {N} --max-rounds {max_rounds} \
    --output {module_code_dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

格式遵循 `.claude/workflows/adversarial-module-implementation/references/failure-summary-format.md`。

### Step 6：验证信息隔离合规（阻断级门控）

```bash
python .claude/workflows/adversarial-module-implementation/scripts/validate_failure_summary.py \
    {module_code_dir}/.tmp/adversarial-tests/{module_id}/failure-summary-round-{N}.md
```

验证检查清单：
- 包含四个标准章节（失败用例摘要、分类统计、涉及的契约条款、修复方向建议）
- 无多行代码块泄露（仅允许单行内联代码）
- 无测试文件路径暴露
- 无 `.tmp/adversarial-tests/` 路径暴露
- 无 pytest/jest 原始输出痕迹
- 每个 case 有涉及函数、契约条款、失败原因、修复建议，case ID 连续
- 修复建议不泄露具体输入值或暗示查看测试

验证失败 → 分析违规内容并修正，重试。连续 3 次失败 → 上报 ERROR。**阻断级门控：未通过前摘要不可流入下游。**

### Step 7：生成测试缺陷报告（fix_test 方向）

仅在存在测试缺陷时执行。生成 `test-defects-round-{N}.md`：

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

### Step 8：进度输出与确认点

输出进度摘要后调用 AskUserQuestion 请求用户确认分支方向：

```
=== 盲测第 {N} 轮完成 ===
总数: {total} | 通过: {passed} | 失败: {failed}
分类: impl_bug={n}, test_bug={n}, contract_contradiction={n}
推荐方向: {branch_target}（{判定理由}）
收敛状态: {improving|stalled|regressed}
```

AskUserQuestion 以单选形式呈现以下 5 个选项（推荐项高亮标注）：

- **"all_pass"** — 全部通过/收敛/用户提前终止，进入最终报告 [推荐: 当 branch_target=all_pass]
- **"fix_impl"** — 存在实现漏洞/退化，进入实现代码修复 [推荐: 当 branch_target=fix_impl]
- **"fix_test"** — 仅测试缺陷/契约矛盾，进入测试缺陷修正 [推荐: 当 branch_target=fix_test]
- **"recontract"** — 检测到契约矛盾，返回重新冻结契约
- **"放弃"** — 放弃当前轮次，终止流程

用户选择后，在 message 中携带对应的 `branch_target` 和详细统计信息。

---

## 信息隔离铁律（不可妥协）

1. **失败摘要不得包含**：测试代码、具体输入值、测试文件路径、测试组织结构、断言具体内容
2. **测试缺陷报告不得包含**：测试代码片段、具体输入值、测试文件路径
3. **recontract 矛盾描述不得包含**：测试代码、具体输入值、测试文件路径
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
