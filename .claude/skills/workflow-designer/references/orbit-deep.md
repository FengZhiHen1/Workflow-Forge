# 深度设计流程

前置：已完成 Phase 0，已读取 `orbit-common.md`。

适用场景：复杂系统、平台级工作流。时间目标 半天-一天。

---

## Phase 1-Deep 目标：完成高质量工作流设计并通过评审

### 核心任务
1. 读取输入
2. 调用 analyzer（`references/analyzer-prompt.md`，`analysis_depth: deep`）
   - 输出包含：逻辑步骤、AskUserQuestion 点、SubAgent 点、**详细依赖关系**（强/弱依赖、并行机会、循环风险）
3. 基于 analyzer 报告，7 维度讨论（5 基础 + **依赖关系清晰度** + **并发优化空间**），实时填入扩展决策文档（`references/phase1-decision-template-deep.md`）
4. 产出 Stage 结构草案（含依赖图标注 + 并行可行性分析）
5. 【门控】用户确认决策文档
6. 调度 designer（`references/designer-prompt.md`，深度模式），输出：WORKFLOW.yaml + WORKFLOW.md + **dependency-graph.yaml** + skill_manifest.json

### 质量门控
- 【门控】L1 校验
- 【门控】L2 规则检查（6 项：确认点密度、死 Stage、循环出口、数据流完整性、并发效率、反模式检测）
- 【门控】调用 reviewer SubAgent（`references/reviewer-prompt.md`）评审设计质量
   - 输入：WORKFLOW.yaml + WORKFLOW.md + dependency-graph.yaml + 决策文档
   - 输出：`$WD/review-report.yaml`
   - 最多 2 轮迭代：critical 问题必须修正后才能进入 Phase 2
- 【门控】展示最终摘要，AskUserQuestion 确认

---

## Phase 2-Deep 目标：按依赖层级并行生成并通过审查

### 核心任务
1. 构建 Skill 依赖 DAG：读取 dependency-graph.yaml，按 level 分组
2. 按依赖层级调度：同 level 无依赖 Skill 同时进入 Phase 2 讨论
3. 每 Skill 完整 Phase 2 流程：独立决策文档 → 讨论 → skill-writer
4. 同 level 并行调度 skill-writer
5. Skill 独立审查：
   - 触发条件：深度设计轨道 + 用户未明确说"跳过审查"
   - 调度方式：skill-writer 完成一个，立即启动该 Skill 的 skill-reviewer
   - 输入：SKILL.md + Skill 完整目录 + 对应 Stage 片段 + Phase 2 决策摘要 + 迁移清单
   - 输出：`$WD/skills/<skill_id>/review-report.yaml`
   - 结果处理：
     - critical → 打回 skill-writer 修正，修正后重新审查（最多 2 轮）
     - warning → 呈现给用户，用户决定是否修正
     - 全部 pass → 进入集成校验
6. 集成校验：
   - 汇总所有 skill-reviewer 报告，确认无未解决的 critical
   - 校验所有 Skill 间的接口一致性、共享资源引用完整性、SKILL.md 中无 `artifacts/` 路径

### 质量门控
- 【门控】每个 SKILL.md 生成后，通过 `validate_skill_boundary.py` 扫描
- 【门控】每个 SKILL.md 通过 skill-reviewer 审查
- 【门控】集成校验通过
- 【门控】L1 最终校验（含 skills-dir + 子工作流）
- 【门控】转正确认（强制门控）

### 资源迁移
Phase 1 决策文档中的"旧 Skill 捆绑资源迁移"表已包含影响分析和适配说明。Phase 2 讨论时逐项复审，确认后作为 skill-writer 的输入 #5 传递。集成校验时额外检查：所有 ✅ 资源是否确实出现在新 Skill 目录中。
