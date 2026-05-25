# Message 通信协议规范 v3.0.0

---

## 一、定位

Message 是 SubAgent 与主 Agent 之间**唯一的通信协议**。SubAgent 通过 `wfctl message write` 上报，wfctl 在写入时注入全部元数据字段，Message 落盘后永不修改。主 Agent 通过 `wfctl next` 的返回结果间接阅读。

---

## 二、文件路径

```
.agent/instances/<instance_id>/messages/<message_id>.json
```

---

## 三、完整 schema

```json
{
  "schema_version": "3.0.0",
  "message_id": "msg-001",
  "instance_id": "20260517-001",
  "stage_id": "s01",
  "stage_instance_id": "s01",
  "status": "DONE",
  "report": "完成选题分析，推荐方案 B",
  "checkpoint_summary": "已完成：3 个候选方案评估。关键结论：方案 B 数据可得性最优。",
  "confirm_questions": [],
  "parallel_targets": null,
  "modified_files": [],
  "timestamp": "2026-05-17T14:30:00+08:00"
}
```

---

## 四、字段定义

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `schema_version` | `string` | wfctl 注入 | `"3.0.0"` |
| `message_id` | `string` | wfctl 注入 | 唯一标识，由 wfctl 使用 UUID v4 生成，全局唯一。SubAgent 不指定、不可覆盖 |
| `instance_id` | `string` | wfctl 注入 | 所属实例 |
| `stage_id` | `string` | wfctl 注入 | YAML 中的 stage 标识 |
| `stage_instance_id` | `string` | wfctl 注入 | 实例级 stage 标识（parallel 时区分） |
| `status` | `enum` | SubAgent | `RUNNING` / `DONE` / `ERROR` / `AWAITING_CONFIRM` |
| `report` | `string` | SubAgent | 面向用户的执行摘要，非空。采用 conventional commit 格式，包含标题行和可选的正文（如 `feat(s03): 完成选题分析\n\n- 评估了三个候选方案\n- 方案 B 数据可得性最优`）。wfctl 消费时取此字段作为 git commit message 的完整内容 |
| `checkpoint_summary` | `string` | SubAgent | 面向下一个 SubAgent 的交接说明。格式：`已完成：...；关键上下文：...；待处理：...`。用于冷启动恢复时向下一个 SubAgent 注入上下文 |
| `confirm_questions` | `string[]` | SubAgent | `status=AWAITING_CONFIRM` 时必填，长度 1-4（匹配 AskUserQuestion 上限） |
| `parallel_targets` | `object[]` | SubAgent | 拆分目标列表 `[{id, label, context}]`。下游有 `parallel` 声明时通过提示词注入要求产出 |
| `routing_choice` | `string` | SubAgent | 选择边值。当上游 stage 有多条 SUCCESS edge 且设置了 `choice` 时，SubAgent 上报 DONE 需携带此字段，值须与某条 SUCCESS edge 的 `choice` 严格一致。`MessageConsumerProcessor` 校验该值合法性（通过 `TransitionPolicy.validate_routing_choice()`），非法则 stage → ERROR |
| `modified_files` | `string[]` | wfctl 注入 | wfctl 通过 `git status --porcelain` 获取变更列表。**SubAgent 不填此字段** |
| `timestamp` | `string` | wfctl 注入 | ISO 8601 带时区 |

---

## 五、status 枚举

| 值 | 含义 | 流转 |
|----|------|------|
| `RUNNING` | SubAgent 已启动，正在处理 | 冷启动后首次上报。非心跳协议——SubAgent 无独立定时器，存活检测由 `timeout_seconds` 兜底 |
| `DONE` | 阶段完成 | wfctl 将 stage 置为 DONE，解锁下游 |
| `ERROR` | 失败 | wfctl 进入 retry/ERROR 分支 |
| `AWAITING_CONFIRM` | 等待用户确认 | wfctl 将 stage 置为 AWAITING_CONFIRM，阻塞下游 |

---

## 六、SubAgent 侧契约

SubAgent 通过 `wfctl message write` 写入消息。调用前必须通过 `wfctl identity` 获取自身身份参数（instance_id、stage_id 等），禁止凭记忆构造。

SubAgent 填写业务字段（`status`、`report`、`checkpoint_summary`、`confirm_questions`、`parallel_targets`）。`message_id`、`timestamp`、`modified_files` 由 wfctl 在 `message write` 时注入，SubAgent 不填。Message 落盘后永不修改。

---

## 七、wfctl 消费与校验

`next` 对每条未消费消息：校验 `modified_files` 是否触及保护区（`.agent/`、`.git/`、他人 worktree），违规则写入 deviation 日志。校验通过后根据 `status` 更新 stage 状态，追加至 `consumed_message_ids`。

---

## 八、与旧协议的区别

- `draft_files` / `output_files` / `upstream_files` / `tmp_dir`：已移除。产物管道统一为 worktree
- `modified_files`：从 SubAgent 上报改为 wfctl 注入
- `confirm_required`：移除冗余字段，`status=AWAITING_CONFIRM` 本身已表达
- `metadata`：移除，审计信息由日志系统承载
