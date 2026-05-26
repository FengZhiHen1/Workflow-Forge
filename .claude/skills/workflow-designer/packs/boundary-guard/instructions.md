# Boundary Guard Pack — Skill-工作流边界红线

> **此包为强制加载包。** 在编写任何 SKILL.md 时，必须内化并遵守以下规则。在产出 SKILL.md 后，必须运行 `scripts/validate.py` 进行扫描。

## 核心规则

**Skill 绝对不能感知、干涉工作流。** Skill 是一个盲执行者。它收到输入材料、完成任务、上报 DONE——仅此而已。它不知道自己在工作流的哪个 Stage，不知道上游是谁、下游是谁，不知道编排器的存在。

| Skill 知道的事 | Skill 不该知道的事 |
|---------------|-------------------|
| 它的任务是什么 | 它在哪个 Stage 中 |
| 输入材料在哪里 | 上游 Stage 是谁、产出什么 |
| 产出放在哪里 | 下游 Stage 会怎么消费它的产出 |
| 任务完成后上报 DONE | DONE 之后编排器会做什么 |
| 输入缺失时降级或报错 | 编排器的状态机怎么处理它的报错 |

### 为什么必须隔离

- **可复用**：同一个 Skill 应该能用在不同的工作流中，甚至脱离工作流独立使用
- **可测试**：Skill 不感知工作流协议，就能脱离工作流单独测试
- **工作流重构不波及 Skill**：改 Stage 名、调 edges 时，Skill 不应该需要任何修改
- **复杂度可控**：编排器已经足够复杂。每个 Skill 都去操心全局状态，系统会不可维护

## 禁止写入 SKILL.md 的内容

以下内容**绝对不能**出现在产出的 SKILL.md 中：

| 禁止写入 | 原因 | 正确替代 |
|---------|------|---------|
| 内部 SubAgent 调度 | SubAgent 不能再调度 SubAgent | 编排器按 edges 调度 |
| Stage 名称、workflow_id | Skill 不感知工作流结构 | 只写业务身份，如"方案设计专家" |
| `[WORKFLOW_CONFIG]` 代码块 | v3 已移除，由 prompt 注入 | 不写 |
| 生产车间路径（`artifacts/`、`workshop/`） | Skill 运行在消费者项目中 | 使用消费者项目路径 |

**AskUserQuestion 可以保留**——它是 Skill 的自然交互方式。框架在 SubAgent 启动时先于 SKILL.md 注入替换规则（AskUserQuestion → AWAITING_CONFIRM），SubAgent 自觉替换。Skill 不需要知道替换的存在。

## 确认点的正确理解

Skill 可以自然使用 `AskUserQuestion` 请求用户决策。框架注入的替换规则会在工作流调度时自动将 AskUserQuestion 转为 AWAITING_CONFIRM 消息——Skill 不需要知道这个替换的存在，也不需要在 SKILL.md 中做任何适配。

编排器收到 AWAITING_CONFIRM 消息后暂停，呈现给用户。用户确认/拒绝后，编排器将答案注回**同一个** SubAgent 实例继续执行。

## 多阶段 Skill 的编写

当一个 Skill 被多个连续 Stage 引用时，Skill 按**连贯的步骤序列**编写——每步结束用 AskUserQuestion 确认，然后自然进入下一步。Skill 不需要知道每一步对应一个 Stage，也不需要做任何 stage_id 路由。框架自动延续同一个 SubAgent 实例。

## 路径引用规则

引用工作流级共享资源时，必须使用**相对于项目根目录的路径**（如 `.claude/workflows/<id>/references/xxx.md`），禁止使用相对路径（如 `../references/xxx.md`）。Skill 不知道自己在 `.claude/skills/<id>/` 下，相对路径会因目录深度不同而失效。

## 交互强制规则

**当 AskUserQuestion 用于路由选择（决定下游路径）时，SKILL.md 必须满足以下两项：**

1. **必须包含至少一处 `AskUserQuestion`** —— 用户裁决点不能悬空
2. **AskUserQuestion 的选项文本必须与需求规格中的 `choices` 逐字一致** —— 一个字都不能差

## 自检清单（产出 SKILL.md 前必做）

- [ ] **无** Stage 名称、stage_id、edges 等工作流结构信息
- [ ] **无** 内部 SubAgent 调度
- [ ] **无** `[WORKFLOW_CONFIG]` 块
- [ ] **无** `[WORKFLOW_MESSAGE]` 等工作流协议块
- [ ] **无** 生产车间路径（`artifacts/`、`workshop/`）
- [ ] **无** "触发下一阶段"、"进入 Stage XXX" 等下游行为描述
- [ ] **无** 外部对接协议段
- [ ] **无** Message 上报契约段
- [ ] **若需要用户交互，至少有一处 AskUserQuestion 调用**
- [ ] **AskUserQuestion 区分场景：路由选择类须与 WORKFLOW.yaml edges 的 choices 逐字一致；内部澄清类须清晰互斥、可自由定义**

## 自动校验

产出 SKILL.md 后，立即运行：
```bash
python .claude/skills/workflow-designer/packs/boundary-guard/scripts/validate.py --skill-md <path>
```

Critical 违规则必须修正后才能继续。
