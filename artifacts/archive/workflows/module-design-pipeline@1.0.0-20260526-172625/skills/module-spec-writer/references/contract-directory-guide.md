# 契约目录组织指南

本文档定义 `docs/contracts/` 目录的标准结构和文件格式。

---

## 目录结构

```
docs/contracts/
├── _index.json                      # 全局索引：所有契约的注册表
├── M01/
│   ├── _module-index.json           # M01 的契约清单
│   ├── UserInput.json               # M01 定义的契约
│   └── ParsedInput.json
├── M02/
│   ├── _module-index.json
│   ├── WorldBuildInput.json         # M02 定义的契约
│   ├── WorldBuildOutput.json
│   └── BuildStatus.json
└── M03/
    ├── _module-index.json
    └── _ref/                        # M03 复用其他模块的契约引用（可选）
        └── ParsedInput.json -> ../../M01/ParsedInput.json
```

## 文件说明

### `_index.json`（全局索引）

```json
{
  "version": "1.0.0",
  "description": "全局契约索引",
  "last_updated": "2026-05-05T21:00:00+08:00",
  "contracts": {
    "UserInput": {
      "file": "docs/contracts/M01/UserInput.json",
      "defined_by": "M01",
      "maturity": "stable",
      "consumers": ["M02"]
    },
    "WorldBuildOutput": {
      "file": "docs/contracts/M02/WorldBuildOutput.json",
      "defined_by": "M02",
      "maturity": "draft",
      "consumers": []
    }
  }
}
```

### `_module-index.json`（模块级索引）

```json
{
  "module_id": "M02",
  "module_name": "世界观构建引擎",
  "contracts": [
    {
      "title": "WorldBuildInput",
      "file": "WorldBuildInput.json",
      "type": "input",
      "maturity": "draft",
      "defined_by": "M02"
    },
    {
      "title": "WorldBuildOutput",
      "file": "WorldBuildOutput.json",
      "type": "output",
      "maturity": "stable",
      "defined_by": "M02",
      "consumers": ["M03"]
    },
    {
      "title": "ParsedInput",
      "file": "../M01/ParsedInput.json",
      "type": "input",
      "maturity": "stable",
      "defined_by": "M01",
      "reference_only": true
    }
  ]
}
```

> `reference_only: true` 表示本模块复用其他模块的契约，未新建。

### 单个契约文件（JSON Schema）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "docs/contracts/M02/WorldBuildInput.json",
  "title": "WorldBuildInput",
  "description": "世界观构建的输入参数",
  "type": "object",
  "x-defined-by": "M02",
  "x-consumers": [],
  "x-maturity": "draft",
  "x-contract-type": "input",
  "x-version": "1.0.0",
  "properties": {
    "genre": {
      "type": "string",
      "description": "题材类型",
      "minLength": 1,
      "maxLength": 50,
      "examples": ["科幻", "奇幻"]
    },
    "style_tags": {
      "type": "array",
      "items": {"type": "string"},
      "default": [],
      "description": "风格标签列表"
    }
  },
  "required": ["genre"],
  "additionalProperties": false
}
```

## 操作规则

1. **新建契约**：module-spec-writer 的 contract-harmonizer 确认无冲突后写入
2. **复用契约**：不在本模块目录新建文件，在 `_module-index.json` 中记录 `reference_only: true`
3. **变更契约**：修改契约文件后，必须更新 `x-version` 和全局索引中的 `last_updated`
4. **废弃契约**：将 `x-maturity` 改为 `deprecated`，在 `_index.json` 中保留至少一个版本周期
