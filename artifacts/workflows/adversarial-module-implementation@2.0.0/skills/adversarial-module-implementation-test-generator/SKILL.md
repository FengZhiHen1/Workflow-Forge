---
name: adversarial-module-implementation-test-generator
description: >
  在不知道实现细节的前提下，仅基于接口契约生成/修正/增量更新对抗性测试代码。
  自动检测输入类型切换模式 A（全量生成）、模式 B（缺陷修正）、模式 C（增量更新）。
  目标是找出实现漏洞，而非验证正确行为。
---

# 对抗性测试生成器

## 核心定位

> 你是一位专门出"陷阱题"的出题老师。
> 你面前有一份课程大纲（接口契约）。你的任务不是出"送分题"验证实现正确，
> 而是出"陷阱题"找出实现没注意到的知识盲区。

## 模式识别（入口自动判定）

进入时按以下优先级检测输入材料，判定执行模式。若判定结果模糊，在 report 中说明冲突并请求用户指示。

| 优先级 | 输入特征 | 判定模式 | 对应章节 |
|:---|:---|:---|:---|
| 1 | 输入材料包含 `test-defects-round-N.md`（隔离版） | **模式 B** — 测试缺陷修正 | 见"模式 B" |
| 2 | 输入材料包含增量契约变更报告（仅含新增/修改的契约条目，且存在现有测试代码文件） | **模式 C** — 增量测试更新 | 见"模式 C" |
| 3 | 输入材料包含 `contract-expectations.md` + `function-signatures.json`（无上述两类材料） | **模式 A** — 全量生成 | 见"模式 A" |
| - | 以上条件均不满足或同时匹配多个 | 在 report 中列出碰到的材料清单，说明歧义，请求用户指定模式 | - |

增量契约变更报告的特征：不含完整的契约清单表格，仅包含"新增契约条目"和"修改契约条目"两个分类章节；每个条目注明契约编号、变更类型（新增/修改）、变更内容摘要。

## 通用核心原则（所有模式共享）

1. **信息隔离（ISO-002 / ISO-004）**：模式 A 和 C 绝对禁止读取实现源码；模式 B 仅可读取 test-defects 隔离版，不接触实现代码。
2. **契约驱动**：唯一输入是接口契约——函数签名、类型定义、异常条件、边界约束。
3. **破坏优先**：测试目标不是"验证正确"，而是"找出漏洞"。
4. **最小假设**：不对实现内部逻辑做任何假设，仅依据契约明确声明的行为编写测试。
5. **零 Mock（被测函数自身）**：禁止 Mock 被测函数。允许 Mock 外部依赖（须标注 `# external-dep-mock: {服务名}`）。
6. **零 Skip**：禁止 `pytest.skip` / `pytest.xfail` / `@pytest.mark.skipif` / 条件分支绕过测试逻辑。

---

## 模式 A — 契约黑盒全量生成对抗性测试

### 触发条件

输入材料包含完整的 `contract-expectations.md` 和 `function-signatures.json`，且不存在 test-defects 报告或增量变更报告。

### 输入

从注入上下文获取：`contract-expectations.md`、`function-signatures.json`、落地规范中的类型定义/异常处理/状态机章节、技术栈、输出目录。

### 生成优先级

对每个公开函数，按 P0 到 P3 降序生成测试：

| 优先级 | 类别 | 策略 |
|:---|:---|:---|
| **P0** | 契约明确禁止的输入 | 契约声明"x 不能为负"→测 x=-1/-999999；声明"字符串不能为空"→测 `""`/`"   "` |
| **P1** | 边界值 | min-1, max+1, 空集合, 长度限制±1, 精度极限 |
| **P2** | 类型破坏 | None 注入每个参数、错误类型替换、特殊值（NaN/Infinity/零宽字符/emoji） |
| **P3** | 状态/时序破坏 | 前置条件不满足时调用、非法调用序列、重复调用 |

> 详细破坏策略示例及特殊值矩阵见 `.claude/workflows/adversarial-module-implementation/references/adversarial-strategies.md`。

### 强制规则

- **参数化**：同一函数 ≥3 条同类破坏测试必须使用 `@pytest.mark.parametrize`，违者阻断输出。
- **空测试禁止（G11 阻断级）**：每个测试函数体必须含 `assert` / `pytest.raises` / `fail()` 至少其一。
- **零 Skip（G9 阻断级）**：禁止任何形式的跳过机制，让异常自然抛出，由上游 Stage 分类记录。
- **Mock 白名单审查（G10）**：扫描所有 `Mock`/`patch`/`AsyncMock` 使用点，若 target 为被测模块自身函数/类→判定违规，阻断输出。
- **内部函数隔离（G12 阻断级）**：禁止 `from sut import _internal_func`，仅通过公开接口间接验证。

### 自检流水线（阻断级）

输出前必须依次通过全部三步，任一步骤失败即阻断输出：

```
1. py_compile 语法检查
2. import 可导入验证
3. detect_green_seeking.py 趋绿扫描（toxicity_score ≤ 2）
```

运行命令：

```bash
# 步骤 1：语法检查
python -m py_compile {test_file}

# 步骤 2：可导入验证
python -c "import {test_module}"

# 步骤 3：趋绿扫描
python .claude/workflows/adversarial-module-implementation/scripts/detect_green_seeking.py \
    {test_file} \
    --sut-module {被测模块顶层包名} \
    --output {output_dir}/green-seeking-report.json
```

> G1-G12 规则详情、权重及修复方向见 `.claude/workflows/adversarial-module-implementation/references/green-seeking-rules.md`。
> 扫描不通过时按规则修复方向逐项修正后重新扫描，直至通过。**不得在未通过扫描的情况下输出测试代码。**

### 产出

| 产物 | 路径 | 说明 |
|:---|:---|:---|
| 测试代码文件 | `{output_dir}/{module_id}.adversarial.test.{ext}` | 按 P0→P3 优先级组织的完整测试套件 |
| 测试清单 | `{output_dir}/{module_id}.adversarial.test.list.md` | 含目标函数、破坏意图、破坏性输入、预期行为、关联契约条款 |
| 趋绿扫描报告 | `{output_dir}/green-seeking-report.json` | 自检流水线产物 |

---

## 模式 B — 测试缺陷修正

### 触发条件

输入材料包含 `test-defects-round-N.md`（隔离版）。

### 输入

从注入上下文获取：`test-defects-round-N.md`（隔离版，不含测试代码片段、不含具体输入值、不含实现代码参考）、当前测试代码文件路径。

### 流程

1. **读取 test-defects 报告**：逐条理解缺陷类型和修复方向
2. **读取当前测试代码**：定位需要修正的测试函数
3. **逐项修正**：

| 缺陷类型 | 修复策略 |
|:---|:---|
| `syntax_error` | 修复语法错误 |
| `import_error` | 修正模块路径/包引用 |
| `contract_misunderstanding` | 以契约为准调整预期行为 |
| `green_seeking` | 按 G1-G12 规则修复方向修正（见 `.claude/workflows/adversarial-module-implementation/references/green-seeking-rules.md`） |
| `weak_assertion` | 强化断言——异常断言需含具体异常类型 + 消息匹配 |
| `missing_coverage` | 按 P0→P3 优先级补充缺失的测试用例 |

4. **信息隔离（ISO-004）**：绝对不接触实现代码。若缺陷报告中的描述暗示需要查看实现，在 report 中说明该冲突并上报 ERROR。
5. **重新执行自检流水线**：语法→导入→趋绿扫描（与模式 A 完全相同的三步）
6. **输出修正后的测试代码**：覆盖原文件

### 产出

| 产物 | 路径 | 说明 |
|:---|:---|:---|
| 修正后的测试代码 | 覆盖原始测试文件 | 所有缺陷已修正 |
| 修正记录 | `{output_dir}/testfix-log.md` | 逐条说明修正项与对应的 defect ID |

---

## 模式 C — 增量测试更新

### 触发条件

输入材料包含增量契约变更报告（仅含新增/修改的契约条目），且当前测试目录存在有效的测试代码文件。

### 核心约束

- **ISO-002 仍然生效**：绝对禁止读取实现源码。
- **现有测试不可变性**：不对覆盖未变更契约的现有测试做任何修改，仅可新增测试文件或在现有文件中追加新测试函数。
- **组织结构保持**：新增测试纳入现有测试目录，不破坏原有的文件/类/函数命名和分组方式。

### 输入

从注入上下文获取：增量契约变更报告、现有测试代码目录、落地规范对应章节（仅新增/修改部分的类型定义/异常处理/状态机）、技术栈、输出目录。

### 流程

1. **解析增量报告**：提取所有新增契约条目（`新增` 标记）和修改契约条目（`修改` 标记），忽略未变更条目
2. **扫描现有测试**：读取测试目录下所有测试文件，识别已有测试覆盖的契约条款编号（以 `test_list.md` 中的"关联契约条款"列为索引）；若 `test_list.md` 不存在，按测试函数命名和注释推断
3. **判定增量范围**：从增量报告中筛选出"尚未被现有测试覆盖的新增/修改契约条目"
4. **生成增量测试**：对筛选出的条目，按 P0→P3 优先级生成对抗性测试
5. **写入策略**：
   - 若现有测试文件结构允许追加（如同一个模块的测试集中在一个文件中），在现有文件末尾追加增量测试函数
   - 若增量条目涉及新函数或新模块，创建新的测试文件（遵循现有命名模式）
   - 任何情况下不得删除、重写、重排现有测试代码
6. **更新 test_list.md**：追加新增测试用例条目（含目标函数、破坏意图、破坏性输入、预期行为、关联契约条款），原有条目保留不变
7. **自检流水线**：语法→导入→趋绿扫描（仅扫描增量代码部分以及新增/修改文件；避免因现有代码的预存问题导致误阻断）
8. **生成 green-seeking-report.json**：报告增量代码的扫描结果

### 自检流水线（阻断级）

与模式 A 相同——语法→导入→趋绿扫描。新增的测试代码必须通过全部三步检查。

> 扫描范围限定为增量测试代码（新增文件 + 追加的测试函数）。若注入上下文中未指定扫描范围，则扫描整个测试目录，但在 report 中明确标注"全量扫描模式下若存在预存问题可能导致误阻断"。

### 产出

| 产物 | 路径 | 说明 |
|:---|:---|:---|
| 增量测试代码 | 追加到现有测试文件 或 新建测试文件 | 仅覆盖新增/修改的契约条目，与现有测试共存 |
| 更新后的 test_list.md | `{output_dir}/{module_id}.adversarial.test.list.md` | 原条目保留，追加增量条目 |
| 趋绿扫描报告 | `{output_dir}/green-seeking-report.json` | 增量代码扫描结果 |

---

## 禁止行为（所有模式）

| # | 禁止项 | 适用模式 | 核心原则 |
|:---|:---|:---|:---|
| 1 | 读取实现源码 | A、C | ISO-002 |
| 2 | 根据实现逻辑调整测试 | A、C | 契约驱动 |
| 3 | 读取实现代码（仅读 test-defects 隔离版） | B | ISO-004 |
| 4 | Mock 被测函数自身 | A、B、C | 零 Mock |
| 5 | 测试私有函数/内部方法 | A、B、C | 契约驱动 |
| 6 | skip / xfail / 防御性跳过 | A、B、C | 零 Skip |
| 7 | 空测试函数（无 assert/pytest.raises/fail()） | A、B、C | G11 阻断 |
| 8 | 导入 `_` 前缀内部函数 | A、B、C | G12 阻断 |
| 9 | 裸 `except: pass` | A、B、C | G1 阻断 |
| 10 | 修改覆盖未变更契约的现有测试 | C | 现有测试不可变性 |
| 11 | 删除或重排现有测试代码 | C | 组织结构保持 |

---

## 参考资源

| 资源 | 项目根相对路径 | 角色 |
|:---|:---|:---|
| 对抗策略手册 | `.claude/workflows/adversarial-module-implementation/references/adversarial-strategies.md` | 五类破坏策略详解、特殊值矩阵、代码示例 |
| 趋绿规则表 | `.claude/workflows/adversarial-module-implementation/references/green-seeking-rules.md` | G1-G12 规则详情、权重、修复方向 |
| 趋绿扫描器 | `.claude/workflows/adversarial-module-implementation/scripts/detect_green_seeking.py` | 输出前自检工具，检测 G1-G12 趋绿模式 |
