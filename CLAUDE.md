# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 工作空间定位

本仓库是 **Workflow v3.0.0** 工作流体系的生产车间，不是真实项目环境。绝对不要把本仓库路径当作真实项目路径。

三层角色：
1. **规范演进场** — 工作流协议、契约、状态机的制定与迭代
2. **生产车间** — 工作流与 Skill 的设计、实现与维护
3. **分发源头** — 消费者项目通过 `workflow-env-init` 从本仓库拉取工件

## 核心架构

### 三大分区

| 区 | 目录 | 职责 | 是否分发 |
|----|------|------|---------|
| 车间区 | `workshop/` | 规范文档 + 模板 + 参考 + 归档 | 否 |
| 产物区 | `artifacts/` | 工作流定义、全局 Skill、契约、基础设施脚本 | 是 |
| 车间自用 | `.claude/` | 本仓库自身的 Claude Code 维护 Skill | 否 |

隐藏目录：`.agent/`（运行时状态机、Message 池）、`.tmp/`（临时草稿），二者均 gitignore。

### 三角色架构

| 角色 | 本质 | 实现 |
|------|------|------|
| **主 Agent** | 智能决策者 — 理解意图、匹配工作流、呈现确认、全局调度 | Claude Code / Kimi Code 自身 |
| **wfctl** | 机械调度器 — 读 YAML、判就绪、推流转、返回结构化指令 | `artifacts/scripts/wfctl/` (纯 Python，无 AI) |
| **SubAgent** | 执行者 — 在隔离 worktree 中干活，通过 Message 上报 | 被主 Agent 调度的后台 Agent |

### wfctl 命令一览

```
wfctl resolve    — 工作流匹配与参数解析
wfctl create     — 创建工作流实例
wfctl next       — 获取下一步调度指令（核心命令，主 Agent 循环调用）
wfctl confirm    — 处理用户确认/拒绝
wfctl pause      — 暂停实例
wfctl resume     — 恢复实例
wfctl rollback   — 回退到指定 stage 锚点
wfctl skip       — 跳过指定 stage
wfctl status     — 项目全局状态或单实例详情
wfctl sync       — 同步 worktree 变更
wfctl deviate    — 记录偏差
wfctl identity   — SubAgent 启动后获取身份参数
wfctl message write — SubAgent 写入标准化 Message
wfctl cleanup    — 清理已完成/失败的实例 worktree
wfctl terminate  — 终止实例
```

wfctl 源码结构：
- `cli/` — 各子命令的 argparse 注册与 handler
- `core/` — 原子写、DAG 解析、Git 操作、Schema 校验、锁、时间戳
- `services/` — 业务逻辑：调度器、创建器、状态管理器、worktree 管理器、状态构建器、消息处理器

运行测试：`cd artifacts/scripts/wfctl && python -m pytest tests/`

### 主 Agent 调度循环

主 Agent 通过循环调用 `wfctl next` 获取 action 并执行：

```
wfctl next --instance <id>
  ↓ 返回 action 数组
  - spawn   → Agent(run_in_background=true)，写入条目到 .agent/running_agents.json
  - continue → SendMessage(to=system_agent_id)，next 自动更新文件中的 stage_id
  - confirm → 呈现 AskUserQuestion → wfctl confirm
  - retry / await / conflict / merge_to_main / terminate
  ↓ 每条 action 执行后重新调用 next（循环）
```

**映射表**：`.agent/running_agents.json`（项目级唯一文件），`next` 自动读取并按 `instance_id` 过滤。

## 产物区结构 (`artifacts/`)

### 工作流 (`workflows/<id>@<version>/`)
每个工作流固定包含 `WORKFLOW.md`（人类可读）+ `WORKFLOW.yaml`（机器权威，调度唯一依据）+ 可选的 `sduiykills/`、`scripts/`、`references/`。

现行工作流只保留最新版本，旧版本移入 `artifacts/archive/workflows/`。

### Skill (`skills/<skill_id>/`)
`SKILL.md`（YAML frontmatter + 正文）+ 可选 `references/`、`scripts/`。
- 被 ≥2 个工作流引用 → 全局 Skill (`artifacts/skills/`)
- 仅 1 个工作流引用 → 局部 Skill（工作流目录下 `skills/`）
- 二者绝对不可合并

### 契约 (`contracts/`)
`common.md`（硬禁令、Git 操作禁令、降级熔断）、`input.md`、`output.md`。所有 Skill 强制读取。

## 关键设计原则

- **最大化并发**：编排器默认并行推进，仅在确认点显式阻塞
- **两级 worktree 隔离**：实例级 worktree（所有 stage 共享）+ 并发时按 stage 拆分 worktree
- **Git 锚点**：每个 stage 完成后打 lightweight tag `wf-{instance_id}-{stage_id}`，支持精确到阶段的原子回退
- **Message 是唯一通信协议**：SubAgent 不直接对话用户，通过 `wfctl message write` 上报，主 Agent 统一收口
- **权限靠隔离不靠自觉**：worktree 物理屏障 + wfctl 事后校验 + git 锚点可逆 + prompt 禁令兜底

## 版本历史

- v1 原型验证（顺序流水线）→ v2 核心规范（并发调度 + 独立状态机）→ v3.0.0（目录重构：docs/results/reference → workshop/artifacts）
- 迁移方案见 `workshop/specs/迁移方案.md`，当前处于迁移执行阶段
