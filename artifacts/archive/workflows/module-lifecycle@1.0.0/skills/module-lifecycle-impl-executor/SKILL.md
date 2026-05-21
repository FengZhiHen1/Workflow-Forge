---
name: module-lifecycle-impl-executor
description: >
  对功能模块按设计文档进行初始代码实现（Stage exec-impl），或根据盲测失败摘要进行定向修复（Stage exec-fix）。
  处理两种场景：(1) 按落地规范优雅实现模块，输出实现代码 + 函数签名清单；
  (2) 根据失败摘要修复实现代码，不接触任何测试代码。
  当用户要求实现模块、根据失败摘要修复代码、或作为 module-lifecycle 工作流的实现与修复阶段时，**必须优先使用本 Skill**。
---

# 模块生命周期实现执行器

## 外部对接协议（Protocol）

### 1. 契约读取义务

被工作流调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-impl-executor/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-impl-executor/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件（如 `implementation-patterns.md`）

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`、`agent_id`、`skill_id`、`stage_id`
- `upstream_files`（上游产物文件路径列表）
- `upstream_message_ids`（可选）
- `workflow_ref_dir`、`workflow_refs`（可选）：工作流级共享参考目录和文件列表
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-impl-executor`）不一致：立即终止，上报 `ERROR`。
- `stage_id` 必须为 `exec-impl` 或 `exec-fix` 之一，否则拒绝执行并上报 `ERROR`。

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

- **方案级降级**（实现策略变更、精度降低、功能裁剪）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（简化实现结构、减少校验层级）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中两个阶段的执行器。

### Stage exec-impl（初始实现落地）

- **上游**：`orch-contract` → `exec-impl`（条件：契约确认通过）
  - 上游产物：经过裁决的统一设计契约（落地规范、设计文档、项目结构文档、模块代码目录路径）
- **下游**：`exec-impl` → `orch-impl-validate`
  - 本 Stage 产物（实现代码 + 函数签名清单 + 待确认事项文件）将作为下游验证的输入

### Stage exec-fix（实现代码修复）

- **上游**：`orch-blindtest` → `exec-fix`（条件：盲测发现实现缺陷 `impl-bug`）
  - 上游产物：失败摘要（Markdown），不含任何测试代码或具体输入值
- **下游**：`exec-fix` → `orch-blindtest`（循环，最大 3 次）
  - 本 Stage 产物（修复后的代码 + 修复说明）将重新进入盲测验证

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-impl-executor",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```

---

## 核心原则

1. **不运行测试**：本 Skill 不执行任何测试代码。测试由工作流的盲测阶段统一执行。
2. **信息隔离**：绝对禁止读取 `{module_code_dir}/.tmp/adversarial-tests/` 目录下的任何文件。不要查看、不要搜索、不要分析该目录中的内容。扫描现有代码时也必须排除该目录。
3. **契约权威**：实现代码严格按落地规范编写，不根据想象中的测试来调整实现。
4. **增量优先**：优先修改现有代码以适配新需求，仅在现有文件职责不符或不存在时新建。禁止为"统一风格"而大面积重写未涉及的现有代码。
5. **不向用户提问**：所有需要用户裁决的事项必须按保守假设处理，并以"待确认事项"形式记录到 `pending-confirmations.md`，由下游 `orch-impl-validate` 阶段决定是否向用户提问。

---

## 实现规范（适用于 exec-impl 和 exec-fix）

> 复杂后端实现模式（状态机、重试降级、依赖注入等）的详细代码示例和模式说明见 `references/implementation-patterns.md`（由 `workflow_refs` 提供路径）。前端模块或简单 CRUD 可跳过。

### 实现顺序

代码按以下顺序生长：

1. 类型系统（枚举、常量、字面量类型）
2. 数据契约层（Pydantic / TypeScript interface / Zod / Go struct）
3. 工具代码（helper/utility 函数）
4. 原子功能单元（每步骤一个独立函数/组件）
5. 状态机（如需要，独立的状态流转模块）
6. 组合层（编排——主文件只调度核心功能）
7. 异常处理（装饰器 / 中间件 / ErrorBoundary）
8. 依赖适配（依赖注入 / Props / Context）

### 代码组织铁律

- **严格遵循项目结构设计文档**：文件必须放在指定目录中
- **命名必须符合项目规范**：文件、类、函数、常量命名与项目一致
- **共享资源必须复用**：通用工具、类型、常量使用项目指定的共享位置
- **单文件长度上限**：超过 500 行（不含空行和注释）必须拆分

### 质量要求

- 100% 兑现设计文档，除非有不可改变的技术限制
- 所有 I/O 使用强类型
- 通过函数组合、依赖注入连接，非深层继承
- 每个边界条件有保护分支，写入操作尽量幂等
- 注释即文档：后端 Google Style docstring，前端 TSDoc/JSDoc

### 多模块依赖处理

读取落地规范「依赖与集成接口」章节，区分两类依赖：

| 依赖类型 | 处理方式 | 说明 |
|:---|:---|:---|
| **关键基础设施依赖**（数据库、日志、外部 API、消息队列等） | **必须真实实现**，不可用 mock 替代 | 若基础设施连接配置缺失，在待确认事项中说明 |
| **核心功能依赖**（其他业务模块的接口） | 若未落地，提取接口定义生成 mock / stub | 在待确认事项中标注"依赖项 {模块名} 待落地" |

### 禁止行为

| 禁止项 | 原因 |
|:---|:---|
| 上帝函数/组件 | 职责不清，难以测试和维护 |
| 全局变量通信 | 引入隐式耦合 |
| 跳过输入校验 | 盲测的首要目标就是发现校验缺失 |
| 魔法字面量替代枚举 | 可读性差，易出错 |
| 静默吞异常 | 掩盖错误，导致调试困难 |
| 引入设计文档外的依赖 | 破坏技术栈约定 |
| 交付无注释的公开接口 | 可维护性下降 |
| 违反项目结构设计文档的代码组织 | 项目结构混乱 |
| 无必要地重写现有代码 | 破坏增量优先原则 |
| 向用户提问（AskUserQuestion） | 本 Skill 不可交互，待确认事项应写入 `pending-confirmations.md` |

---

## 工作模式 A：初始实现落地（Stage exec-impl）

当 `stage_id` 为 `exec-impl` 时进入此模式。上游 `orch-contract` 已提供统一的裁决契约。

### A.1 解析设计文档

每模块由三份文档组成：**落地规范**（编码主要来源）、**设计文档**（项目上下文）、**项目结构设计文档**（代码组织规范）。

#### 解析项目结构设计文档（强制）

开始编码前必须定位并解析项目结构设计文档。文件命名一般为 `xxx-项目结构.md`。

**必须提取的内容**：目录结构规范、模块边界、命名规范、技术栈约束、共享资源位置。

**若找不到项目结构设计文档**：
1. 搜索 `docs/` 目录下包含"项目结构"、"目录结构"、"structure"等关键词的文件
2. 若仍找不到，在待确认事项中记录"项目结构文档缺失，采用最小合理结构"
3. 采用模块内"最小合理结构"

#### 解析模块设计文档

从落地规范的"技术栈绑定"章节确定本模块的技术栈。

> 契约信息已由上游 `orch-contract` 提取：输入/输出类型定义、异常条件、状态约束、边界定义等均已写入 `contract-expectations.md`，直接读取该文件即可，无需自行提取。

#### 设计文档冲突仲裁

若多份设计文档要求不一致，按以下优先级执行：

| 优先级 | 文档 | 约束范围 |
|:---|:---|:---|
| P0 | 项目结构设计文档 | 目录结构、模块边界、命名规范、共享资源位置 |
| P1 | 落地规范 | 类型定义、逻辑步骤、状态机、异常策略 |
| P2 | 设计文档 | 业务意图、上下文说明、兼容性分析 |

**即：文件放哪里 → 按项目结构设计文档；代码怎么写 → 按落地规范；为什么这么写 → 按设计文档。**

若发现冲突，在待确认事项中记录冲突详情和按优先级采取的裁决策略。

### A.2 扫描现有代码

目标：识别可复用的现有代码，为"增量优先"原则提供依据。

手动扫描方法：
1. 按"已有设计兼容性分析"章节给出的文件路径定位
2. grep 设计文档中的类型/组件/类名
3. 搜索与模块名称同名的文件/类/函数/组件
4. 搜索核心动词——仅在前 3 步无结果时使用

**输出要求**：`文件路径 → 已实现项 → 差异（缺失字段/类型不匹配/步骤缺失）`，标注增量潜力评估。

若文档标注"全新模块"，跳过此步骤。

**额外约束**：文件扫描范围排除 `{module_code_dir}/.tmp/adversarial-tests/`。

### A.3 差异比对

逐项比对，只记差异。

| 差异类型 | 判定标准 | 处理方式 |
|:---|:---|:---|
| 缺失实现 | 设计文档有要求，代码中完全不存在 | **直接实现** |
| 字段/类型/枚举值冲突 | 已有代码存在但定义不一致 | 在待确认事项中记录，按保守假设处理 |
| 已有逻辑冲突 | 已有实现逻辑与设计文档步骤不符 | 在待确认事项中记录，按保守假设处理 |
| 技术栈冲突 | 已有代码使用设计文档外的技术 | 在待确认事项中记录 |
| 设计文档冲突 | 多份设计文档要求不一致 | 按优先级仲裁，在待确认事项中记录 |
| 设计未覆盖 | 某实现细节未定义 | 在待确认事项中记录，采用最宽松假设 |

### A.4 优雅实现

**核心哲学**：每个功能点是高内聚、低耦合的原子单元，通过管道/策略/装饰器模式组合，避免"上帝对象"。

**增量优先原则**：
- 优先修改现有文件以适配需求，仅在现有文件职责不符或不存在时新建
- 禁止为"统一风格"而重写未涉及的现有代码
- 修改现有代码时，保持其原有接口契约

**多模块依赖处理**：见上文"多模块依赖处理"章节。

**质量要求**：100% 兑现设计文档、强类型 I/O、函数组合而非深层继承、边界条件保护、注释即文档。

### A.5 生成函数签名清单（强制）

提取所有公开函数/方法的签名，生成 JSON 格式的函数签名清单：

```json
{
  "module_id": "模块编号（如 M01）",
  "module_name": "模块名称",
  "functions": [
    {
      "name": "func_name",
      "signature": "def func_name(param_a: int, param_b: str = \"\") -> ResultType",
      "parameters": [
        {"name": "param_a", "type": "int", "required": true},
        {"name": "param_b", "type": "str", "required": false, "default": "\"\""}
      ],
      "return_type": "ResultType",
      "exceptions": ["ValueError", "TimeoutError"]
    }
  ]
}
```

**存放路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/function-signatures.json`

> `{module_code_dir}` 由上游 `orch-contract` 通过 `upstream_files` 或 `special_instructions` 提供。若未提供，将文件写入 `.tmp/adversarial-tests/{module_id}/`（相对于当前工作目录），并在 `report` 中注明路径变量缺失。

### A.6 输出

完成实现后输出：
1. 实现代码文件列表（完整路径）
2. 函数签名清单（JSON 文件路径）
3. 实现说明（简要）
4. **待确认事项文件路径**（格式见「待确认事项处理」章节）

---

## 工作模式 B：修复迭代（Stage exec-fix）

当 `stage_id` 为 `exec-fix` 时进入此模式。上游 `orch-blindtest` 已验证通过盲测发现了实现缺陷。

### B.1 阅读输入

**输入材料**（均通过 `upstream_files` 提供）：
- **失败摘要**（Markdown，来自 `orch-blindtest` 阶段）
- 当前实现代码
- 落地规范
- 契约期望清单（可选，用于理解契约条款）

**失败摘要格式**：
```markdown
#### [case-001] TypeError: 参数收到非期望类型
- **涉及函数**：`calculate_limit`
- **涉及参数**：`limit`（类型：int (≥1)）
- **契约条款**：§3.2
- **失败原因**：参数收到 None，函数未进行类型校验
- **修复建议**：在函数入口处添加参数非空和类型校验
```

### B.2 分析修复优先级

按失败摘要中的"修复方向建议"排序，优先处理影响用例数多的问题。

**修复策略映射**：

| 失败原因 | 修复动作 |
|:---|:---|
| 参数未校验 | 添加输入校验（类型检查、非空检查） |
| 边界未处理 | 添加边界检查（范围、长度） |
| 空值未防护 | 添加 None/空值分支 |
| 异常未抛出 | 添加异常抛出（按契约要求的异常类型） |
| 状态未检查 | 添加前置条件/状态检查 |
| 返回值错误 | 修正返回值（按契约要求的返回类型/值） |

### B.3 修复实现

**约束**：
- 仅修改实现代码，不修改任何测试文件
- 每处修改必须对应失败摘要中的一个 case ID
- 修复应最小化，不引入超出当前失败摘要范围的行为
- 保持现有接口契约不变（不增删参数、不改返回值类型）
- 若修复方向与现有代码产生冲突，按保守假设处理并在待确认事项中记录
- **绝对禁止读取** `{module_code_dir}/.tmp/adversarial-tests/` 目录下的任何文件

### B.4 输出

返回：
1. 修改后的实现代码文件列表
2. 修改说明（Markdown 格式）：

```markdown
## 修复说明（第 {N} 轮）

### case-001
- **修复文件**：`src/services/calculator.py`
- **修复内容**：在 `calculate_limit` 函数入口处添加参数校验
  ```python
  if limit is None:
      raise TypeError("limit must be int, got None")
  ```
- **对应契约**：§3.2

### case-042
...
```

3. **待确认事项文件路径**（格式见「待确认事项处理」章节）

---

## 待确认事项处理

> **关键约束**：本 Skill 不可调用 `AskUserQuestion`。所有需要用户裁决的事项按保守假设处理，并以"待确认事项"文件返回，由下游 `orch-impl-validate` 阶段审查。

### 两类保留的待确认事项

| 情形 | 保守假设策略 | 记录要求 |
|:---|:---|:---|
| **与现有代码冲突** | 优先**修改现有文件**以适配新需求（保持向后兼容），仅在会破坏现有接口契约时新建 | 在待确认事项中说明冲突详情和策略 |
| **无法判断缺失或冲突** | 按**缺失实现**处理（新建），同时保留现有代码不变 | 记录为"疑似冲突，待确认" |

### 待确认事项记录格式

**存放路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/pending-confirmations.md`

> `{module_code_dir}` 由上游通过 `upstream_files` 或 `special_instructions` 提供。若未提供，写入 `.tmp/adversarial-tests/{module_id}/pending-confirmations.md`（相对于当前工作目录），并在 `report` 中注明路径变量缺失。

文件内容格式（即使为空也必须生成该文件）：

```markdown
## 待确认事项（无法向用户提问，已按保守假设处理）

### 事项 1：与现有代码冲突
- **涉及文件**：`src/existing.py`
- **冲突描述**：已有字段 `status` 为 `str` 类型，落地规范要求 `StatusEnum`
- **采取策略**：保持现有 `str` 类型，新增 `StatusEnum` 用于新接口
- **风险**：新旧接口混用可能导致类型不一致

### 事项 2：无法判断缺失或冲突
- **涉及文件**：`src/utils.py`
- **描述**：`validate_input` 函数已实现部分校验，不确定是补充还是冲突
- **采取策略**：补充缺失校验，保留现有校验逻辑不变
- **判断依据**：现有校验仅检查非空，落地规范要求额外检查格式
```

**无待确认事项时的空文件格式**：
```markdown
## 待确认事项

无。
```

---

## 设计文档补充更新

执行完毕后（包括修复迭代），若发现项目结构设计文档或落地规范存在未覆盖的场景，**选择性更新**。

**更新判定标准**：仅当执行过程中向用户确认过、且用户给出了明确方向时，才更新设计文档。禁止将未经用户确认的假设写入设计文档。（在本 Skill 不可交互的约束下，此项通常由下游 `orch-impl-validate` 阶段触发。）

**更新操作模板**：

对 `xxx-项目结构.md` 的追加格式：
```markdown
## 补充条目（{YYYY-MM-DD}）

### {条目名称}
- **场景**：{触发条件}
- **规范**：{用户确认后的存放位置/命名规则/边界定义}
- **来源**：module-lifecycle-impl-executor 执行 {module_id} 时发现
```

对 `xxx-落地规范.md` 的修正格式：
```markdown
## 修正记录（{YYYY-MM-DD}）

### {修正项}
- **原内容**：{原文引用}
- **修正为**：{修正后内容}
- **原因**：{与项目结构设计文档冲突 / 歧义 / 未覆盖场景}
- **来源**：module-lifecycle-impl-executor 执行 {module_id} 时确认
```

若用户明确指示"不要修改设计文档"，则跳过此步骤，但在结果汇报中注明"设计文档未更新（按用户指示）"。

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 完成阶段任务后：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 设置 `status: "DONE"`（两个 Stage 的 `confirmation_point` 均为 `false`）；
   - 在 `report` 字段中附上本章节要求的全部输出内容；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. 若在降级熔断或其他异常场景需要用户确认，设置 `status: "PENDING_CONFIRM"`，并在 `confirm_questions` 中列出具体问题（字符串数组，长度 1-4）。一次性全部列出，不要分多次终止。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。
