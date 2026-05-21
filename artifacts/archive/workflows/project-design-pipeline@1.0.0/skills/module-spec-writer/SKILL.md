---
name: module-spec-writer
description: >
  基于已冻结的意图文档，为功能模块生成瘦身版设计文档和精确编码级落地规范。
  使用场景：(1) 用户要求为某个模块编写技术规格、编码规范或落地实现；
  (2) 用户提到"技术设计"、"编码规格"、"类型定义"、"接口契约"、"状态机实现"、"写模块规范"等关键词；
  (3) 模块开发的第二阶段——意图文档已冻结锁定，进入技术实现；
  (4) 用户要求将高层设计转化为包含精确类型定义、异常处理、状态机、验收测试的代码级文档；
  (5) 用户要求检查或更新已有模块的技术规格，或处理模块间的契约冲突；
  (6) 用户要求生成模块对外接口的 JSON Schema 契约文件。
  核心工作方式：以已冻结的意图文档和上游技术决策报告为强制输入，
  自主完成技术规格文档编写与契约管理，仅将业务矛盾和契约冲突上报用户裁决。
  每次调用为指定模块输出两份文档到 `docs/功能设计/[所属分组]/[编号]-[名称]/` 目录下：
  `[编号]-[名称]-设计文档.md`（瘦身版，给维护者看）和 `[编号]-[名称]-落地规范.md`（给 Agent 看）。
  必须优先使用本 skill 当用户要求编写模块的技术规格、编码实现规范、接口定义、状态机设计、测试用例或契约文件时。
---

# Module Spec Writer

基于已冻结的**意图文档**和上游技术决策报告，为功能模块生成两份技术文档：
- **设计文档（瘦身版）**：技术实现思路、架构权衡、兼容性分析、设计原则兑现
- **落地规范**：精确的类型定义、接口契约、状态转换表、异常阈值、可复制粘贴的测试数据

> **流水线关系**：module-intent-writer → 意图文档（冻结）→ spec-researcher → module-spec-writer → 设计文档 + 落地规范 + 契约文件
> module-spec-writer **必须**以已冻结的意图文档为输入，**禁止**跳过意图文档直接生成技术规格。

---

## 外部对接协议（Protocol）

### 1. 契约读取义务

执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-spec-writer/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-spec-writer/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（`workflow_refs` 非空时按需读取，如目录规范、输出模板）

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`
- `workflow_ref_dir`, `workflow_refs`
- `special_instructions`, `stage_direction`

**校验规则**：
- 必填身份字段缺失任意一项：终止，上报 `ERROR`，说明缺失字段。
- `skill_id` 与自身 `skill_id` 不一致：终止，上报 `ERROR`。

### 3. 输出上报

完成后调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（算法变更、精度降低、功能裁剪）：**禁止自主执行**。在 `report` 中说明原因，上报 `PENDING_CONFIRM`。
- **资源级降级**（分批计算、降采样）：可自主执行，但必须在 `report` 中说明措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `project-design-pipeline` 中多个 Stage 的执行器。

**上游 Stage**：
- `s11-intent-freeze`（module-intent-writer）→ 已冻结的意图文档
- `s13-spec-research`（spec-researcher）→ 技术决策完整报告（含业务矛盾标记清单）
- `s17-spec-contract-harmonize`（contract-harmonizer）→ 契约协调报告（含冲突/可复用清单）

**下游 Stage**：
- `s20-next-module-confirm`（pipeline-director）→ 本 Skill 的产物作为下游输入

**输出文件约定**：
- 设计文档：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-设计文档.md`
- 落地规范：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-落地规范.md`
- 契约文件：`docs/contracts/{module_id}/*.json`
- 契约索引：`docs/功能设计/_contracts.md`
- 全局契约索引：`docs/contracts/_index.json`
- 项目同步问题：`docs/功能设计/_sync-issues.md`

---

## Stage 执行规范

根据 `stage_id` 执行对应任务：

### s12-spec-prepare：输入准备与前置检查

**目标**：验证输入完备性，检测意图缺陷，执行项目级一致性扫描。

1. **读取上游产物**：从 `upstream_files` 读取已冻结的意图文档。
2. **意图文档校验**：
   - 检查文档存在性。缺失 → 上报 `ERROR`，说明缺失文件路径。
   - 检查冻结状态。未冻结 → 上报 `ERROR`，说明文档尚未冻结。
3. **意图缺陷初筛**：扫描意图文档中的业务约束和验收标准，判断是否存在明显技术不可行项（性能指标无法达成、与项目技术栈根本性冲突、业务规则自相矛盾）。
4. **收集材料路径**：定位并记录以下路径（仅记录，不读取内容）：
   - 技术栈设计文档：`docs/技术栈设计.md`（或扫描 `docs/*技术栈*.md`）
   - 项目结构设计文档：`docs/项目结构设计.md`（或扫描 `docs/*项目结构*.md`）
   - 功能模块全拆解表：`docs/功能设计/功能模块全拆解.md`
   - 契约索引：`docs/功能设计/_contracts.md`
   - 已有规格文档：扫描 `docs/功能设计/` 下的 `[编号]-[名称]-落地规范.md`
5. **项目级一致性检查**：
   - 扫描 `docs/功能设计/` 下所有已有规格文档的模块编号、状态定义、接口命名。
   - 检查是否存在同名异构类型、状态定义冲突、循环依赖迹象。
   - 将发现的问题追加写入 `docs/功能设计/_sync-issues.md`（按时间戳分节，不覆盖已有内容）。
   - 若无问题，确保 `_sync-issues.md` 中本模块对应节标注 "✅ 无冲突"。
6. **上报**：输出材料清单、意图缺陷结论（如有）、同步检查结果。上报 `DONE`。

> 若发现意图缺陷，不走后续 Stage，直接触发**回退机制**（见下方）。

---

### s14-spec-contradiction：业务矛盾确认

**目标**：接收 spec-researcher 报告中的业务矛盾清单，请求用户裁决。

1. **读取上游产物**：从 `upstream_files` 读取《技术决策完整报告》。
2. **提取矛盾清单**：定位报告中的 "5. 业务矛盾标记清单" 章节。
3. **矛盾为空**：若清单为空或已解决 → 上报 `DONE`，说明无待裁决矛盾。
4. **矛盾非空**：
   - 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`。
   - `confirm_questions` 按以下模板组织（1-4 个）：
     ```
     spec-researcher 在技术预研中发现 N 处需裁决的业务矛盾：
     1. [矛盾点简述]（类别：XXX）—— 推荐方案：[方案]。是否采纳？
     2. ...
     若以上方案不可行，请在回复中说明替代方案。
     ```
   - 调用 `write_message.py` 上报，终止执行，等待编排器恢复。

---

### s15-spec-design-doc：生成设计文档（瘦身版）

**目标**：基于技术决策报告（及用户裁决结论）生成设计文档。

**前置条件**：s14 已通过（无矛盾或矛盾已解决）。

1. **读取上游产物**：spec-researcher 报告、`upstream_message_ids` 中的用户裁决（如有）。
2. **项目级一致性检查**：
   - 再次扫描已有规格文档，核对本模块设计决策是否与已有模块产生新增冲突。
   - 将新增冲突追加到 `docs/功能设计/_sync-issues.md`。
3. **生成设计文档**：
   - 以 spec-researcher 报告为核心依据，用户裁决结论为修正项。
   - 若裁决与报告冲突，**以用户裁决为准**，并在文档中标注冲突及选择理由。
   - 输出路径：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-设计文档.md`
   - 模板参考：`references/human-design-template.md`
   - 必须包含章节：1.1 技术实现思路 → 1.2 已有设计兼容性分析 → 1.3 依赖关系概述 → 1.4 状态机设计（如适用）→ 1.5 设计原则兑现清单 → 1.6 架构权衡与备选方案 → 1.7 注意事项与禁止行为 → 1.8 引用：配套意图文档
4. **上报**：说明设计文档路径及关键决策摘要。上报 `DONE`。

---

### s16-spec-contract-draft：生成契约草案

**目标**：确定对外接口边界，提取契约草案供 contract-harmonizer 扫描。

1. **读取设计文档**：确认对外接口类型。
2. **生成对外接口类型定义**：
   - 1.3 输入定义（本模块从外部接收的数据结构）
   - 1.4 输出定义（本模块向外部返回的数据结构）
   - 1.6 接口契约（公开函数的完整签名、docstring、参数、返回值、异常）
   - 1.7 依赖与集成接口（本模块调用的其他模块接口）
3. **提取契约草案**：将对外类型整理为临时 JSON 文件：
   ```
   .tmp/contract-draft/{module_id}/
   ├── {TypeName}.json
   └── _draft-index.json
   ```
   排除内部类型（私有函数参数、内部辅助模型、数据库映射模型）。
4. **上报**：说明草案目录路径、对外接口类型清单。上报 `DONE`。

> **注意**：此时不写入 `docs/contracts/`，契约文件在 s19 经冲突解决后才正式落盘。

---

### s18-spec-contract-conflict：契约冲突确认

**目标**：接收 contract-harmonizer 报告中的冲突清单，请求用户裁决。

1. **读取上游产物**：从 `upstream_files` 读取《契约协调报告》。
2. **提取冲突清单**：定位 `findings.conflicts` 和 `findings.reusables`。
3. **无冲突/无复用**：若 `conflicts` 为空且 `reusables` 为空 → 上报 `DONE`。
4. **存在可复用项**：若仅 `reusables` 非空 → 上报 `PENDING_CONFIRM`，询问是否复用已有类型。
5. **存在冲突**：若 `conflicts` 非空 → 上报 `PENDING_CONFIRM`：
   ```
   contract-harmonizer 扫描发现本模块与已有契约存在冲突：
   1. [类型名] 与 [已有模块] 定义冲突（严重程度：XXX）—— [差异描述]。
      选项：A) 以已有契约为准，修改本模块定义；B) 以本模块为准，标记已有契约为 deprecated；C) 两者代表不同概念，本模块改名；D) 其他（请说明）。
   2. ...
   ```
6. 调用 `write_message.py` 上报，终止执行，等待编排器恢复。

---

### s19-spec-internal-design：生成落地规范

**目标**：在已锁定的对外边界内完成内部设计，输出最终文档和契约文件。

**前置条件**：s18 已通过（无冲突或冲突已解决）。

1. **读取输入**：设计文档、契约草案、用户裁决结论（如有）、contract-harmonizer 报告。
2. **生成对内章节**：
   - 1.1 技术栈绑定 → 1.2 文件归属
   - 1.5 核心逻辑步骤（原子操作，含操作对象、具体操作、输入来源、输出去向、失败行为）
   - 1.8 状态机（表格形式，如适用）
   - 1.9 异常与边界条件（≥3 种，每种含精确触发阈值、处理策略、重试参数）
   - 1.10 验收测试场景（≥2 正 + 2 异常，Given-When-Then + 完整 JSON）
   - 1.11 注意事项与禁止行为（编码层面）
   - 1.12 文档详细度自检清单
   - 1.13 意图一致性声明
   - 1.14 外部接口契约清单
3. **合并落地规范**：
   对外章节（s16 生成，锁定不变） + 对内章节（本节生成） → 最终落地规范。
   对外接口章节标记 `【已锁定】`，内部章节标记 `【对内实现】`。
   输出路径：`docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-落地规范.md`
   模板参考：`references/agent-spec-template.md`
4. **写入契约文件**：
   - 按用户裁决和 harmonizer 报告，将对外类型写入 `docs/contracts/{module_id}/`
   - 格式必须符合 `references/schemas/contract.schema.json`
   - `x-defined-by` 填写本模块编号，`x-maturity` 初始为 `draft`
   - 复用已有契约的类型 → 不新建文件，在 `_module-index.json` 中记录 `reference_only: true`
5. **更新索引**：
   - 更新 `docs/contracts/_index.json`：追加本模块契约条目，更新 `x-consumers` 和 `last_updated`
   - 更新 `docs/功能设计/_contracts.md`：按模块编号插入/更新本模块条目（参考 `references/contract-index-template.md`）
   - 更新时间戳通过 `<skill-root>/scripts/get_timestamp.py` 获取
6. **上报**：说明落地规范路径、契约文件清单、索引更新状态。上报 `DONE`。

---

## 回退机制

> **硬性约束：发现意图缺陷时，无权自行妥协。**

**触发条件**（满足任一）：
1. 意图文档中的业务约束或验收标准在当前技术架构下**不可能实现**
2. 意图文档中的要求与项目技术栈设计文档存在**不可调和的冲突**
3. 意图文档中"留给规范阶段的技术决策"清单不完整，缺少关键技术决策项
4. 意图文档中的业务规则存在**逻辑矛盾**，无法转化为一致的技术实现

**回退流程**：
1. **立即停止当前所有工作**，不得继续生成设计文档或落地规范。
2. 上报 `ERROR`，`report` 中必须包含：
   - 标记为 **"意图缺陷"**
   - 缺陷内容（引用意图文档的具体章节和原文）
   - 技术不可行的依据
   - **回退路径**："请使用 module-intent-writer 回退到意图文档阶段，修正以下缺陷后重新冻结：\n- [缺陷 1 描述]\n- [缺陷 2 描述]\n修正后重新冻结意图文档，再调用 module-spec-writer 进入规范阶段。"

**禁止行为**：
- 禁止发现意图缺陷后自行修改或妥协
- 禁止绕过意图缺陷继续生成文档
- 禁止将意图缺陷隐藏在设计文档的注释中

---

## 质量检查清单

输出前逐项确认：

**通用检查**：
- [ ] 意图文档已存在且已冻结
- [ ] 无未解决的意图缺陷
- [ ] 两份文档的模块编号、名称、版本记录保持一致
- [ ] 所有"（待确认）"标注都有合理推断
- [ ] 已更新 `docs/功能设计/_contracts.md` 和 `docs/contracts/_index.json`
- [ ] 已参考项目技术栈设计文档和项目结构设计文档
- [ ] 版本记录每行以 `> ` 开头（引用块内）

**设计文档检查**：
- [ ] 聚焦"为什么这样实现"，非罗列步骤
- [ ] 架构权衡有具体方案对比和选择理由
- [ ] 不包含业务定位、用户旅程、验收标准、业务约束

**落地规范检查**：
- [ ] 对外接口类型已转为契约引用，不再写完整字段定义
- [ ] 契约文件已写入 `docs/contracts/{module_id}/`，格式符合 JSON Schema
- [ ] 异常场景 ≥ 3 种，每种含精确触发阈值、处理策略、重试参数
- [ ] 验收测试 ≥ 2 正 + 2 异常，Given-When-Then + 完整 JSON
- [ ] 包含"意图一致性声明"和"外部接口契约清单"章节
- [ ] 无偷懒表述（"等等"、"..."、"其他字段"、"参考其他模块"）

**契约文件检查**：
- [ ] `x-defined-by` 已正确填写为本模块编号
- [ ] `x-contract-type` 已正确标注
- [ ] 与已有契约的冲突已解决（以用户裁决为准）

---

## Message 上报契约

1. `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 完成任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <agent_id> --skill-id <skill_id>`；
   - 若脚本返回错误，根据 stderr 修正后重试；连续失败 3 次，将 `status` 改为 `ERROR` 并终止。
3. `message_id` 由脚本自动生成，无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。有多项待确认时一次性全部列出，不要分多次终止。
5. 终止前，最终回答必须包含脚本返回的 message 文件路径。

### 确认点上报（Confirmation Point）

本 Skill 对应以下 Stage 的 `confirmation_point=true`：
- **s14-spec-contradiction**：接收 spec-researcher 报告中的业务矛盾清单后，上报 `PENDING_CONFIRM`，等待用户裁决。
- **s18-spec-contract-conflict**：接收 contract-harmonizer 报告中的契约冲突清单后，上报 `PENDING_CONFIRM`，等待用户裁决。

**非确认点 Stage**（s12、s15、s16、s19）：完成任务后直接上报 `status: "DONE"`。

**确认问题设计原则**：
- 必须基于本 Stage 接收到的上游报告内容提问
- 提供具体选项（如 A/B/C/D）供用户选择
- 若用户选择"其他"，引导其描述替代方案

编排器恢复后，根据 `metadata.confirm_responses` 继续执行下游 Stage。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-spec-writer",
  "version": "2.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/skills/module-spec-writer/references/contract-input.md",
    "output": ".claude/skills/module-spec-writer/references/contract-output.md"
  },
  "task_modes": ["planning", "core", "extension"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
