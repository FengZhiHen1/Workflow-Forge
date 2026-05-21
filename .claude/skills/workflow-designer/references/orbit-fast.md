# 快速通道流程

前置：已完成 Phase 0，已读取 `orbit-common.md`。

适用场景：单 Skill 改造、小版本升级、简单工作流。时间目标 15-30 分钟。

---

## Phase 1-Fast 目标：产出最小可用的 WORKFLOW.yaml

### 核心任务
1. 读取输入（旧 SKILL.md 全文 / 已有 WORKFLOW.yaml + md）
2. 提取关键信息：逻辑步骤数、AskUserQuestion 点、SubAgent 点
3. 与用户对齐：目标清晰度 + 确认点映射（1-2 轮对话）
4. 产出 Stage 结构草案（套用 `references/workflow-patterns.md` 内置模式模板）
5. 调度 designer-fast（`references/designer-prompt-fast.md`）生成 WORKFLOW.yaml + WORKFLOW.md + skill_manifest.json

### 质量门控
- 【门控】调度 designer-fast 前，必须确认用户已认可 Stage 结构草案
- 【门控】designer-fast 产出后，必须通过 L1 校验
- 【门控】执行 L2 快速检查（确认点密度、死 Stage、循环出口）

### 可选策略
- 输入是简单旧 Skill（<200 行，AskUserQuestion≤2，SubAgent≤1）→ 跳过 analyzer，主 Agent 内联提取
- 用户目标非常明确 → 对齐轮次压缩到 1 轮
- 模板套用后用户不满意 → 允许升级到标准轨道

---

## Phase 2-Fast 目标：串行生成所有 SKILL.md

### 核心任务
按 Stage 顺序串行生成 Skill。

1. 按 Stage 顺序逐个调度 skill-writer
2. 每个 Skill 生成后展示关键部分（description + 工作流程摘要），轻量确认
3. 全部生成后执行 L1 校验（含 skills-dir + 子工作流）

### 质量门控
- 【门控】每个 SKILL.md 生成后，通过 `validate_skill_boundary.py` 扫描
- 【门控】全部 Skill 生成后，执行完整 L1 校验
- 【门控】转正确认（强制门控）

### 资源迁移
Phase 1 决策文档中的"旧 Skill 捆绑资源迁移（简化）"表直接作为 skill-writer 输入 #5，无需额外讨论（除非用户在此步骤提出异议）。
