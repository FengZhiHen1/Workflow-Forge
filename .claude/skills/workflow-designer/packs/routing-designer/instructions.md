# Routing Designer Pack — 条件路由设计

> 当 Stage 下游需要多条互斥路径、根据运行时条件路由时，加载此包。

## 唯一路由机制：`success` + `choice`

所有下游路由通过 **SUCCESS 边 + choice** 实现。不存在 `confirmed`/`rejected` 边条件。

| | SubAgent 自主路由 | SubAgent 询问用户后再路由 |
|---|---|---|
| **谁决定** | SubAgent 自主判断 | 用户选择，SubAgent 根据选择决定路由 |
| **SubAgent 行为** | 分析 → DONE `--choice "xxx"` | 分析 → AskUserQuestion → AWAITING_CONFIRM → confirm → continue → 收到 choice → DONE `--choice "xxx"` |
| **典型场景** | 检测结果明确（如：扫描到代码→走逆向工程） | 用户做主观决策（如：方案选择、范围确认） |
| **工作流定义** | 只需 SUCCESS + choice 边 | 只需 SUCCESS + choice 边（Skill 内部处理 AskUserQuestion） |

**关键洞察**：两种场景在工作流定义层面完全相同——都是 SUCCESS + choice。唯一的区别在 Skill 内部：是否需要 AskUserQuestion。工作流定义不区分两者。

## 核心原则

**确认是 Skill 的内部行为，不是工作流定义的概念。** 无论 SubAgent 自主决定还是询问用户，最终都是 `DONE + routing_choice` 匹配 SUCCESS 边。Skill 用 AskUserQuestion 获取用户输入，然后决定 routing_choice。

## 决策流程

```
需要多条下游路径？
  ├─ 否 → 普通 SUCCESS 边（无需 choice）
  └─ 是 → SUCCESS + choice
         │
         Skill 能自主判断选哪条？
           ├─ 是 → SubAgent 直接 DONE --choice "xxx"
           └─ 否 → Skill 中加 AskUserQuestion → confirm → continue → DONE --choice "xxx"
```

## YAML 示例——SubAgent 自主路由

```yaml
- stage_id: s02-analyze
  name: "分析与路由判定"
  skill_id: path-analyzer

edges:
  - from: s02-analyze
    to: s03-full-design
    condition: success
    choice: "full_design"        # ← SubAgent 的 --choice 匹配此处
  - from: s02-analyze
    to: s04-reverse-engineer
    condition: success
    choice: "code_only"
  - from: s02-analyze
    to: s99-workflow-end
    condition: failure
```

## YAML 示例——需要用户确认后路由

```yaml
# 工作流定义完全相同——都是 SUCCESS + choice
- stage_id: s05-review
  name: "审查确认"
  skill_id: quality-reviewer

edges:
  - from: s05-review
    to: s06-publish
    condition: success
    choice: "accept"              # 用户选"接受"→ SubAgent DONE --choice "accept"
  - from: s05-review
    to: s03-redo
    condition: success
    choice: "reject"              # 用户选"重做"→ SubAgent DONE --choice "reject"
  - from: s05-review
    to: s99-workflow-end
    condition: failure
```

SKILL.md 中对应：
```
1. 完成审查，汇总发现
2. AskUserQuestion：
   - "accept：接受，发布到下一阶段"
   - "reject：重做，回到设计阶段"
3. confirm → continue → 收到用户选择 → DONE --choice "<用户选择>"
```

## 自循环 confirm

如果 SubAgent 需要多轮确认（如：确认后继续完善，再确认），不需要在工作流中定义自循环边。Skill 内部多轮 AskUserQuestion → confirm → continue → 继续干活 → AskUserQuestion → ... 直到 SubAgent 最终 DONE。

若需要循环上限保护，在工作流中定义 `loop_exceeded` 边：

```yaml
edges:
  - from: s05-review
    to: s05-review            # 自循环（可选，仅用于语义标注，实际循环由 Skill 内部完成）
    condition: success
    choice: "继续完善"
  - from: s05-review
    to: s99-workflow-end
    condition: loop_exceeded
    max_loop: 5               # 循环 5 次后强制退出
```

## 注意事项

- 若全部 SUCCESS 边都没有 `choice`，则 `valid_routing_choices` 为空列表——SubAgent 无需传 `--choice`，DONE 后所有 SUCCESS 边视为同时满足（OR 语义激活下游）
- `choice` 值应清晰、互斥、可理解
- 条件路由的汇聚点必须清晰，避免"发散后永不汇聚"的设计
