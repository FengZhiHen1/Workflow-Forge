---
name: module-review-identify
description: >
  模块审查工作流入口阶段——解析模块编号、条件确认审查范围（仅模糊范围词时）、
  四级优先级定位设计文档与落地规范、输出 identified_modules.json。
  confirmation_point=conditional，普通情况直接 DONE。
---

# 模块定位 (Module Review Identify)

## 定位

工作流 `module-review@1.0.0` Stage `s01-identify` 执行器。
上游 `s00-workflow-start`，下游 `s02-audit` 依赖 `identified_modules.json`。

## 输入

从编排器注入的 `stage_direction` 或 `special_instructions` 提取用户指定的模块编号。

## 执行步骤

### Step 1：提取模块编号

解析用户输入中的模块标识，支持格式：
`M01` / `M02` / `M1`（字母+数字）、`模块1` / `模块2`（中文）、`Module-A`（英文）。

### Step 2：条件确认

**仅当用户使用模糊范围词时触发 PENDING_CONFIRM**：
"所有模块"、"全部模块"、"所有"、"全部"、"all modules"。

触发时 → 扫描 `docs/功能设计/` 列出所有模块编号 → 上报 `PENDING_CONFIRM` 附清单 → 终止等待用户确认。
**普通情况**（明确编号如 `M01 M03`）→ 跳过确认，直接进入 Step 3。

### Step 3：四级优先级定位文档

每个模块需定位两份文档：`-设计文档.md`（项目上下文）和 `-落地规范.md`（精确编码规格）。
按 P0 → P1 → P2 → P3 降级搜索，优先使用高优先级：

| 优先级 | 来源 | 匹配方式 |
|:---:|------|------|
| **P0** | 独立双文件 | `docs/功能设计/{分组}/{编号}-{名称}/` 下以 `-设计文档.md` / `-落地规范.md` 结尾 |
| **P1** | 旧版单文件 | `docs/` 下 `{编号}-{名称}.md`（仅一份文档，兼含设计与规格） |
| **P2** | 总设计文档 | `总设计.md` / `架构设计.md` / `功能模块全拆解.md` 中对应章节 |
| **P3** | 其他位置 | `docs/` 下 `README.md` / `DESIGN.md` / `ARCHITECTURE.md` 等根目录文档 |

- P0 时两份都必须定位；仅找到一份 → 降级并标记"⚠️ 文档不完整"
- 找不到任何文档 → 标记"⚠️ 缺失"，仍纳入输出

### Step 4：输出 identified_modules.json

保存至 `.tmp/<workflow_instance_id>/identified_modules.json`，每条记录：
```json
{"module_id": "M01", "design_doc": "path|null", "spec_doc": "path|null", "priority": "P0-P3", "status": "已定位|部分缺失|完全缺失"}
```

## 确认点行为

| 场景 | 行为 |
|:---|:---|
| 明确编号 | `DONE`，产物写入 `upstream_files` |
| 模糊范围词 | `PENDING_CONFIRM`，列出模块清单供用户确认 |
| 用户拒绝 | 自循环（最多 2 次），超限 → `s99-workflow-end` |

## 输出

| 产物 | 路径 | 下游 |
|:---|:---|:---|
| `identified_modules.json` | `.tmp/<workflow_instance_id>/` | s02-audit, s03-report |

## 禁止行为

- 禁止在明确编号情况下上报 PENDING_CONFIRM
- 禁止跳过文档定位直接输出
- 禁止编造不存在的文档路径

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-review-identify",
  "version": "1.0.0",
  "workflow_id": "module-review",
  "stage_id": "s01-identify",
  "confirmation_point": true,
  "confirmation_conditional": true,
  "confirmation_condition": "仅当用户使用"所有模块"、"全部模块"等模糊范围词时触发",
  "task_modes": ["core"],
  "autonomous_degradation": false
}
```
