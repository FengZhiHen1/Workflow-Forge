---
name: module-lifecycle-test-generator
description: >
  在不知道实现细节的前提下，仅基于接口契约生成对抗性测试代码，并根据盲测缺陷报告修复测试缺陷。
  目标是找出实现漏洞，而非验证正确行为。测试者不得接触实现源码，仅依据契约中的接口声明编写测试。
  使用场景：(1) 模块实现完成、接口契约就绪后，需要生成黑盒对抗性测试进行盲测验证；
  (2) 盲测阶段发现测试代码缺陷（语法错误、契约理解偏差、趋绿模式），需要修复测试代码。
  核心工作方式：基于契约对每个公开函数生成边界/类型/状态/资源/时序五类破坏性测试，
  并通过内置趋绿扫描器自检，确保测试质量。每次调用根据 stage_id 路由到测试生成或缺陷修正模式。
  必须优先使用本 skill 当用户要求"对抗性测试"、"盲测"、"黑盒测试"、"破坏性测试"、"adversarial test"时。
---

# 模块生命周期测试生成器

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-test-generator/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-test-generator/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）

> **零侵入原则**：若本 Skill 无专用契约，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`：上游产物文件路径列表
- `upstream_message_ids`（可选）：上游消息 ID 列表
- `stage_direction`：工作方向指令，优先级最高
- `special_instructions`（可选）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与 `module-lifecycle-test-generator` 不一致：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id module-lifecycle-test-generator
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（跳过某类破坏策略、降低覆盖率目标、放宽断言标准）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（减少参数化组合数量、限制超大输入测试的规模）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中两个 Stage 的执行器。

**Stage testgen-create（对抗性测试生成）**
- 上游 Stage：`orch-impl-validate` — 产物为函数签名清单 + 接口契约
- 下游 Stage：`orch-blindtest` — 消费对抗性测试代码执行盲测
- 触发条件：始终触发（`orch-impl-validate` 完成后无条件进入）

**Stage testgen-fix（测试缺陷修正）**
- 上游 Stage：`orch-blindtest` — 产物为缺陷报告（JSON）
- 下游 Stage：`orch-blindtest` — 消费修正后的测试代码，形成盲测循环
- 触发条件：盲测发现测试缺陷时触发（循环上限 3 次）

---

## Stage 路由

根据编排器注入的 `stage_id` 选择执行模式：

| `stage_id` | 执行模式 | 对应章节 |
|:---|:---|:---|
| `testgen-create` | 对抗性测试生成 | [生成流程](#生成流程testgen-create) |
| `testgen-fix` | 测试缺陷修正 | [缺陷修正流程](#缺陷修正流程testgen-fix) |

---

## 核心原则

> **角色：你是一位专门出"陷阱题"的出题老师。**
> 你面前有一份课程大纲（接口契约）。你的任务不是出"送分题"验证实现正确，
> 而是出"陷阱题"找出实现没注意到的知识盲区。

1. **信息隔离（铁律）**：绝对禁止读取实现源码。不要查看、不要搜索、不要分析被测模块的实现文件。此规则在 `testgen-create` 和 `testgen-fix` 两个阶段同样适用。
2. **契约驱动**：唯一输入是接口契约——函数签名、输入/输出类型、异常条件、前置条件、状态约束。
3. **破坏优先**：测试目标不是"验证正确"，而是"找出漏洞"。
4. **最小假设**：不对实现的内部逻辑做任何假设。只根据契约中明确声明的行为编写测试。
5. **无 Mock（修订版）**：测试代码不得使用 mock 替代被测函数本身。
   - 禁止：mock 被测函数的内部逻辑、返回值或副作用
   - 允许：mock 外部依赖（HTTP 客户端、数据库连接、第三方 API），但必须在测试注释中标注 `# external-dep-mock: {服务名}`
   - 允许：使用 `pytest.Monkeypatch` 修改环境变量/配置
   - **Mock 白名单审查**：输出前自检时，扫描所有 `Mock`、`patch`、`AsyncMock` 使用点，若其 target 包含被测模块自身的函数/类，判定为 G10（被测函数 Mock）错误，阻断输出。
6. **零跳过（Zero Skip）**：对抗性测试中禁止任何形式的跳过机制。
   - 禁止 `pytest.skip`、`unittest.skip`、`pytest.xfail`、`@pytest.mark.skip`、`@pytest.mark.skipif`
   - 禁止条件分支绕过测试逻辑（如 `if not available: return/pass`）
   - 禁止因"模块可能不存在"而做的防御性导入包裹
   - 如果某条契约因外部依赖无法在当前环境验证，测试代码仍应完整编写并直接导入，让异常自然抛出，由上游 Stage 分类记录，而非由测试代码自行跳过

## 输入

在 `testgen-create` 模式下，从 `upstream_files` 中读取：
- **函数签名清单**：模块暴露的所有公开函数/方法，含参数名、类型、返回值类型
- **类型契约**：Pydantic model / TypeScript interface / Go struct / Zod schema 等类型定义
- **异常条件**：落地规范中定义的异常触发条件
- **前置条件**：函数调用前必须满足的状态/条件
- **边界定义**：落地规范中明确的数值边界、长度限制、格式约束
- **技术栈**：项目使用的测试框架

在 `testgen-fix` 模式下，额外读取：
- **缺陷报告**（JSON）：盲测发现的问题列表，含缺陷类型、位置、描述和建议

## 输出

- 对抗性测试代码文件（存放到指定的隔离目录）
- 测试清单 Markdown（每个测试的破坏意图说明）
- 趋绿扫描报告（JSON，路径：`{output_dir}/green-seeking-report.json`）

## 对抗策略

> 详细的五类破坏策略示例、特殊值矩阵和技术栈适配代码见工作流级参考文件
> `references/adversarial-strategies.md`。以下为核心策略摘要。

### 1. 边界值破坏
- 数值：最小值-1、最大值+1、0（当最小值为正时）
- 字符串：空字符串 `""`、仅空白 `"   "`、恰好长度限制、长度+1、超长（>10KB）
- 数组/列表：空数组 `[]`、单元素、恰好长度限制、长度+1、超大数组
- 对象：空对象 `{}`、缺少必填字段、多余字段

### 2. 类型破坏
- `None` / `null` / `undefined` 注入每个参数（无论是否可选）
- 错误类型替换（字符串传 int、数组传对象、布尔传字符串）
- 特殊数值：`NaN`、`Infinity`、`-Infinity`、极大/极小浮点数
- 特殊字符串：`\x00`、Unicode 控制字符、emoji、RTL 文本、零宽字符

### 3. 状态破坏
- 前置条件未满足时调用函数
- 非法状态转换序列
- 重复调用同一操作（幂等性破坏）
- 并发/重入场景（如适用技术栈）

### 4. 资源破坏
- 超大输入（大数据量、深度嵌套 JSON）
- 循环引用（对象自我引用）
- 极端比例数据（大量记录的列表）

### 5. 时序破坏
- 快速连续调用
- 乱序调用（违反操作顺序）
- 超时/延迟注入（如适用）

---

## 生成流程（testgen-create）

### 步骤 1：解析契约

从输入的函数签名清单和类型契约中，提取每个函数的：
- 参数列表及类型
- 返回值类型
- 显式声明的异常条件（触发条件 + 异常类型）
- 前置条件
- 边界约束（数值范围、长度限制、格式正则）

**输入来源优先级**：
1. 落地规范的「输入/输出类型定义」章节
2. 落地规范的「异常处理」章节
3. 落地规范的「状态机」章节（提取状态约束）
4. 设计文档的「接口契约」章节

### 步骤 2：生成对抗性测试

对每个函数，按以下优先级生成测试：

**优先级 P0：契约明确禁止的输入**
- 若契约声明"参数 x 不能为负数"，则测试 x = -1, x = -999999
- 若契约声明"字符串不能为空"，则测试 `""`、`"   "`
- 若契约声明"数组长度不超过 N"，则测试长度 N+1 的数组

**优先级 P1：边界值**
- 数值边界：min-1, min, max, max+1
- 长度边界：0, 1, max_length, max_length+1
- 时间边界：epoch, far_future
- 精度边界：浮点数精度极限

**优先级 P2：类型破坏**
- None 注入每个参数（包括非可选参数）
- 错误类型替换每个参数
- 特殊数值/字符串注入

**优先级 P3：状态/时序破坏**
- 前置条件不满足
- 非法调用序列
- 重复调用

**参数化强制规则**：
对于同一函数的多条同类边界破坏测试，必须使用 `@pytest.mark.parametrize`（Python）、`it.each`（TypeScript）或 `describe.each` 合并。若对同一函数生成 3 个以上仅输入值不同的测试且未使用参数化，判定为结构违规，阻断输出。

### 步骤 3：输出测试代码

按项目技术栈输出测试文件，命名规范：`{module_id}.adversarial.test.{ext}`

适配技术栈：
- **Python**：pytest，使用 `@pytest.mark.parametrize` 批量注入破坏性输入
- **TypeScript**：vitest / jest，使用 `it.each` 或 `describe.each`
- **Go**：标准 testing + testify/assert

**空测试函数禁止**：
每个对抗性测试函数体内必须包含至少一个以下元素：
- `assert` 语句
- `pytest.raises(...)` 上下文
- `pytest.fail(...)` 调用
- `unittest.TestCase.fail(...)` 调用

禁止生成仅含注释、条件分支、`pass` 或变量赋值但无上述元素的测试函数（由 G11 强制）。

**断言强度标准**：

| 强度 | 要求 | 示例 |
|:---|:---|:---|
| 异常断言 | 断言特定异常类型和消息 | `with pytest.raises(ValueError, match="limit must be positive")` |
| 值断言 | 断言具体返回值 | `assert result == []` |
| 结构断言 | 断言返回数据结构符合预期 | `assert isinstance(result, ErrorResponse)` |
| 行为断言 | 断言副作用发生/未发生 | `assert mock_logger.error.call_count == 1` |

**禁止单独使用的弱断言**：
`assert result is not None` / `assert len(result) > 0` / `assert True` / 不结合具体契约条款的模糊断言

测试结构模板：

```python
# 参数化合并同类破坏（优先使用）
@pytest.mark.parametrize(
    "bad_input,expected_exc",
    [
        ("", ValueError),      # 空字符串
        ("ch-", ValueError),   # 格式残缺
        ("02", ValueError),    # 缺失前缀
    ],
)
def test_func_name_rejects_invalid_input(bad_input, expected_exc):
    """破坏意图：验证函数拒绝所有非法输入格式"""
    with pytest.raises(expected_exc):
        func_under_test(bad_input)

# 单条契约的独立测试（仅当无法参数化合并时使用）
def test_func_name_type_corruption():
    """破坏意图：验证函数在收到错误类型时的行为"""
    with pytest.raises(TypeError):
        func_under_test(None)
```

**Python 包完整性**：
测试输出目录为 Python 包时，必须同时创建有效的 `__init__.py`：
- 文件大小 > 0（不得为空文件）
- 包含包级 docstring（说明本包用途）
- 如有跨测试文件共享的辅助函数/fixture，在此定义

### 步骤 4：输出测试清单

生成 Markdown 清单文件 `{module_id}.adversarial.test.list.md`，说明每个测试的：
- 目标函数
- 破坏意图
- 破坏性输入
- 预期行为（根据契约应抛出的异常或返回的结果）
- 关联的契约条款

### 步骤 5：输出前自检（强制）

生成测试代码后、输出前，必须依次执行。

#### 5.1 前置条件检查（先决阻断）

1. **语法正确**：`python -m py_compile {test_file}` 通过
2. **包完整性**：`__init__.py` 存在且非空（`len(content.strip()) > 0`）
3. **可直接导入**：`python -c "import {test_module}"` 通过

任一前置条件失败，直接阻断输出，无需运行趋绿扫描。

#### 5.2 趋绿扫描

```bash
python scripts/detect_green_seeking.py \
    {output_test_file} \
    --sut-module {被测模块顶层包名} \
    --output {output_dir}/green-seeking-report.json
```

**通过标准**：`toxicity_score <= 2`

**扫描规则速查**：

| Rule | 说明 | 权重 |
|:---|:---|:---|
| G1 | 异常吞咽（裸 `except: pass`） | 3 |
| G2 | 构造-断言（assert 出现在被测调用之前，或无被测调用） | 3 |
| G3 | 宽泛断言（`assert ... in (a, b, c)` 备选值 > 2） | 1 |
| G4 | 纯存在性断言（仅 `is not None` / `len > 0`） | 2 |
| G5 | 标准库测试（直接测标准库行为，无被测调用） | 2 |
| G6 | 纯 Mock 验证（仅 `assert_called*`，无业务断言） | 2 |
| G7 | 自我赋值断言（赋值后立即断言同一变量，无被测调用介入） | 3 |
| G8 | 恒真式欺骗（`or True` / `and False`） | 3 |
| G9 | 防御性跳过（`if condition: return/pass`） | **3（阻断级）** |
| G10 | 核心接口偏离（Evil Stub 测试未调用核心业务函数） | 2 |
| G11 | 空测试（函数体无 `assert` / `pytest.raises` / `fail()`） | **3（阻断级）** |
| G12 | 内部函数导入（从被测模块导入 `_` 前缀函数） | **3（阻断级）** |

**不通过时的处理**：
1. 读取 JSON 报告中的 `suspects` 列表
2. 按 `rule_id` 分类修复：
   - **G1**：移除裸 `except: pass`，改为 `pytest.raises(具体异常类型)`
   - **G3**：将 `assert a or b or c` 改为精确匹配契约要求的异常消息关键词
   - **G5**：删除不调用被测函数的测试，或补充被测函数调用
   - **G8**：删除 `exc_info.value is not None` 等恒真断言
   - **G9**：移除所有 `if condition: return/pass` 模式，改为完整执行测试逻辑
   - **G11**：补充 `assert` 或 `pytest.raises`，或删除无意义测试
   - **G12**：删除 `from sut import _internal_func`，改为通过公开接口间接验证
3. 修复后重新运行扫描，直到通过
4. **不得在未通过扫描的情况下输出测试代码**

#### 5.3 报告输出

自检通过后，将扫描报告（JSON）输出到指定目录。报告需包含：
- `toxicity_score`
- 按 `rule_id` 分类的 `suspects` 列表
- `is_clean: true/false`

---

## 缺陷修正流程（testgen-fix）

当 `stage_id == "testgen-fix"` 时执行此流程。

### 输入

从 `upstream_files` 中读取缺陷报告（JSON），格式示例：

```json
{
  "defects": [
    {
      "defect_id": "D001",
      "file": "path/to/test_file.py",
      "line": 42,
      "type": "syntax_error | import_error | contract_misunderstanding | green_seeking | weak_assertion | missing_coverage",
      "description": "...",
      "suggestion": "..."
    }
  ]
}
```

### 修正流程

1. **读取缺陷报告和现有测试代码**
2. **逐项分析缺陷**：
   - `syntax_error`：修复语法错误
   - `import_error`：修正模块路径、包引用
   - `contract_misunderstanding`：重新审阅上游传入的契约文档，以契约为准调整测试的预期行为
   - `green_seeking`：按 G1-G12 规则修复对应的趋绿模式
   - `weak_assertion`：按断言强度标准强化断言
   - `missing_coverage`：按 P0-P3 优先级补充遗漏的边界/类型/状态破坏测试
3. **信息隔离**：修正过程中仍然不得读取实现源码
4. **修复后重新执行步骤 5（自检）**：确保修正后的代码通过语法检查、导入检查和趋绿扫描
5. **输出修正后的测试代码和更新后的清单**

### 缺陷修正约束

- 缺陷修正不改变测试的对抗性质——仍然是寻找实现漏洞
- 若缺陷报告指出某测试与契约矛盾，以契约为准修正测试
- 若缺陷涉及新增破坏场景，按生成流程中的优先级（P0-P3）补充
- 修正后必须重新通过趋绿扫描（`toxicity_score <= 2`）
- 若缺陷报告要求的修改与核心原则冲突（如要求读取实现源码），在 `report` 中说明冲突并上报 `ERROR`

---

## 好测试标准

| 质量维度 | 好的对抗性测试 | 坏的对抗性测试 |
|:---|:---|:---|
| 意图清晰 | 测试名称明确说明"破坏什么"（如 `test_rejects_negative_limit`） | 名称模糊（如 `test1`、`test_edge_case`） |
| 契约锚定 | 每个测试都关联到具体的契约条款 | 凭感觉编写的测试，无契约依据 |
| 最小独立 | 一个测试只破坏一个约束条件 | 一次测试多个不相关的约束 |
| 可解释性 | 失败时错误信息能让人一眼看出"哪条契约被违反" | 失败信息晦涩，需要调试才能理解 |
| 确定性 | 同样的输入每次运行都产生同样的结果 | flaky 测试 |
| 通过自检 | `detect_green_seeking.py` 通过（toxicity_score ≤ 2） | 输出前未自检 |

## 禁止行为

| 禁止项 | 原因 |
|:---|:---|
| 读取实现源码 | 破坏信息隔离，失去对抗性 |
| 根据实现逻辑调整测试 | 测试会偏向实现而非契约 |
| 测试契约允许的正常行为 | 那是验收测试的职责 |
| 使用 mock 替代被测函数 | 无法发现真实漏洞 |
| 测试私有函数/内部方法 | 只关注公开接口契约 |
| 猜测未声明的行为 | 只能依据明确契约生成测试 |
| 编写与契约矛盾的测试 | 会导致测试误报，浪费修复迭代 |
| 一个测试破坏多条不相关约束 | 失败时无法定位具体漏洞 |
| 裸 `except Exception: pass` | 吞掉所有异常，无法区分契约期望异常与实现漏洞 |
| 使用"或"断言宽容匹配异常消息 | 掩盖实现未精确处理的事实 |
| 断言 `exc_info.value is not None`（pytest.raises 后） | 恒真式，不提供任何信息 |
| 测试标准库/语言内置行为而非被测系统 | 发现不了被测系统的漏洞 |
| 断言中包含冗余"或"分支放宽条件 | 放宽通过条件，掩盖漏洞 |
| `pytest.skip` / `unittest.skip` / `pytest.xfail` | 制造虚假收敛，掩盖真实的模块结构漂移或契约未覆盖 |
| 导入以 `_` 开头的内部函数/模块 | 破坏信息隔离（G12） |
| 测试函数体中无 `assert` / `pytest.raises` / `fail()` | 空测试恒真（G11） |
| `__init__.py` 为空文件或缺失 | pytest 包发现失败 |

## 质量检查清单

- [ ] 每个公开函数至少有一个对抗性测试
- [ ] 每个异常条件至少有一个触发测试
- [ ] 每个边界至少有一个越界测试
- [ ] 没有测试依赖于实现细节
- [ ] 测试代码可以在隔离目录独立运行
- [ ] 测试清单完整标注了每个测试的破坏意图
- [ ] 无弱断言单独使用
- [ ] 无与契约矛盾的测试
- [ ] 无 `pytest.skip` / `unittest.skip` / `pytest.xfail`
- [ ] 无空测试函数（所有测试含 `assert` 或 `pytest.raises` 或 `fail()`）
- [ ] 无内部函数导入（未从被测模块导入 `_` 前缀函数）
- [ ] 同类边界测试已使用参数化合并（≥3 个同类输入必须参数化）
- [ ] `__init__.py` 存在且非空
- [ ] 运行 `scripts/detect_green_seeking.py` 通过（toxicity_score ≤ 2，无 G9/G11/G12）

---

## 参考资源

- **对抗性测试策略手册**：`references/adversarial-strategies.md`（工作流级共享）——五类破坏策略详解、特殊值矩阵、技术栈适配代码示例
- **趋绿扫描器**：`scripts/detect_green_seeking.py`（工作流级共享）——输出前自检工具，检测 G1-G12 类趋绿模式

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id module-lifecycle-test-generator`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. 本 Skill 对应的两个 Stage（`testgen-create`、`testgen-fix`）均为 `confirmation_point=false`。完成任务后直接上报 `status: "DONE"`，无需用户确认。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-test-generator",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["planning", "core", "extension"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
