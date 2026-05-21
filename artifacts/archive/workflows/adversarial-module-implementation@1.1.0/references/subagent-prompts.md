# SubAgent 调度 Prompt 模板

> 所属工作流：`adversarial-module-implementation@1.1.0`
> 建立者：`adversarial-module-implementation-init`（s01-init）
> 复用者：`adversarial-module-implementation-impl-executor`（s02-impl, s06-fix）、`adversarial-module-implementation-test-generator`（s04-testgen, s07-testfix）
>
> 本文件定义编排器调度各 SubAgent 时的标准 prompt 模板。
> 每个模板使用 `{placeholder}` 标记变量部分，编排器在调度时替换。

---

## 通用约束速查

所有 SubAgent 调度共享以下参数：

| 参数 | 值 |
|:---|:---|
| `subagent_type` | `"coder"` |
| `model` | `"opus"` |

信息隔离方向（四铁律速查）：

| SubAgent | Stage | 禁止读取 | 对应铁律 |
|:---|:---|:---|:---|
| `adversarial-module-implementation-impl-executor` | s02-impl | `.tmp/adversarial-tests/` 下所有文件 | ISO-001 |
| `adversarial-module-implementation-test-generator` | s04-testgen | 实现源码目录下所有文件 | ISO-002 |
| `adversarial-module-implementation-impl-executor` | s06-fix | 测试代码，仅可读 failure-summary | ISO-003 |
| `adversarial-module-implementation-test-generator` | s07-testfix | 实现代码，仅可读 test-defects | ISO-004 |

---

## 模板 1：s02-impl — 实现落地执行（模式 A）

**目标 Skill**：`adversarial-module-implementation-impl-executor`
**Stage**：`s02-impl`
**模式**：A（优雅实现）
**铁律**：ISO-001

```
【你被 adversarial-module-implementation@1.1.0 工作流编排器调度执行 adversarial-module-implementation-impl-executor skill (模式 A)】

任务：按设计文档优雅实现模块代码。

落地规范路径：{spec_path}
设计文档路径：{design_doc_path}
项目结构文档路径：{structure_doc_path}
模块代码目录：{module_code_dir}
契约文件目录：docs/contracts/{module_id}/
契约期望清单：{contract_expectations_path}

输出要求：
  - 实现代码文件（按实现顺序：类型系统 → 数据契约 → 工具 → 原子功能 → 状态机 → 组合层 → 异常处理 → 依赖适配）
  - `function-signatures.json`（所有公开函数的完整签名清单）
  - `pending-confirmations.md`（实现过程中的待确认项）

**绝对约束（铁律 ISO-001）**：
  - 绝对禁止读取、搜索、查看 `{module_code_dir}/.tmp/adversarial-tests/` 目录下的任何文件
  - 绝对禁止搜索、分析任何测试相关文件（包括文件名含 test 的文件）
  - 测试对你完全不可见，你只能看到设计文档和现有代码
  - 禁止使用 AskUserQuestion 工具向用户提问

**契约约束**：
  - 对外接口的类型定义必须与落地规范的「输入/输出类型定义」章节一致
  - 实现代码不得 import 或依赖 `docs/contracts/` 下的契约文件（契约文件在 docs/ 下，不是代码依赖）
  - 实现代码自行定义数据模型，但字段名、类型、必填性必须与契约一致

**提交前自检清单**（请在提交实现前逐项确认）：
  - [ ] 本模块所有对外接口的数据模型字段名、类型、必填性与落地规范一致
  - [ ] 未在代码中引入契约文件未声明的新字段
  - [ ] 若发现实现需要偏离契约，已在 `pending-confirmations.md` 中记录待确认项

**工作目录约束**：排除 `{module_code_dir}/.tmp/adversarial-tests/` 目录
```

---

## 模板 2：s04-testgen — 对抗性测试生成（模式 A）

**目标 Skill**：`adversarial-module-implementation-test-generator`
**Stage**：`s04-testgen`
**模式**：A（对抗性测试生成）
**铁律**：ISO-002

```
【你被 adversarial-module-implementation@1.1.0 工作流编排器调度执行 adversarial-module-implementation-test-generator skill (模式 A)】

任务：基于接口契约黑盒生成对抗性测试代码。目标是找出实现漏洞，而非验证正确行为。

契约期望清单：{contract_expectations_path}
函数签名清单：{function_signatures_path}
落地规范路径：{spec_path}（仅读取类型定义、异常处理、状态机章节）
技术栈：{tech_stack}
输出目录：{output_dir}

生成优先级：
  1. P0：契约禁止输入（None、空值、超范围值等直接违反契约的输入）
  2. P1：边界值与边界扰动（min-1、max+1、边界内部、边界外部）
  3. P2：类型破坏（错误类型、混合类型、类型转换边界）
  4. P3：状态/时序破坏（非法状态转换、并发冲突、时序假设违反）

**绝对约束（铁律 ISO-002）**：
  - 绝对禁止读取实现源码文件
  - 绝对禁止查看被测模块的实现目录
  - 绝对禁止搜索、分析任何实现相关文件
  - 你只拥有接口契约（函数签名、类型定义、异常条件），实现对你是完全黑盒
  - 如果你能猜出实现的行为，你的测试就太弱了——专注于契约明确声明的行为

**强制自检流水线（阻断级）**：
  1. `py_compile` 语法检查：确保测试代码语法正确
  2. `import` 可导入验证：确保测试模块可被 Python 导入
  3. `scripts/detect_green_seeking.py` 趋绿扫描：toxicity_score 必须 ≤ 2
  上述任一步骤不通过 → 修正后重新自检，全部通过后方可提交。

**输出要求**：
  - 测试代码文件
  - `test_list.md`（测试用例清单，含优先级标记）
  - `green-seeking-report.json`（趋绿扫描报告）

**工作目录约束**：排除所有实现源码目录，仅提供契约文档路径
```

---

## 模板 3：s06-fix — 实现代码修复（模式 B）

**目标 Skill**：`adversarial-module-implementation-impl-executor`
**Stage**：`s06-fix`
**模式**：B（根据失败摘要修复）
**铁律**：ISO-003

```
【你被 adversarial-module-implementation@1.1.0 工作流编排器调度执行 adversarial-module-implementation-impl-executor skill (模式 B)】

任务：根据盲测失败摘要修复实现代码。

失败摘要路径：{failure_summary_path}
  注意：此文件是信息隔离版本，仅包含错误类型、涉及函数/参数名、契约条款、失败原因一句话
当前实现代码目录：{implementation_dir}
落地规范路径：{spec_path}
模块代码目录：{module_code_dir}
契约文件目录：docs/contracts/{module_id}/

修复策略：
  1. 阅读 failure-summary-round-N.md，按 case ID 排序修复优先级
  2. 按优先级逐一修复，每处修改对应一个 case ID
  3. 最小化修复：仅修改实现代码，保持接口契约不变
  4. 不得新增公开函数或修改函数签名
  5. 不得删除已有的正确行为

输出要求：
  - 修改后的实现代码文件
  - `修改说明.md`（每处修改引用对应 case ID）
  - `pending-confirmations-round-{N}.md`（本轮待确认项）

**绝对约束（铁律 ISO-003）**：
  - 仅可读取 failure-summary-round-N.md，这是信息隔离版本
  - 绝对禁止读取、搜索、查看 `{module_code_dir}/.tmp/adversarial-tests/` 目录下的任何文件
  - 绝对禁止读取任何测试代码（包括测试文件路径、测试用例、断言内容）
  - 绝对禁止搜索、分析任何测试相关文件
  - 你只能看到失败摘要中提供的有限信息，测试细节对你完全不可见

**契约约束**：
  - 修复后的对外接口类型必须与落地规范一致
  - 不得在修复中引入落地规范未声明的新字段

**提交前自检清单**：
  - [ ] 修复后的数据模型字段名、类型、必填性与落地规范一致
  - [ ] 未在修复中引入落地规范未声明的新字段
  - [ ] 每处修改有对应的 case ID 引用

**工作目录约束**：排除 `{module_code_dir}/.tmp/adversarial-tests/` 目录
```

---

## 模板 4：s07-testfix — 测试缺陷修正（模式 B）

**目标 Skill**：`adversarial-module-implementation-test-generator`
**Stage**：`s07-testfix`
**模式**：B（修正测试缺陷）
**铁律**：ISO-004

```
【你被 adversarial-module-implementation@1.1.0 工作流编排器调度执行 adversarial-module-implementation-test-generator skill (模式 B)】

任务：修正对抗性测试代码中的缺陷。

当前测试代码路径：{test_file_path}
测试缺陷报告路径：{defects_report_path}
  注意：此文件不含测试代码片段和具体输入值，仅描述缺陷类型和修正方向
契约期望清单：{contract_expectations_path}
函数签名清单：{function_signatures_path}

输出要求：修正后的测试代码（覆盖原文件）

**绝对约束（铁律 ISO-004）**：
  - 仅可读取 test-defects-round-N.md
  - 该报告不含测试代码片段和具体输入值，仅描述缺陷类型和修正方向
  - 绝对禁止读取任何实现代码文件
  - 绝对禁止搜索、查看实现源码目录

**修复方向**（由缺陷报告指定）：
  - 修复语法错误或导入问题
  - 修正与契约矛盾的断言逻辑
  - 移除无效测试（输入或行为与契约不符）
  - 修正趋绿倾向（测试过于宽松、仅验证不拒绝无效输入等）

**强制自检流水线（阻断级）**：
  1. `py_compile` 语法检查：修正后的测试代码语法正确
  2. `import` 可导入验证：测试模块可被 Python 导入
  3. `scripts/detect_green_seeking.py` 趋绿扫描：toxicity_score 必须 ≤ 2
  上述任一步骤不通过 → 修正后重新自检

**工作目录约束**：仅提供测试代码目录和契约文档，排除实现源码目录
```

---

## 占位符速查表

编排器在调度 SubAgent 时，需将以下占位符替换为实际值：

| 占位符 | 含义 | 来源 | 模板使用 |
|:---|:---|:---|:---|
| `{module_id}` | 模块编号（如 M01） | 工作流输入 | 1, 2, 3, 4 |
| `{module_code_dir}` | 模块代码目录路径 | s01-init 产物 | 1, 3 |
| `{spec_path}` | 落地规范文件路径 | s01-init 定位 | 1, 2, 3 |
| `{design_doc_path}` | 设计文档路径 | s01-init 定位 | 1 |
| `{structure_doc_path}` | 项目结构文档路径 | s01-init 定位 | 1 |
| `{contract_expectations_path}` | 契约期望清单路径 | s01-init 产物 | 1, 2, 4 |
| `{function_signatures_path}` | 函数签名清单路径 | s03-validate 产物 | 2, 4 |
| `{tech_stack}` | 技术栈描述 | 项目配置 | 2 |
| `{output_dir}` | 测试代码输出目录 | 编排器指定 | 2 |
| `{failure_summary_path}` | 失败摘要路径 | s05-blindtest 产物 | 3 |
| `{implementation_dir}` | 实现代码目录 | 编排器指定 | 3 |
| `{test_file_path}` | 测试代码文件路径 | 编排器指定 | 4 |
| `{defects_report_path}` | 测试缺陷报告路径 | s05-blindtest 产物 | 4 |

## 信息隔离四铁律完整说明

编排器在注入 prompt 时，须确保以下隔离规则生效：

| 规则 | Stage | 约束 | 执行方式 |
|:---|:---|:---|:---|
| **ISO-001** | s02-impl | SubAgent 禁止读取 `.tmp/adversarial-tests/` 目录及任何内容 | Prompt 铁律 + `check_isolation.py` 事后审计 |
| **ISO-002** | s04-testgen | SubAgent 禁止读取实现源码文件 | Prompt 铁律 + `check_isolation.py` 事后审计 |
| **ISO-003** | s06-fix | SubAgent 仅可读 failure-summary（信息隔离版本），禁读测试代码 | `validate_failure_summary.py` 阻断级门控 + Prompt 铁律 |
| **ISO-004** | s07-testfix | SubAgent 仅可读 test-defects（不含测试代码片段和输入值），禁读实现代码 | Prompt 铁律 + 事后审计 |

**编排器职责**：
1. 调度每个 SubAgent 前，确认其工作目录已排除禁止访问的路径
2. 注入的 prompt 中必须包含对应的铁律声明
3. 调度时传入的 failure-summary 和 test-defects 必须是信息隔离版本
4. 工作流结束后运行 `check_isolation.py` 进行事后审计

**隔离版本文件说明**：
- `failure-summary-round-N.md`（隔离版本）不含：测试代码片段、具体输入值、测试文件路径、断言内容
- `test-defects-round-N.md`（隔离版本）不含：测试代码片段、具体输入值、实现代码参考
