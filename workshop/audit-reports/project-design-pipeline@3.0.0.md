# 工作流审计报告: project-design-pipeline@3.0.0

| 指标 | 值 |
|------|-----|
| 审计模式 | symbolic + semantic + skill-cross-audit + live |
| 总 Stage 数 | 8（6 业务 + 2 虚拟） |
| 确认点数 | 6 |
| 并行 Stage 数 | 1（s07 子工作流扇出） |
| 子工作流引用 | 1（module-design-pipeline@1.0.0，最大嵌套深度: 1） |
| 结果 | ❌ Fail |

---

## Critical Findings（必须修复）

### AUDIT-001: loop_exceeded 未触发 — wfctl 循环计数器不工作

- **攻击场景**: SM-1 循环到底 — 在每个确认点反复选择"继续完善"/"重新选择"直到超过 `max_loop`
- **涉及 Stage**: s01, s02, s03, s04, s06（所有含 `max_loop: 3` 的确认点）
- **发现**: Phase 4 真实调用中，对 5 个确认点分别反复确认 `max_loop + 1` 轮（4 轮），wfctl **均未触发 `loop_exceeded`**，实例始终停留在 ACTIVE 状态而非进入 FAILED。YAML 中所有含 `max_loop` 的 edge 都定义了 `condition: loop_exceeded → s99-workflow-end`，但 wfctl 的循环计数器没有正确递增/检测。
- **预期行为**: `loop_counter >= max_loop` 时 wfctl 应沿 `loop_exceeded` edge 流转，instance 进入 FAILED
- **修复建议**: 检查 wfctl 调度器的循环计数器更新逻辑（`core/` 或 `services/` 中处理 `confirm` → self-loop 的代码路径），确认在 self-loop edge 上正确递增 `loop_counter` 并在下一轮 `next` 时检测超限

### AUDIT-002: 全部放弃后实例未进入终态

- **攻击场景**: SM-2 全部放弃 — 在每个确认点选"放弃"/"终止工作流"
- **涉及 Stage**: 所有确认点（s01, s02, s03, s04, s06, s08）
- **发现**: Phase 4 中逐确认点选择 rejected choice（"放弃"），所有确认点 reject 后实例状态为 **ACTIVE**，而非预期的 COMPLETED 或 FAILED。YAML 中每个确认点都有 `condition: rejected → s99-workflow-end` edge，但 wfctl 未将 reject 正确路由到终态。
- **预期行为**: rejected edge 应使 stage 状态流转到 s99，instance 最终为 COMPLETED 或 FAILED
- **修复建议**: 检查 wfctl 对 `condition: rejected` 的 routing 逻辑，确认 rejected 确认后 stage 正确标记并沿 rejected edge 流转

---

## Warning Findings（建议修复）

### AUDIT-003: code-reverse-engineering-writer 跨 Skill 资源引用静态不可验证

- **攻击场景**: SK-1 Skill 资源引用完整性检查（子工作流）
- **涉及 Skill**: `code-reverse-engineering-writer`（子工作流 module-design-pipeline@1.0.0）
- **发现**: 该 Skill 的参考文件表声明引用 `intent-template.md`（位于 `module-intent-writer/references/`）和 `agent-spec-template.md`（位于 `module-spec-writer/references/`）。这两个文件**实际存在**于对应 Skill 的 references 目录中，但审计脚本的静态查找逻辑（本 Skill → 工作流级 → 父工作流级）无法覆盖跨 Skill 引用，报告为"缺失"。这不是功能缺陷，但暴露了跨 Skill 资源引用的脆弱性——如果被引用 Skill 修改或删除其模板，`code-reverse-engineering-writer` 运行时会失败。
- **预期行为**: 跨 Skill 资源引用应为显式依赖，或被审计脚本识别
- **修复建议**: （可选项）在 `code-reverse-engineering-writer` 中复制所需模板到自身 references/ 目录，或在 WORKFLOW.md 中标注此跨 Skill 依赖关系

---

## Info Findings（参考信息）

### AUDIT-004: 确认点组合数超过穷举上限

- **攻击场景**: 确认点选项组合穷举
- **发现**: 6 个确认点 × 平均 3 个选项 = 1458 种组合路径，超过脚本穷举上限 1000。脚本已自动跳过组合穷举，不影响其他检查。此数量级在 6 确认点工作流中属于正常范围。
- **建议**: 无需处理。当确认点 >5 时组合爆炸是预期行为。

### AUDIT-005: 子工作流阻塞下游风险已由架构缓解

- **攻击场景**: SW-2 子工作流挂起阻塞下游
- **涉及 Stage**: s07, s06, s08
- **发现**: s07（module-design-pipeline@1.0.0 子工作流并行调度）如果内部 AWAITING_CONFIRM 挂起，会阻塞 s08。但工作流设计已考虑此风险：(1) s07→s06 的 failure edge 允许失败后回退重新选择；(2) 子工作流内部有多处确认点但合理的 timeout 可防永久挂起；(3) 每个子工作流实例在独立 worktree 中运行，互不干扰。
- **建议**: 评估是否需要为 s07 添加 `timeout_seconds` 全局超时

### AUDIT-006: s05 降级跳过路径设计合理

- **攻击场景**: SM-4 失败路径分析
- **涉及 Stage**: s05
- **发现**: s05（module-dependency-analyzer）同时有 `success` 和 `failure` edge 指向 s06，形成降级跳过路径。这与 WORKFLOW.md 中"依赖分析失败降级"设计一致。s06 的 project-dispatch-manager 明确声明"若不存在则跳过此步骤"，确保降级不阻塞后续流程。设计合理。

---

## Phase 4 攻击结果详情

| 攻击 | 结果 | 说明 |
|------|------|------|
| SM-1 循环到底 | ❌ FAIL | 5 个确认点均未触发 loop_exceeded |
| SM-2 全部放弃 | ⚠️ FAIL | 实例停留在 ACTIVE 而非终态 |
| choice 不匹配 | ✅ PASS | wfctl 正确拒绝不存在的 choice |
| IF-1 超时 | ⚠️ N/A | 审计脚本 NameError bug，未完成测试 |
| SW-1 子传播 | ✅ PASS | 子工作流 FAILED → 父 stage ERROR 传播正确 |
| IF-2 合并冲突 | ✅ PASS | conflict-resolver Skill 可用 |

---

## 审计脚本问题

Phase 4 脚本 `audit_workflow_live.py` 的 `attack_if1_timeout()` 函数中存在 bug：`sid` 变量未定义（第 487 行），导致 IF-1 超时攻击测试无法执行。需修复：在 `if not candidates: return` 之后添加 `sid = candidates[0]["stage_id"]`。

---

## Skill 交叉审计

### 父工作流 (project-design-pipeline@3.0.0)

| 检查项 | 结果 |
|--------|------|
| SK-1 Skill 存在性 | ✅ 5/5 全部存在（5 个局部 Skill） |
| SK-2 禁词扫描 | ✅ 未发现 |
| SK-3 资源引用完整性 | ✅ 无孤立文件 |
| SK-4 choice ↔ confirm_questions 对齐 | ✅ 所有 6 个确认点 choice 值与 Skill AskUserQuestion 完全匹配 |
| SK-5 上下游 I/O 一致性 | ✅ s01→s02→s03→s04→s05→s06 数据流路径一致 |
| SK-6 parallel.source 产出可拆分 | ✅ s06 产出 `parallel_targets`（模块列表），s07 并行扇出匹配 |

### 子工作流 (module-design-pipeline@1.0.0)

| 检查项 | 结果 |
|--------|------|
| SK-1 Skill 存在性 | ✅ 7/7 全部存在 |
| SK-2 禁词扫描 | ✅ 未发现 |
| SK-3 资源引用完整性 | ⚠️ 2 处跨 Skill 引用静态不可验证（见 AUDIT-003） |
| SK-4 choice ↔ confirm_questions 对齐 | ✅ 所有确认点 choice 值完全匹配（含 6 种增量路径路由） |
| SK-5 上下游 I/O 一致性 | ✅ 数据流路径一致（含 code_only / both_exist 替代路径） |
| SK-6 parallel.source 产出可拆分 | N/A（子工作流无并行 stage） |
