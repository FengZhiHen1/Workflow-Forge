# 轨道公共流程

所有轨道共享的 Phase 0 和通用交付流程。

---

## Phase 0：输入识别与方向确认

### 目标
确认输入类型、推荐轨道、初始化工作目录，获得用户授权。

### 核心任务
1. **识别输入类型**：旧 Skill / 已有工作流 / 多 Skill / 从零开始
2. **检查旧 Skill 目录结构**：如果输入是旧 Skill 且存在 `references/`、`scripts/`、`assets/` 目录，列出完整文件清单——这些路径必须作为 analyzer 的额外输入
3. **自动评估轨道**：基于失败路径复杂度、共享资源必要性、Skill 间依赖、并发优化需求、用户明确意图，推荐快速/标准/深度
4. **AskUserQuestion 确认**（强制门控）：展示推荐轨道 + workflow_id + 版本 + 红线，用户可覆盖推荐
5. **创建 $WD**：`.tmp/workflow-designer-<YYYYMMDD-HHMMSS>/`
6. **按需读取规范**：
   - Phase 0：`工作流思想.md`、`目录规范.md`
   - 不提前读取 Phase 1/2 规范——SubAgent 自行读取各自需要的部分
7. **初始化决策文档**：复制对应轨道的决策模板到 `$WD/`

### 输出
- 轨道确认结果
- workflow_id + version
- $WD 初始化完成
- Phase 0 决策文档

---

## 通用交付流程

适用于所有轨道的 Phase 结尾，以及增量更新。

### 【门控】L1 校验
```bash
python .claude/skills/workflow-designer/scripts/validate_workflow.py \
  --workflow-yaml $WD/WORKFLOW.yaml \
  --skills-dir $WD/skills/ \
  --workflows-dir artifacts/workflows/ \
  --mode <standard|optimization>
```

### 【门控】L2 规则检查
```bash
python .claude/skills/workflow-designer/scripts/evaluate_workflow_design.py \
  --workflow-yaml $WD/WORKFLOW.yaml \
  --mode <fast|standard|deep>
```

### 【门控】用户确认
展示工作流摘要（Stage 数、确认点数、Mermaid 图），AskUserQuestion 确认。

### 【门控】转正
用户选择"确认转正"后执行落盘。**禁止在用户未确认转正预览的情况下执行转正。**

---

## Phase 2 通用规则

### 输入清洗
主 Agent 在调度 skill-writer 前，必须对 Stage 需求规格执行脱敏：

| 原始表述 | 清洗后 |
|---------|--------|
| "在 `p2-scheme-design` Stage 中" | 删除 |
| "上游 `p1c-dependency-analysis` 的产出" | `.agent/workspace/<problem>/dependency-analysis.md` |
| "下游 `p3-implementation` 需要" | 删除 |
| "完成后触发下一阶段" | "完成后上报 DONE" |
| "调用 scheme-reviewer SubAgent" | 删除（不写） |

### Skill 边界扫描
每个 skill-writer 产出 SKILL.md 后，立即执行：
```bash
python .claude/skills/workflow-designer/scripts/validate_skill_boundary.py \
  --skill-md $WD/skills/<skill_id>/SKILL.md
```
Critical 违规 → 打回 skill-writer，附带具体行内容 + 修改建议。
