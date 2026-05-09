# Skill 定义规范 v3.0.0

---

## 一、定位

Skill 是**自包含的执行能力单元**，可被工作流 stage 直接引用。

### Skill 的视角

编写 SKILL.md 时，你把它当作写给一个独立 Agent 看的协作指令。Skill 以为自己在独立运行——它可以自然地使用 `AskUserQuestion` 向用户提问。Skill 不知道工作流的存在、不知道 Stage 编号、不知道 edges、不知道上下游关系。

### 框架的接管

当 Skill 被工作流调度为 SubAgent 时，框架在 SubAgent 启动时**先于 SKILL.md 注入提示词**，申明：
- `AskUserQuestion` → `AWAITING_CONFIRM` Message 的替换规则
- Message 上报方式（`wfctl message write`）
- 身份获取方式（`wfctl identity`）
- 需自行读取的契约文件路径

SubAgent 阅读 SKILL.md 时已持有这些规则，**自觉将 AskUserQuestion 替换为 AWAITING_CONFIRM 消息**。框架不截获——是 SubAgent 遵从注入规则主动替换。

### 替换后的流转

```
Skill 写：     AskUserQuestion("选哪个方案？")
SubAgent 执行： 转为 AWAITING_CONFIRM message → 用户看到 → 用户回答
编排器：        用户答案注回同一个 SubAgent 实例
SubAgent 看到：  AskUserQuestion 返回了用户选择 → 继续工作
```

**一个 Skill = 一个固定的 SubAgent 实例。** 中途确认不销毁实例，用户答案回传后继续执行。Skill 不需要"冷启动恢复"——它在内存中保持着上下文。

### 绝对边界

| Skill 可以做 | Skill 不可以做 |
|------------|--------------|
| 使用 AskUserQuestion 请求用户决策 | 感知或引用工作流结构（Stage ID、edges、并发策略） |
| 产出文件到指定路径 | 猜测或干涉下游行为 |
| 读取注入的上下文材料 | 直接触碰 instance.json 或消息池 |
| 上报 DONE | 知道自己被哪个工作流调度 |

---

## 二、文件结构

```
<skill_id>/
├── SKILL.md             # 主文件
├── references/          # [可选] 参考文档
└── scripts/             # [可选] 辅助脚本
```

| 位置 | 说明 |
|------|------|
| **生产车间** `artifacts/skills/<skill_id>/` | 全局 Skill，被 ≥2 个工作流引用 |
| **生产车间** `artifacts/workflows/<id>@<ver>/skills/<skill_id>/` | 局部 Skill，仅本工作流使用。与工作流同目录，方便生产管理 |
| **消费者项目** `.claude/skills/<skill_id>/` | 所有 Skill（全局 + 局部）在拉取后统一汇入此目录，不分来源 |

生产车间中分散存放是为了管理，消费者项目中扁平化是为了加载。wfctl 读取 Skill 时仅查找 `.claude/skills/`。

---

## 三、SKILL.md 结构

采用 **YAML frontmatter + 正文** 结构：

```markdown
---
name: <skill名称>
description: <一句话描述，供主 Agent 识别>
---

# <name>

...
```

| frontmatter 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | `string` | 是 | Skill 名称 |
| `description` | `string` | 是 | 供主 Agent 识别此 Skill 的用途 |

---

## 四、AskUserQuestion 与 AWAITING_CONFIRM 的替换机制

### 注入顺序

SubAgent 启动时，框架按以下顺序注入内容：

```
1. 框架层规则注入（先）
   ├── AskUserQuestion → AWAITING_CONFIRM 替换规则
   ├── wfctl identity 调用指令
   ├── wfctl message write 契约
   └── 契约文件读取指令

2. SKILL.md 正文加载（后）
   └── SubAgent 在已持有上述规则的前提下阅读 Skill 指令
```

因为规则注入在前，SKILL.md 加载在后，**SKILL.md 可以自然使用 AskUserQuestion**——SubAgent 已经被教导如何转化它。

### 替换行为

当 SKILL.md 指导 SubAgent 发起 AskUserQuestion 时，SubAgent 自觉将其替换为：
1. 调用 `wfctl message write` 写入 `status: AWAITING_CONFIRM` 的消息
2. `confirm_questions` 字段携带 1-4 条来自原 AskUserQuestion 选项的具体问题
3. 上报后暂停，等待编排器回传用户答案

用户确认后，编排器将答案注回**同一个** SubAgent 实例。SubAgent 从暂停点恢复，看到 AskUserQuestion 返回了用户选择，继续执行后续逻辑。

### 与独立使用的兼容性

单独调用 Skill（非工作流环境）时，没有框架注入。Skill 中的 AskUserQuestion 正常触发——这正是它的自然行为。**同一份 SKILL.md 同时兼容独立使用和工作流调度**，无需任何修改。

---

## 五、多阶段连续叙事模型

### 多阶段 Skill 的本质

有些 Skill 自然跨越多个阶段。例如 `design-tech-stack` 覆盖三个连续的阶段（收集需求→架构选型→技术栈输出）。它的 SKILL.md 不是三个独立指令的拼接，而是一个**连贯的步骤序列**：

```
Step 1：扫描 docs/，提取约束，判定场景，AskUserQuestion("需求上下文是否完整？")
  ↓（用户确认）
Step 2：逐一提问 8 个架构关节点选型，AskUserQuestion("技术选型是否符合预期？")
  ↓（用户确认）
Step 3：分层细化方案，输出文档，AskUserQuestion("是否确认输出技术栈文档？")
  ↓（用户确认）
```

### WORKFLOW.yaml 的角色

WORKFLOW.yaml 把这段连续叙事**切割为多个 Stage**，以便在关键决策点挂载确认：

```yaml
- stage_id: s01-collect-requirements  # 锚定 Step 1
  skill_id: design-tech-stack
  confirmation_point: true

- stage_id: s02-architecture-selection  # 锚定 Step 2
  skill_id: design-tech-stack
  confirmation_point: true

- stage_id: s03-tech-stack-output  # 锚定 Step 3
  skill_id: design-tech-stack
  confirmation_point: true
```

但 **Skill 不感知这些 Stage 划分**。它只知道"我有一个三步流程，每步结束时会请用户确认"。

### 同实例跨 Stage 延续

当多个 Stage 使用**同一个 `skill_id`** 时（无论是连续出现还是被其他 Skill 的 Stage 隔开），编排器复用同一个 SubAgent 实例。

**检测机制**：映射表存储在 `.agent/running_agents.json`（项目级唯一文件）。编排器在 `spawn` 成功后写入，`next` 自动读取并按 `instance_id` 过滤。`next` 计算就绪 Stage 时查表——命中则生成 `continue` action（而非 `spawn`），将已有 SubAgent 注入新 worktree 和 task 后继续执行。

```
s01: SubAgent 启动 → 执行 Step 1 → AskUserQuestion → AWAITING_CONFIRM → DONE
  ↓（next 检测 s02 的 skill_id = s01，命中映射表 → continue action）
s02: 同一个 SubAgent 收到 continue 信号 → 切换 worktree → 执行 Step 2 → AWAITING_CONFIRM → DONE
  ↓（同上）
s03: 同上 → 执行 Step 3 → DONE
```

即使中间插入了其他 Skill 的 Stage（如 s02 是 `compliance-reviewer`），也不影响映射表：

```
s01(design-tech-stack) → spawn agent-001 → 映射表: {design-tech-stack: agent-001}
s02(compliance-reviewer) → spawn agent-002 → 映射表: {..., compliance-reviewer: agent-002}
s03(design-tech-stack) → next 命中映射表 → continue agent-001
```

**SubAgent 视角**：从头到尾按 SKILL.md 的步骤序列执行，偶尔向用户提问。不知道每一步对应一个 Stage，不知道中间有其他 SubAgent 工作过。收到 continue 信号后重新读盘，自然拿到最新的文件状态。

**编排器视角**：每个 Stage 完成后记录状态、打锚点、消费 Message。`next` 通过映射表决定 spawn 还是 continue。worktree 分配逻辑在 spawn 和 continue 间完全一致。

### 实例销毁条件

| 条件 | 行为 |
|------|------|
| 就绪 stage 的 skill_id 在映射表中命中 | `continue` action，保留实例 |
| 就绪 stage 的 skill_id 未命中 | `spawn` action，创建新实例 |
| SubAgent 崩溃/超时 | 主 Agent 从映射表移除，下次命中时走 `spawn` 重建 |
| `parallel` 拆分 | 每个拆分实例独立，不参与映射表 |
| `confirmed(to=self)` 中继确认 | 同一 Stage 循环，重新 spawn |
| 用户 rollback | 级联清理 `system_agent_id`，映射表自然失效 |

### 跨 Stage 状态传递

SubAgent 实例在内存中保持上下文（文件读取结果、中间分析、用户历史回答）——无需通过文件系统做"冷启动恢复"。因失败/超时/用户回退而重建 SubAgent 时，编排器从上一个 Stage 的 `checkpoint_summary` 中注入上下文。

---

## 六、框架注入

Skill 是标准 Claude Code Skill，正文中只描述业务能力。**AskUserQuestion 是业务交互的一部分，可以保留在正文中。**

当 Skill 被工作流调度为 SubAgent 时，主 Agent 在启动 prompt 中注入以下内容：

| 注入内容 | 来源 |
|------|------|
| AskUserQuestion → AWAITING_CONFIRM 替换规则（见 §四） | 工作流框架 |
| 调用 `wfctl identity` 指令 | SubAgent 启动后自取身份参数，禁止凭记忆构造 |
| 上报契约（何时调 `wfctl message write`、status 取什么值） | 本规范 |
| 要求读取通用契约 | 主 Agent 注入读取指令 |
| 特殊字段要求（`confirm_questions`、`parallel_targets` 等） | `wfctl next` 的 `requires_*` 标志 |

身份参数由 `wfctl identity` 返回，**不包含** `project_root`——SubAgent 不知主仓库位置，仅通过 worktree 路径和 `wfctl message write` 间接通信。

---

## 七、契约体系

契约不由主 Agent 读取或转述。主 Agent 在启动 prompt 中**要求 SubAgent 自行读取**：

| 契约文件 | 路径 | 内容 |
|------|------|------|
| 通用契约 | `.claude/contracts/common.md` | 硬禁令（不可触碰的路径、文件系统限制、Git 操作禁令）、降级熔断 |
| 输入契约 | `references/contract-input.md` | [可选] 专用输入字段定义 |
| 输出契约 | `references/contract-output.md` | [可选] 专用输出字段定义 |

SubAgent 在启动后自行读取上述文件。主 Agent 不读取、不解析、不转述契约内容。

---

## 八、与旧 Skill 规范的区别

- `[WORKFLOW_CONFIG]` 代码块：已移除。workflow 相关信息由 prompt 注入。
- 外部对接协议段：已移除。对接行为由主 Agent 在 prompt 中注入，不写在 Skill 正文中。**AskUserQuestion 例外——它是 Skill 的自然交互方式，不是工作流协议，保留在正文中。**
- 契约读取义务：主 Agent 不转述契约，改为要求 SubAgent 自行读取契约文件。
- **新增 AskUserQuestion 替换机制**：框架注入规则在先，SKILL.md 在后——SubAgent 自觉替换，框架不截获。
