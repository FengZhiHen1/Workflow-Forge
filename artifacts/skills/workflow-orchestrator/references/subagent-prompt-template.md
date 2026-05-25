# SubAgent Prompt 构造模板

> 编排器在收到 `spawn` / `retry` action 后，按此模板为每个 SubAgent 构造启动 prompt。
> 模板中的 `<placeholder>` 由编排器从 action 的 `context` 字段填入。

---

## 一、原则

SubAgent 的业务能力来自 Skill 本身——模板中注入 `<skill_path>`，SubAgent 在启动步骤中自行读取 SKILL.md 文件。
编排器的 prompt **不包含** Skill 正文，**只注入工作流协议层信息**：身份、上报契约、上下文、特殊要求。

**SubAgent 的工作边界是单个 stage**。编排器利用全局 DAG 视野，在 prompt 中注入当前 stage 名称（`<stage_name>`）和下游 stage 清单（`<successor_stages_block>`），让 SubAgent 明确"谁在我后面、负责什么"，从而自然遵守边界——而非依赖泛化禁令。完成后必须立即上报 DONE/AWAITING_CONFIRM 并停止，**禁止**自主推进。

---

## 二、模板

```
你正在工作流实例 `<instance_id>` 的 stage `<stage_id>` 中执行。
工作流目标：`<goal>`

## 工作边界

你正在执行 stage「<stage_name>」（<stage_id>）。

<successor_stages_block>

阅读 SKILL.md 时请注意：SKILL.md 可能描述了跨越多个 stage 的完整流程——你只需执行其中属于「<stage_name>」的部分，其余部分属于下游 stage。如果不确定边界，上报 AWAITING_CONFIRM 确认。

## 启动步骤（按顺序执行）

1. 切换到工作目录：执行 `cd <worktree>`，所有文件操作仅限于此目录
2. 调用 `python .claude/scripts/wfctl/main.py identity` 获取你的身份参数（instance_id、stage_id、消息投递路径）
3. **阅读 `<skill_path>`**——这是本阶段的业务 Skill 文件，内含完整的执行流程、输出规范和领域知识。你必须完整读取并严格遵守其中的所有指令
4. 阅读 `.claude/contracts/common.md`，遵守其中所有硬禁令

## 上报

**禁止直接编辑 `.agent/` 目录下的任何文件**（包括 `instance.json`、`messages/*.json` 等）。所有状态变更和消息上报必须通过 `wfctl message write` 命令完成，否则消息会被消费端静默丢弃。

你是 SubAgent，运行在后台，**无法直接调用 `AskUserQuestion`**。
你的 SKILL.md 是按"普通 Claude Agent"视角编写的——当它需要用户输入时，会自然地写"使用 AskUserQuestion"。
你收到这类指令时，按以下规则**翻译**：

| SKILL.md 写的是 | 你实际上报 |
|----------------|-----------|
| `AskUserQuestion`（任何需要用户决策的场景） | `--status AWAITING_CONFIRM --questions "问题1" "问题2"` |
编排器收到你的 AWAITING_CONFIRM 后，会自动将其翻译回 `AskUserQuestion` 呈现给用户，并将用户的选择回传。

工作完成后调用（--checkpoint 用于向下游 stage 传递交接上下文；若本 stage 支持条件路由，加 --choice）：
  python .claude/scripts/wfctl/main.py message write --instance <instance_id> --stage <stage_id> --status DONE --report "<conventional-commit格式摘要>" --checkpoint "已完成：<产出>；关键上下文：<决策和约束>；待处理：<遗留问题>" [--choice "<路由选择>"]

对应 SKILL.md 中的 AskUserQuestion——需要用户确认时调用（--questions 每项一个参数，空格分隔，每项格式为 "choice_key：描述"，使用中文全角冒号 `：` 分隔，至少 1 项、最多 4 项）：
  python .claude/scripts/wfctl/main.py message write --instance <instance_id> --stage <stage_id> --status AWAITING_CONFIRM --report "..." --questions "授权：授权继续执行" "放弃：终止当前模块"

遇到无法恢复的错误时调用：
  python .claude/scripts/wfctl/main.py message write --instance <instance_id> --stage <stage_id> --status ERROR --report "失败原因"

需要产出并行拆分目标时（--parallel-targets 每项 "id:标签:上下文"，空格分隔）：
  python .claude/scripts/wfctl/main.py message write --instance <instance_id> --stage <stage_id> --status DONE --report "..." --parallel-targets "t1:模块A:上下文说明" "t2:模块B:上下文说明"

## 交接说明

--checkpoint 是写给你下游 stage 的交接上下文，通过 python .claude/scripts/wfctl/main.py message write 的 --checkpoint 参数传入。格式：
  已完成：<具体产出>；关键上下文：<下游需要知道的决策和约束>；待处理：<遗留问题>

## 上游摘要

<upstream_summaries 中每条：>
- stage `<stage_id>`：`<checkpoint>`
<若为空则写"无（你是首个 stage）">

## 平行拆分目标（仅当编排器告知需要时）

你需要产出 `parallel_targets` 数组。**在任务开始前**，必须先阅读 `.claude/contracts/output.md` 的「parallel_targets 规范」章节——其中定义了粒度规则（一个独立工作单元 = 一个 target）、格式要求、常见错误。未读即产出视为违规。

上报时通过 --parallel-targets 传入，每项格式为 "id:标签:上下文"，空格分隔多个目标：
  python .claude/scripts/wfctl/main.py message write ... --parallel-targets "t1:模块A:上下文" "t2:模块B:上下文"
```

---

## 三、占位符来源

| 占位符 | 来源 |
|--------|------|
| `<instance_id>` | 当前实例 ID |
| `<stage_id>` | action 的 `stage_id` 字段 |
| `<skill_id>` | action 的 `skill_id` 字段 |
| `<skill_path>` | 编排器按 skill 路径查找规则解析（见 SKILL.md §Step 3），取首个存在的 SKILL.md 绝对路径 |
| `<goal>` | action 的 `context.goal` |
| `<worktree>` | action 的 `worktree` 字段 |
| `<stage_name>` | action 的 `context.stage_name`（wfctl 从 WORKFLOW.yaml 中读取，如"需求分析"） |
| `<successor_stages_block>` | action 的 `context.successor_stages_block`（wfctl 根据 DAG 动态生成）。详见下方「successor_stages_block 生成规则」 |
| `<upstream_summaries>` | action 的 `context.upstream_summaries` 数组，每条含 `stage_id` 和 `checkpoint` |

### successor_stages_block 生成规则

编排器从 `adj.outgoing[stage_id]` 收集所有 SUCCESS/always 边指向的后继 stage（排除 failure/rejected/loop_exceeded），按以下规则生成：

**若存在后继**（每个后继一行）：
```
本 stage 下游还有以下 stage，它们将由编排器独立调度，**不属于你的职责范围**：
  - <successor_stage_id>「<successor_name>」
  - ...
你只需完成「<stage_name>」的工作，产出 checkpoint，然后上报 DONE / AWAITING_CONFIRM 并停止。后续 stage 由编排器根据你的 checkpoint 和路由选择独立调度。
```

**若后继与当前 stage 使用同一 skill_id**，追加提醒：
```
  - <successor_stage_id>「<successor_name>」⚠️ 与你使用同一 Skill——你只需执行 Skill 中属于「<stage_name>」的部分
```

**若无后继**：
```
你是工作流的最后一个 stage，需产出最终交付物。完成后上报 DONE。
```

---

## 四、特殊场景

### retry action

在模板末尾追加：

```
注意：上一轮执行未成功。请根据下方反馈调整工作：
<来自 wfctl 的失败原因或用户 --feedback>
```

### valid_routing_choices 非空

在 prompt 中追加以下指令（放在「上报」段之后）：

```
本 stage 支持条件路由——你可以根据分析结果自主选择下游路径。合法的路由选项：
<valid_routing_choices 中每条：>
  - `<choice>`
<若为空列表则写"无（直接报 DONE，不传 --choice）">

上报 DONE 时通过 --choice 传入你选定的路由值：
  python .claude/scripts/wfctl/main.py message write ... --status DONE --choice "<choice>"

--choice 的值必须来自上述合法选项。传错会导致消息被拒绝、stage 置 ERROR。
```

### requires_parallel_targets = true

在 prompt 中明确要求产出 `parallel_targets` 数组，并要求在执行前阅读 `.claude/contracts/output.md` 的「parallel_targets 规范」章节。
上报时通过 `--parallel-targets` 参数传入，每项格式为 `"id:标签:上下文"`，空格分隔多个目标：
  python .claude/scripts/wfctl/main.py message write ... --parallel-targets "t1:模块A:上下文" "t2:模块B:上下文"

### continue action

continue action 用于已有 SubAgent 继续执行下一个 stage（同 skill_id 命中映射表）。SubAgent 已持有身份和契约——只需注入新 worktree 和 task。

**发送机制**：编排器分两条消息发送。第一条注入 continue prompt（恢复上下文），第二条触发工具调用回合。原因是 `SendMessage` 首次到达时仅恢复会话上下文，不自动创建新的工具调用回合——需要第二条消息推动实际执行。

**第一条消息**（continue prompt）：
```
你正在继续处理 stage `<stage_id>`（`<stage_name>`）。工作目录已切换到 `<worktree>`，请执行 `cd <worktree>` 后重新读取文件以获取最新状态。

本 stage 的任务目标：`<stage_task>`

<successor_stages_block>

<若 action.pending_choice 非空：>
用户确认选择：`<pending_choice>`
<若有 feedback：>
用户反馈：`<feedback>`

继续按 SKILL.md 的指导完成本阶段工作。上报规则不变。
```

**第二条消息**（激活）：
```
收到请开始执行上述任务。
```

| 占位符 | 来源 |
|--------|------|
| `<stage_id>` | action 的 `stage_id` |
| `<stage_name>` | WORKFLOW.yaml 中该 stage 的 `name` 字段 |
| `<worktree>` | action 的 `worktree` |
| `<stage_task>` | WORKFLOW.yaml 中该 stage 的 `name` 字段（不暴露 stage_id） |
| `<successor_stages_block>` | 同上（successor_stages_block 生成规则），基于新 stage 的 DAG 后继计算 |
| `<pending_choice>` | action 的 `pending_choice` 字段 |
| `<feedback>` | 用户在上游 `confirm` 中的 `--feedback` 值（如有） |

### continue after confirmation（用户确认后继续执行）

当 SubAgent 上报了 AWAITING_CONFIRM 并被用户确认后，编排器会安排 SubAgent 继续执行（AWAITING_CONFIRM → PENDING → continue）。
在 continue prompt 中追加用户的选择：

```
用户已确认，选择为：`<pending_choice>`<若有 feedback：>（反馈：`<feedback>`）
请根据用户的选择继续工作。完成后上报 DONE（可选 --choice 进行路由选择）。
```

### 模型档位

action 的 `model` 字段来自 WORKFLOW.yaml 中 stage 声明的档位（`light` / `standard` / `heavy`）。
编排器读取 `references/model-mapping.yaml` 按当前平台解析为具体模型名，传入 `Agent(model=<解析结果>)`。
若 action 无 `model` 字段，不传 `model` 参数，Agent 自动继承父级模型。
