---
name: contract-harmonizer
description: >
  模块契约协调与冲突检测 Skill。
  先从设计文档提取对外接口类型草案（Pydantic Models、函数签名、状态枚举等），
  再扫描项目中已有契约文件，检查命名冲突、语义冲突及可复用共享类型，输出精确协调报告。
  当编排器调度契约协调、冲突检测、接口类型比对、复用类型扫描、
  contract harmonize、类型冲突检查时使用本 Skill。
  由 project-design-pipeline 工作流 s14 Stage 调度。
---

# 契约协调 Skill

## 前置要求

执行任务前，必须依次读取：
1. `references/agent-protocol.md` — Agent 协议样板（契约读取、消息上报、降级熔断、确认点规范）
2. 编排器注入的 `workflow_refs` 中列出的文件（如有）
3. `references/directory-convention.md` — 全局目录结构约定

## 核心原则

你是契约协调执行器：从设计文档提取对外接口类型草案，扫描已有契约文件，检查本模块对外接口类型是否存在命名冲突、语义冲突或可复用的共享类型，输出协调报告。

- **冲突探测器，不仲裁**：只发现和呈现冲突。所有冲突的最终裁决由下游 Stage 或用户决定。
- **宁可多报，不可漏报**：对于语义等价但名称不同的类型，即使只有 70% 字段重合，也值得在 `reusables` 中标注（附 match_score），供后续判断。
- **精确比对**：禁止模糊表述（"差不多"、"类似"），比对结果必须精确到字段级别。
- **中文输出**：所有报告文本使用中文，代码与专有名词除外。

## 执行逻辑

### Step H0：从设计文档提取契约草案

**目标**：从上游 s13 产出的设计文档中提取本模块的对外接口类型定义，整理为类型清单。

1. 读取上游设计文档：
   - 从 `upstream_files` 获取路径，定位到目标设计文档（按 directory-convention.md 约定，位于 `docs/功能设计/[分组]/[编号]-[名称]/[编号]-[名称]-设计文档.md`）
   - 若 `upstream_files` 为空或未找到匹配文件，则扫描 `docs/功能设计/` 下与注入 context 中模块编号匹配的设计文档
2. 从设计文档中提取对外接口类型定义：
   - Pydantic Model / dataclass / TypedDict 定义（字段名、类型、必填性、约束/校验器）
   - 公开函数的参数类型签名和返回类型签名
   - 状态枚举、错误码枚举（被其他模块消费的）
   - JSON Schema / OpenAPI 片段中声明的对外数据结构
3. 整理为本模块类型清单（内存中保持，作为 H1 的输入）：

```json
[
  {
    "name": "WorldBuildInput",
    "kind": "model",
    "fields": [
      {"name": "genre", "type": "str", "required": true, "constraints": ["min_length=1", "max_length=50"]},
      {"name": "style_tags", "type": "list[str]", "required": false, "default": "[]"}
    ],
    "defined_in": "M02"
  }
]
```

**提取规则——只提取「对外接口」**：
- ✅ 公开函数的参数类型和返回类型
- ✅ 模块暴露给外部的 Pydantic Model / 数据结构
- ✅ 状态枚举、错误码枚举（如果其他模块会消费）
- ❌ 内部辅助类型、私有函数参数、数据库模型

### Step H1：确认与补充类型清单

**目标**：以 H0 产出的类型清单为基准，必要时用落地规范补充验证。

**读取来源**（按优先级）：
1. **H0 步骤产出的本模块类型清单**（主要来源）
2. 本模块落地规范中的 **1.3 输入定义**、**1.4 输出定义**、**1.6 接口契约** 章节——从 `upstream_files` 中获取落地规范路径，用于 fallback 补充和交叉验证

> 与 v1.0.0 的区别：不再依赖上游单独传入的契约草案 JSON 文件。类型清单由本 Skill 在 H0 中自主从设计文档提取。

### Step H2：扫描已有契约文件

**扫描范围**：`docs/contracts/` 下所有 `**/*.json` 文件（排除 `_index.json` 和 `_module-index.json`）。

对每个已有契约文件：
1. 读取 JSON 内容
2. 提取关键信息：`title`、`type`、`x-defined-by`、`x-consumers`、`x-maturity`、`properties`、`enum`
3. 将 `properties` 扁平化为字段清单（名称、类型、是否必填）

### Step H3：执行比对

对 H0/H1 产出的类型清单中的每个类型，与已有契约逐一比对。

**比对维度**：

| 维度 | 判定标准 |
|:---|:---|
| **同名同构** | 名称相同，且所有字段名+类型完全一致 |
| **同名异构**（冲突） | 名称相同，但字段、类型、必填性任一不同 |
| **异名同构**（可复用） | 名称不同，但字段结构和语义高度重合（≥80% 字段同名同类型） |
| **异名异构** | 名称和结构均不同 |

**字段比对规则**：
- JSON Schema type 不同 → 结构差异
- required 状态不同 → 结构差异
- enum 值集合不同 → 结构差异
- bounds（min/max/pattern）不同 → 记录为约束差异（非致命，但需标注）
- 仅在一个类型中出现的字段：若必填 → 结构差异；若非必填且无默认值 → 可能为扩展

### Step H4：输出《契约协调报告》

使用以下精确 JSON 格式输出报告，保存到 `.tmp/<workflow_instance_id>/s14-contract-harmonize-report.json`：

```json
{
  "module_id": "M02",
  "module_name": "世界观构建引擎",
  "scan_summary": {
    "existing_contracts_scanned": 12,
    "modules_with_contracts": ["M01", "M03", "M05"]
  },
  "findings": {
    "conflicts": [
      {
        "type_name": "OrderStatus",
        "our_definition": {
          "defined_in": "M02",
          "kind": "enum",
          "values": ["pending", "paid", "shipped"]
        },
        "existing_contract": {
          "file": "docs/contracts/M03/OrderStatus.json",
          "defined_in": "M03",
          "kind": "enum",
          "values": ["pending", "completed", "cancelled"],
          "maturity": "stable",
          "consumers": ["M05"]
        },
        "diff": "枚举值不一致：本模块有 ['paid', 'shipped']，已有契约为 ['completed', 'cancelled']",
        "severity": "high"
      }
    ],
    "reusables": [
      {
        "type_name": "UserProfile",
        "our_definition": {"fields": ["user_id", "display_name", "avatar_url"]},
        "existing_contract": {
          "file": "docs/contracts/M01/UserProfile.json",
          "title": "UserProfile",
          "fields": ["user_id", "display_name", "avatar_url"],
          "maturity": "stable"
        },
        "match_score": 1.0,
        "recommendation": "直接复用 M01 的 UserProfile，本模块不再定义"
      }
    ],
    "new_types": [
      {
        "type_name": "WorldBuildOutput",
        "reason": "在已有契约中未找到同名或语义等价类型",
        "recommendation": "作为新契约写入 docs/contracts/M02/WorldBuildOutput.json"
      }
    ],
    "consistent": [
      {
        "type_name": "ParsedInput",
        "existing_contract": "docs/contracts/M01/ParsedInput.json",
        "note": "本模块作为消费方使用，与已有定义完全一致"
      }
    ]
  },
  "recommendations": {
    "contracts_to_create": ["WorldBuildInput", "WorldBuildOutput"],
    "contracts_to_reference": ["UserProfile", "ParsedInput"],
    "contracts_needing_resolution": ["OrderStatus"]
  }
}
```

**严重程度判定**：
- `high`：同名异构且已有契约为 `stable`（多个模块已引用）
- `medium`：同名异构但已有契约为 `draft`
- `low`：约束差异（如 min_length 不同）但结构一致

报告 JSON 写入成功后，按 agent-protocol.md 的消息上报规范调用 `write_message.py`，上报 `DONE`。

## 禁止行为

- ❌ **禁止自行裁决冲突**。即使冲突显而易见（如已有 `stable` 契约），也不得建议"以已有为准"或"以本模块为准"——必须将冲突详情写入报告，由下游 Stage 或用户裁决。
- ❌ 禁止修改任何已有契约文件
- ❌ 禁止将内部类型误标为对外类型
- ❌ 禁止在报告中使用模糊表述（"差不多"、"类似"）——比对结果必须是精确的

## 参考文件索引

| 文件 | 归属 | 用途 | 加载时机 |
|------|------|------|----------|
| `references/agent-protocol.md` | 工作流共享 | Agent 协议样板（契约读取、消息上报、降级熔断、确认点规范） | 前置必读 |
| `references/directory-convention.md` | 工作流共享 | 全局目录结构约定（docs/ 产物路径、命名规则） | 前置必读 |
