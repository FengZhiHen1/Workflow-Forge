# 标准设计流程

前置：已完成 Phase 0，已读取 `orbit-common.md`。

适用场景：多 Skill 合并、中等复杂度工作流。时间目标 1-2 小时。

---

## Phase 1 目标：完成工作流结构设计并获得用户确认

### 核心任务
1. 读取输入（旧 SKILL.md 全文 / 已有 WORKFLOW.yaml + md）
2. 调用 analyzer（`references/analyzer-prompt.md`，`analysis_depth: standard`）输出结构化分析报告
3. 基于 analyzer 报告，与用户围绕 5 维度展开讨论，实时填入决策文档（`references/phase1-decision-template.md`）：
   - 目标清晰度
   - 信息传递保真度
   - 用户决策有效性
   - 产物完整性与可用性
   - 异常路径的鲁棒性
4. 讨论完成后，产出 Stage 结构草案 + 共享资源识别方案，填入决策文档
5. 【门控】**在调度 designer 之前，必须获得用户对决策文档的确认。** 未经确认不得进入下一步
6. 调度 designer（`references/designer-prompt.md`），输出：WORKFLOW.yaml + WORKFLOW.md + skill_manifest.json

### 质量门控
- 【门控】L1 校验：validate_workflow.py
- 【门控】L2 规则检查（4 项：确认点密度、死 Stage、循环出口、数据流完整性）
- 【门控】展示工作流摘要（Stage 数、确认点数、Mermaid 图），AskUserQuestion 确认

---

## Phase 2 目标：并行生成所有 SKILL.md

### 核心任务
1. 每 Skill 独立 Phase 2 决策文档 → 讨论 → 确认
2. 读取 skill_manifest.json 中的依赖信息
3. 无依赖关系的 Skill 可同时进入 Phase 2 讨论
4. 前置 Skill 决策文档确认后，后置 Skill 可调度 skill-writer
5. 并行调度 skill-writer（基于依赖关系）

### 质量门控
- 【门控】每个 SKILL.md 生成后，通过 `validate_skill_boundary.py` 扫描
- 【门控】全部 Skill 生成后，执行 L1 校验（含 skills-dir + 子工作流）
- 【门控】转正确认（强制门控）

### 资源迁移
Phase 2 讨论时，必须将 Phase 1 决策文档中的"旧 Skill 捆绑资源迁移"表作为讨论材料，与用户逐项确认每项迁移决策。确认后，将迁移清单作为 skill-writer 的输入 #5 传递。
