# Subworkflow Designer Pack — 子工作流设计

> 当某个 Stage 的业务逻辑自身是多步骤流程、需要独立重试、或需要复用时，加载此包来设计子工作流。
>
> 基础概念（子工作流 vs parallel 扇出 vs 多 Stage 串行 vs 条件路由）见 `packs/pattern-matcher/instructions.md` 的模式对比表。

## 判定条件（满足 ≥2 条即应考虑子工作流）

- 子任务有 ≥2 个内部确认点
- 子任务可独立于父流程重试
- 子任务会在 N 个目标上并行执行（结合 `parallel`）
- 子任务本身可能在其他场景被复用

**核心判断标准**：如果 Stage 内部还要分好几步、还要用户确认——那就该用子工作流而不是单个 Skill。

## 设计约束

- 嵌套深度上限 **3 层**（含父工作流，即最多到孙级）
- 父 Stage 状态 = 子实例汇总状态
- 禁止在子工作流中回指父工作流的 Stage
- 禁止使用子工作流替代单个 Skill 能完成的任务（不过度嵌套）

## 子工作流感知义务

- 分析已有工作流：检测 `workflow` 字段 → 读取子工作流 → 纳入分析
- 优化已有工作流：父工作流改了，必须检查子工作流是否有同步优化空间
- 设计新工作流：判定需要子工作流后，同步产出子工作流骨架
- 增量更新：修改父工作流 `workflow` 引用版本时，同步检查子工作流

## 子工作流骨架

判定使用子工作流后，必须同步产出精简 WORKFLOW.yaml：

```yaml
schema_version: "3.0.0"
workflow_id: "<sub-id>"
version: "<semver>"
max_parallel_agents: <N>
stages:
  - stage_id: s00-workflow-start
    name: "工作流启动"
  # ... 核心业务 Stage（含 confirmation_point 标注）
  - stage_id: s99-workflow-end
    name: "工作流终止"
edges:
  # ... 完整的流转边
```

保存到 `$WD/sub-workflows/<sub-id>@<ver>/WORKFLOW.yaml`。

子工作流骨架不进入 Skill 编写阶段，但它是设计决策的产出物——用于质量评审时检查父子衔接。
