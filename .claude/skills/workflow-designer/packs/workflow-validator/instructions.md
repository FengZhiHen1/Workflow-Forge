# Workflow Validator Pack — L1 YAML 合法性校验

> **此包为强制加载包。** 产出 WORKFLOW.yaml 后，必须运行本包脚本进行 L1 校验。

## 校验范围

L1 校验检查 WORKFLOW.yaml 的**格式合法性**和**结构正确性**：

### 1. YAML 语法校验
- 文件可解析为有效 YAML
- 根节点为对象

### 2. Schema v3.0.0 结构校验
- `schema_version` 必须为 `"3.0.0"`
- `workflow_id`：必填，kebab-case
- `version`：必填，语义化版本
- `max_parallel_agents`：必填，正整数
- `stages`：必填数组，非空
- `edges`：必填数组
- v2 遗留字段检测（`concurrency_rules`、`conflict_resolution`、`git_anchors`）

### 3. Stage 字段校验
- 虚拟 stage（`s00-workflow-start`、`s99-workflow-end`）不应有 skill_id/workflow
- 非虚拟 stage 必须有 `skill_id` 或 `workflow`（互斥）
- `mandatory`、`exclusive` 为布尔值（`confirmation_point` 已废弃）
- `retry` 为非负整数
- `timeout_seconds` 为正整数
- `model` 只能是 `light`/`standard`/`heavy`
- `parallel` 与 `exclusive` 互斥
- v2 `retry_policy` 对象 → 报错

### 4. Edge 校验
- `from`、`to`、`condition` 必填
- `condition` 只能是：`always`、`success`、`failure`、`loop_exceeded`（`confirmed`/`rejected` 已废弃）
- `from`/`to` 引用的 stage_id 必须存在
- `max_loop` 只能是正整数，且 condition 必须为 `failure`/`loop_exceeded`
- `aggregation` 只能是 `all`/`any`，且仅用于 parallel 场景
- 同一 `(from, to, condition, choice)` 组合不能重复
- 冗余 edge 检测（`always` + `success`/`failure` 指向同一目标）

### 5. 条件路由与 Edge 匹配
- 条件路由须有 `success` + `choice` 出边
- 需循环保护的 stage 应有 `loop_exceeded` 出口
- 同一 from stage 的 `success` edge 的 `choice` 值不能重复
- 残留的 `confirmed`/`rejected` 边条件将被拒绝

### 6. 图结构校验
- 存在起始 stage（无入边）
- 无孤立节点（非虚拟 stage 必须有 edge 引用）
- 所有非虚拟 stage 可达
- 所有非虚拟 stage 可到达 `s99-workflow-end`

### 7. 循环出口完整性
- 所有带 `max_loop` 的 edge 必须有对应的 `loop_exceeded` 出口

### 8. 虚拟 Stage 约束
- `s00-workflow-start` 只能有 `always` 出边，不能有入边
- `s99-workflow-end` 不能有出边

### 9. 子工作流校验
- 含 `workflow` 的 stage 必须有 `failure` edge
- 子工作流嵌套深度 ≤ 3 层
- 子工作流引用必须存在（需 `--workflows-dir`）

### 10. retry 降级路径
- `retry>0` 的非确认点 stage 必须有 `failure` 或 `loop_exceeded` 出口

### 11. 回边可回复性
- SUCCESS 回边（拓扑序 to < from）目标必须能重新到达源 stage

### 12. parallel 约束
- `parallel.source` 引用的 stage 必须存在
- `parallel.max_instances` ≤ `max_parallel_agents`

## 执行方式

```bash
python .claude/skills/workflow-designer/packs/workflow-validator/scripts/validate.py \
  --workflow-yaml $WD/WORKFLOW.yaml \
  --skills-dir $WD/skills/ \
  --workflows-dir artifacts/workflows/ \
  --mode standard
```

返回 JSON：`{"valid": true}` 或 `{"valid": false, "errors": [...]}`

**校验不通过时**：修复 WORKFLOW.yaml 中的错误，重新校验，直到通过。
