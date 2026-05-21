---
name: adversarial-module-implementation-init
description: >
  模块生命周期工作流（adversarial-module-implementation@1.0.0）入口阶段——环境就绪与契约冻结。
  合并原 preflight + contract 职责：环境检查（Python 版本、脚本完整性、设计文档定位）→
  契约提取（P0 落地规范 → P1 设计文档 → P2 项目结构）→ 设计仲裁 →
  冻结 contract-expectations.md → 执行计划预览 → 用户确认。
  当编排器调度模块实现流水线入口阶段时，必须优先使用本 Skill。
  确认点触发 AQ-001（契约完整性）、AQ-002（执行计划确认）、AQ-004（条件：契约模糊/矛盾）。
---

# 模块生命周期初始化 (Init)

## 定位

你是工作流 `adversarial-module-implementation@1.0.0` 中 Stage `s01-init` 的执行器。
上游 `s00-workflow-start` 无业务输入；下游 `s02-impl` 依赖本阶段冻结的 `contract-expectations.md` 与用户确认。

**职责边界**：只负责检查和提取，不实现代码，不生成测试。

## 输入

从编排器注入的上下文中获取：

| 字段 | 说明 | 必填 |
|------|------|:--:|
| `module_id` | 目标模块编号（如 M01） | 是 |
| `module_code_dir` | 模块代码目录路径 | 是 |
| `workflow_ref_dir` | 工作流共享资源目录 | 否 |

`module_id` 缺失 → 立即终止，上报 `ERROR`。

## 执行步骤

### Step 1：环境就绪检查 (Preflight)

**1.1 Python 版本检查**：确认当前运行环境 Python >= 3.8。不满足 → `ERROR` 阻断。

**1.2 脚本完整性验证**：
```bash
python {workflow_ref_dir}/scripts/preflight_check.py --module-id {module_id}
```
退出码 0（通过）→ 继续；退出码 1（警告）→ 继续但记录；退出码 2（阻断）→ `ERROR`。

**1.3 设计文档定位**：搜索 `docs/功能设计/` 下匹配 `{module_id}` 的设计文档，四级优先级搜索：

| 优先级 | 文件模式 | 说明 |
|:---:|------|------|
| P0 | `{group}/{module_id}-*/{module_id}-*-落地规范.md` | 独立落地规范（首选） |
| P0 | `{group}/{module_id}-*/{module_id}-*-设计文档.md` | 独立设计文档 |
| P1 | `{group}/{module_id}-*/{module_id}-*-功能设计文档.md` | 旧版单文件 |
| P2 | `{module_id}-总设计文档.md` | 总设计文档 |
| P3 | 其他路径 | 兜底搜索 |

至少定位到一份 P0 或 P1 文档（落地规范或设计文档），否则 → `ERROR`。

### Step 2：契约提取

按优先级读取设计产物（详细解析算法见 `references/contract-extractor.md`）：

| 优先级 | 来源 | 提取内容 |
|:---:|------|------|
| P0 | 落地规范「输入/输出类型定义」 | 参数类型、必填/可选、bounds、默认值、枚举 |
| P0 | 落地规范「异常处理」 | 异常类型、触发条件 |
| P1 | 落地规范「状态机」 | 状态转换约束、前置条件 |
| P1 | 设计文档「接口契约」 | 业务层面输入约束、边界定义 |
| P2 | 项目结构文档 | 命名规范、模块边界 |

P0 文件全部缺失 → `ERROR`。P1/P2 缺失 → 记录警告，继续使用可用文件。

核心流程（5 步，详见 `references/contract-extractor.md`）：
1. 解析「输入/输出类型定义」章节 → 每个类型的字段列表
2. 将类型定义映射为函数参数契约
3. 解析「异常处理」章节 → 异常契约列表
4. 解析「状态机」章节 → 状态约束 + 前置条件
5. 组装契约条目（**A 系列**参数约束 + **B 系列**状态约束），生成破坏性输入矩阵

### Step 3：设计文档冲突仲裁

当多份文档对同一接口的描述不一致时：

1. **仲裁规则**：P0（落地规范）> P1（设计文档）> P2（项目结构文档）
2. **冲突记录**：将每个冲突写入 `conflict_log`：
   ```
   | 冲突维度 | P0/P1/P2 各自声明 | 裁决结果 | 备注 |
   ```
3. **未覆盖场景**：字段未定义、边界未声明、异常触发条件缺失 → 标记为「约束未声明，采用最宽松假设」。**禁止自行假设**。

### Step 4：生成并冻结 contract-expectations.md

**产物路径**：`{module_code_dir}/.tmp/adversarial-tests/{module_id}/contract-expectations.md`

**格式要求**：见 `references/contract-extractor.md` 的「输出格式」章节。

**冻结前验证**：
```bash
python {workflow_ref_dir}/scripts/validate_contract_expectations.py \
    {contract_path} \
    --function-signatures {function_signatures_path}
```

验证内容包括：结构完整性、编号格式、覆盖完整性（每个公开函数至少一条条目）、破坏性输入明确性。

验证失败 → 修正后重试，最多 3 次。连续 3 次失败 → `ERROR`。
验证通过 → 更新冻结时间为当前时间戳，标记文件为冻结状态。**冻结后不得擅自修改**。

### Step 5：构建执行计划预览

向用户展示以下四部分信息，供确认：

1. **模块信息**：`module_id`、模块名称、模块描述
2. **契约统计**：A 系列条目数（参数约束）、B 系列条目数（状态约束）、公开函数数量、未覆盖字段数量
3. **SubAgent 调度计划**：
   - 实现阶段（s02-impl）：1 个 SubAgent，按设计文档优雅实现
   - 对抗测试阶段（s04-testgen）：1 个 SubAgent，黑盒生成对抗测试
   - 预计对抗循环 2-3 轮（含 s06-fix / s07-testfix 修复迭代）
4. **风险提示**：约束未声明条目列表、设计文档冲突摘要

### Step 6：确认点上报

`confirmation_point=true`，完成任务后上报 `PENDING_CONFIRM`，**不得直接上报 `DONE`**。

确认问题基于实际产出内容设计，必须包含：

| 编号 | 问题 | 触发条件 |
|:---|------|------|
| AQ-001 | 「契约提取是否完整？以上执行计划是否可以启动？」 | 始终触发 |
| AQ-002 | 「执行计划预览是否合理？SubAgent 调度安排是否可行？」 | 始终触发 |
| AQ-004 | 「以下未覆盖/矛盾场景需要确认：{具体场景列表}。是否按最宽松假设处理，或需要补充约束？」 | 条件触发：存在未覆盖字段、文档冲突或约束缺失时 |

若存在多项待确认事项，一次性全部列入 `confirm_questions`（最多 4 条），不分多次终止。

**确认后行为**：
- 用户确认 → 编排器标记 DONE，`contract-expectations.md` 作为冻结产物传递下游 `s02-impl`
- 用户拒绝/要求修改 → 编排器恢复本 Stage，根据反馈修改后重新上报（自循环最多 2 次）
- 超过 2 次 → 走 `loop_exceeded` → `s99-workflow-end`

## 输出

| 产物 | 路径 | 下游使用者 |
|------|------|----------|
| `contract-expectations.md` | `{module_code_dir}/.tmp/adversarial-tests/{module_id}/` | s02-impl, s04-testgen, s05-blindtest, s08-report |
| 执行计划预览 | inline 在 confirm_questions 中 | 用户审查 |

## 错误处理

| 场景 | 处理 |
|------|------|
| `module_id` 缺失 | `ERROR` 阻断 |
| Python < 3.8 | `ERROR` 阻断 |
| preflight_check.py 退出码 2 | `ERROR` 阻断 |
| 设计文档全部未定位到 | `ERROR` 阻断 |
| P0 落地规范缺失 | `ERROR` 阻断 |
| P1/P2 文件部分缺失 | 记录警告，继续 |
| 验证连续 3 次失败 | `ERROR` 阻断 |
| 契约冲突 | 按 P0>P1>P2 仲裁，记录 conflict_log |
| 未覆盖场景 | 标记「约束未声明」，条件触发 AQ-004 |

## 禁止行为

- 禁止自行假设未声明的约束（必须标记或上报确认）
- 禁止修改任何上游设计文档（落地规范、设计文档、契约文件）
- 禁止在 PENDING_CONFIRM 阶段上报 DONE
- 禁止查阅或读取实现代码
- 禁止编造不存在的约束或异常条件
- 禁止跳过验证直接冻结
- 禁止在未获取用户确认前释放下游

## 资源引用

| 资源 | 类型 | 路径 | 角色 |
|------|------|------|------|
| contract-extractor.md | references | `references/contract-extractor.md` | 详细解析算法（建立者） |
| subagent-prompts.md | references | `references/subagent-prompts.md` | 下游 SubAgent prompt 模板（建立者） |
| preflight_check.py | scripts | `{workflow_ref_dir}/scripts/preflight_check.py` | 使用者 |
| validate_contract_expectations.py | scripts | `{workflow_ref_dir}/scripts/validate_contract_expectations.py` | 使用者 |

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "adversarial-module-implementation-init",
  "version": "1.0.0",
  "workflow_id": "adversarial-module-implementation",
  "stage_id": "s01-init",
  "confirmation_point": true,
  "task_modes": ["core"],
  "autonomous_degradation": false
}
```
