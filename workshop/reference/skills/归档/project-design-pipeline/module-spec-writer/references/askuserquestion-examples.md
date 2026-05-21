# AskUserQuestion 使用示例

本文件存放 module-spec-writer 中所有 AskUserQuestion 调用的完整示例，供 Skill 执行时按需参考。

---

## 示例 1：业务矛盾处理提问（Step 3.3）

```
调研背景：模块 M02（世界观构建引擎）对应总设计 3.2 节，意图文档已冻结。
上游为 M01，下游为 M03/M05。已审查契约索引，无冲突。
意图文档中留给规范阶段的技术决策：超时秒数、重试策略、Pydantic 类型定义、状态机技术实现。

AskUserQuestion({
  questions: [
    {
      question: "输入模型采用哪种校验方案？方案对比：Pydantic 提供自动类型校验和序列化，下游 Agent 对字段完整性有强依赖；手动校验更灵活但需自行维护校验逻辑。",
      header: "输入模型",
      options: [
        { label: "Pydantic 严格校验 (Recommended)", description: "BaseModel + Field 约束，自动校验，类型安全，下游可直接信任数据" },
        { label: "字典 + 手动校验", description: "原生 dict + if/else，灵活但容易遗漏边界，需额外编写测试覆盖" }
      ]
    },
    {
      question: "状态机持久化采用哪种方案？意图文档要求支持超时降级和重试。",
      header: "状态持久化",
      options: [
        { label: "Neo4j 节点属性 (Recommended)", description: "利用现有图数据库，减少一致性复杂度，事务原子性保证" },
        { label: "独立 PostgreSQL 状态表", description: "更传统的关系型方案，但需维护额外的数据库连接和表结构" },
        { label: "Redis 内存状态", description: "高性能，但持久化可靠性低于数据库，重启可能丢失状态" }
      ]
    },
    {
      question: "LLM 调用超时阈值设为多少秒？意图文档要求'在合理时间内完成'，需要确定具体秒数。",
      header: "超时阈值",
      options: [
        { label: "30s (Recommended)", description: "平衡响应时间和成功率，适用于大多数 LLM 调用场景" },
        { label: "60s", description: "更宽松，适合复杂世界观生成，但用户等待时间翻倍" },
        { label: "15s", description: "更激进，适合简单场景，但可能频繁触发超时降级" }
      ]
    }
  ]
})
```

---

## 示例 2：核心模块轻量级确认（Step 3.4）

核心模块的轻量级确认为**纯文本输出**，禁止调用 AskUserQuestion。输出摘要后结束当前回合，等待用户下一回合自然回复。

```
SubAgent 已基于项目设计文档完成技术预研，做出 8 项技术决策，
发现 2 处需您确认的业务矛盾（已解决）。

关键决策摘要：
- 技术边界：接收上游 M01 的 ParsedInput，产出 WorldBuildOutput 给下游 M03/M05
- 核心技术选型：crewai>=0.30.0, pydantic>=2.0, neo4j>=5.0
- 状态机方案：IDLE→BUILDING→COMPLETED|FAILED，含 PARTIAL 降级，Neo4j 节点属性持久化
- 主要异常策略：LLM 超时 30s/重试 3 次指数退避，Neo4j 连接失败 3 次固定间隔

如无异议请确认，我将进入文档生成阶段。
```

> **注意**：这是核心模块的纯文本输出，没有 AskUserQuestion 调用。输出后**结束当前回合**，等待用户在下一回合确认。与旧版"书写授权"不同，轻量级确认不使用提问工具。

---

## 示例 3：一般模块轻量级确认（Step 3.4）

一般模块的轻量级确认为**纯文本输出**，禁止调用 AskUserQuestion。输出摘要后直接在同一回合进入 Step 4。

```
SubAgent 已基于项目设计文档完成技术预研，做出 5 项技术决策，无业务矛盾。

关键决策摘要：
- 技术边界：接收 M01 的 ParsedInput，产出 WorldBuildOutput
- 核心技术选型：CrewAI 串行编排 + Neo4j 持久化
- 状态机方案：IDLE→BUILDING→COMPLETED|FAILED，Neo4j 节点属性存储
- 主要异常策略：LLM 超时 30s/重试 3 次，Neo4j 失败 3 次固定间隔

我将直接进入文档生成阶段。
```

> **注意**：这是一般模块的纯文本输出，没有 AskUserQuestion 调用。输出后直接执行 Step 4。

---

## 示例 4：回退机制触发（意图缺陷）

```
在审查意图文档时，发现以下技术不可行项：

**意图缺陷 1**：意图文档中验收标准 AC-03 要求"生成内容与用户输入的一致性率 ≥ 99%"，
但基于当前 LLM 的能力和项目已有的测试基准，该指标在技术上无法稳定达成（实测最高约 92%）。

**意图缺陷 2**：意图文档中业务约束要求"所有操作必须在 1 秒内完成"，
但本模块涉及 LLM 调用（平均响应时间 15-30 秒），该约束在现有架构下不可能实现。

---

以上缺陷属于意图文档层面的问题，spec-writer 无权自行修改。

处理流程：
1. 停止所有工作
2. 向用户报告上述缺陷
3. 给出回退路径

AskUserQuestion({
  questions: [
    {
      question: "发现意图文档存在技术不可行项（详见上方说明）。module-spec-writer 无权自行妥协。请确认是否回退到 module-intent-writer 修正意图文档？\n\n修正建议：\n- 一致性率建议调整为 ≥ 90% 或 ≥ 95%\n- 超时约束建议拆分为'用户可感知的反馈在 1 秒内'和'完整生成在合理时间内'\n\n修正后需重新冻结意图文档，再进入规范阶段。",
      header: "意图缺陷回退",
      options: [
        { label: "确认回退 (Recommended)", description: "停止当前工作，回退到 intent-writer 修正意图文档" },
        { label: "坚持原意图", description: "用户确认原意图可行，继续生成技术规格（需用户承担技术风险）" }
      ]
    }
  ]
})
```

---

## 示例 5：意图一致性确认提问

```
在澄清过程中，发现意图文档中的业务定义存在多种技术解读可能：

意图文档中描述："用户可编辑 AI 生成的内容后再保存"
技术实现歧义：
- 选项 A：用户在界面上直接编辑文本，点击保存后覆盖原结果
- 选项 B：用户编辑后创建新版本，保留原始 AI 生成结果
- 选项 C：用户编辑触发重新生成，以编辑后的内容作为新输入

AskUserQuestion({
  questions: [
    {
      question: "意图文档要求'用户可编辑后保存'，但技术实现上有多种解读。请确认具体行为：",
      header: "编辑行为",
      options: [
        { label: "直接覆盖原结果 (Recommended)", description: "用户在界面上编辑文本，保存后直接覆盖 AI 生成的原结果，简单直接" },
        { label: "创建新版本", description: "保留原始 AI 生成结果，用户编辑后创建新版本，支持历史回溯" },
        { label: "触发重新生成", description: "用户编辑后，以编辑内容作为新输入重新生成世界观" }
      ]
    }
  ]
})
```
