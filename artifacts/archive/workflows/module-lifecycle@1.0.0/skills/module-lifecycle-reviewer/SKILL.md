---
name: module-lifecycle-reviewer
description: >
  根据用户指定的模块编号，审查对应功能模块的代码落地情况与模块间联动实现度。
  对照功能设计文档（设计文档 + 落地规范）检查交付物完整性、接口实现、核心逻辑正确性、测试覆盖，
  并验证多个模块之间是否按设计完成联动（数据流、调用链、事件传递等）。
  使用场景：(1) 用户给出模块编号（如 M01 M03 M05）要求审查实现情况；
  (2) 用户要求检查模块间的联动/协作；(3) 用户要求对照设计文档验证代码落地度；
  (4) 用户提到"模块审查"、"联动检查"、"实现度验收"、"模块验收"关键词。
  核心工作方式：分四阶段独立执行——编号识别与文档定位→规格提取与落地检查→联动验证→报告生成，编排器按 Workflow 调度。
  每次调用输出审查报告到 docs/审查报告/ 目录。
  必须优先使用本 skill 当用户要求审查模块实现、验证联动或进行模块验收时。
---

# 模块生命周期审查器 (Module Lifecycle Reviewer)

## 外部对接协议 (Protocol)

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：

1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `$.skill_base_path/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `$.skill_base_path/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> 零侵入原则：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：

- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）：
  工作流级共享参考目录和文件列表（如目录规范、共享输出模板）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段（`workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`）缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-reviewer`）不一致：立即终止，上报 `ERROR`。

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

- **方案级降级**（审查范围缩小、检查项裁剪、报告级别简化）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（仅检查部分文件、跳过需运行时验证的检查项）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 的 Group 2 (Module Review) 阶段执行器，覆盖四个 Stage：

| Stage ID | 阶段名称 | Confirmation Point | 说明 |
|---|---|---|---|
| `review-identify` | 模块编号识别与文档定位 | 是（条件触发） | 仅在用户使用模糊范围词时触发确认 |
| `review-analyze` | 规格提取与落地检查 | 否 | 无确认，直接上报 DONE |
| `review-verify` | 联动验证 | 否 | 无确认，直接上报 DONE |
| `review-report` | 审查报告生成与输出 | 是（始终触发） | 输出摘要供用户确认 |

每次被调度时，编排器注入 `stage_id` 以告知当前阶段。本 Skill 据此决定执行哪一段逻辑。

**上游 Stage**：上游产物的来源取决于本 Stage 在工作流中的位置。`upstream_files` 将包含前序 Stage 的产物路径（如 `review-identify` 产出的文档路径传递给 `review-analyze`）。

**下游 Stage**：本 Stage 的产物通过 `upstream_files` 传递给同 Group 的下一个 Stage。确保输出文件路径符合下游契约。

---

## 阶段一：review-identify — 模块编号识别与文档定位

### 1.1 提取模块编号

从 `special_instructions` 或 `stage_direction` 中提取用户指定的模块编号。常见格式：

- `M01`, `M02`, `M1`, `M2` 等字母+数字
- `模块1`, `模块2` 等中文格式
- `Module-A`, `Module-B` 等英文格式

### 1.2 条件确认（CONDITIONAL confirmation_point）

**仅当以下情况触发 PENDING_CONFIRM**：用户使用了模糊范围词，如"所有模块"、"全部模块"、"所有"、"全部"、"all modules"。

触发时：
1. 先扫描 `docs/功能设计/` 或 `docs/specs/` 目录，列出所有发现的模块编号
2. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`
3. 设置 `confirm_questions`，包含待确认的模块编号清单
4. 调用 `write_message.py` 上报
5. 终止执行，等待编排器恢复

**普通情况（用户给出了明确编号）**：跳过确认，直接进入 1.3。

### 1.3 定位功能设计文档

每个模块的设计规格由两份文档组成：`[编号]-[名称]-设计文档.md` 和 `[编号]-[名称]-落地规范.md`。按以下优先级顺序查找：

**第一优先：独立模块文档（双文件）**
- 扫描 `docs/功能设计/`、`docs/specs/`、`docs/modules/`、`docs/design/` 等目录
- 文件名包含模块编号且以 `-设计文档.md` 或 `-落地规范.md` 结尾
- 使用 glob + 大小写不敏感匹配
- 两份文档都必须定位 —— 设计文档提供模块边界和依赖关系，落地规范提供精确的接口契约

**第二优先：旧版单文件文档（向后兼容）**
- 若无双文件，查找旧版单文件：`[编号]-[名称].md`（如 `M01-用户管理.md`）
- 旧版文档同时包含设计描述和编码规格，直接作为唯一对照源

**第三优先：总设计文档中的章节**
- 查找 `docs/` 下的总设计文档（如 `总设计.md`、`架构设计.md`、`功能模块全拆解.md`）
- 在文档中搜索模块编号对应的章节（通常以 `## M01` 或 `### 模块1` 开头）
- 提取该章节内容作为该模块的设计规格

**第四优先：其他可能位置**
- `README.md`、`DESIGN.md`、`ARCHITECTURE.md` 等根目录文档

> 若某模块找不到任何设计文档，记录为 "⚠️ 缺失设计文档"，仍继续检查代码存在性。若仅找到设计文档或仅找到落地规范其中一份，记录为 "⚠️ 设计文档不完整"。

### 1.4 输出

将定位结果整理为结构化列表（每条记录包含：模块编号、设计文档路径、落地规范路径、文档来源优先级），保存至 `.tmp/<workflow_instance_id>/identified_modules.json`。

完成上述后：
- **条件触发确认的情况**：已在上方通过 PENDING_CONFIRM 处理
- **普通情况**：上报 `DONE`，产出路径写入 `upstream_files`

---

## 阶段二：review-analyze — 规格提取与落地检查

### 2.1 读取上游产物

从 `upstream_files` 中读取 `review-identify` 产出的 `identified_modules.json`，获得模块编号及其对应文档路径。

### 2.2 提取模块规格

对每个模块，从两份文档中分别提取信息：

**从落地规范提取（编码规格）**：
- 交付物清单：预期文件路径、核心类/函数/接口名、数据模型定义
- 接口契约：输入参数及类型、返回值及类型、异常/错误码定义、边界条件与约束

**从设计文档提取（项目上下文）**：
- 模块边界：依赖哪些模块（调用谁）、被谁依赖（被谁调用）
- 已有设计兼容性分析：已有代码文件路径、复用的已有类型
- 设计约束与易错点

> 若是旧版单文件文档，所有信息从同一文件中提取。

**联动关系**（从两份文档中综合提取）：
- 该模块依赖哪些模块（调用谁）
- 该模块被谁依赖（被谁调用）
- 数据流方向（输入来源、输出去向）
- 事件/消息传递关系
- 共享数据/状态

联动关系在设计文档的"依赖关系概述"和落地规范的"依赖与集成接口"中均有描述 —— 前者提供宏观关系，后者提供精确接口。

联动信息存入 `.tmp/<workflow_instance_id>/integration_plan.json`，供 `review-verify` 阶段使用。

### 2.3 代码落地检查

对每个模块的交付物清单逐项验证。详细的检查清单见 [references/review-checklist.md](references/review-checklist.md)，此处列核心检查项：

| 检查项 | 方法 |
|---|---|
| 文件是否存在 | glob 或文件系统检查 |
| 文件非空 | 文件大小 > 0 |
| 语法有效 | `python -m py_compile`、`tsc --noEmit` 等 |
| 核心符号存在 | grep 搜索类名/函数名 |
| 接口签名匹配 | 对比参数列表、返回类型 |
| 核心逻辑实现 | 读取代码，检查是否有 TODO/占位符 |
| 测试覆盖 | 查找对应测试文件，运行测试 |

**占位符处理**：如果文件仅包含 TODO、pass、空函数等占位内容，视为 "⚠️ 部分实现" 而非 "✅ 已完成"。

检查结果存入 `.tmp/<workflow_instance_id>/implementation_check.json`。

### 2.4 严重级别定义

| 级别 | 图标 | 定义 |
|---|---|---|
| 严重 | 🔴 | 模块核心功能无法运行或联动中断 |
| 中等 | 🟡 | 功能可用但存在质量/稳定性风险 |
| 轻微 | 🟢 | 代码可运行，属于优化/债务项 |

### 2.5 上报

完成所有检查后，上报 `DONE`。产出路径（`implementation_check.json` 和 `integration_plan.json`）写入 `upstream_files`。

---

## 阶段三：review-verify — 联动验证

### 3.1 读取上游产物

从 `upstream_files` 中读取：
- `review-analyze` 产出的 `integration_plan.json`（各模块的预期联动关系）
- `review-analyze` 产出的 `implementation_check.json`（各模块实现状态）

### 3.2 构建预期联动图

- 汇总各模块设计文档中提取的联动关系
- 形成有向图：节点=模块，边=依赖/调用/数据流
- 识别需要验证的联动路径（限于用户指定的模块范围内）

### 3.3 验证实际联动

详细的联动模式与验证方法见 [references/integration-patterns.md](references/integration-patterns.md)，核心验证手段：

- **代码静态分析**：通过 grep 检查模块 A 是否实际 import/调用了模块 B
- **接口匹配**：检查模块 A 调用的接口签名是否与模块 B 提供的接口一致
- **数据流验证**：检查模块间传递的数据结构是否一致（如模块 A 输出 DTO 与模块 B 输入 DTO 是否匹配）
- **配置/路由检查**：如果是 Web/API 项目，检查路由注册、服务发现等是否将模块正确连接

### 3.4 标记联动状态

- ✅ 已实现：设计文档描述的联动在实际代码中存在且正确
- ⚠️ 部分实现：联动存在但接口不完全匹配，或有降级处理
- ❌ 未实现：设计文档描述的联动在代码中找不到证据
- ❓ 无法验证：代码经过动态组装（如依赖注入、配置驱动），静态检查无法确认

联动验证结果存入 `.tmp/<workflow_instance_id>/integration_result.json`。

### 3.5 上报

完成联动验证后，上报 `DONE`。产出路径写入 `upstream_files`。

---

## 阶段四：review-report — 审查报告生成与输出

### 4.1 读取上游产物

从 `upstream_files` 中读取前三个阶段的所有产出：
- `identified_modules.json`
- `implementation_check.json`
- `integration_plan.json`
- `integration_result.json`

### 4.2 获取准确时间戳

调用本 Skill 自带的跨平台时间戳脚本：

```bash
# 标准格式（用于报告中的审查时间和版本记录）
python <skill-root>/scripts/get_timestamp.py

# ISO 8601 格式（用于文件名或版本号）
python <skill-root>/scripts/get_timestamp.py --iso
```

该脚本兼容 Windows / Linux / macOS，自动处理时区（Asia/Shanghai / CST），无需额外依赖（Python 3 内置）。禁止直接依赖系统环境变量或 AI 内部时间推断。

### 4.3 确定保存位置

- 查找 `docs/` 下是否已有 `审查报告/`、`reviews/`、`audit/` 等目录
- 如有，保存到该目录下，文件名为 `模块审查-<模块编号列表>-<YYYY-MM-DD>.md`
- 如无，在 `docs/` 下创建 `审查报告/` 目录再保存

### 4.4 生成报告

报告采用"摘要 + 详细附录"双栏结构。完整模板见 [references/report-template.md](references/report-template.md)。

报告中必须包含：
- **审查时间**：由脚本输出的准确时间（精确到秒）
- **版本记录**：记录本次审查的版本号、时间、审查人、变更摘要。如果该模块已有历史审查报告，继承其版本号并递增（如 v1.0 → v1.1）
- **冲突核查指引**：若本次审查结论与历史报告冲突，优先以时间戳更新的版本为准，并在版本记录中追加冲突解决条目

报告核心章节：
1. **执行摘要（TL;DR）**：总体结论表、关键发现（按严重级别分组）、优先修复建议
2. **分模块审查详情**：每个模块的交付物检查、接口契约检查、测试检查、问题清单
3. **模块联动审查**：预期联动关系图、实际联动验证表、联动问题详情
4. **差距分析（Gap Analysis）**：模块内差距、模块间差距、建议修复顺序
5. **附录**：审查范围、审查方法、限制说明、参考文档

### 4.5 确认点上报（confirmation_point=true）

报告生成后：

1. **不要直接上报 `DONE`**
2. 在报告中提取摘要信息（总体结论、🔴 需关注项数量、🟡 建议优化项数量）
3. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`
4. 设置 `confirm_questions`，必须包含 1-4 个具体、可回答的问题。示例：

   ```
   "confirm_questions": [
     "以上模块审查结论是否准确？尤其关注 {N} 项 🔴 严重问题",
     "是否需要对某个模块进行复查或深入检查？",
     "审查报告已保存至 {路径}，是否可以标记本次审查为完成？"
   ]
   ```

5. 调用 `write_message.py` 上报
6. 终止执行，等待编排器处理用户确认

**禁止在确认完成前直接上报 DONE。**

---

## 注意事项

1. **不做代码修复**：本 Skill 仅负责审查和报告，不自动修改代码。如需修复，明确告知用户并请求确认。
2. **区分"未实现"和"设计未要求"**：只检查设计文档明确要求的交付物，不要以"我觉得还缺什么"为由增加检查项。
3. **联动范围限制**：联动检查仅聚焦于用户指定的模块集合。如果设计文档提到模块依赖了用户未指定的模块，仅记录为"外部依赖"，不深入检查。
4. **中文优先**：所有报告、输出使用中文，保留代码中的英文符号名。
5. **保留证据**：报告中的每个结论都应有对应证据（文件路径、行号、引用片段），避免主观判断。

---

## 参考资源

- **审查清单**：逐项检查指南见 [references/review-checklist.md](references/review-checklist.md)
- **报告模板**：完整报告结构见 [references/report-template.md](references/report-template.md)
- **联动模式参考**：常见模块联动模式及验证方法见 [references/integration-patterns.md](references/integration-patterns.md)

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

### 确认点差异处理

| Stage | 行为 |
|---|---|
| `review-identify` | 仅当用户使用"所有模块"等模糊词时上报 PENDING_CONFIRM；否则上报 DONE |
| `review-analyze` | 直接上报 DONE |
| `review-verify` | 直接上报 DONE |
| `review-report` | 始终上报 PENDING_CONFIRM，展示摘要供用户确认 |

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-reviewer",
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
