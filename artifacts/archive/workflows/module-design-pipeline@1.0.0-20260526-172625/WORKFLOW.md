---
name: "模块设计流水线"
description: "单模块设计流水线 v1.0.0：存量检测 -> 增量路径判定 -> 意图编写/逆向工程/差异对比 -> 规格编写 -> 契约协调 -> 同步上报"
tags: [module-design, intent, spec, contract, incrementality, reverse-engineering]
---

# module-design-pipeline@1.0.0

> 存量检测 -> 增量路径判定 -> 意图编写/逆向工程/差异对比 -> 规格编写 -> 契约协调 -> 同步上报

---

## 工作流概览

- **工作流 ID**：`module-design-pipeline`
- **版本**：`1.0.0`
- **Stage 数量**：13（含 2 个虚拟 stage）
- **确认点数量**：6（由 edges 中 `success` + `choice` 模式隐式定义）
- **最大并发**：1（模块内阶段串行执行）
- **父工作流**：`project-design-pipeline@3.1.0`（s08 调度）

### 适用场景

本工作流作为 `project-design-pipeline@3.1.0` 的子工作流运行，每个实例对应一个模块的设计。支持 6 种增量场景：

1. **full_design** -- 模块从零开始，完整走意图编写 + 规格编写
2. **design_docs_only_intent_frozen** -- 意图已冻结，只需补充规格文档
3. **design_docs_only_all_complete** -- 设计文档已齐全，仅上报同步状态
4. **design_docs_only_intent_draft** -- 意图草稿存在但未冻结，续写意图
5. **code_only** -- 无设计文档有代码，从代码逆向推导意图
6. **both_exist** -- 设计文档和代码均存在，diff 对比增量更新

### v1.0.0 规范同步更新（2026-05-25）

| 维度 | 变更前 | 变更后 |
|------|--------|--------|
| edges `condition` 值 | 使用已废弃的 `confirmed` / `rejected` | 统一使用 `success` + `choice` |
| stage `confirmation_point` 字段 | 每个 stage 显式声明 | 移除（确认现在是 Skill 内部行为） |

---

## 流程图

```mermaid
flowchart TD
    s00["s00-workflow-start<br/>工作流启动"]
    s99["s99-workflow-end<br/>工作流终止"]

    s01["s01-detect-existing-artifacts<br/>存量检测与增量路径判定"]
    s02re["s02-reverse-engineer-intent<br/>逆向推导意图与规格"]
    s02diff["s02-diff-and-update<br/>差异对比与增量更新"]
    s03["s03-intent-clarify-authorize<br/>意图澄清与书写授权"]
    s04["s04-intent-generate-freeze<br/>生成意图文档与冻结授权"]
    s05["s05-spec-prepare<br/>材料准备"]
    s06["s06-spec-research<br/>技术决策预研"]
    s07["s07-spec-design-doc<br/>生成设计文档"]
    s08["s08-contract-harmonize<br/>契约协调"]
    s09["s09-spec-final<br/>最终规格输出"]
    s10["s10-module-sync-report<br/>模块级同步矛盾上报"]

    s00 --> s01

    s01 -->|"success full_design"| s03
    s01 -->|"success design_docs_only_intent_draft"| s03
    s01 -->|"success design_docs_only_intent_frozen"| s05
    s01 -->|"success design_docs_only_all_complete"| s10
    s01 -->|"success code_only"| s02re
    s01 -->|"success both_exist"| s02diff
    s01 -.->|"failure"| s10

    s03 -->|"success 授权"| s04
    s03 -->|"success 继续澄清"| s03
    s03 -->|"success 放弃模块"| s10
    s03 -->|"loop_exceeded"| s10

    s04 -->|"success 确认冻结 [全流程]"| s05
    s04 -->|"success 冻结并进入契约协调 [code_only路径]"| s08
    s04 -->|"success 重新澄清"| s03
    s04 -->|"success 放弃模块"| s10
    s04 -->|"loop_exceeded"| s10

    s05 --> s06
    s05 -.->|"failure"| s10

    s06 -->|"success"| s07
    s06 -->|"failure"| s10

    s07 -->|"success 通过"| s08
    s07 -->|"success 继续完善"| s07
    s07 -->|"success 放弃模块"| s10
    s07 -->|"loop_exceeded"| s10

    s08 -->|"success"| s09
    s08 -->|"failure"| s10

    s09 -->|"success 输出规格"| s10
    s09 -->|"success 继续完善"| s09
    s09 -->|"success 放弃模块"| s10
    s09 -->|"loop_exceeded"| s10

    s02re -->|"success 确认逆推结果"| s04
    s02re -->|"success 继续完善"| s02re
    s02re -->|"success 放弃模块"| s10
    s02re -->|"loop_exceeded"| s10

    s02diff -->|"success 确认更新"| s09
    s02diff -->|"success 继续完善"| s02diff
    s02diff -->|"success 放弃模块"| s10
    s02diff -->|"loop_exceeded"| s10

    s10 --> s99
```
