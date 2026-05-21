# 平台差异对照（Kimi Code vs Claude Code）

## SubAgent 启动

| 维度 | Kimi Code | Claude Code |
|------|-----------|-------------|
| 工具 | `Agent` | `Agent` |
| 后台执行 | `run_in_background=true` | `run_in_background=true` |
| Prompt 注入 | Skill system prompt + `[WORKFLOW_CONTEXT]` + `[WORKFLOW_INJECTED_BANS]` | 同上 |

## SubAgent 恢复（PENDING_CONFIRM → CONFIRMED 后）

| 维度 | Kimi Code | Claude Code |
|------|-----------|-------------|
| 首选方式 | `Agent(resume=<agent_id>)` | `SendMessage(agent_id=<agent_id>)` |
| 恢复时注入 | `checkpoint_summary` + 用户确认结果 | 同上 |
| 降级方案 | 创建新 Agent，注入历史上下文 | 创建新 Agent，注入历史上下文 |
| 平台恢复能力 | 原生 `resume` 保留完整 Soul 上下文 | 消息池兜底 |

## 编排器轮询/触发

| 维度 | Kimi Code | Claude Code |
|------|-----------|-------------|
| 触发源 | 用户消息 / SubAgent 完成通知 | 用户消息 / SubAgent 完成通知 |
| 后台 Agent 通知 | 系统自动通知主 Agent | 系统自动通知主 Agent |
| AskUserQuestion | 同步调用，回复在当前 turn 返回 | 同步调用，回复在当前 turn 返回 |
| 并发启动 | 一个 turn 内批量 `Agent` 调用 | 一个 turn 内批量 `Agent` 调用 |

## 文件路径与权限

两种平台统一使用 `/` 作为路径分隔符（脚本内部处理）。
编排器作为顶层 Skill，拥有对 `.agent/` 和 `.claude/` 的完整读写权限。

## 模型档位映射差异

编排器根据 stage 的 `model_tier`（如 `light`/`standard`/`heavy`）解析具体模型名时，**统一读取调度器自身的 `references/model-tiers.yaml`**。该文件按平台维护映射表：

```yaml
default_platform: kimi-code
tiers:
  light:
    # Kimi Code CLI 当前仅支持单一模型 kimi-k2.6
    kimi-code: "kimi-k2.6"
    claude-code: "claude-sonnet-4-20250514"
  standard:
    kimi-code: "kimi-k2.6"
    claude-code: "claude-opus-4-20250514"
  heavy:
    kimi-code: "kimi-k2.6"
    claude-code: "claude-opus-4-20250514"
```

**运行时解析规则**：
1. 调度器检测当前平台（优先环境变量 `AGENT_PLATFORM`，其次运行时特征推断）
2. 读取 `model-tiers.yaml`，按 `tiers[<model_tier>][<platform>]` 取模型名
3. 若当前平台无显式映射，fallback 到 `default_platform`

> 新增平台时，只需在 `model-tiers.yaml` 每个 tier 下增加一行，工作流与 Skill 均零感知。

## 关键提示词差异

在 SKILL.md 中描述恢复逻辑时，使用平台无关的表述：

> 当需要恢复 SubAgent 时，使用平台原生机制：
> - Kimi Code：通过 `Agent` 工具的 `resume` 参数恢复同一实例
> - Claude Code：通过 `SendMessage` 向指定 agent_id 发送恢复指令
> - 若平台恢复机制不可用，创建新 SubAgent，在 prompt 中注入 `checkpoint_summary` 和确认结果
