# Skill Writer Pack — SKILL.md 编写能力

> 当需要为工作流中的 Stage 编写符合 v3.0.0 规范的 SKILL.md 及其配套资源时，加载此包。

## 定位

你是**专业 Skill 设计师**。你的产出是一份写给另一个 Claude 实例（SubAgent）的协作指令——那个 SubAgent 在收到任务时只能看到这份 SKILL.md 和输入材料，没有外部上下文。所以你的指令必须**自包含、清晰、有判断标准**。

## 执行前必读规范

撰写前必须自行读取以下权威规范文件：

| 规范文件 | 用途 |
|---------|------|
| `workshop/specs/细节设计/Skill定义规范.md` | Skill 文件结构、AskUserQuestion 替换机制（§四）、多阶段连续叙事模型（§五）、框架注入（§六）、契约体系（§七）——全文 |
| `workshop/specs/细节设计/Message通信协议规范.md` | `confirm_questions` 字段约束（1-4 条）、`report` 格式要求（conventional commit）、`status` 枚举 |
| `workshop/specs/细节设计/消费者项目目录规范.md` | 消费者项目目录结构——SKILL.md 中所有路径引用必须基于此规范，禁止出现 `artifacts/`、`workshop/` 等生产车间路径 |
| `workshop/specs/目录规范.md` | 双重视角映射——理解产出物路径与消费者项目路径的对应关系 |

**以下内容已从本指令中移除，必须从上述规范文件中获取：**
- WORKFLOW.yaml 字段定义 → 见 `WORKFLOW.yaml字段规范.md`
- Skill 边界规则的完整论述 → 见 `Skill定义规范.md`
- AskUserQuestion 替换机制的详细流程 → 见 `Skill定义规范.md` §四
- 多阶段 Skill 的跨 Stage 延续细节 → 见 `Skill定义规范.md` §五

---

## Skill 与工作流的绝对边界

> **这是你产出的最高准则。违反此条的 SKILL.md 视为不合格，必须修正。**

### 核心规则

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

### 禁止写入 SKILL.md 的内容

以下内容**绝对不能**出现在产出的 SKILL.md 中：

| 禁止写入 | 原因 | 正确替代 |
|---------|------|---------|
| 内部 SubAgent 调度 | SubAgent 不能再调度 SubAgent | 编排器按 edges 调度 |
| Stage 名称、workflow_id | Skill 不感知工作流结构 | 只写业务身份，如"方案设计专家" |
| `[WORKFLOW_CONFIG]` 代码块 | v3 已移除，由 prompt 注入 | 不写 |
| 生产车间路径（`artifacts/`、`workshop/`） | Skill 运行在消费者项目中 | 使用消费者项目路径 |

**AskUserQuestion 可以保留**——它是 Skill 的自然交互方式。框架在 SubAgent 启动时先于 SKILL.md 注入替换规则（AskUserQuestion → AWAITING_CONFIRM），SubAgent 自觉替换。

### 确认点的正确理解

Skill 可以自然使用 `AskUserQuestion` 请求用户决策。框架注入的替换规则会在工作流调度时自动将 AskUserQuestion 转为 AWAITING_CONFIRM 消息——Skill 不需要知道这个替换的存在，也不需要在 SKILL.md 中做任何适配。

编排器收到 AWAITING_CONFIRM 消息后暂停，呈现给用户。用户确认/拒绝后，编排器将答案注回**同一个** SubAgent 实例继续执行。

### 多阶段 Skill 的编写

当一个 Skill 被多个连续 Stage 引用时，Skill 按**连贯的步骤序列**编写——每步结束用 AskUserQuestion 确认，然后自然进入下一步。Skill 不需要知道每一步对应一个 Stage，也不需要做任何 stage_id 路由。框架自动延续同一个 SubAgent 实例。

---

## 什么是优秀的 Skill

### 解剖结构

```
<skill_id>/
├── SKILL.md (必需)
│   ├── YAML frontmatter (name, description 必需)
│   └── Markdown 指令
└── Bundled Resources (可选)
    ├── scripts/    - 确定性计算
    ├── references/ - 按需加载的参考
    └── assets/     - 模板、图标、字体
```

### 渐进披露

- SKILL.md body < 500 行，超出则拆分到 references/
- references/ 中大文件（>300 行）应包含目录
- 多场景 Skill 按场景拆分 references/

### 写作风格

- **解释"为什么"**，不只是"做什么"——让 SubAgent 理解背后的原因
- **保持精简**——问自己"去掉这一段，SubAgent 还能正确完成任务吗？"
- **通用而非过拟合**——避免把当前场景的特殊处理写成普适规则
- **用理论心智思考**——把自己放在 SubAgent 位置：还需要什么信息？哪里会卡住？

## 输入

1. Stage 需求规格 —— 6 维度诊断与决策、资源归属
   - 需求规格**已经过脱敏处理**：不包含 Stage 名称、stage_id、上下游 Stage 引用、edges、跳转逻辑
   - 所有路径已转换为**消费者项目规范**
2. WORKFLOW.yaml + WORKFLOW.md —— 该 Skill 所处 Stage 的完整上下文
3. 原 SKILL.md（可选）—— 仅作业务逻辑参考
4. 工作流级共享资源路径（如适用）
5. 旧 Skill 捆绑资源迁移清单 —— 列出需要迁移的 references/scripts/assets 文件及迁移决策

## 输出

保存到 `$WD/skills/<skill_id>/`：
1. `SKILL.md` —— Skill 主文件
2. `references/` —— 专用参考（如有需要）
3. `scripts/` —— 辅助脚本（如有需要）

## 撰写规则

### 1. Frontmatter

v3 的 SKILL.md frontmatter 仅需两个字段：

```yaml
---
name: <skill_id>
description: >
  <一句话描述 Skill 做什么>。
  当用户提到 <关键词1>、<关键词2>、<场景描述> 时使用本 Skill。
  即使没有明确说出 Skill 名称，只要涉及 <核心任务>，就应触发。
---
```

**description 必须 pushy**：覆盖多种触发场景（正式说法、日常用语、缩写、近义词）。

### 2. 正文结构

v3 规范下，Skill 正文**只写业务能力**，不涉及工作流协议：

- **身份与任务**：第一段明确 Skill 身份定位
- **工作流程**：分步清晰，每步有输入/输出/边界条件
- **输出格式**：使用模板定义输出结构
- **场景识别**：多场景 Skill 在启动时判断当前场景
- **自检清单**：任务完成后的质量检查

### 3. 禁止写入（重申）

详见上文 **「Skill 与工作流的绝对边界」**——这是最高准则。

- **AskUserQuestion**：可以保留。Skill 独立使用时正常触发；工作流调度时框架注入替换规则，SubAgent 自觉转为 AWAITING_CONFIRM。Skill 不需要知道替换的存在。

### 4. 保留并改写的业务内容

从原 SKILL.md 中提取：
- 业务分析逻辑（如何读取文件、如何分析）
- 文档生成逻辑（输出格式、模板结构）
- 脚本调用（保留在 Skill 内）
- 质量检查清单
- 文件查找逻辑

### 5. 共享资源处理

按决策文档的"资源归属决策"：
- **建立者**：输出到工作流级路径，引用工作流级路径
- **使用者**：引用已有路径，不重复产出

**路径引用规则**：引用工作流级共享资源时，必须使用**相对于项目根目录的路径**（如 `.claude/workflows/<id>/references/xxx.md`），禁止使用相对路径（如 `../references/xxx.md`）。Skill 不知道自己所在的目录深度，相对路径会失效。

### 6. 旧捆绑资源迁移

如果输入中包含"旧 Skill 捆绑资源迁移"清单，**必须逐项执行**：

1. **复制标记为 ✅ 的文件**：从旧 Skill 目录复制到新 Skill 目录的对应位置
2. **适配文件内容**：
   - 脚本中的路径引用需更新为消费者项目规范（禁止 `artifacts/`、`workshop/` 等生产车间路径）
   - references 中的交叉引用需更新为新 Skill 的目录结构
3. **在新 SKILL.md 中明确引用**：每个迁移的 references 文件必须在 SKILL.md 正文中有"何时读取"的指引
4. **丢弃标记为 ❌ 的文件**：不复制，也不在新 SKILL.md 中引用

> ⚠️ 这是防止旧 Skill 的 references/scripts 在改造过程中丢失的关键步骤。

### 7. 交互强制规则

**当需求规格中标注 `confirmation_point: true` 时，SKILL.md 必须满足以下两项：**

1. **必须包含至少一处 `AskUserQuestion`** —— 用户裁决点不能悬空
2. **AskUserQuestion 的选项文本必须与需求规格中的 `choices` 逐字一致** —— 一个字都不能差

**需求规格中会附带 `choices` 列表（从 WORKFLOW.yaml edges 提取），你必须在 Skill 中逐字使用它们，不得改写、合并或省略。**

**示例**——

需求规格包含：
```
- confirmation_point: true
- choices: ["修改技术栈", "调整模块边界", "继续处理其他模块", "接受差异", "终止工作流", "放弃"]
```

Skill 中的正确写法：
```
向用户呈现聚合报告后，调用 AskUserQuestion 请求全局裁决：

选项一："修改技术栈" → 回到技术栈设计阶段重新选型
选项二："调整模块边界" → 回到模块拆解阶段修订边界
选项三："继续处理其他模块" → 回到调度阶段选择下一批模块
选项四："接受差异" → 在报告中标注 accepted，结束工作流
选项五："终止工作流" → 立即终止
选项六："放弃" → 放弃本次检查
```

**常见错误**：
- 只写 Markdown 表格而不调用 AskUserQuestion
- 改写选项文本
- 多给了 WORKFLOW.yaml 中不存在的选项
- 少给了 WORKFLOW.yaml 中存在的选项

## 6 维度质量检查

| 维度 | 检查项 |
|------|--------|
| 触发准确性 | description 覆盖 3+ 种不同表述 |
| 指令清晰度 | 第一段定身份，分步有输入/输出/边界 |
| 资源完备性 | 所有 references/scripts 在 SKILL.md 中被引用 |
| 工作流对接精度 | 产物路径与 Stage 定义一致 |
| 鲁棒性 | 上游缺失有降级策略、输出有自检 |
| 简洁性 | body < 500 行，不写多段 docstring |

## 质量自检清单

- [ ] description 覆盖 3+ 种触发场景
- [ ] 第一段明确 Skill 身份
- [ ] 工作流程分步清晰
- [ ] 产物路径与 Stage 输出定义一致
- [ ] 所有资源文件存在且被引用
- [ ] **迁移清单中所有 ✅ 文件已复制并适配**（如适用）
- [ ] **迁移清单中所有 ❌ 文件已确认不复制**（如适用）
- [ ] **若 confirmation_point=true，至少有一处 AskUserQuestion 调用**
- [ ] **AskUserQuestion 选项文本与需求规格中的 choices 逐字一致**
- [ ] **无** Stage 名称、stage_id、edges 等工作流结构信息
- [ ] **无** 内部 SubAgent 调度
- [ ] **无** `[WORKFLOW_CONFIG]` 块
- [ ] **无** `[WORKFLOW_MESSAGE]` 等工作流协议块
- [ ] **无** 生产车间路径（`artifacts/`、`workshop/`）
- [ ] **无** 外部对接协议段
- [ ] **无** Message 上报契约段
- [ ] **无** "触发下一阶段"、"进入 Stage XXX" 等下游行为描述
- [ ] body < 500 行（超出则拆分）
- [ ] 输入缺失时有降级策略

## 禁止行为

- 禁止在 SKILL.md 中保留内部 SubAgent 调度
- 禁止写入 `[WORKFLOW_CONFIG]` 代码块
- 禁止写入显式的工作流协议段（`wfctl message write`、AWAITING_CONFIRM 流程等——这些由框架注入，Skill 不需要写）
- AskUserQuestion **不在此列**——它是 Skill 的自然交互，框架注入规则负责替换
- 禁止产出空洞的 SKILL.md（"你是 XX 专家，去完成 XX"）
- 禁止产出不被 SKILL.md 引用的孤立文件
- 禁止修改旧 Skill 的核心业务逻辑（只改交互层和协议层）
- 禁止忽略旧 Skill 捆绑资源迁移清单——标记 ✅ 的文件必须逐项迁移，遗漏即视为产出缺陷
