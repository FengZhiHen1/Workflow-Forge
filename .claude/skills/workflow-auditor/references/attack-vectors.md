# 攻击向量目录

> 5 类 / 18 种攻击向量。标注 🤖 的 = 脚本确定性完成，标注 🧠 的 = 需 AI 补充语义判断。

---

## 一、状态机攻击 (SM: State Machine)

### SM-1 🤖 循环到底 (Loop Exhaustion)

(已废弃) 确认点字段已移除。确认现在是 Skill 内部 AskUserQuestion 行为。

**推演方式**：
- (已废弃) confirmed 边不再存在
- 模拟 `loop_counter` 递推到 `max_loop`
- 检查 `loop_exceeded` edge 是否存在
- 追踪 `loop_exceeded` 出口是否可达终态

**Severity**：缺 `loop_exceeded` → critical；出口不可达终态 → warning

### SM-2 🤖 全部放弃 (All Reject)

(已废弃) 确认点字段已移除。

**推演方式**：
- (已废弃) rejected 边不再存在
- (已废弃)
- (已废弃)

(已废弃)

### SM-3 🤖 选项组合穷举 (Choice Combination Enumeration)

(已废弃) 确认点字段已移除。choice 路由现在由 SubAgent 通过 DONE + routing_choice 选择。

**推演方式**：
- (已废弃)
- 笛卡尔积穷举（>1000 种时降级为 info，不穷举）
- 每条组合推演到终态

**Severity**：存在死路组合 → critical

### SM-4 🤖 失败路径检查 (Failure Exhaustion)

**攻击场景**：Stage 报 ERROR，retry 耗尽后无处可去。

**推演方式**：
- 遍历所有非确认、非虚拟 stage
- 检查是否存在 `failure` edge
- 追踪 failure 出口是否可达终态

**Severity**：无 failure edge → warning

### SM-5 孤立 Stage（由 `validate_workflow.py` 覆盖，不重复检查）

---

## 二、并发攻击 (CC: Concurrency)

### CC-1 🤖 parallel + exclusive 共存

**攻击场景**：同一 Stage 同时声明 `parallel` 和 `exclusive: true`。

**Severity**：warning（`validate_workflow.py` 已作为 error 拦截，此处二次确认）

### CC-2 🤖 parallel.max_instances vs max_parallel_agents

**攻击场景**：Stage 声明可并行 10 个实例，但全局上限只有 6。

**Severity**：warning（`validate_workflow.py` 已拦截）

### CC-3 🧠 并行 Stage 文件冲突风险

**攻击场景**：多个并行 Stage 的 Skill 可能产出同名文件，`aggregation: all` 下产生合并冲突。

**审计方式**：纯 YAML 无法判断——Skill 的输出路径在 SKILL.md 中定义。脚本不执行此向量，由 AI 在 Phase 2 语义分析中完成。

**AI 检查要点**：
- 读取并行 Stage 对应 Skill 中定义的产出路径
- 检查是否有路径交集
- 评估冲突后的合并策略

**Severity**：info（需人工确认）

### CC-4 🤖+🧠 aggregation:any 使用场景

**攻击场景**：`aggregation: any` 用于互补拆分（而非互斥替代），导致部分结果丢失。

**审计方式**：脚本标记所有 `aggregation=any` 的 edge。AI 读取 Skill 语义判断是否为互斥替代。

**Severity**：info（需人工确认）

---

## 三、用户行为攻击 (UB: User Behavior)

### UB-1 🤖 (已废弃) rejected 回跳一致性

(已废弃) 确认点字段已移除。rejected 边不再存在。回边检查现在由 wfctl domain/dag/validator.py 的通用回边可回复性检查覆盖。

**推演方式**：
- (已废弃)
- BFS 判断从 `to` 能否回到 `from`

**Severity**：不可达 → info（需结合业务语义判断是否为设计意图）

### UB-2 🧠 rollback 锚点完整性

**攻击场景**：用户 rollback 到某个 stage 锚点后，下游状态是否能正确重置。

**审计方式**：脚本不执行（需要实例运行信息）。AI 在 Phase 2 评估。

**AI 检查要点**：
- 每个 stage 完成后是否有 git 锚点
- 回退后下游 `consumed_message_ids` 是否能正确清理
- 依赖 wfctl 行为规范保证，仅做合理性检查

**Severity**：info

### UB-3 🧠 terminate 残留检测

**攻击场景**：用户终止实例后，是否有未清理的 worktree 或 dangling 引用。

**审计方式**：静态无法模拟。AI 仅在 live 模式执行。

**Severity**：info

---

## 四、基础设施攻击 (IF: Infrastructure)

### IF-1 🤖 超时→retry→failure 链路

**攻击场景**：Stage 超时 → retry 耗尽 → 无 failure 或 loop_exceeded edge。

**推演方式**：
- 对每个有 `retry > 0` 的 stage
- 检查 retry 耗尽后是否有 `failure` 或 `loop_exceeded` edge

**Severity**：无出口 → warning

### IF-2 🧠 conflict-resolver 可用性

**攻击场景**：并行 stage 合并冲突时，conflict-resolver Skill 是否可用。

**审计方式**：脚本不执行。AI 检查 `artifacts/skills/conflict-resolver/` 是否存在。

**Severity**：缺失 → warning

### IF-3 🧠 parallel 配置与上游产出匹配

**攻击场景**：WORKFLOW.yaml 的 `parallel` 配置引用了不产出可拆分业务产物的上游 stage，导致并行扇出失败。

**审计方式**：
1. 从 YAML 中提取所有 `parallel` 声明，获取 `source` 字段
2. 读取 `source` stage 对应 Skill，检查是否产出了**可被并行处理的业务产物**（如列表、目录、多个独立文件）
3. **不检查** Skill 是否声明 "parallel_targets"——这是工作流层的概念，Skill 不应感知
4. 如果 source stage 的 Skill 产出是单文件/单结果，但 parallel 期望多目标 → 标记问题

**责任层**：**工作流层**（应调整 parallel 配置或更换 source stage）

**Severity**：配置不匹配 → warning

---

## 五、子工作流攻击 (SW: Sub-Workflow)

### SW-1 🤖 子工作流 FAILED 传播

**攻击场景**：子工作流 FAILED，父 Stage 标记为 ERROR。如果父 Stage 没有 `failure` edge，传播链断裂。

**推演方式**：
- 对所有含 `workflow` 字段的 stage
- 检查是否有 `failure` edge
- 追踪 failure 出口是否可达终态

**Severity**：无 failure edge → critical

### SW-2 🤖 子工作流挂起阻塞

**攻击场景**：子工作流内部 AWAITING_CONFIRM 挂起，父 Stage 保持 RUNNING，阻塞下游。

**推演方式**：
- 所有含 `workflow` 字段的 stage
- 检查下游是否有其他 stage
- 标记为 info：阻塞可能是设计意图

**Severity**：info

### SW-3 🤖 嵌套深度超限

**攻击场景**：子工作流嵌套深度超过 3 层（规范硬上限）。

**推演方式**：
- 从父工作流开始，递归检查每个 `workflow` stage
- 读取子工作流 YAML，继续检测孙级引用
- 深度 ≥ 4 时报 critical

**Severity**：嵌套 > 3 → critical

---

## 六、Skill 交叉攻击 (SK: Skill Cross-Audit)

> 不审查 Skill 写得好不好——只审查 YAML 声明和 Skill 实际行为之间有没有裂缝。
> 脚本层做 3 项机械检查（🤖），AI 层做 3 项语义交叉检查（🧠）。

### SK-1 🤖 skill_id → SKILL.md 存在性

**攻击场景**：YAML 声明了 `skill_id: xxx`，但对应 SKILL.md 不存在。

**推演方式**：
- 对每个 `skill_id`，依次查找工作流局部 `skills/`、全局 `skills/`
- 都不存在 → critical

**Severity**：缺失 → critical

### SK-2 🤖 禁词扫描

**攻击场景**：SKILL.md 中包含消费者项目不应有的内容。

**推演方式**：正则扫描 SKILL.md 全文，检测：
- `artifacts/`、`workshop/` 路径
- `[WORKFLOW_CONFIG]` 遗留代码块
- SubAgent 调度关键词（`Agent(`、`subagent_type`）
- Stage ID / workflow_id 感知
- 相对路径引用（`../`）

**Severity**：`artifacts/` 或 `[WORKFLOW_CONFIG]` → critical；其余 → warning

### SK-3 🤖 资源引用完整性

**攻击场景**：SKILL.md 引用的 `references/`/`scripts/`/`assets/` 文件不存在，或目录中有文件但未在正文中引用。

**推演方式**：
- 正则提取 SKILL.md 中所有资源路径引用
- 逐一校验磁盘存在性
- 反向扫描：目录中存在但正文未引用的文件 → info

**Severity**：引用缺失 → warning；孤立文件 → info

### SK-4 🧠 choice 值 ↔ confirm_questions 对齐

**攻击场景**：YAML edges 的 `choice` 值与 Skill 中 AskUserQuestion 的选项之间出现裂缝，导致 wfctl `confirm --choice` 匹配失败或用户意图被错误映射。

**审计方式**：AI 读取 Skill 正文，定位 AskUserQuestion 段落，提取选项值文本。与 YAML edges 的 `choice` 字段逐项比对。

**AI 检查要点**：
- Skill 的 AskUserQuestion options 列表 → 提取每个 option 的 value（业务语义）
- (已废弃) confirmed/rejected 边不再存在
- 判断映射关系：
  - **YAML choice 在 Skill 选项中无对应**：WORKFLOW.yaml 的 `confirm_questions` 未覆盖 Skill 的所有业务选项 → **工作流层问题**
  - **Skill 选项在 YAML edges 中无对应**：可能是工作流层遗漏了 edges，也可能是 Skill 内部逻辑选项（如"重新尝试"）不需要对应 edge → **需人工确认**
  - (已废弃)

**责任归属**：

| 不匹配方向 | Severity | 责任层 | 修复原则 |
|-----------|---------|--------|---------|
| YAML choice 缺少 Skill 对应选项 | warning | **工作流层** | 修改 WORKFLOW.yaml 的 confirm_questions，匹配 Skill 自然交互 |
| Skill 选项缺少 YAML 对应 choice | info | 待确认 | 确认是否为设计意图；如需补充，修改 WORKFLOW.yaml edges |
| 语义映射歧义（如一个 choice 对应多个业务选项） | warning | **工作流层** | 澄清 confirm_questions 的语义 |

**禁止**：审计报告不得要求 Skill 修改 AskUserQuestion 来"对齐" YAML choice。Skill 的交互是业务语义的原点，工作流层负责正确映射。

### SK-5 🧠 上游产出 ↔ 下游输入一致性

**攻击场景**：上游 Skill 的产出路径与下游 Skill 的输入路径不一致，导致数据流断裂。

**审计方式**：AI 读上下游 Skill SKILL.md，提取产出/输入路径描述并比对。

**AI 检查要点**：
- 对每对 `from → to` 的 edge
- 读取 `from` stage 的 Skill，定位"产出路径/输出目录"描述（业务产物）
- 读取 `to` stage 的 Skill，定位"输入路径/查找文件"描述（业务输入）
- 检查 I/O 路径是否匹配

**责任层**：**工作流层**

**修复原则**：
- 路径不匹配 → 检查 WORKFLOW.yaml 的 edges 数据流配置是否正确
- **禁止**修改 Skill 的路径来硬编码上游产出位置——Skill 应该使用配置传入的路径或约定路径，不应感知上下游关系

**Severity**：不匹配 → warning（责任层：工作流层）

### SK-6 🧠 parallel.source 产出可拆分业务产物

**攻击场景**：声明了 `parallel` 的 stage，其 `source` 上游 Skill 不产出可被并行处理的业务产物，导致扇出失败。

**审计方式**：AI 读取 `parallel.source` stage 对应 Skill，检查是否产出了**可被拆分的业务产物**（如列表、目录、多个独立文件）。

**责任层**：**工作流层**

**修复原则**：
- 如果 source Skill 产出不可拆分（如单文件、单结果）→ 修改 WORKFLOW.yaml 的 parallel 配置，更换 source stage 或调整并行策略
- **禁止**要求 Skill 在 SKILL.md 中声明 "parallel_targets" 或描述自己的工作产物如何被工作流消费

**Severity**：无可拆分业务产物 → warning（责任层：工作流层）

### SW-4 🧠 子工作流异常处理完备性

**攻击场景**：检查子工作流自身的异常处理是否与父预期一致。

**审计方式**：AI 读取子工作流 YAML 后评估。脚本仅提供子工作流统计信息。

**Severity**：由 AI 判断

---

## 七、Live 攻击 (LV: Live Audit)

> 以下攻击在 Phase 4 中通过 `audit_workflow_live.py` 真实驱动 wfctl 执行。
> 沙箱环境：`.tmp/audit-live-<ts>/`，含完整 git repo + workflow + skills。
> 判定标尺：**wfctl 实际行为 ≠ 规范预期 → critical**。

### LV-1 🤖 循环到底 (Loop Exhaustion Live)

**攻击场景**：反复调用 `wfctl confirm --choice "继续完善"`，验证 `loop_exceeded` 触发。

**推演方式**：
- `wfctl create` → 循环 `next`
- (已废弃)
- 写 AWAITING_CONFIRM Message → `wfctl next` → `wfctl confirm --choice "继续完善"`
- 重复直到 `loop_counter >= max_loop`
- 检查 instance 是否走向 FAILED（loop_exceeded 触发）

**Severity**：未触发 → critical

### LV-2 🤖 全部放弃 (All Reject Live)

(已废弃) 确认点字段已移除。用户"放弃"由 SubAgent 通过 DONE + routing_choice 处理。

**推演方式**：
- `wfctl create` → 循环 `next`
- (已废弃)
- 循环直到 instance COMPLETED 或 FAILED
- 检查终态是否合法

**Severity**：异常终态 → warning

### LV-3 🤖 Choice 不匹配 (Choice Mismatch Live)

**攻击场景**：`wfctl confirm --choice "___NONEXISTENT___"`，传入 YAML edges 中不存在的 choice 值。

**推演方式**：
- (已废弃)
- `wfctl confirm --choice` 传入不存在的值
- 检查 wfctl 是否返回 error

**Severity**：静默接受（无 error）→ critical

### LV-4 🤖 超时→retry (Timeout Live)

**攻击场景**：临时设 `timeout_seconds: 1`，不写任何 Message，验证 stage 进入 ERROR。

**推演方式**：
- 修改 WORKFLOW.yaml 副本：目标 stage 的 `timeout_seconds: 1`
- 创建实例，驱动到目标 stage
- 不写 Message，等待 2 秒 → `wfctl next`
- 检查 stage 状态是否为 ERROR

**Severity**：未进入 ERROR → warning

### LV-5 🤖 子工作流 FAILED 传播 (Sub-Workflow Failure Live)

**攻击场景**：手动写子 instance FAILED 状态文件，验证父 stage 进入 ERROR。

**推演方式**：
- 创建实例，驱动到含 `workflow` 字段的 stage
- 手动创建 `children/<child_id>/instance.json`（status: FAILED）
- `wfctl next`
- 检查父 stage 状态是否为 ERROR

**Severity**：未传播 → critical

### LV-6 🤖 合并冲突处理 (Merge Conflict Live)

**攻击场景**：检查 `conflict-resolver` Skill 是否存在。

**推演方式**：
- 检查沙箱中 `.claude/skills/conflict-resolver/SKILL.md` 是否存在
- 如果工作流有 `parallel` stage 但 conflict-resolver 不存在 → warning

**Severity**：缺失且工作流有并行 → warning
