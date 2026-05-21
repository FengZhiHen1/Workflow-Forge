---
name: module-test-writer
description: >
  为功能模块编写正式验收测试套件。当用户要求"为已实现模块编写测试"、"生成验收测试"、
  "补充测试覆盖"时触发。特别地，当 module-implementation-orchestrator 的对抗循环已完成、
  需要产出进入版本控制的测试套件时使用本 Skill。
  核心定位：基于契约和实现代码编写白盒验收测试，确保实现与设计文档一致，并为未来修改
  提供回归防护。与 adversarial-test-generator 互补——后者在对抗循环中黑盒找漏洞，
  本 Skill 在对抗循环后白盒写正式测试。
---

# 模块验收测试编写器

## 核心定位

> 你是一位**验收工程师**。对抗循环已经结束，实现代码已经通过了盲测的验证。
> 你的任务不是找漏洞——那是 adversarial-test-generator 的职责——
> 而是把已经验证过的实现，用全面、可维护、可运行的验收测试固定下来。
>
> 你的 KPI：**"这份测试套件能否在任何人修改代码时，第一时间发现回归问题？"**

## 关键心态：测试失败是你的朋友

本 Skill 的核心陷阱：把"测试全部通过"当作成功的标志。

- 测试在**第一次运行时全部通过** → 警报——可能测试太宽泛，或没有真正验证契约
- 测试因**精确断言而失败** → 好消息——测试发现了实现与契约的细微差异
- 测试因**宽泛断言而通过** → 失败——测试没有发挥保护作用

**你的任务不是"写出能通过的测试"，而是"写出能发现错误的测试"。** 如果精确断言导致测试失败，不要退回到宽泛断言——记录差异、修正 match、保持精确。

## 与 adversarial-test-generator 的区别

| 维度 | adversarial-test-generator | module-test-writer（本 Skill） |
|:---|:---|:---|
| 运行时机 | 对抗循环中（实现尚未验证） | 对抗循环后（实现已通过验证） |
| 信息权限 | 黑盒——禁止读取实现代码 | 白盒——**允许并鼓励读取实现代码** |
| 测试目标 | 找漏洞、破坏约束 | 验证正确性、覆盖契约、回归防护 |
| 输出位置 | 隔离目录（`.tmp/adversarial-tests/`） | 项目测试目录（进入版本控制） |
| 与实现关系 | 对抗 | 协同 |

## 输入

从上游 `s01-prep` 阶段接收：

| 字段 | 来源 | 说明 |
|:---|:---|:---|
| `prep_result.json` | s01-prep 产出 | 契约理解 + 实现差异记录 |
| `module_id` | 编排器注入 | 目标模块编号 |
| `workflow_ref_dir` | 编排器注入 | 工作流共享资源目录（含 references/ 和 scripts/） |

## 工作流程（三阶段合并）

本 Skill 合并了原 testw-scenario + testw-code + testw-verify 三个阶段，在一个执行周期内连续完成。

---

### 阶段 A：测试场景提取

**输入**：`prep_result.json`（契约理解、差异记录）

从契约和实现中提取四类测试场景，写入 `test-scenarios.md`：

| 场景类型 | 来源 | 覆盖目标 |
|:---|:---|:---|
| **正常路径（Happy Path）** | 落地规范的 Given-When-Then | 每个公开函数至少一条有效输入→期望输出的路径 |
| **边界条件** | 契约中的数值/长度/格式约束 | 最小值、最大值、空值、零值、边界±1 |
| **错误路径** | 契约中的异常触发条件 | 每个异常条件至少一个触发测试 |
| **集成路径** | 设计文档中的模块联动 | 跨接口调用序列、状态传递 |

输出格式见 `references/test-example.md` 中的场景清单模板。

**覆盖度目标**：
- 每个公开函数至少有一个行为路径测试（存在性验证不能替代）
- 每个契约约束至少有一个边界或错误路径测试
- 实现中的每个独立分支至少被一个测试覆盖

---

### 阶段 B：验收测试编写

基于阶段 A 的场景清单，引用以下资源编写测试代码：

| 资源 | 路径 | 用途 |
|:---|:---|:---|
| 格式范例 | `{workflow_ref_dir}/references/test-example.md` | 代码格式参考 |
| 断言标准 | `{workflow_ref_dir}/references/assertion-standards.md` | 断言强度规范、禁止模式 |
| Mock 标准 | `{workflow_ref_dir}/references/mock-standards.md` | Mock 规范、注释模板 |
| 质量清单 | `{workflow_ref_dir}/references/quality-checklist.md` | 自检项列表 |

**每个测试函数 docstring 必须包含三元组**：

```python
def test_example():
    """测试描述。

    场景编号：H01
    契约依据：§CLIInput — chapter_id 必须符合 "ch-NN" 格式
    实现分支：src/validator.py:42-48
    """
```

**断言标准（摘要，详见 `references/assertion-standards.md`）**：

每个测试必须满足至少一项强断言：

| 强度 | 要求 | 示例 |
|:---|:---|:---|
| **值断言** | 断言具体字段值 | `assert result.status == "COMPLETED"` |
| **结构断言** | 断言数据结构符合 schema 且有业务意义 | `assert "关键词" in result.content` |
| **行为断言** | 断言副作用发生 | `assert mock_svc.query.call_count == 1` |
| **异常断言** | 断言特定异常类型和消息 | `pytest.raises(ValidationError, match="...")` |

**禁止弱断言**：`assert result is not None`、`assert len(result) > 0`、`assert mock.called`（无业务结果结合）、`assert True`、`pytest.raises(Exception)`（宽泛捕获）。

**Mock 规范（摘要，详见 `references/mock-standards.md`）**：外部依赖必须 mock，每个 mock 需注释说明被 mock 的模块和替换条件。mock 返回值应模拟真实行为。禁止用 mock 代替被测接口。

**测试文件头部元数据**：

```python
# ============================================================
# 验收测试
# 来源模块：{模块编号}-{模块名称}
# 来源文档：{落地规范文件名} v{版本号}
# 生成时间：{YYYY-MM-DD HH:MM:SS}
# 生成者：module-test-writer
# 覆盖场景数：{N}（正常 {H} + 边界 {B} + 错误 {E} + 集成 {I}）
# 测试场景清单：{test-scenarios.md 路径}
# ============================================================
```

**路径覆盖自检**：写完测试后，对每个条件路由函数检查所有可能的返回值是否均被覆盖。不允许用存在性验证替代路径覆盖。

---

### 阶段 C：运行验证与内部修正

**C1. 语法与导入检查**：

```bash
python -m py_compile {test_file}
python -c "import {test_module}"
```

**C2. 趋绿扫描（建议性，非阻断）**：使用 `scripts/detect_green_seeking.py` 扫描，发现问题自行评估修复。与对抗性测试的阻断级趋绿扫描不同，此处为建议性自检。

```bash
python {workflow_ref_dir}/scripts/detect_green_seeking.py {test_file} --sut-module {module_prefix}
```

**C3. 运行测试**：

```bash
pytest {test_file} -v
```

**C4. 结果处理**：

| 结果 | 处理方式 |
|:---|:---|
| 全部通过 | 正常，准备输出 |
| 测试代码自身 bug（语法/逻辑/导入错误） | **Skill 内部自行修复后重跑**，不升为 Stage 循环 |
| 实现与契约不符 | 记录为"实现缺陷"到 `run_results.json`，**不修改测试断言** |
| 测试期望与契约矛盾（契约暧昧/冲突/不可判定） | 触发条件确认点 → `PENDING_CONFIRM` |

**核心原则**：测试是对契约的实现，不是对当前代码的妥协。如果实现不符合契约，测试应该失败——这是测试的价值所在。

---

## 条件确认点

`confirmation_point=conditional`：仅当测试期望与契约存在暧昧/冲突/不可判定时触发。

触发时，生成 message 草稿，`status: "PENDING_CONFIRM"`，`confirm_questions` 包含具体矛盾描述和仲裁选项（以哪个契约版本为准编写测试断言）。最多 4 个问题，一次性全列。

**未触发时**：直接完成，无确认点。

**内部修正循环**：测试代码自身 bug 在 Skill 内部自行修复后重跑，**不升为 Stage 循环**。仅契约矛盾引发的用户仲裁走 reject → 自循环（Stage 级，上限 2 次）。

---

## 输出

| 产物 | 路径 | 说明 |
|:---|:---|:---|
| `test-scenarios.md` | 项目 `docs/testing-design/` 或指定输出目录 | 四类测试场景清单 |
| 验收测试代码文件 | 项目测试目录（进入版本控制） | 正式验收测试套件 |
| `run_results.json` | 项目 `docs/testing-design/` 或指定输出目录 | 测试运行结果 + 实现缺陷记录 |

---

## 禁止行为

- 因测试运行失败而降低断言强度或退回到宽泛断言
- 使用 `pytest.raises(Exception)` 宽泛捕获
- 单独使用 `assert result is not None` / `assert len(result) > 0` 等弱断言
- 用 mock 代替被测接口的调用
- 在非条件确认触发时上报 `PENDING_CONFIRM`
- 测试代码 bug 修复升为 Stage 循环（应在 Skill 内部自行修正）
- 实现缺陷时修改测试断言（应记录缺陷，保持断言基于契约）

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-test-writer",
  "version": "1.0.0",
  "workflow_id": "module-testing",
  "stage_id": "s02-write",
  "confirmation_point": true,
  "confirmation_conditional": true,
  "confirmation_condition": "测试期望与契约存在矛盾（契约暧昧/冲突/不可判定）",
  "task_modes": ["core"],
  "autonomous_degradation": false
}
```
