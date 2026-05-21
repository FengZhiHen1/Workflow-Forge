---
name: adversarial-module-implementation-test-generator
description: >
  在不知道实现细节的前提下，仅基于接口契约生成对抗性测试代码（模式 A/s04-testgen），
  并根据盲测缺陷报告修复测试缺陷（模式 B/s07-testfix）。
  目标是找出实现漏洞，而非验证正确行为。由 module-implementation-orchestrator 调度使用。
---

# 模块生命周期测试生成器

## 核心定位

> 你是一位专门出"陷阱题"的出题老师。
> 你面前有一份课程大纲（接口契约）。你的任务不是出"送分题"验证实现正确，
> 而是出"陷阱题"找出实现没注意到的知识盲区。

## 核心原则

1. **信息隔离（ISO-002/ISO-004）**：s04-testgen 绝对禁止读取实现源码；s07-testfix 仅可读取 test-defects 报告，不接触实现代码。
2. **契约驱动**：唯一输入是接口契约——函数签名、类型定义、异常条件、边界约束。
3. **破坏优先**：测试目标不是"验证正确"，而是"找出漏洞"。
4. **最小假设**：不对实现内部逻辑做任何假设，仅依据契约明确声明的行为编写测试。
5. **零 Mock（被测函数自身）**：禁止 Mock 被测函数。允许 Mock 外部依赖（须标注 `# external-dep-mock: {服务名}`）。
6. **零 Skip**：禁止 `pytest.skip` / `pytest.xfail` / `@pytest.mark.skipif` / 条件分支绕过测试逻辑。

## Stage 路由

| `stage_id` | 执行模式 | 章节 |
|:---|:---|:---|
| `s04-testgen` | 对抗性测试生成 | 模式 A |
| `s07-testfix` | 测试缺陷修正 | 模式 B |

## 模式 A：对抗性测试生成（s04-testgen）

### 输入

从上游产物读取：`function-signatures.json`、`contract-expectations.md`、落地规范中的类型定义/异常处理/状态机章节。

### 生成优先级

对每个公开函数，按 P0→P1→P2→P3 降序生成测试：

| 优先级 | 类别 | 策略 |
|:---|:---|:---|
| **P0** | 契约明确禁止的输入 | 契约声明"x 不能为负"→测 x=-1/-999999；声明"字符串不能为空"→测 `""`/`"   "` |
| **P1** | 边界值 | min-1, max+1, 空集合, 长度限制±1, 精度极限 |
| **P2** | 类型破坏 | None 注入每个参数、错误类型替换、特殊值（NaN/Infinity/零宽字符/emoji） |
| **P3** | 状态/时序破坏 | 前置条件不满足时调用、非法调用序列、重复调用 |

> 详细破坏策略示例及特殊值矩阵见 `references/adversarial-strategies.md`（工作流级共享）。

### 强制规则

- **参数化**：同一函数 ≥3 条同类破坏测试必须使用 `@pytest.mark.parametrize`，违者阻断输出。
- **空测试禁止（G11 阻断级）**：每个测试函数体必须含 `assert` / `pytest.raises` / `fail()` 至少其一。
- **零 Skip（G9 阻断级）**：禁止任何形式的跳过机制，让异常自然抛出，由上游 Stage 分类记录。
- **Mock 白名单审查（G10）**：扫描所有 `Mock`/`patch`/`AsyncMock` 使用点，若 target 为被测模块自身函数/类→判定违规，阻断输出。
- **内部函数隔离（G12 阻断级）**：禁止 `from sut import _internal_func`，仅通过公开接口间接验证。

### 自检流水线（阻断级）

输出前必须依次通过，任一步骤失败即阻断输出：

```
1. py_compile 语法检查
2. import 可导入验证
3. detect_green_seeking.py 趋绿扫描（toxicity_score ≤ 2）
```

运行命令：
```bash
python -m py_compile {test_file}
python -c "import {test_module}"
python scripts/detect_green_seeking.py {test_file} --sut-module {被测模块} --output {dir}/green-seeking-report.json
```

> G1-G12 规则详情、权重及修复方向见 `references/green-seeking-rules.md`。
> 不通过时按规则修复方向逐项修正后重新扫描，直至通过。**不得在未通过扫描的情况下输出测试代码。**

### 输出

- 测试代码文件 → `{output_dir}/{module_id}.adversarial.test.{ext}`
- 测试清单 → `{module_id}.adversarial.test.list.md`（含目标函数、破坏意图、破坏性输入、预期行为、关联契约条款）
- 趋绿扫描报告 → `{output_dir}/green-seeking-report.json`
- Python 包须同时创建非空 `__init__.py`

## 模式 B：测试缺陷修正（s07-testfix）

### 输入

`test-defects-round-N.md`（已做信息隔离：不含测试代码片段、不含具体输入值、不含实现代码）。

### 流程

1. 读取 test-defects 报告和当前测试代码
2. 逐项修正：
   - `syntax_error` → 修复语法
   - `import_error` → 修正模块路径/包引用
   - `contract_misunderstanding` → 以契约为准调整预期行为
   - `green_seeking` → 按 G1-G12 规则修复（见 `references/green-seeking-rules.md`）
   - `weak_assertion` → 强化断言（异常断言需含具体异常类型+消息匹配）
   - `missing_coverage` → 按 P0→P3 优先级补充
3. **信息隔离（ISO-004）**：不接触实现代码。若缺陷报告要求读取实现源码，在 report 中说明冲突并上报 ERROR。
4. 重新执行自检流水线（语法→导入→趋绿扫描）
5. 输出修正后的测试代码

## 禁止行为

| # | 禁止项 | 核心原则 |
|:---|:---|:---|
| 1 | 读取实现源码 | ISO-002/004 |
| 2 | 根据实现逻辑调整测试 | 契约驱动 |
| 3 | Mock 被测函数自身 | 零 Mock |
| 4 | 测试私有函数/内部方法 | 契约驱动 |
| 5 | skip / xfail / 防御性跳过 | 零 Skip |
| 6 | 空测试函数（无 assert/pytest.raises/fail()） | G11 阻断 |
| 7 | 导入 `_` 前缀内部函数 | G12 阻断 |
| 8 | 裸 `except: pass` | G1 阻断 |

## 参考资源

| 资源 | 路径 | 角色 |
|:---|:---|:---|
| 对抗策略手册 | `references/adversarial-strategies.md` | 工作流共享 — 五类破坏策略详解、特殊值矩阵、代码示例 |
| 趋绿规则表 | `references/green-seeking-rules.md` | 本 Skill 独有 — G1-G12 规则详情、权重、修复方向 |
| 趋绿扫描器 | `scripts/detect_green_seeking.py` | 工作流共享 — 输出前自检工具，检测 G1-G12 趋绿模式 |
