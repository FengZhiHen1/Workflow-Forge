# Action 处理详解

> 本文档是 `SKILL.md`「Action 速查」的补充，包含每种 action 的完整 JSON 格式和详细执行步骤。
> 各 action 的字段定义权威来源为 `references/wfctl-commands.md` §2.3。

---

## spawn —— 启动新 SubAgent

```json
{
  "action": "spawn",
  "instance_id": "20260517-001",
  "stage_id": "s03",
  "skill_id": "topic-analyst",
  "worktree": ".tmp/worktrees/instance-20260517-001/",
  "model": "standard",
  "requires_parallel_targets": false,
  
  "valid_routing_choices": ["full_design", "code_only", "both_exist"],
  "context": {
    "goal": "为 M01-M05 模块编写落地规范",
    "upstream_summaries": [{"stage_id": "s01", "checkpoint": "已完成选题分析..."}],
    "parallel_target": null
  }
}
```

执行步骤：

1. **解析 skill 路径**：按以下优先级查找 `<skill_id>` 对应的 SKILL.md，取首个存在者：
   - `.claude/skills/<skill_id>/SKILL.md`
   - `artifacts/workflows/<workflow_id>/skills/<skill_id>/SKILL.md`（工作流专属 Skill）
   - `artifacts/skills/<skill_id>/SKILL.md`（全局 Skill）
   将找到的绝对路径填入模板的 `<skill_path>` 占位符。
2. 按 `references/subagent-prompt-template.md` 模板构造 prompt。prompt 仅注入工作流协议信息（身份、上报契约、上下文），不包含 Skill 正文——SubAgent 会在启动步骤中自行读取 `<skill_path>` 指定的 SKILL.md 文件
3. 解析模型：读取 `references/model-mapping.yaml`，将 action 的 `model` 档位按当前平台映射为具体模型名，传入 `Agent(model=...)`。若 action 无 `model` 字段则省略，Agent 继承父级模型
4. 启动 SubAgent：`Agent(worktree=<worktree>, model=<resolved_model>, prompt=<构造的prompt>, run_in_background=true)`
   - **禁止** `isolation: "worktree"`：worktree 已由 wfctl 管理，Agent 工具不应创建第二层隔离
   - `Agent()` 返回 `system_agent_id`
5. **写入 stage state**：调用 `wfctl register-agent --instance <id> --stage <id> --agent-id <system_agent_id>` 将 agent ID 写入 stage state，供后续 `next` 匹配 continue
6. **不等待**——继续处理下一个 action

---

## continue —— 延续已有 SubAgent

```json
{
  "action": "continue",
  "instance_id": "20260517-001",
  "stage_id": "s02",
  "skill_id": "design-tech-stack",
  "worktree": ".tmp/worktrees/instance-20260517-001/",
  "system_agent_id": "agent-001",
  "requires_parallel_targets": false,
  
  "valid_routing_choices": [],
  "context": {
    "goal": "为 M01-M05 模块编写落地规范",
    "upstream_summaries": [{"stage_id": "s01", "checkpoint": "已完成需求收集..."}]
  }
}
```

执行步骤：

1. 按 `references/subagent-prompt-template.md` 的 continue 模板构造 prompt（单条消息，末尾包含自激活指令，无需额外激活消息）
2. `next` 已自动更新 `.agent/running_agents.json` 中该条目的 `stage_id`
3. **不**调用 `Agent()` 创建新实例——向已有 SubAgent（`system_agent_id`）发送继续消息：`SendMessage(to=<system_agent_id>, message=<构造的prompt>)`
4. 若 `SendMessage` 返回 agent 已失效的错误（如 "No transcript found"、"stopped (completed)"、agent 已退出）→ **降级为 spawn**——使用 action 中已有的 `skill_id`、`worktree`、`context`、`valid_routing_choices` 等字段，按上方 spawn 步骤 1-6 启动新 SubAgent
5. 若 `SendMessage` 返回其他错误（如网络超时、权限拒绝）→ **向用户报告错误**，不静默降级，交由用户决策
6. **不等待**——继续处理下一个 action

---

## conflict —— 合并冲突

```json
{
  "action": "conflict",
  "stage_id": "s03",
  "worktree": ".tmp/worktrees/stage-<id>-s03/",
  "conflict_files": ["src/a.py", "src/b.py"],
  "source_stage": "s03"
}
```

执行步骤：
1. 启动 `conflict-resolver` 全局 Skill 作为 SubAgent
2. prompt 注入：冲突文件列表、冲突所在 worktree 路径、产出冲突的 stage 信息
3. conflict-resolver 自动消解简单冲突；语义冲突通过 `AWAITING_CONFIRM` 追问用户
4. 消解后调用 `wfctl next`——wfctl 重试合并，无冲突则 stage → DONE

---

## merge_to_main —— 合入主仓库

```json
{"action": "merge_to_main", "status": "completed"}
```

**一级实例（`parent_instance_id` 为空）**：全部 stage DONE 后，wfctl 不会直接合入——先注入虚拟确认 stage `__merge__`，通过 `confirm` action 由编排器向用户确认。确认后下次 `next` 才执行合入。

**子实例**：全部 stage DONE 后直接合入父实例 worktree，不设确认环节。

有冲突时 wfctl 返回 `conflict` action，按冲突处理流程消解。

---

## terminate —— 实例终态

```json
{"action": "terminate", "status": "FAILED", "reason": "s03 重试耗尽，无可用 failure handler"}
```

向用户报告终态原因。`COMPLETED` → 成功总结。`FAILED` → 失败原因和建议。wfctl 已在 `next` 中自动完成 worktree 清理。

---

## await —— 等待

```json
{"action": "await", "reason": "no ready stages"}
```

无就绪 stage 可调度。等待 SubAgent 完成通知（宿主平台会通知你），收到通知后再次调用 `wfctl next`。

---

## reinforce —— 强化重试

```json
{
  "action": "reinforce",
  "type": "parallel_targets_missing",
  "stage_id": "s08-dispatch-modules",
  "source_stage_id": "s07-project-sync-and-dispatch",
  "system_agent_id": "agent-xxx",
  "retry_count": 1,
  "max_retry": 2,
  "message": "你在 stage s07 的上报中未包含 parallel_targets。请根据 contracts/output.md 规范补充..."
}
```

**含义**：wfctl 检测到上游 stage 缺失必要产出，但上游 SubAgent 仍存活，调度器自动发起强化重试要求补交。

**执行步骤**：

1. 通过 `SendMessage` 将 `message` 发送给 `system_agent_id` 对应的 SubAgent
2. SubAgent 收到后应补充产出（`message write --parallel-targets ...`）
3. 立即再次调用 `wfctl next`——如果 SubAgent 已补充，新消息被消费后自动完成并行拆分；如果未补充，重试计数 +1
4. 最多重试 2 次，超次后调度器将该 stage 置为 `ERROR`，走现有错误处理链（通常为 `terminate`）
