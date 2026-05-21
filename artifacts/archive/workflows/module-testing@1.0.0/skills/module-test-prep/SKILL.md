---
name: module-test-prep
description: >
  module-testing@1.0.0 工作流 s01-prep 阶段。遗留问题审查→四级设计文档定位→白盒读取实现代码→实现与设计差异比对。
  条件确认点仅当检测到重大差异时触发。由 module-testing 编排器调度使用。
---

# 就绪与契约读取 (s01-prep)

## 定位

module-testing 工作流入口业务阶段。上游 s00-workflow-start；下游 s02-write。
**职责边界**：审查+读取+比对，不编写测试、不修改实现代码。

## 核心原则

- **白盒读实现**：与 adversarial-test-generator（黑盒）互补——本 Skill 读全部实现源码
- **契约权威**：落地规范为比对基准。实现偏离契约 → 轻微差异按实现写测试+记录，重大差异触发确认
- **条件确认**：仅当存在重大差异时 PENDING_CONFIRM，否则自动流转

## 执行流程

### 1. 遗留问题审查（门控）

读取 orchestrator 前置产物（`contract-expectations.md`、`function-signatures.json`）审查遗留问题：

| 检查项 | 常见表现 | 处理 |
|:---|:---|:---|
| 宽泛异常断言 | `pytest.raises(Exception)` | 替换为具体异常类型 |
| 注释与代码不一致 | docstring 描述与断言不匹配 | 修正 docstring 或代码 |
| 元数据头部不准 | 头部数字 ≠ 实际场景数 | 修正头部 |
| 存在性验证替代 | 仅有 `callable(fn)` / `isinstance` | 补充行为路径测试 |

**门控**：遗留问题未修复完毕，不允许进入后续步骤。产物缺失则跳过此步。

### 2. 四级文档读取 (P0→P3)

在 `docs/功能设计/{分组}/{编号}-{名称}/` 下按优先级降级定位：

| 优先级 | 文档 | 提取内容 |
|:---:|------|------|
| P0 | 落地规范 | 类型定义、异常条件、状态机、边界约束 |
| P1 | 设计文档 | 业务意图、模块边界、依赖关系 |
| P2 | 总设计文档 | 项目结构约定、命名规范 |
| P3 | 技术栈/其他 | 测试框架、import 路径 |

P0 缺失→降级下一级。全部缺失→WARNING，按实现代码反推契约。

### 3. 白盒读取实现代码

读取被测模块全部实现源文件，关注：
- 公开函数签名（与落地规范逐项比对）
- 内部分支逻辑（if/else、switch、try/except）
- 状态转换实现、副作用（I/O/数据库/外部调用）
- 依赖注入点（确定需 mock 的外部依赖）

### 4. 实现与设计差异比对

| 比对维度 | 内容 |
|:---|:---|
| 接口签名 | 函数名、参数名/类型/默认值、返回值类型 |
| 类型定义 | model/interface/type alias |
| 异常条件 | 异常类型、触发条件 |
| 状态机 | 状态枚举、合法转移 |

**差异分级**：
- **轻微差异**（字段名不同但语义一致、注释差异等）→ 按实现写测试，prep_result.json 记录标注
- **重大差异**（缺少契约异常处理、核心状态转移缺失、签名完全不同）→ 触发条件确认

### 5. 复用 orchestrator 产物

若存在 `contract-expectations.md` 和 `function-signatures.json`，直接复用并与实现代码交叉验证。不存在则自行从落地规范提取。

### 6. 条件确认

```
存在重大差异 → 输出 prep_result.json + PENDING_CONFIRM
  确认问题示例：
    "落地规范要求 {X}，实现代码为 {Y}。按哪个编写测试？"
    "实现缺少异常处理 {Z}，是否按契约编写测试（预期失败）？"
无重大差异 → 输出 prep_result.json + 自动确认流转 s02-write
```

## 输出

`prep_result.json`：
```json
{
  "module_id": "M01",
  "legacy_issues": [],
  "documents_used": ["P0: 落地规范", "P1: 设计文档"],
  "contract_summary": {"functions": [], "exceptions": [], "states": [], "constraints": []},
  "diffs": [{
    "severity": "minor|major",
    "dimension": "signature|type|exception|state",
    "contract": "...",
    "implementation": "...",
    "resolution": "follow_impl|follow_contract"
  }],
  "external_deps": [],
  "needs_confirmation": false,
  "confirm_questions": []
}
```

## 禁止行为

- 修改实现代码或设计文档
- 编写测试代码
- 仅读接口签名不读实现内部逻辑
- 对未声明约束自行假设
- 无重大差异时触发确认
