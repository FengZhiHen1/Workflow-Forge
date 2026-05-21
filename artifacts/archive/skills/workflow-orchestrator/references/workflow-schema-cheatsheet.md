# Workflow / Instance / Message 字段速查

## Reference 目录（`.claude/workflows/<workflow_id>@<version>/`）

每个工作流一个目录，内含两个文件：
- `WORKFLOW.md` —— 人类可读：名称、概览、Mermaid 流程图、各 stage 自然语言描述
- `WORKFLOW.yaml` —— 机器规范：stages、edges、并发规则、冲突仲裁

机器以 `WORKFLOW.yaml` 为唯一权威；`WORKFLOW.md` 中的同名字段仅作参考，冲突时以 yaml 为准。

### WORKFLOW.yaml 关键字段

机器规范 YAML 中的关键字段：

| 字段 | 含义 | 实例中对应 |
|------|------|-----------|
| `workflow_id` | 工作流标识 | `reference.workflow_id` |
| `version` | 语义化版本 | `reference.version` |
| `model_tiers` | 可用抽象档位列表 | 如 `["light", "standard", "heavy"]` |
| `default_model_tier` | 默认档位 | `stages[].model_tier` 省略时的继承值 |
| `stages[].stage_id` | 阶段 ID | `stages[].stage_id` |
| `stages[].skill_id` | 执行该阶段的 Skill | `stages[].skill_id` |
| `stages[].model_tier` | 抽象模型档位（如 `light`/`standard`/`heavy`） | 工作流只声明档位，具体模型名由 Skill 映射决定 |
| `stages[].mandatory` | 是否不可跳过 | 偏差仲裁时检查 |
| `stages[].confirmation_point` | 是否需用户确认 | 调度时检查 |
| `stages[].fan_out` | fan-out 配置（可选） | `{source, split_by?, max_instances?}`。source 为触发拆分的上游 stage_id；编排器按上游 Message 的 `fan_out_targets` 创建对应数量实例 |
| `stages[].retry_policy.max_attempts` | 最大重试次数 | `stages[].attempt_count` |
| `stages[].retry_policy.on` | 触发重试的条件 | ERROR 时判断 |
| `edges[].from/to` | 流转方向 | 依赖计算用 |
| `edges[].condition` | 流转条件 | `always/success/failure/confirmed/rejected/loop_exceeded` |
| `edges[].max_loop` | 最大循环次数 | `stages[].loop_counter` 比对 |
| `edges[].aggregation` | 多实例聚合模式（可选，默认 `all`） | `all`：全部上游实例完成才推进；`any`：任一完成即推进 |
| `edges[].loop_counter_stage` | 循环计数器归属 stage | 递增 `loop_counter` 的目标 |
| `concurrency_rules.max_parallel_agents` | 并发上限 | 调度时限制 |
| `concurrency_rules.allowed_parallel_stages` | 允许并行的 stage 组 | 资源冲突检查 |

## Instance 状态机（`.agent/workflows/instances/<id>.json`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `instance_id` | string | `wf-{workflow_id}-{timestamp}-{seq}-{random}` |
| `status` | enum | `PLANNING/EXECUTING/SUSPENDED/COMPLETED/FAILED/CANCELLED` |
| `reference.*` | object | 绑定的 Reference 信息 + `snapshot_hash`（`WORKFLOW.yaml` 内容的 SHA256） |
| `current_stage` | string/null | 当前聚焦的 stage_id |
| `stages[].stage_instance_id` | string | 实例唯一标识。单实例时与 `stage_id` 相同；多实例时格式为 `{stage_id}#{n}`（n 从 1 开始）。兼容旧数据：缺失时等同 `stage_id` |
| `stages[].status` | enum | `PENDING/RUNNING/BLOCKED/DONE/ERROR/SKIPPED/CANCELLED/SUPERSEDED` |
| `stages[].assigned_agent_id` | string/null | 编排器生成的**逻辑 agent_id**（格式：`{stage_id}-{timestamp}-{random}`），注入 SubAgent prompt |
| `stages[].system_agent_id` | string/null | 平台返回的**系统 agent_id**（创建 SubAgent 后回填），用于 resume / SendMessage |
| `stages[].attempt_count` | int | 当前重试次数（由编排器管理） |
| `stages[].loop_counter` | int | Workflow 循环计数（edges 回跳） |
| `stages[].blocked_by_confirm` | bool | 是否因确认阻塞 |
| `stages[].git_anchor_tag` | string | 回退锚点 tag |
| `pending_confirmations` | string[] | 待确认的 message_id 列表 |
| `deviation_log[]` | object | 用户覆盖规范的偏差记录 |
| `execution_summary` | object | 运行统计 |

## Message 文件（`.agent/messages/YYYY-MM-DD/<id>.json`）

| 字段 | 说明 |
|------|------|
| `message_id` | `YYYYMMDD-序号-4位随机` |
| `status` | `RUNNING/PENDING_CONFIRM/DONE/ERROR` |
| `workflow_instance_id` | 归属实例 |
| `skill_id` / `agent_id` | 执行者信息 |
| `confirm_required` / `confirm_questions` | 确认请求 |
| `report` / `checkpoint_summary` | 执行摘要与恢复上下文 |
