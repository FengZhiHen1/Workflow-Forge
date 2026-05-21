# 工作流审计报告: mathematical-model@3.0.0

| 指标 | 值 |
|------|-----|
| 审计模式 | symbolic + semantic + live（Phase 1-4 完整审计） |
| 审计时间 | 2026-05-21 12:45 UTC+8 |
| 父工作流 Stage 数 | 8 业务 + 2 虚拟 |
| 子工作流 Stage 数 | 7 业务 + 2 虚拟 |
| 确认点数 | 父: 3，子: 2 |
| 并行 Stage 数 | 父: 3，子: 0 |
| 子工作流引用 | 1（嵌套深度: 1） |
| 结果 | ⚠️ Conditional Pass（0 critical / 19 warning / 3 info） |

---

## Critical Findings（必须修复）

无。

---

## Warning Findings（建议修复）

### 父工作流: W01 — data-scout 产物文件名与 dependency-analyst 预期不一致

- **攻击场景**: SK-5 上游产出 ↔ 下游输入一致性
- **涉及 Stage**: p1b-data-exploration → p1c-dependency-analysis
- **发现**: data-scout 输出文件名为 `P1b-数据探索报告_Task[N].md`，但 dependency-analyst 期望读取 `P1b-数据侦察_Task[N].md`。两个文件名不一致（"数据探索报告" vs "数据侦察"），运行时 dependency-analyst 将找不到上游产物。
- **预期行为**: 上游产出文件名应与下游读取文件名一致
- **责任层**: 工作流层 — data-scout 的 Skill 描述中使用"数据侦察员"但产出文件名使用"数据探索报告"，dependency-analyst 使用"数据侦察"命名。需统一命名约定
- **修复建议**: 将 data-scout 产出文件名改为 `P1b-数据侦察_Task[N].md`，或修改 dependency-analyst 中引用的文件名

### 父工作流: W02 — p1a-topic-analysis 产出形态与 per-question 并行不匹配

- **攻击场景**: IF-3 / SK-6 — parallel.source 产出可拆分性
- **涉及 Stage**: p1b-problem-analysis（parallel.source = p1a-topic-analysis）
- **发现**: p1a-topic-analysis（topic-analyst Mission 2）产出单一选题分析文件 `GLOBAL_SHARED/P1a-选题分析.md`，而非可直接拆分的 per-question 目标列表。p1b-problem-analysis 声明 parallel.source 为 p1a-topic-analysis，期望从此 stage 的产出中获取并行拆分目标，但从 Skill 定义看，该产出不包含可直接程序化拆分的 question 列表。
- **预期行为**: parallel.source 指向的 stage 应产出可被工作流引擎拆分的业务产物（如列表、目录、多个独立文件）
- **责任层**: 工作流层 — 应澄清 parallel.source 的拆分依据，或由 p1a-topic-analysis 产出显式的小问清单
- **修复建议**: (1) 在 WORKFLOW.yaml 中澄清 parallel 拆分由编排器根据 `target_question` 参数驱动，而非 source stage 产出；或 (2) 让 p1a-topic-analysis 额外产出 `P1a-小问清单.yaml` 作为拆分输入

### 父工作流: W03 — p1b-data-exploration 缺少通用 failure 出口

- **攻击场景**: SM-4 失败路径（Phase 1 脚本检测）
- **涉及 Stage**: p1b-data-exploration
- **发现**: stage 有 `retry: 1`（自动重试）和 failure edge（choice: "数据不合格"，max_loop: 2），但仅有特定 choice 的 failure 路由。若 data-scout 因非"数据不合格"原因失败（如基础设施错误、文件读取失败），缺少回退路径，实例将直接 FAILED。
- **预期行为**: 非确认点应有通用 failure edge 覆盖所有失败场景
- **修复建议**: 添加 `from: p1b-data-exploration, to: p1b-problem-analysis, condition: failure` 的通用回退边（当前 "数据不合格" choice 会匹配特定失败原因；通用 failure edge 覆盖其余场景）

### 父工作流: W04 — init Skill 硬编码 workflow_id

- **攻击场景**: SK-2 禁词扫描 — Stage ID/Workflow ID 感知
- **涉及 Stage**: p0-init
- **发现**: `skills/init/SKILL.md` L59 在 MANIFEST.yaml 模板中硬编码 `workflow_id: mathematical-model`。虽然这是 init 写入消费者项目的模板内容而非运行时读取，但严格来说 init Skill 感知了它所属的工作流 ID。若本 Skill 被复用到其他工作流，该模板值将不正确。
- **预期行为**: Skill 应通过编排器注入的参数获取 workflow_id，而非硬编码
- **责任层**: 工作流层 — init 是局部 Skill（仅本工作流使用），硬编码在此场景下可接受，但未来如需复用需参数化
- **修复建议**: 若保持局部 Skill 定位则无需修复；若规划为全局 Skill，改为从上下文注入 `workflow_id`

### 父工作流: W05 — init Skill 引用不存在的资源

- **攻击场景**: SK-3 资源引用完整性
- **涉及 Stage**: p0-init
- **发现**: init Skill 引用了 `scripts/results/` 但该目录不存在（已查找 `skills/init/scripts/results/` 和 `skills/scripts/results/`）
- **修复建议**: 若该引用为过时残留，从 SKILL.md 中删除；若为必需，创建对应目录

---

### 子工作流: W06 — s06-validation 确认点缺少 rejected 出口

- **攻击场景**: SM-3 选项穷举 — 确认点仅有 confirmed 边
- **涉及 Stage**: s06-validation（子工作流 question-solution@1.0.0）
- **发现**: s06-validation 的 3 条出边全部为 `condition: confirmed`（"继续审查"→s07、"调参修复"→s04、"假设修正"→s03），无 `condition: rejected` 边。用户无法在验证评估阶段选择"放弃"或"终止"。
- **预期行为**: 确认点应至少提供一条 rejected 出边，给用户放弃/中止的选项
- **修复建议**: 添加 `from: s06-validation, to: s99-workflow-end, condition: rejected, choice: "放弃"` 边

### 子工作流: W07 — s01-scheme-design 缺少直接放弃路径

- **攻击场景**: SM-3 选项穷举 — rejected 仅自循环
- **涉及 Stage**: s01-scheme-design（子工作流 question-solution@1.0.0）
- **发现**: s01 的 rejected 边只有 "重新设计"（自循环，max_loop: 2），无双向拒绝（用户无法在第一轮就选择"放弃"）。用户必须消耗 max_loop (3 次总计) 触发 loop_exceeded 才能终止，增加了不必要的摩擦。
- **预期行为**: 确认点的 rejected 选项应同时覆盖"重新设计"（rejected→自循环）和"放弃"（rejected→s99）
- **修复建议**: 添加 `from: s01-scheme-design, to: s99-workflow-end, condition: rejected, choice: "放弃"` 边

### 子工作流: W08 — s02/s07 缺少通用 failure 出口

- **攻击场景**: SM-4 失败路径
- **涉及 Stage**: s02-adversarial-review, s07-adversarial-review（子工作流）
- **发现**: 两个对抗审查 stage 仅有 choice="致命缺陷" 的 failure 回退边。若审查因非"致命缺陷"原因失败（如基础设施错误），缺少回退路径，实例将直接 FAILED。
- **修复建议**: 为两个 stage 分别添加通用 `condition: failure` 边（保留现有 choice 边作为特定路由，通用边覆盖其余场景）

### 子工作流: W09–W12 — validation-reviewer 缺少 4 个引用资源文件

- **攻击场景**: SK-3 资源引用完整性（Phase 1 脚本检测）
- **涉及 Stage**: s07-adversarial-review（子工作流）
- **发现**: `skills/validation-reviewer/SKILL.md` 引用了 4 个 references 文件，但均不存在：
  - `references/attack-dimensions-by-type.md`
  - `references/counterexample-framework.md`
  - `references/judge-faq.md`
  - `references/paper-review-checklist.md`
- **预期行为**: 所有在 SKILL.md 中引用的资源文件应存在于 Skill 自身目录或工作流级共享目录
- **修复建议**: 创建这 4 个参考文件，或如果 Skill 可在无参考文件的情况下工作，从 SKILL.md 中移除引用

### 子工作流: W13–W16 — s03/s04/s05 缺少通用 failure 出口

- **攻击场景**: SM-4 失败路径（Phase 1 脚本检测）
- **涉及 Stage**: s03-math-modeling, s04-code-core, s05-code-extension（子工作流）
- **发现**: 这三个 stage 的 failure 处理仅通过 self-loop retry（max_loop: 2）实现。若 retry 耗尽后仍失败且失败原因不匹配 self-loop choice，缺少回退到上游 stage 的 failure 边。当前设计为"重试耗尽 → s99"，这是设计意图——建模/编码失败后直接终止，但未显式标注此意图。
- **预期行为**: 显式标注"失败即终止实例"或添加通用 failure edge
- **修复建议**: 若"耗尽即终止"为设计意图，在 WORKFLOW.md 中显式说明；否则为每个 stage 添加 `condition: failure` 回退边

---

## Info Findings（参考信息）

### 父工作流: I01 — p2 子工作流可能阻塞下游

- **来源**: Phase 1 SW-2
- **发现**: p2-question-solution 使用 `aggregation: all` 汇聚，若子工作流内部确认点挂起（AWAITING_CONFIRM），下游 p5-paper-materializer 将被阻塞。这是正常设计——论文生成必须等待所有小问求解完成。

### 子工作流: I02 — quality-inspector 存在孤立文件

- **来源**: Phase 1 SK-3
- **发现**: `skills/quality-inspector/references/review-checklist.md` 存在于目录但 SKILL.md 正文未引用。需确认该文件是否需要保留或应在 SKILL.md 中添加使用说明。

### 父工作流: I03 — p1a-topic-analysis → p1b-problem-analysis 拆分语义

- **来源**: Phase 2 IF-3
- **发现**: p1a-topic-analysis（topic-analyst）产出 `GLOBAL_SHARED/P1a-选题分析.md` + 多选题时产出各题独立分析，形成事实上的 per-question 文件列表。并行拆分可基于这些独立文件或 `target_question` 参数驱动。当前设计不构成硬缺陷，但建议在 WORKFLOW.md 中说明并行拆分的数据来源。

---

## Phase 2 语义补充详情

### CC-3：并行文件冲突评估

✅ 通过。三个并行 stage（p1b-problem-analysis、p1b-data-exploration、p2-question-solution）均使用 per-question 命名空间（`Task[N]` 后缀），无文件路径交叠。p2-question-solution 使用独立 worktree，完全隔离。

### CC-4：aggregation:any 语义核实

✅ 不适用。本工作流无 `aggregation: any` 边，所有汇聚点均使用 `aggregation: all`。

### IF-2：conflict-resolver 可用性

✅ 通过。`artifacts/skills/conflict-resolver/SKILL.md` 存在，可处理并行合并冲突。

### IF-3：parallel 配置与上游产出匹配

⚠️ 已记录为 W02、I03。p1a-topic-analysis 产出形态与 per-question 并行拆分之间的映射关系需要澄清。

### SW-4：子工作流异常处理完备性

⚠️ 子工作流 question-solution@1.0.0 独立审计结果：0 critical / 10 warning / 1 info。关键发现已合并到 W06–W16。

---

## Phase 3 Skill 交叉审计详情

### SK-4：choice 值 ↔ confirm_questions 对齐

| 确认点 | YAML choices | Skill 选项 | 对齐状态 |
|--------|-------------|-----------|---------|
| p1a-ambiguity-scan | 确认解读, 重新扫描 | 歧义报告提交用户确认 | ✅ 对齐 |
| p1a-topic-analysis | 确认继续, 重新分析, 修正歧义 | 选题分析提交用户确认 | ✅ 对齐 |
| p5-paper-materializer | 确认完成, 回退修改 | 素材块提交用户审核 | ✅ 对齐 |
| s01-scheme-design | 锁定方案, 重新设计 | Skill 声明"编排器接管确认" | ✅ 对齐 |
| s06-validation | 继续审查, 调参修复, 假设修正 | 修复建议章节明确列出三选项 | ✅ 对齐 |

s06-validation 三个 choice 与 quality-inspector 的"修复建议"章节（"继续审查 / 调参修复 / 假设修正"）精确对应，是优秀的对齐范例。

### SK-5：上游产出 ↔ 下游输入一致性

⚠️ 主要发现已记录为 W01（data-scout ↔ dependency-analyst 文件名不匹配）。其余边均对齐：

- p0-init → p1a-ambiguity-scan: ✅ init 创建目录 → topic-analyst 写入
- p1a-ambiguity-scan → p1a-topic-analysis: ✅ Mission 1 产出由 Mission 2 读取
- p1b-problem-analysis → p1b-data-exploration: ✅ problem-decomposer 产出字段清单由 data-scout 直接读取
- p1c-dependency-analysis → p2-question-solution: ✅ 调度指令 YAML 驱动 per-question worktree 启动
- p2-question-solution → p5-paper-materializer: ✅ 子工作流产出的建模文档和验证报告由 paper-materializer 消费

### SK-6：parallel.source 产出可拆分性

- p1b-problem-analysis (source: p1a-topic-analysis): ⚠️ 产出为单一选题分析文件（已记录为 W02）
- p1b-data-exploration (source: p1b-problem-analysis): ✅ 已为 per-question 文件，自然可拆分
- p2-question-solution (source: p1c-dependency-analysis): ✅ 产出 `P1c-调度指令.yaml` 包含 `lanes.independent` 和 `lanes.serial` 结构化列表，可直接拆分

---

## Phase 4：真实调用（已执行）

Phase 4 在沙箱中实际驱动 wfctl，执行 6 种攻击场景。结果：0 critical / 2 warning。

### W17 — SM-2 全部放弃后实例未进入终态

- **攻击场景**: SM-2 全部放弃
- **发现**: 在全部确认点选择 rejected（放弃）后，预期实例应进入终态（COMPLETED 或 FAILED），但实际状态保持 ACTIVE
- **预期行为**: 所有 rejected 边指向 s99 后，实例应标记为 COMPLETED 或 FAILED
- **性质**: wfctl 实现层问题，非 WORKFLOW.yaml 设计问题
- **修复建议**: 检查 wfctl 的终态判断逻辑，确保 rejected→s99 路径正确触发终态转换

### W18 — IF-1 超时后 stage 状态不正确

- **攻击场景**: IF-1 超时（p1b-data-exploration 设置 timeout_seconds=1）
- **涉及 Stage**: p1b-data-exploration
- **发现**: 预期超时后 stage 应进入 ERROR 状态，但实际状态为 PENDING
- **预期行为**: 超时后 stage 应标记为 ERROR，触发 retry 或 failure edge
- **性质**: wfctl 实现层问题
- **修复建议**: 检查 wfctl 超时检测和状态转换逻辑

### Phase 4 通过场景

| 攻击 | 结果 |
|------|------|
| SM-1 循环到底（反复 reject 至 loop_exceeded） | ✅ 通过 |
| choice 不匹配（发送不存在 choice） | ✅ 通过 |
| SW-1 子工作流 FAILED 传播 | ✅ 通过 |
| IF-2 conflict-resolver 可用性 | ✅ 通过 |

---

## 总结

父工作流 mathematical-model@3.0.0 和子工作流 question-solution@1.0.0 均无 critical finding。

需优先修复的问题：
1. **W01** — data-scout 与 dependency-analyst 之间的文件名不一致（可能导致运行时找不到输入）
2. **W09–W12** — validation-reviewer 缺失 4 个参考文件（Skill 执行时可能功能降级）
3. **W06** — s06-validation 无 rejected 出口（用户无法放弃）
4. **W02** — p1a-topic-analysis 与 per-question 并行的拆分映射需澄清

Phase 4 发现的 wfctl 层问题（W17、W18）已在 `artifacts/scripts/wfctl/` 中修复，详见下文「修复记录」。

---

## 修复记录

### W17 修复 — `cli/confirm.py`：rejected self-loop 边增加 max_loop 强制

**根因**：`_handle_confirm()` 中 rejected 边处理器（原 L100–122）未检查 `max_loop`。`max_loop` 仅在 confirmed self-loop 边有逻辑，rejected self-loop 边无限循环，用户无法通过拒绝达到 loop_exceeded 终态。

**修复**：在 rejected 边匹配后、执行路由前，插入 self-loop + max_loop 检测。超限则走 `get_loop_exceeded_edge` 终止实例（与 confirmed self-loop 逻辑对称）。未超限则递增 `loop_counter` 并正常自循环。

**修改文件**：`artifacts/scripts/wfctl/cli/confirm.py` L100–160（插入 L107–130）

### W18 修复 — `services/scheduler.py`：超时检测与错误处理分离

**根因**：`run_next()` 中 `_check_timeouts()` 和 `_handle_error_stages()` 在同一周期内执行。超时将 stage 设为 ERROR 后，同周期的 `_handle_error_stages()` 立即触发 retry 将其重置为 PENDING。主 Agent 无法观测到 ERROR 中间状态，且 retry 和 spawn 在同一 `next` 调用中原子完成。

**修复**：将 `_check_timeouts()` 移至 `_handle_error_stages()` 之后执行。本轮检测到的超时 stage 保持 ERROR 状态直到下一轮 `next` 调用，由下一轮的 `_handle_error_stages()` 执行 retry。主 Agent 可在两轮之间观测到 ERROR 状态。

**修改文件**：`artifacts/scripts/wfctl/services/scheduler.py` L62–92（调整步骤顺序，同步更新后续步骤编号）
