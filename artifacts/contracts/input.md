# 通用输入契约（Input Contract） v3.0.0

> 定义主 Agent 启动 SubAgent 时在 prompt 中注入的全部字段。SubAgent 禁止自行编造任何注入字段。

---

## 一、注入来源

身份参数由 SubAgent 启动后调用 `wfctl identity` 获取，不从主 Agent prompt 中注入。主 Agent 在 prompt 中注入**要求调用 identity 的指令**，SubAgent 禁止凭记忆或假设构造身份信息。

wfctl 统一调用格式：`python .claude/scripts/wfctl/main.py <command> [options]`（从项目根目录执行）。

---

## 二、核心身份字段

SubAgent 启动后调用 `wfctl identity` 获取：

| 字段 | 说明 |
|------|------|
| `instance_id` | 当前工作流实例 ID |
| `stage_id` | 当前 stage 标识 |
| `stage_instance_id` | 实例级 stage 标识（parallel 时区分） |

身份参数不包含 `project_root`。

---

## 三、工作流上下文字段

以下字段由主 Agent 从 wfctl 获取后注入 prompt：

| 字段 | 来源 | 说明 |
|------|------|------|
| `stage_direction` | 主 Agent 基于 `wfctl next` 返回的 spawn action 中的 `context` 生成 | 阶段定位、任务指令、边界、衔接要求 |
| `goal` | `wfctl status --instance <id>` 返回的 `goal` 字段 | 实例目标声明 |
| `special_instructions` | 用户启动时提供 | 补充指令，可为空 |
| `attempt_count` | `wfctl status --instance <id>` 返回的 stage 条目中的 `attempt_count` 字段 | 当前重试次数（仅 ERROR 状态的 stage 有此字段） |
| `loop_counter` | `wfctl status --instance <id>` 返回的 stage 条目中的 `loop_count` 字段 | 当前回跳计数 |

---

## 四、产出要求注入

主 Agent 根据 `wfctl next` 返回的 spawn action 中的标志位，在 prompt 中追加以下要求：

| 标志 | 来源 | 注入内容 |
|------|------|---------|
| `confirmation_point: true` | WORKFLOW.yaml stage 定义 → 编排器感知 | 需要用户确认时，Message 中填入 `confirm_questions` |
| `requires_parallel_targets: true` | spawn action 字段 | 上游产出需包含 `parallel_targets` 列表 |

---

## 五、上游上下文字段

主 Agent 根据 `wfctl next` 返回的 spawn action 中的 `context` 字段，在 prompt 中注入上游 stage 的产出信息：

| 字段 | 来源 | 说明 |
|------|------|------|
| `upstream_summaries` | spawn action 的 `context.upstream_summaries` | 上游 stage 的 `checkpoint_summary` 列表，每条含 `stage_id` + `checkpoint` |
| `upstream_files` | spawn action 的 `context.upstream_files`（由 wfctl 自动汇总） | 上游 stage 产生/修改的文件列表（相对于实例 worktree 根） |

`upstream_summaries` 和 `upstream_files` 均由 wfctl 在消费上游 Message 后自动汇总。主 Agent **不自行推断或编造这些字段**——直接从 spawn action 的 `context` 中取用。

---

## 六、契约与禁令注入

主 Agent 在 prompt 末尾固定注入：

1. 简明禁令（文件系统 + Git 操作禁止清单）
2. 契约读取指令（"执行前先读 `.claude/contracts/common.md`"）
3. 上报指令（"终止前调 `wfctl message write`，status 取 DONE / ERROR / AWAITING_CONFIRM"）
