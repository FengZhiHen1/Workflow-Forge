---
name: module-lifecycle-contract
description: >
  模块生命周期契约提取与设计仲裁 Skill。
  从设计文档中按 P0(契约文件) → P1(落地规范) → P2(设计文档) 优先级提取接口契约，
  生成 contract-expectations.md 并经验证后冻结，仲裁多份设计文档之间的冲突，
  输出执行计划预览供用户确认。
  当编排器调度契约提取、接口契约生成、设计文档冲突仲裁、执行计划预览、
  契约模糊矛盾确认、contract extraction、contract expectations 时，必须优先使用本 Skill。
  核心工作方式：读取上游设计产物 → 多层次提取契约 → 验证冻结 → 冲突仲裁 → 输出执行计划 → 等待用户确认。
  每次调用输出 contract-expectations.md 和执行计划摘要到临时目录。
---

## 定位说明

你是模块生命周期工作流中 `orch-contract` 阶段的执行器。任务是从上游预检阶段传入的设计文档中提取接口契约、仲裁文档冲突、生成冻结的契约期望清单，并预览执行计划。

你**不负责实现代码**，也不负责生成测试。你只负责提取和冻结契约——契约是下游实现阶段和对抗性测试阶段的唯一事实来源。

---

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-contract/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-contract/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

额外业务字段（从上游 preflight 阶段产物中提取）：
- `module_id`：模块编号（如 M01）
- `module_name`：模块名称
- `module_code_dir`：模块代码目录路径

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-contract`）不一致：立即终止，上报 `ERROR`。
- `upstream_files` 全部不存在：立即终止，上报 `ERROR`，说明无可用输入。

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

- **方案级降级**（跳过某些文档来源、降低提取严格度、跳过冲突仲裁）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（对超长文档分段读取、分批解析类型定义）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中的 Stage `orch-contract` 的执行器。

**上游 Stage**：`orch-preflight`（来自 Skill `module-lifecycle-preflight`）
- 上游产物包括：已校验的设计文档路径、落地规范文件路径、契约文件路径、function-signatures.json（如有）
- 本 Skill 启动时，`upstream_files` 将包含上述路径

**下游 Stage**：`exec-impl`（进入 Skill `module-lifecycle-impl-executor`），走 `condition: confirmed` 边
- 下游仅在用户确认本阶段产出后才会解锁
- 本 Skill 的 `contract-expectations.md` 和执行计划是下游实现阶段的强制输入
- 确保输出文件路径稳定，以便下游通过 `upstream_files` 读取

---

## 执行步骤

### Step C1：读取并分类上游文件

从 `upstream_files` 中读取所有文件，按优先级分类：

| 优先级 | 文件类型 | 识别方式 | 提取内容 |
|:---|:---|:---|:---|
| P0 | 契约文件 | `docs/contracts/{module_id}/**/*.json` | 参数类型、返回值类型、必填/可选、枚举值、bounds |
| P1 | 落地规范 | `{module_id}-落地规范.md` | 类型定义章节、异常处理章节、状态机章节 |
| P2 | 设计文档 | `{module_id}-设计文档.md` | 业务层面的输入约束、边界定义 |
| P2 | 项目结构设计 | 项目结构设计文档 | 命名规范、模块边界（影响公开接口范围） |

若 `upstream_files` 中部分文件不存在，在 `report` 中说明缺失情况，继续使用可用文件。若 P0 和 P1 文件均缺失，上报 `ERROR`。

### Step C2：从落地规范提取接口契约

对落地规范 Markdown 文本执行程序化提取，五步骤算法如下：

**步骤 2.1：解析「输入/输出类型定义」章节**

定位二级标题 `## 输入/输出类型定义` 或 `## 类型定义`，在其下搜索三级标题 `### {TypeName}`。对每个类型定义列表项，按以下规则解析：

```
模式:  `- `field_name`: type, 必填/可选, 约束描述`
或:    `- `field_name` (type): 约束描述`
```

提取每个字段的：类型、必填性（"必填"/"required" → true；"可选"/"optional" → false，默认 false）、默认值、bounds（长度 `1-100` → {min:1, max:100}，范围 `[0, 1000]` → {min:0, max:1000}）、正则格式、枚举值列表。

**示例**：

输入 Markdown：
```markdown
- `field_a`: string, 必填, 长度 1-100
- `field_b`: int, 可选, 默认 0, 范围 [0, 1000]
- `status`: StatusEnum, 必填, 枚举 [CREATED, PENDING, APPROVED]
```

解析结果：

| 字段名 | 类型 | 必填 | 默认 | bounds |
|:---|:---|:---|:---|:---|
| field_a | str | true | — | {min:1, max:100} |
| field_b | int | false | 0 | {min:0, max:1000} |
| status | StatusEnum | true | — | {allowed_values:[CREATED,PENDING,APPROVED]} |

**步骤 2.2：将类型定义映射为函数参数契约**

对于 `function-signatures.json`（如有）中的每个公开函数：
- 若参数名与已解析的类型定义中某字段同名，直接复用约束信息
- 若参数名以 `input_` / `request_` 为前缀，尝试去除前缀后匹配
- 若无法匹配任何类型定义，标记为「约束未声明，采用最宽松假设」，必填参数至少标注 "non-empty" 约束

**步骤 2.3：解析「异常处理」章节**

定位二级标题 `## 异常处理`，提取表格或列表中每一条异常记录：
- 触发条件（如 "param_a 为空或仅空白"）
- 异常类型（如 `ValueError`、`TypeError`、`TimeoutError`）
- 处理策略（可选）
- 若原文有 `§N.N` 引用则直接使用，否则按章节顺序分配临时编号

**步骤 2.4：解析「状态机」章节**

定位二级标题 `## 状态机`，提取状态转换表：
- 对每个状态转换行，若前置条件列非空，生成前置条件约束
- 对终态（操作列空缺），生成约束：「{终态} 状态下调用任何转换操作应抛出异常」
- 对非相邻状态，生成约束：「从 {状态A} 直接转换到 {状态B}（不经过中间状态）应被拒绝」

**步骤 2.5：组装契约条目**

对每个公开函数的每个参数：
- 若有 bounds 或 constraints：生成 **A 系列**条目（`A01, A02, ...`）
- 编号规则：`{契约维度}` = `"{函数名} 参数 {参数名} 的 {约束类型}"`
- 破坏性输入按以下矩阵生成：

| 约束类型 | 生成的破坏性输入 | 期望行为（通用） |
|:---|:---|:---|
| required=true | `None`, `null`, `undefined` | 抛出 TypeError / ValueError |
| type=str, bounds.min > 0 | `""`, `"   "` | 抛出 ValueError |
| bounds.min, bounds.max | `min-1`, `max+1` | 抛出 ValueError / 边界外值 |
| bounds.regex | 不匹配正则的字符串 | 抛出 ValueError |
| bounds.allowed_values | 不在列表中的值 | 抛出 ValueError |
| type=int | `NaN`, `Infinity`（JS 场景）, 浮点数 | 抛出 TypeError |
| type=List[T] | `[]`, 超长度列表 | 按契约要求（返回空或抛异常） |

对每个状态约束：生成 **B 系列**条目（`B01, B02, ...`），契约维度 = `"{函数名} 状态前置条件"`。

### Step C3：设计文档冲突仲裁

当 P0 契约文件、P1 落地规范和 P2 设计文档对同一接口的描述不一致时：

1. 按优先级裁决：**P0（契约文件）> P1（落地规范）> P2（设计文档）**
2. 记录所有冲突到 `conflict_log`：
   ```
   | 冲突维度 | P0/P1/P2 各自声明 | 优先级裁决结果 | 备注 |
   ```
3. **未覆盖场景**（字段未定义、边界值未声明、异常触发条件缺失）：不要自行假设。收集为一个列表，上报 `PENDING_CONFIRM` 时一并列出

**P0/P1/P2 文档均未覆盖的字段**：标记为「约束未声明，采用最宽松假设」，在 `report` 中说明。

### Step C4：生成契约期望清单

将步骤 C2 的所有条目组装为 `contract-expectations.md`，使用以下格式：

```markdown
# {模块名} 契约期望清单
> 来源：{落地规范文件名}、{设计文档文件名}、{契约文件列表}
> 冻结时间：{生成时的 ISO 时间戳}

| 编号 | 契约维度 | 破坏性输入 | 期望行为 | 来源章节 |
|:---|:---|:---|:---|:---|
| A01 | username 空值 | "" | 抛出 ValueError | §3.2 |
| A02 | username 类型 | None | 抛出 TypeError | §3.2 |
| B01 | approve_order 状态前置 | 订单状态为 CANCELLED | 抛出 RuntimeError | §5.2 |
```

**编号规则**：
- 格式 `[A-Z]\d{2,3}`（如 A01, B123），全局唯一
- A 系列：参数/输入约束
- B 系列：状态/前置条件约束

保存路径：`.tmp/{workflow_instance_id}/contract-expectations.md`

### Step C5：验证并冻结

调用验证脚本：

```bash
python scripts/validate_contract_expectations.py \
    .tmp/{workflow_instance_id}/contract-expectations.md \
    --function-signatures {function-signatures.json 路径}
```

验证包括：
- 结构完整性：标题、来源标注、冻结时间、编号格式、破坏性输入明确性、期望行为可测试性
- 覆盖完整性（若有 function-signatures.json）：每个公开函数至少有一条契约条目

验证失败则根据错误修正后重新运行，最多重试 3 次。连续 3 次验证失败则上报 `ERROR`，说明无法通过验证的具体原因。

验证通过后，更新冻结时间为当前时间戳，标记文件为冻结状态。冻结后的文件是下游阶段的强制输入，不得擅自修改。

### Step C6：构建执行计划预览

基于提取结果，汇总执行计划：

1. **模块信息**：`module_id`、`module_name`、模块描述
2. **契约统计**：
   - A 系列条目数（参数约束）
   - B 系列条目数（状态约束）
   - 公开函数数量
   - 未覆盖字段数量
3. **SubAgent 调度计划**（供下游阶段参考）：
   - 实现阶段：1 个 SubAgent（impl-executor）
   - 对抗性测试阶段：1 个 SubAgent（adversarial-test-generator）
   - 预计循环轮次：建议 2-3 轮
4. **风险提示**：列出所有标记为「约束未声明」的条目、设计文档冲突摘要

### Step C7：上报确认

本 Stage 的 `confirmation_point=true`，完成任务后必须上报 `PENDING_CONFIRM`，不得直接上报 `DONE`。

---

## 确认点上报（Confirmation Point）

本 Skill 对应 stage 的 `confirmation_point=true`。完成任务后：

1. 在 `.tmp/{workflow_instance_id}/` 下生成 message 草稿 JSON
2. 设置 `status: "PENDING_CONFIRM"`
3. 设置 `confirm_required: true`
4. 设置 `confirm_questions`（1-4 个字符串，必须具体、可回答）
5. 调用 `write_message.py` 上报
6. 终止执行，等待编排器处理用户确认

用户确认后，编排器会自动将本 stage 标记为 DONE，并走 `condition: confirmed` 的 edge 解锁下游 `exec-impl`。

**确认问题设计**：

必须基于实际产出内容提问，至少包含以下核心问题：

- **AQ-001 契约提取完整性**：「契约提取是否完整？以上执行计划是否可以启动？」
- **AQ-002 执行计划确认**：展示执行计划摘要后，询问「执行计划预览是否合理？SubAgent 调度安排是否可行？」
- **AQ-004 契约模糊/矛盾确认（条件触发）**：若步骤 C3 发现未覆盖场景、文档冲突或约束缺失，提问「以下未覆盖场景需要确认：{具体场景列表}。是否按最宽松假设处理，还是需要补充约束？」

若同时存在多个待确认事项，一次性全部列出在 `confirm_questions` 中（最多 4 条），不要分多次终止。

**期望确认后的行为**：
- 用户确认 → 编排器将本 stage 标记为 DONE，`contract-expectations.md` 作为冻结产物传递给下游
- 用户拒绝/要求修改 → 编排器通过特殊指令恢复本 stage，根据用户反馈修改后重新上报

---

## 质量检查清单

上报前自检：

- [ ] `contract-expectations.md` 中编号全局唯一，格式 `[A-Z]\d{2,3}`
- [ ] 每个公开函数至少有一条契约条目
- [ ] 每个必填参数至少有一条破坏性输入
- [ ] 每个异常条件至少有一条对应的期望行为
- [ ] 状态机中的非法转换都有对应的期望行为
- [ ] 已运行 `validate_contract_expectations.py` 并通过
- [ ] 文件头部有来源和冻结时间标注
- [ ] 设计文档冲突已按 P0 > P1 > P2 仲裁，结果记录在案
- [ ] 未覆盖场景已明确列出，未自行假设

---

## 禁止行为

- 禁止在未读取完所有上游文件前开始提取
- 禁止自行假设未声明约束（必须标记「约束未声明」或上报确认）
- 禁止跳过验证直接冻结
- 禁止在 PENDING_CONFIRM 阶段上报 DONE
- 禁止修改任何上游源文件（设计文档、落地规范、契约文件）
- 禁止在契约提取时查阅实现代码（契约提取仅基于设计文档）
- 禁止编造不存在的约束或异常条件

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/{workflow_instance_id}/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
5. `report` 中必须包含：本次提取的契约条目总数、A/B 系列分别数量、未覆盖场景数、仲裁的冲突数量、验证结果。
6. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "module-lifecycle-contract",
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
