# 通用输出契约（Output Contract） v3.0.0

> 定义 SubAgent 通过 `wfctl message write` 上报 Message 时必须提供的全部字段。

wfctl 统一调用格式：`python .claude/scripts/wfctl/main.py <command> [options]`（从项目根目录执行）。

---

## 一、上报方式

SubAgent 终止前调用 `wfctl message write`，传入以下字段。`message_id`、`timestamp`、`modified_files` 由 wfctl 自动注入，SubAgent 不填。

---

## 二、SubAgent 提供字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | `enum` | 是 | `DONE` / `ERROR` / `AWAITING_CONFIRM` |
| `report` | `string` | 是 | 面向用户的执行摘要，非空 |
| `checkpoint_summary` | `string` | 是 | 面向下一个 SubAgent 的交接说明。格式：`已完成：...；关键上下文：...；待处理：...` |
| `confirm_questions` | `string[]` | 条件 | `status=AWAITING_CONFIRM` 时必填，长度 [1, 4] |
| `parallel_targets` | `object[]` | 条件 | 被要求产出时填入 `[{id, label, context}]` |

---

## 三、status 语义

| 值 | 含义 | 流转 |
|----|------|------|
| `DONE` | 阶段完成 | wfctl 消费消息后将 stage 置为 DONE，`wfctl next` 计算下游就绪 |
| `ERROR` | 失败 | wfctl 消费消息后将 stage 置为 ERROR，`wfctl next` 返回 retry action |
| `AWAITING_CONFIRM` | 需要用户确认 | wfctl 消费消息后将 stage 置为 AWAITING_CONFIRM，`wfctl next` 返回 confirm action。编排器通过 AskUserQuestion 呈现给用户，用户回复后调用 `wfctl confirm` |

---

## 四、report 规范

- 非空字符串
- 说明"做了什么、结果如何"
- 若发生资源级降级，在此说明

---

## 五、checkpoint_summary 规范

- 三段式：`已完成：...；关键上下文：...；待处理：...`
- 面向冷启动恢复：假设下一个 SubAgent 仅通过此摘要重建上下文
- 此字段将作为 `upstream_summaries` 传递给下游 stage 的 SubAgent

---

## 六、parallel_targets 规范

> 仅当编排器告知你需要产出 `parallel_targets` 时适用。如果编排器未要求，跳过此节。

### 6.1 含义

每个 `parallel_target` 对应下游 stage 的**一个并行实例**。你产出的 targets 数量和身份直接决定 wfctl 创建多少个并行 SubAgent。

> 这是工作流调度层面的机制，不涉及你的业务分析逻辑——你只需要把你的分析结论**正确地映射到 targets 上**。

### 6.2 粒度规则

**一个独立工作单元 = 一个 target。** 从你的业务分析中识别出可自然分解的并行单元：

- 赛题有 N 个独立小问 → 产出 N 个 target，每个对应一个小问
- 系统有 M 个独立模块 → 产出 M 个 target，每个对应一个模块

**禁止行为**：

| 错误 | 后果 | 正确做法 |
|------|------|---------|
| 将多个独立单元打包为 1 个 target | 下游只有 1 个实例，丢失全部并行性 | 每个独立单元一个 target |
| 产出 target 数量与自然单元数不一致 | 下游实例过多（冗余）或过少（缺失） | 对齐业务分析中的自然分解数 |

### 6.3 格式

每个 target 为一个对象：`{id: string, label: string, context: string}`

| 字段 | 说明 | 示例 |
|------|------|------|
| `id` | 业务标识，下游 SubAgent 以此识别自己的任务 | `Task1`、`Q3`、`module-auth` |
| `label` | 简短中文标签，便于编排器日志和用户查看 | `问题一：功耗最优降低` |
| `context` | 下游 SubAgent 需要的任务上下文（数据、约束、目标） | `分析 raw_data.csv 中 Q1 的功耗趋势…` |

上报时通过 `--parallel-targets` 传入，每项格式为 `"id:标签:上下文"`，空格分隔多个目标：

```
python .claude/scripts/wfctl/main.py message write \
  --instance <id> --stage <stage_id> --status DONE \
  --report "..." \
  --parallel-targets \
    "Task1:问题一：功耗最优降低:数据源为附件1，约束见p1b分析" \
    "Task2:问题二：分时电价最小化电费:数据源为附件2，需考虑峰谷价差" \
    "Task3:问题三：含BESS和冷却惯性约束:数据源为附件3，约束见p1c依赖分析"
```

### 6.4 常见错误

| 错误 | 后果 | 正确做法 |
|------|------|---------|
| 将所有单元打包为 1 个 L0 级 target | 下游只有 1 个实例，丢失并行性 | 每个独立单元一个 target |
| 产出 target 数量与自然单元数不一致 | 下游实例过多或过少 | 对齐自然分解数 |
| target 的 `id` 无业务含义（如 `t1`、`t2`） | 下游 SubAgent 无法识别自己的任务 | 使用业务标识（如 `Task1`、`Q3`） |
| `context` 为空或过简 | 下游 SubAgent 缺少执行上下文 | 包含数据源、约束、目标等关键信息 |

---

## 七、confirm_questions 规范

> 仅当 `status=AWAITING_CONFIRM` 时适用。confirm_questions 是自由格式的选项列表，选项值不再需要匹配工作流边定义。SubAgent 通过后续 continue prompt 获取用户选择并自行决定后续行为（包括通过 DONE + routing_choice 选择下游路径）。

### 7.1 格式约定

建议使用 **`<choice值>：<显示文本>`** 格式，中文冒号分隔：

```
"<choice值>：<面向用户的自然语言描述>"
```

- `：`（中文全角冒号）**之前**的部分 = 编排器取此前缀作为 `--choice` 参数传给 `wfctl confirm`
- `：`**之后**的部分 = 面向用户的显示文本，可以是自然语言、含括号注释等

### 7.2 示例

```json
[
  "full_design：全新设计，从意图澄清开始走完整流程",
  "code_only：存量代码逆向，从反向工程开始",
  "放弃：终止本工作流实例"
]
```

### 7.3 约束

| 规则 | 说明 |
|------|------|
| 选项数量 ≤ 4 | wfctl 限制 |
| `choice` 值不含 `：` | 避免解析歧义 |
| 选项值自由定义 | 不再需要匹配工作流边 choice，SubAgent 自行决定如何映射 |
