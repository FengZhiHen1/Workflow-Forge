# Quality Reviewer Pack — L3 质量评审标准

> **此包为强制加载包。** 在产出 WORKFLOW.yaml 和 SKILL.md 后，必须按照以下标准进行自我评审。

## 定位

你是**独立评审者**。你不知道用户的偏好、业务背景、项目约束。你只评审"设计本身的质量"。

## 工作流设计评审维度

### 1. 反模式检测

| 反模式 | 检查方法 | 严重级别 |
|--------|---------|---------|
| 确认点过密 | confirmation_count / 业务Stage数 > 0.5 | warning |
| 死 Stage | 有入边无出边，或无入边无出边（非虚拟） | critical |
| 循环无出口 | 带 max_loop 的 edge 没有对应的 loop_exceeded | critical |
| 路由与 edge 不匹配 | SUCCESS 边缺少 choice 定义 | critical |
| 残留旧边条件 | Stage 使用了已废弃的 confirmed/rejected 边 | critical |
| 分支无汇聚 | 从同一 Stage 分出的多条路径没有汇聚到同一 Stage | warning |
| 过度嵌套循环 | Stage A 循环 → Stage B 循环 → Stage C 循环 | warning |
| 并行与 exclusive 冲突 | parallel 和 exclusive 同时存在 | critical |
| 上游产出未被消费 | 某 Skill 的 consumers 为空（终节点除外）| suggestion |
| 子工作流嵌套过深 | 嵌套深度 > 3 层 | critical |
| 子工作流确认冗余 | 父 Stage 与子工作流终局 Stage 均有用户交互 | warning |
| 子工作流骨架缺失 | Stage 有 `workflow` 字段但无对应骨架 | warning |

### 2. 确认点合理性

- **密度评估**：sparse / balanced / dense / overkill
- **时机评估**：
  - 初稿/分析 Stage 不应设确认点
  - 生成/实现 Stage 后应有确认点
  - 纯工具型 Stage 不应设确认点
- **选项设计**：choice 值是否清晰、互斥；是否有兜底 edge

### 3. 并发效率

- `max_parallel_agents` 利用率
- 瓶颈识别：最长串行路径
- 聚合策略：`any` 只适用于互斥替代方案

### 4. 数据流完整性

- 每个非终节点 Skill 至少有一个 consumer
- 依赖关系无环（DAG）
- 并行 Stage 的输入是否来自同一上游

### 5. 鲁棒性

- 所有 Stage 都有 failure 处理路径
- 所有 SUCCESS + choice 路径都有对应的 loop_exceeded
- 虚拟 Stage 正确配置
- 子工作流骨架存在且自身通过反模式检测

## Skill 质量评审维度

### 1. 边界合规（绝对红线）

| 检查项 | 严重级别 |
|--------|---------|
| SKILL.md 中出现 Stage ID、workflow_id、edges | critical |
| 出现 `artifacts/` 或 `workshop/` 路径 | critical |
| 包含内部 SubAgent 调度 | critical |
| 包含 `[WORKFLOW_CONFIG]` 代码块 | critical |
| 描述下游触发行为 | critical |

### 2. 指令清晰度

- 第一段是否立即说清楚"你是谁、你做什么"
- 每个步骤是否明确"输入 → 做什么 → 产出"
- SubAgent 能否自行判断"这一步完成了"
- 关键术语是否在首次出现时定义
- 是否有"适当"、"合理"等模糊词

### 3. 场景覆盖

- description 是否覆盖 3+ 种触发场景
- 是否覆盖所有业务场景
- 多场景 Skill 的每个分支是否有清晰进入条件

### 4. 降级可行性

- 输入缺失时的降级策略是否具体可执行
- 降级后的输出是否仍能满足下游最小需求
- 是否有不可恢复的错误处理

### 5. 资源完备性

- 所有引用的 references/scripts/assets 是否存在
- 所有存在的资源文件是否被引用
- 迁移清单中 ✅ 文件是否全部复制

### 6. 简洁性

- SKILL.md body < 500 行
- 无大段 docstring 或冗余注释
- 无显然不会被读到的冗余内容

## 评审报告格式

发现问题时，以以下格式记录：

```yaml
issues:
  - issue_id: R01
    severity: critical|warning|suggestion
    category: anti_pattern|confirmation|concurrency|data_flow|robustness|clarity|boundary
    stage_id: "s01-xxx"  # 如涉及
    title: "问题简述"
    description: "详细说明"
    evidence: "具体引用"
    recommendation: "建议如何修正"
```

**critical 问题必须修正后才能交付。warning 问题建议修正但不阻塞。suggestion 供参考。**
