# Design Evaluator Pack — L2 设计规则检查

> **此包为强制加载包。** 产出 WORKFLOW.yaml 后，必须运行本包脚本进行 L2 设计规则检查。

## 定位

检查**客观规则违反**，不是"质量评估"。通过了不代表设计好，没通过一定有问题。

## 校验范围

### 1. 确认点密度

- **sparse（<10%）**：可能缺乏用户控制
- **balanced（10%-30%）**：合理范围
- **dense（>30%）**：过于繁琐
- **overkill（>50%）**：严重问题

### 2. 死 Stage 检测

检查是否有死 Stage：
- 孤立节点（无任何 edge 连接）
- 无入边（不可达）
- 无出边（死胡同）

虚拟 Stage（s00/s99）除外。

### 3. 循环出口完整性

所有带 `max_loop` 的 edge 必须有对应的 `loop_exceeded` 出口。

### 4. 数据流完整性

每个业务 Stage 至少有一条入边和一条出边（虚拟 Stage 除外）。

### 5. 并发效率

- `parallel` 与 `exclusive` 不能同时存在
- 检查 parallel 声明是否合理

### 6. 反模式检测

- 非确认点有 `confirmed`/`rejected` 出边
- 确认点无 `confirmed`/`rejected` 出边
- 确认点密度异常

## 执行方式

```bash
python .claude/skills/workflow-designer/packs/design-evaluator/scripts/evaluate.py \
  --workflow-yaml $WD/WORKFLOW.yaml \
  --mode standard
```

模式选择：
- `fast`：3 项检查（确认点密度、死 Stage、循环出口）
- `standard`：4 项检查（+ 数据流完整性）
- `deep`：6 项检查（+ 并发效率、反模式检测）

返回 JSON：`{"pass": true, "checks": [...]}` 或 `{"pass": false, "checks": [...], "issues": [...]}`

**检查不通过时**：修复 WORKFLOW.yaml 中的设计问题，重新检查，直到通过。
