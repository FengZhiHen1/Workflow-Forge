# Routing Designer Pack — 条件路由设计

> 当 Stage 下游需要多条互斥路径、根据运行时条件路由、或设计 success + choice / confirmed + choice 机制时，加载此包。

## 两种路由机制

| | `success` + `choice` | `confirmed` + `choice` |
|---|---|---|
| **谁决定** | SubAgent 自主判断 | 用户确认选择 |
| **需要 confirmation_point** | 否 | 是 |
| **SubAgent 行为** | 分析 → 选定 choice → `DONE --choice "xxx"` | 分析 → `AWAITING_CONFIRM` → 用户确认 → continue → `DONE` |
| **典型场景** | 检测结果明确，SubAgent 能独立判定路径（如：扫描到代码→走逆向工程；无代码→走全新设计） | 用户做主观决策（如：方案选择、范围确认、是否需要留档） |
| **安全网** | `valid_routing_choices` 校验 choice 合法性，非法则置 ERROR | `valid_choices` 编排器层面兜底 + confirm 拦截 |

## 核心原则

**不要为了获得条件路由而设立确认点。** 如果 SubAgent 自己就能判断该走哪条路，用 `success + choice`，不设 `confirmation_point`。设了确认点反而多一轮无意义交互。

## 决策流程

```
SubAgent 能自主决定走哪条路？
  ├─ 是 → success + choice，不设 confirmation_point
  └─ 否，需要用户判断 → confirmed + choice + confirmation_point: true
```

## YAML 示例——SubAgent 自主路由（无需确认点）

```yaml
- stage_id: s02-analyze
  name: "分析与路由判定"
  skill_id: path-analyzer
  confirmation_point: false               # ← 无需确认

edges:
  - from: s02-analyze
    to: s03-full-design
    condition: success
    choice: "full_design"                  # ← SubAgent 的 --choice 匹配此处
  - from: s02-analyze
    to: s04-reverse-engineer
    condition: success
    choice: "code_only"
  - from: s02-analyze
    to: s99-workflow-end
    condition: failure
```

## 注意事项

- 若全部 SUCCESS 边都没有 `choice`，则 `valid_routing_choices` 为空列表——SubAgent 无需传 `--choice`，DONE 后所有 SUCCESS 边视为同时满足（OR 语义激活下游）
- `choice` 值应清晰、互斥、可理解
- 条件路由的汇聚点必须清晰，避免"发散后永不汇聚"的设计
