---
name: design-tech-stack
description: >
  根据功能需求设计项目技术栈，或基于已有技术栈进行增量更新与评估。
  当 Kimi 需要执行以下任务时必须优先使用本 skill：
  为新项目从零设计完整技术架构与选型方案、基于功能需求文档输出可落地的技术栈设计文档、
  对现有技术栈进行增量更新或架构演进评估、需要系统化的技术选型决策流程与标准化输出模板。
  核心工作方式：读取设计文档与约束条件，在关键技术选型关节提供选项对比与约束分析，按分层维度输出结构化技术栈设计文档。
  每次调用输出技术栈设计文档到 docs/ 目录。
  必须优先使用本 skill 当用户要求"技术栈"、"技术选型"、"架构设计"、"选什么框架"、"用什么数据库"、"技术方案"时。
---

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/design-tech-stack/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/design-tech-stack/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `workflow_ref_dir`, `workflow_refs`（可选）
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id` 不一致：立即终止，上报 `ERROR`。

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

- **方案级降级**（算法变更、精度降低、功能裁剪）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（分批计算、降采样、稀疏矩阵）：可自主执行，但必须在 `report` 中说明具体措施和影响。

## 工作流上下文

本 Skill 是工作流 `project-design-pipeline` 中的 Stage 执行器。

**上游 Stage**：无（作为工作流起点之一）
- 本 Skill 直接读取用户输入或 `docs/` 目录下的设计文档启动

**下游 Stage**：`s04-module-breakdown`（进入 Skill `module-breakdown-designer`）
- 本 Skill 产出的技术栈设计文档将作为下游模块拆解的架构输入
- 确保输出文件保存到 `docs/` 目录，以便下游 Skill 扫描读取

## 核心原则

- **需求对齐**：每项选型需明确对应功能需求与约束条件。
- **落地优先**：优先成熟、社区活跃、团队可驾驭的方案。
- **风险可控**：新引入技术必须给出后备方案与迁移成本评估。
- **中文输出**：本 skill 所有输出文本（含文档内容）使用中文，代码与专有名词除外。

## 执行逻辑（按 Stage）

根据注入的 `stage_id` 执行对应阶段：

### Stage `s01-collect-requirements`：需求与上下文确认

1. 读取 `upstream_files` 中提供的设计文档，或扫描 `docs/` 目录下的需求/架构 Markdown 文件。
2. 提取并整理以下约束：
   - 团队技术背景（如有说明）
   - 部署环境与运维能力
   - 预期规模（并发、数据量）
   - 合规与预算限制
   - 已有基础设施
3. 判定场景：
   - **情景 A（全新设计）**：无已有技术栈文档 → 构建完整体系。
   - **情景 B（增量更新）**：存在原技术栈文档 → 基于现有架构适配新需求，优先复用已有组件。
4. 输出需求上下文摘要到 `.tmp/<workflow_instance_id>/requirements-context.md`。
5. **上报 `PENDING_CONFIRM`**，等待用户确认需求上下文是否完整。

### Stage `s02-architecture-selection`：架构模式与关键关节分析

1. 读取 `s01` 产出的需求上下文。
2. 在以下关键关节点进行技术选型分析（按需选择，非全部必须）：
   - 前端框架、后端语言、数据存储
   - 实时通信、认证方案、AI 集成
   - 部署方式、架构模式
3. 每个关节点提供 2-4 个候选方案，附一句话优劣说明。
4. 基于约束条件给出首选推荐及理由。
5. 如需对比细节，按需读取 `references/decision-matrix.md`。
6. 输出技术选型决策记录到 `.tmp/<workflow_instance_id>/architecture-decisions.md`。
7. **上报 `PENDING_CONFIRM`**，等待用户确认或调整选型方案。

### Stage `s03-tech-stack-output`：分层方案细化与文档输出

1. 读取已确认的技术选型决策。
2. 按以下维度逐层细化：
   - **前端**：框架、构建工具、状态管理、UI 库、测试方案
   - **后端**：语言/框架、API 风格、异步模型、测试方案
   - **存储**：关系型数据库、缓存、NoSQL、对象存储、向量数据库（如有）
   - **中间件**：消息队列、搜索引擎、任务调度
   - **实时通信**：WebSocket / SSE / 长轮询 / MQTT（如有）
   - **安全**：认证、鉴权、传输加密、敏感数据处理
   - **运维**：部署编排、CI/CD、监控告警、日志收集
3. 每项选型标注：核心作用、版本建议、需求关联、变更影响（增量更新场景）。
4. 生成完整技术栈概览表格。
5. 按 `references/output-template.md` 生成结构化 Markdown 文档：
   - **情景 A**：`docs/项目名称-技术栈设计.md`
   - **情景 B**：全量重写原技术栈文档（保留原结构，头部追加版本记录）
6. **上报 `PENDING_CONFIRM`**，等待用户确认技术栈设计文档。

## 约束与禁忌

- **禁止单方面决定**：在关节点未明确记录对比前，不要直接输出唯一结论。
- **禁止忽略约束**：若文档明确说"团队无 K8s 经验"，不要推荐 K8s 部署。
- **禁止过度设计**：日活 < 1k 的项目不要推荐微服务。
- **变更最小化**：情景 B 下，若现有组件能覆盖新需求，必须明确说"复用现有 XX，无需引入新技术"。

## 参考文件索引

| 文件 | 用途 | 加载时机 |
|------|------|----------|
| `references/decision-matrix.md` | 技术选型决策维度与常见方案对比 | `s02` 关节分析或 `s03` 细化对比 |
| `references/output-template.md` | 标准化技术栈设计文档模板 | `s03` 输出文档 |

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

### 确认点上报（Confirmation Point）

本 Skill 对应 stage 的 `confirmation_point=true`。完成任务后：

1. **不要直接上报 `DONE`**
2. 生成 message 草稿 JSON，设置 `status: "PENDING_CONFIRM"`
3. 设置 `confirm_questions`（1-4 个，必须具体、可回答）：
   - `s01` 示例："以上提取的需求上下文是否完整？是否有遗漏的约束条件？"
   - `s02` 示例："以上技术选型方案是否符合预期？是否有需要替换的组件？"
   - `s03` 示例："以上技术栈设计文档是否满足项目需求？是否需要调整某些组件？"
4. 调用 `write_message.py` 上报
5. 终止执行，等待编排器处理用户确认

用户确认后，编排器会自动将本 stage 标记为 DONE，并走 `condition: confirmed` 的 edge 解锁下游。

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "design-tech-stack",
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
