---
name: module-lifecycle-test-writer
description: >
  为功能模块编写正式验收测试套件。当用户要求"为已实现模块编写测试"、"生成验收测试"、
  "补充测试覆盖"时触发。特别地，当 module-implementation-orchestrator 的对抗循环已完成、
  需要产出进入版本控制的测试套件时使用本 skill。
  核心定位：基于契约和实现代码编写白盒验收测试，确保实现与设计文档一致，并为未来修改
  提供回归防护。与 adversarial-test-generator 互补——后者在对抗循环中黑盒找漏洞，
  本 skill 在对抗循环后白盒写正式测试。
  每次调用输出测试场景清单、验收测试代码和测试报告到 `docs/testing-design/{模块编号}/`。
  必须优先使用本 skill 当用户要求编写验收测试、生成正式测试套件、为模块补充测试覆盖时。
---

# 模块生命周期测试编写器

> **角色：你是一位验收工程师。**
>
> 对抗循环已经结束，实现代码已经通过了盲测的验证。你的任务不是找漏洞——
> 那是 adversarial-test-generator 的职责——而是把已经验证过的实现，用全面、
> 可维护、可运行的验收测试固定下来。
>
> 你的 KPI：**"这份测试套件能否在任何人修改代码时，第一时间发现回归问题？"**

## 与 adversarial-test-generator 的区别

| 维度 | adversarial-test-generator | module-lifecycle-test-writer（本 skill） |
|:---|:---|:---|
| **运行时机** | 对抗循环中（实现尚未验证） | 对抗循环后（实现已通过验证） |
| **信息权限** | 黑盒——禁止读取实现代码 | 白盒——**允许并鼓励读取实现代码** |
| **测试目标** | 找漏洞、破坏约束 | 验证正确性、覆盖契约、回归防护 |
| **输出位置** | 隔离目录（`.tmp/adversarial-tests/`） | 项目测试目录（进入版本控制） |
| **与实现关系** | 对抗 | 协同 |

## 关键心态：测试失败是你的朋友

本 skill 的核心陷阱：**把"测试全部通过"当作成功的标志。**

实际上：
- 测试在**第一次运行时全部通过** → 警报 —— 可能测试太宽泛，或没有真正验证契约
- 测试因**精确断言而失败** → 好消息 —— 测试发现了实现与契约的细微差异
- 测试因**宽泛断言而通过** → 失败 —— 测试没有发挥保护作用

**你的任务不是"写出能通过的测试"，而是"写出能发现错误的测试"。**

特别地：
- `pytest.raises(Exception)` 让测试"太容易通过"——任何崩溃都算通过
- `pytest.raises(ValidationError, match="...")` 让测试"可能失败"——但这正是它的价值
- 如果精确断言导致测试失败，**不要退回到宽泛断言**——记录差异、修正 match、保持精确

---

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-test-writer/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-test-writer/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）：工作流级共享参考目录和文件列表
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-test-writer`）不一致：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（变更测试策略、裁剪场景类型、降低断言强度）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（跳过可选的外部依赖测试、减少集成场景）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中 Group 4（Test Writing）的执行器，负责 5 个 Stage 的执行。

**入点 Stage**：`testw-prep`（遗留审查与契约读取）

**Stage 列表**（按执行顺序）：
| Stage ID | 名称 | confirmation_point | 行为 |
|:---|:---|:---|:---|
| `testw-prep` | 遗留审查与契约读取 | true（条件） | 实现与设计文档存在重大差异时需确认 |
| `testw-scenario` | 测试场景提取 | false | 直接完成 |
| `testw-code` | 验收测试编写 | false | 直接完成 |
| `testw-verify` | 静态自检与运行验证 | true（条件） | 测试期望与契约规定不一致时需确认 |
| `testw-report` | 测试报告输出 | false | 直接完成 |

**上游依赖**：orchestrator 的对抗循环产物（`contract-expectations.md`、`function-signatures.json`），通过 `upstream_files` 注入。

**下游消费**：本 Skill 产出的测试文件进入版本控制，测试报告供后续审查 Stage 使用。

**输入（按 stage_id 路由）**：
- 用户指令（含模块编号或名称）
- 落地规范：`{模块名}-落地规范.md`（契约来源）
- 模块设计文档：`{模块名}-设计文档.md`（辅助理解意图与边界）
- 项目结构设计文档：`xxx-项目结构.md`（确定测试文件位置）
- 技术栈设计文档：`xxx-技术栈设计.md`（确定测试框架）
- **已实现代码**：`src/` 或项目结构文档指定的模块代码目录
- （可选）契约期望清单：`contract-expectations.md`（若已由 orchestrator 生成）
- （可选）函数签名清单：`function-signatures.json`（若已由 orchestrator 生成）

**产物输出目录**：`docs/testing-design/{模块编号}/`
- 测试文件：按项目结构设计文档的测试目录规范存放
- `test-scenarios.md`：测试场景清单
- `test-report.md`：测试报告

---

## 阶段执行规范

本 Skill 每次被调度时，根据注入的 `stage_id` 执行对应阶段的逻辑。各阶段产物应写入一致的输出路径，使后续阶段可通过文件系统读取前置产物。

---

### Stage testw-prep —— 遗留审查与契约读取

**确认点**：条件触发（仅当实现与设计文档存在重大差异时上报 `PENDING_CONFIRM`）

#### 1. 遗留问题审查（如适用）

如果本次是对已有测试的迭代更新，在开始任何新工作前，先检查并修复上一轮评估中标记的问题：

| 检查项 | 常见表现 | 处理方式 |
|:---|:---|:---|
| 宽泛异常断言 | `pytest.raises(Exception)` | 全部替换为具体异常类型 |
| 注释与代码不一致 | docstring 描述的行为与断言不匹配 | 修正 docstring 或修正代码 |
| 元数据头部不准确 | 头部数字不等于实际场景数 | 修正头部 |
| 存在性验证替代路径测试 | 仅有 `callable(fn)` / `isinstance` | 补充行为路径测试 |

**门控规则**：遗留问题未修复完毕前，不允许进入后续阶段。先修好旧测试，再写新测试。

#### 2. 读取设计文档

按优先级读取：

| 优先级 | 文档 | 用途 |
|:---|:---|:---|
| P0 | 落地规范 | 提取契约：类型定义、异常条件、状态机、边界约束 |
| P1 | 设计文档 | 理解业务意图、模块边界、依赖关系 |
| P2 | 项目结构文档 | 确定测试文件存放位置、模块 import 路径 |
| P3 | 技术栈文档 | 确定测试框架（pytest / jest / vitest / Go testing） |

#### 3. 读取实现代码（白盒——关键差异）

读取被测模块的实现代码，关注：
- 公开函数的签名（与落地规范对比，若有差异记录待确认项）
- 内部分支逻辑（if/else、switch、try/except）
- 状态转换实现
- 副作用（I/O、数据库、外部调用）
- 依赖注入点（哪些外部依赖需要 mock）

#### 4. 实现与设计差异比对

- **轻微差异**（如字段名不同但语义一致）：按实现编写测试，在后续报告中标注
- **重大差异**（如缺少契约要求的异常处理、核心状态转移缺失）：触发确认点

#### 5. 复用 orchestrator 产物（如存在）

若 `docs/testing-design/{module_id}/` 下已存在 orchestrator 生成的文件：
- `contract-expectations.md` — 直接复用，作为测试场景的基础
- `function-signatures.json` — 直接复用，验证实现与签名一致

若不存在，自行从落地规范中提取契约。

#### 6. 条件确认逻辑

```
if 存在重大差异:
    生成 message 草稿，设置 status: "PENDING_CONFIRM"
    confirm_questions 示例：
      - "落地规范要求 {X}，但实现代码中 {Y}。是否按实现代码编写测试？"
      - "实现缺少契约规定的异常处理 {Z}，是否仍按契约编写测试（预期失败）？"
    调用 write_message.py 上报，终止等待编排器恢复
else:
    完成任务，上报 status: "DONE"
```

---

### Stage testw-scenario —— 测试场景提取

**确认点**：无

从 testw-prep 阶段已读取的契约和实现中提取四类测试场景，写入《测试场景清单》。

#### 场景分类

| 场景类型 | 来源 | 示例 |
|:---|:---|:---|
| **正常路径（Happy Path）** | 落地规范的 Given-When-Then | 有效输入 → 期望输出 |
| **边界路径** | 契约中的数值/长度/格式约束 | 最小值、最大值、空字符串、空数组 |
| **错误路径** | 契约中的异常触发条件 | 非法参数 → 期望异常类型和消息 |
| **集成路径** | 设计文档中的模块联动 | 多个接口的顺序调用、状态传递 |

#### 输出格式

写入 `docs/testing-design/{模块编号}/test-scenarios.md`：

```markdown
# 测试场景清单
# 模块：{模块编号}-{模块名称}
# 来源：{落地规范文件名} + 实现代码
# 生成时间：{YYYY-MM-DD HH:MM:SS}

## 正常路径

| 编号 | 被测函数 | 输入 | 期望输出 | 契约依据 | 实现分支 |
|:---|:---|:---|:---|:---|:---|
| H01 | validate_chapter_id | "ch-02" | 通过，返回规范化 ID | §CLIInput | 主分支 |

## 边界路径

| 编号 | 被测函数 | 输入 | 期望输出 | 契约依据 | 实现分支 |
|:---|:---|:---|:---|:---|:---|
| B01 | score_pipeline | score=60 | 状态=CONTINUE | §核心逻辑 | 边界分支 |

## 错误路径

| 编号 | 被测函数 | 输入 | 期望异常 | 契约依据 | 实现分支 |
|:---|:---|:---|:---|:---|:---|
| E01 | validate_chapter_id | "02" | ValidationError | §CLIInput | 异常分支 |

## 集成路径

| 编号 | 被测函数序列 | 场景 | 期望结果 | 契约依据 |
|:---|:---|:---|:---|:---|
| I01 | init → validate → process | 完整工作流 | 正常完成 | §编排逻辑 |
```

#### 覆盖度目标

- 每个公开函数至少有一个**行为路径测试**（存在性验证不能替代）
- 每个契约约束至少有一个边界或错误路径测试
- 每个异常条件至少有一个触发测试
- 实现中的每个独立分支至少被一个测试覆盖

完成后上报 `status: "DONE"`。

---

### Stage testw-code —— 验收测试编写

**确认点**：无

基于 testw-scenario 产出的《测试场景清单》，按白盒方式编写验收测试代码。

#### 测试函数结构

每个测试函数的 docstring 中**必须**包含：

```python
def test_validate_chapter_id_rejects_missing_prefix():
    """验证 chapter_id 缺少 "ch-" 前缀时抛出 ValidationError。

    场景编号：E01
    契约依据：§CLIInput — chapter_id 必须符合 "ch-NN" 格式
    实现分支：src/validator.py:42-48
    """
    ...
```

三个必填注释字段：`场景编号`、`契约依据`、`实现分支`。

#### 断言标准（摘要）

每个测试必须满足以下**至少一项**强断言：

| 强度 | 要求 | 示例 |
|:---|:---|:---|
| **值断言** | 断言具体字段值 | `assert result.status == "COMPLETED"` |
| **结构断言** | 断言数据结构符合 schema 且有业务意义 | `assert "地理环境" in result.content` |
| **行为断言** | 断言副作用发生 | `assert mock_neo4j.query.call_count == 1` |
| **异常断言** | 断言特定异常类型和消息 | `with pytest.raises(ValidationError, match="...")` |

**禁止以下弱断言单独使用**：
- `assert result is not None`
- `assert len(result) > 0`
- `assert mock_func.called`（不结合业务结果）
- `assert True` 或任何恒真式
- `assert x in (a, b, c)` 用于核心业务结果
- `with pytest.raises(Exception)` —— 必须使用具体异常类型

完整的断言强度标准、反例模板和路径覆盖自检规则参见 `references/assertion-standards.md`。

#### 异常断言强制要求

在任何异常测试中，**先确认具体异常类型再编写断言**：

```python
# 禁止：过于宽泛
with pytest.raises(Exception):
    some_function()

# 强制：使用具体异常类型
with pytest.raises(ValidationError, match="chapter_id 必须以 ch- 开头"):
    CLIInput(chapter_id="02")
```

> 如果 `match="..."` 导致测试失败，这是测试在正确地做它的工作。记录为"实现差异"，修正 match 字符串以匹配实际实现，绝不退回到 `pytest.raises(Exception)`。

#### Mock 规范

- 外部依赖必须 mock，每个 mock 需注释说明被 mock 的模块和替换条件
- mock 返回值应模拟真实行为，不简单返回空值
- **禁止用 mock 代替被测接口的调用**，mock 只能替换外部依赖

#### 测试文件头部元数据

```python
# ============================================================
# 验收测试
# 来源模块：{模块编号}-{模块名称}
# 来源文档：{落地规范文件名} v{版本号}
# 生成时间：{YYYY-MM-DD HH:MM:SS}
# 生成者：module-lifecycle-test-writer
# 覆盖场景数：{N}（正常 {H} + 边界 {B} + 错误 {E} + 集成 {I}）
# 测试场景清单：docs/testing-design/{模块编号}/test-scenarios.md
# ============================================================
```

#### 路径覆盖自检

写完测试后，对每个条件路由函数（返回枚举值的函数）检查：
1. 列出所有可能的返回值
2. 确认每个返回值至少被覆盖一次
3. 不允许用存在性验证替代路径覆盖

测试代码编写样例参见 `references/test-example.md`。

完成后上报 `status: "DONE"`。

---

### Stage testw-verify —— 静态自检与运行验证

**确认点**：条件触发（仅当测试期望与契约规定不一致时上报 `PENDING_CONFIRM`）

#### 1. 语法与导入检查

```bash
# Python
python -m py_compile <test_file.py>
python -c "import <test_module>"

# TypeScript
npx tsc --noEmit <test_file.ts>
```

#### 2. 趋绿扫描（建议性自检，非阻塞）

```bash
python <skill-path>/scripts/detect_green_seeking.py \
  <test_file.py> \
  --sut-module <module_prefix>
```

**注意**：此处趋绿扫描为**建议性自检**，非阻塞。若发现问题：
- 明确的代码质量问题（如异常吞咽、恒真式）：修复
- 误报（如合理的泛型断言）：记录说明，继续

#### 3. 运行测试

```bash
# Python
pytest <test_file.py> -v

# TypeScript
npx vitest run <test_file>

# Go
go test -v ./...
```

#### 4. 结果处理

| 结果 | 处理方式 |
|:---|:---|
| 全部通过 | 正常，进入下一阶段 |
| 部分失败 | 分析原因：测试代码 bug → 修复测试；实现与契约不符 → 记录为"实现缺陷"，不修改测试断言；测试期望与契约矛盾 → 触发确认点 |
| 无法运行（环境缺失） | 记录为"环境依赖"，在报告中说明如何补全 |

**核心原则**：测试是对契约的实现，不是对当前代码的妥协。如果实现不符合契约，测试应该失败——这是测试的价值所在。

#### 5. 条件确认逻辑

```
if 测试期望与契约规定不一致:
    生成 message 草稿，设置 status: "PENDING_CONFIRM"
    confirm_questions 示例：
      - "测试期望 {X}，但契约规定 {Y}。应以哪个为准？"
      - "契约中约束 {Z} 的实现与测试断言矛盾，请确认正确的行为。"
    调用 write_message.py 上报，终止等待编排器恢复
else:
    完成任务，上报 status: "DONE"
```

---

### Stage testw-report —— 测试报告输出

**确认点**：无

#### 1. 数量一致性门控（强制阻塞）

在输出任何文件之前，运行：

```bash
python <skill-path>/scripts/validate_header_consistency.py \
  <test_file.py> \
  docs/testing-design/{module_id}/test-scenarios.md
```

**若脚本返回非零退出码，阻塞交付**。修正测试文件头部数字后重新运行。

该脚本自动：
1. 从测试文件头部提取声明的场景数
2. 从场景清单文件统计实际场景数（按编号行计数）
3. 对比两者，不一致时报错并给出正确数字

#### 2. 测试报告

报告路径：`docs/testing-design/{module_id}/test-report.md`

报告必须包含以下章节：
- **概要**：测试框架、覆盖场景数、运行结果
- **契约覆盖度**：按场景类型分类统计
- **实现差异记录**：轻微差异和重大缺陷分别标注
- **运行结果**：测试命令输出摘要及失败分析（如有）
- **待确认项**：需用户介入的问题
- **诚实声明**：说明测试编写原则和差异处理方式

完整报告模板参见 `references/report-template.md`。

#### 3. 完成

完成后上报 `status: "DONE"`。

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id`（`module-lifecycle-test-writer`）已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

### 条件确认点上报

本 Skill 的 `testw-prep` 和 `testw-verify` 为条件确认点（`confirmation_point=true, confirmation_conditional=true`）：

**当确认条件触发时**：
1. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`
2. 设置 `confirm_questions`（1-4 个具体、可回答的问题）
3. 调用 `write_message.py` 上报
4. 终止执行，等待编排器恢复

**当确认条件未触发时**：
直接上报 `status: "DONE"`（标准流程）。

确认问题必须基于本 Skill 的产出内容提问，不能是泛泛的"是否继续？"。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-test-writer",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["planning", "core", "extension"],
  "autonomous_degradation": false,
  "checkpoint_policy": "conditional"
}
```

---

## 参考文件

| 文件 | 内容 | 何时阅读 |
|:---|:---|:---|
| `references/test-example.md` | 验收测试代码样例（正常/边界/错误/集成路径，含推导映射速查表） | Stage testw-code |
| `references/report-template.md` | 测试报告完整模板（含契约覆盖度、实现差异记录、诚实声明） | Stage testw-report |
| `references/quality-checklist.md` | 自检动作记录清单（含常见问题模式与处理指南） | Stage testw-verify |
| `references/assertion-standards.md` | 断言强度标准详解（含禁止模式、异常断言模板、路径覆盖自检规则） | Stage testw-code |
| `references/mock-standards.md` | Mock 规范详解（含注释模板、替换条件、禁止模式） | Stage testw-code |
