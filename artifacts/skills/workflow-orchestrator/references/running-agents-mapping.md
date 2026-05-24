# SubAgent 映射表（running_agents.json）

映射表是编排器实现「同 Skill 延续」的核心机制。`wfctl next` 解析 Stage 的 `skill_id`，在映射表中查找同 `skill_id` 的存活 SubAgent，命中则产出 `continue` action 而非 `spawn`。

---

## 文件位置

```
<项目根>/.agent/running_agents.json
```

项目级唯一文件，由编排器维护，`wfctl` 只读不写。

---

## Schema

```json
[
  {
    "skill_id": "design-tech-stack",
    "system_agent_id": "agent-abc123",
    "stage_id": "s02-architecture-selection",
    "instance_id": "20260519-001"
  }
]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `skill_id` | string | SubAgent 执行的 Skill ID，与 WORKFLOW.yaml 中 `skill_id` 字段一致 |
| `system_agent_id` | string | 宿主平台分配的唯一 Agent 标识，用于 `SendMessage` 和 `Agent` 回调 |
| `stage_id` | string | 当前执行的 Stage ID（`next` 生成 `continue` 时自动更新为此轮 stage_id） |
| `instance_id` | string | 所属实例 ID，`next` 按此字段过滤——仅匹配当前实例的条目 |

---

## 生命周期

```
                 spawn 成功
                     │
                     ▼
              ┌──────────────┐
              │   写入条目    │  skill_id, system_agent_id, stage_id, instance_id
              └──────┬───────┘
                     │
          ┌──────────┼──────────┐
          │          │          │
          ▼          ▼          ▼
    next 产出     SubAgent    SubAgent
    continue    正常 DONE    崩溃/超时
          │          │          │
          ▼          ▼          ▼
   next 自动更新 编排器移除   编排器移除
   stage_id 字段  对应条目    对应条目
          │
          ▼
   循环可能多次
   (同 Skill 连续多个 Stage)
```

---

## 写入时机

**仅在 `spawn` 成功后写入**：`Agent(run_in_background=true)` 返回 `system_agent_id` 后，立即追加条目。

```
spawn 返回 system_agent_id
  → 构造条目 { skill_id, system_agent_id, stage_id, instance_id }
  → 读取 running_agents.json，追加条目
  → 原子写回
```

**去重规则**：写入前检查现有条目，若 `system_agent_id` 已存在则覆盖（更新 `skill_id`、`stage_id`、`instance_id`）。

---

## 更新时机

**`wfctl next` 自动处理**：当 `next` 发现就绪 Stage 的 `skill_id` 匹配映射表中某个存活 SubAgent 时，生成 `continue` action 并自动将该条目的 `stage_id` 更新为当前 Stage 的 ID。

编排器无需手动更新 `stage_id`——`next` 的 `continue` action 已包含更新后的映射信息。

---

## 命中规则（continue vs spawn 决策）

`next` 按以下顺序匹配：

1. 读取 `.agent/running_agents.json`
2. 按当前 `instance_id` 过滤
3. 对每个就绪的 Stage，检查其 `skill_id` 是否命中过滤后的映射表条目
4. 命中 → `continue` action（复用已有 SubAgent）
5. 未命中 → `spawn` action（启动新 SubAgent）

**不参与映射表的情况**：
- parallel 拆分实例（`stage_instance_id` 含 `_<digit>` 后缀）
- `StageTargetType.WORKFLOW` 类型的 Stage（由 `wfctl` 直接创建子实例）

---

## 清理时机

| 场景 | 操作 |
|------|------|
| SubAgent 上报 `DONE` | 编排器从映射表移除对应条目 |
| SubAgent 上报 `ERROR` 且重试耗尽 | 编排器从映射表移除对应条目 |
| SubAgent 崩溃/超时（平台通知） | 编排器从映射表移除对应条目 |
| 实例终止（`terminate` / `COMPLETED` / `FAILED`） | 编排器批量清理该 `instance_id` 的全部条目 |
| `wfctl cleanup` 执行 | 清理已完成/失败实例对应的全部条目 |

---

## 编排器操作速查

```
┌──────────────┬─────────────────────────────────────────────┐
│   场景        │   编排器动作                                 │
├──────────────┼─────────────────────────────────────────────┤
│ spawn 后      │ 追加条目                                    │
│ continue 后   │ 无需操作（next 已更新 stage_id）             │
│ SubAgent DONE │ 移除对应条目                                │
│ SubAgent 崩溃 │ 移除对应条目                                │
│ 实例终止后    │ 遍历移除 instance_id 匹配的所有条目          │
│ 恢复中断      │ 扫描存活 SubAgent，重建映射表                │
└──────────────┴─────────────────────────────────────────────┘
```

---

## 与 wfctl 的职责边界

| 操作 | 编排器 | wfctl |
|------|--------|-------|
| 读取映射表 | ✓（仅 spawn/清理时） | ✓（`next` 每次读取） |
| 写入映射表 | ✓（spawn 后） | ✗ |
| 更新 stage_id | ✗ | ✓（`next` 产 continue 时） |
| 删除条目 | ✓（SubAgent 终态时） | ✗ |
| 按 instance_id 过滤 | ✗ | ✓（`next` 自动过滤） |

---

## 示例：同 Skill 延续场景

```
Stage 序列：s01 → s02 → s03, skill_id 均为 design-tech-stack

1. s01: next → spawn → Agent(design-tech-stack, run_in_background=true)
   → 写入 {"skill_id": "design-tech-stack", "system_agent_id": "agent-001", "stage_id": "s01", "instance_id": "20260519-001"}

2. SubAgent 完成 s01，上报 AWAITING_CONFIRM → 确认 → s01 DONE

3. s02: next 检测到 skill_id=design-tech-stack 命中 agent-001
   → 产出 continue action (stage_id 已自动更新为 s02)
   → 编排器 SendMessage 给 agent-001，注入 s02 prompt

4. SubAgent 完成 s02，上报 AWAITING_CONFIRM → 确认 → s02 DONE

5. s03: 同上 continue 流程

6. s03 DONE → SubAgent 完成 → 编排器从映射表移除条目
```
