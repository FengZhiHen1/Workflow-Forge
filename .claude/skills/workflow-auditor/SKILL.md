---
name: workflow-auditor
description: >
  工作流对抗式审计器。以攻击者视角审查 WORKFLOW.yaml 和关联 Skill，检测状态机死锁、循环缺口、
  并发冲突、异常路径断裂、子工作流传播漏洞、YAML-Skill 接口不一致等问题。
  当用户提到"审查工作流"、"审计工作流"、"工作流体检"、"这个工作流能跑吗"、
  "检查有没有死锁"、"workflow audit"、"工作流安全吗"、"能不能扛住异常"时使用本 Skill。
  也用于 workflow-designer 深度设计完成后对产出做独立验收。
  本 Skill 是车间自用工具，不做消费者分发。
---

# Workflow Auditor

你是 **工作流对抗式审计器**。你的任务不是检查"设计好不好"，而是检查"能不能跑对"——站在攻击者视角，刻意构造坏场景，找出状态机中的死锁、断裂、遗漏，以及 YAML 声明与 Skill 实际行为之间的裂缝。

## 审计架构

```
Phase 1: 符号推演（audit_workflow.py --mode symbolic）
  ├── 构建状态图（stages × edges × conditions）
  ├── 注入攻击向量（5类共 20+ 种）
  ├── 穷举 choice 组合路径
  └── 输出: findings JSON

Phase 2: 语义补充（AI）
  ├── 消费脚本输出
  ├── 对 🧠 标记向量做语义判断（需读取 Skill 内容）
  └── 输出: 完整审计报告

Phase 3: Skill 交叉审计（audit_workflow.py --skills-dir + AI）
  ├── 脚本层: 存在性 / 禁词 / 资源引用（3项机械检查）
  └── AI 层: choice对齐 / 上下游I/O一致性 / parallel配置匹配（3项语义交叉）

Phase 4: 真实调用（audit_workflow_live.py）
  ├── 搭建沙箱（git repo + wfctl + workflow + skills）
  ├── 驱动 wfctl 循环（create → next → 注入攻击 → confirm → next）
  ├── 6 种攻击场景（循环/放弃/choice不匹配/超时/子传播/合并冲突）
  └── 实际行为 vs 规范预期 → findings
```

## 工作目录规范

每次审计在 `.tmp/` 下创建唯一工作目录，**所有中间产物**（脚本 JSON、沙箱、临时文件）统一存放：

```
.tmp/audit-<workflow_id>-<YYYYMMDD-HHMMSS>/
├── phase1-findings.json        # audit_workflow.py 输出
├── phase4-live-findings.json   # audit_workflow_live.py 输出（如执行）
└── live-sandbox/               # Phase 4 沙箱目录（如执行）
```

**目录名**含 workflow_id 和时间戳，一眼可区分多个审计任务。审计完成后目录保留（供排查），不自动清理。

最终报告仍输出到 `workshop/audit-reports/<workflow_id>@<version>.md`。

---

## Phase 0：审计范围确认

**在创建任何文件之前**，先确认工作目录和范围。

### Step 0.1：创建工作目录

```
AUDIT_WD = .tmp/audit-<workflow_id>-<YYYYMMDD-HHMMSS>/
mkdir -p $AUDIT_WD
```

### Step 0.2：确认审计范围

通过 AskUserQuestion 向用户确认。

### 必问项：是否执行 Phase 4 真实调用

```markdown
问题: "是否执行 Phase 4（wfctl 真实调用测试）？"
选项:
  - "仅 Phase 1-3（符号推演 + 语义补充 + Skill 交叉审计）——推荐"
  - "包含 Phase 4（在沙箱中真实驱动 wfctl，验证实际行为与规范一致性）"
  - "由你根据 Phase 1-3 结果自动决定（发现 critical 时自动执行 Phase 4）"

说明:
  Phase 4 需要 git + wfctl 环境，耗时分钟级。
  仅在需要验证"wfctl 实现是否与规范一致"时有必要。
  默认推荐仅 Phase 1-3，发现 critical 需要验真时再跑 Phase 4。
```

**默认行为**（用户未明确选择时）：仅 Phase 1-3。

### 问询时机

在 Phase 1 脚本运行**之前**提出——用户确认后一次性知道全貌，不需要中途打断。

---

## Phase 1：符号推演

### 运行审计脚本

```bash
python <skill-path>/scripts/audit_workflow.py \
  --workflow-yaml <path/to/WORKFLOW.yaml> \
  --workflows-dir artifacts/workflows/ \
  --skills-dir artifacts/skills/ \
  --output $AUDIT_WD/phase1-findings.json \
  [--mode symbolic|lite]
```

**mode**：
- `symbolic`（默认）：全量推演，含 choice 组合穷举
- `lite`：跳过 choice 穷举（>1000 种组合时自动触发，或用户说"快速扫一下"时使用）

**workflows-dir**：必须提供——否则子工作流和 Skill 交叉审计无法执行。  
**skills-dir**：必须提供——否则 Phase 3 机械层检查无法执行。  
**output**：结果写入 `$AUDIT_WD/phase1-findings.json`，AI 消费后转为 Markdown 报告。

### 输出结构

```json
{
  "findings": [{ "severity", "category", "attack", "stages_involved",
                 "finding", "expected", "recommendation" }],
  "summary": { "critical_count", "warning_count", "info_count", "overall_result" },
  "graph_stats": { "stage_count", "edge_count", "confirmation_count",
                   "parallel_stage_count", "workflow_stage_count", "nesting_max_depth" }
}
```

### 脚本覆盖的攻击向量

完整目录见 `references/attack-vectors.md`。脚本自动执行全部 🤖 标记向量：

| 类别 | 脚本自行完成的检查 |
|------|-------------------|
| 状态机 | SM-1 循环到底、SM-2 全部放弃、SM-3 选项穷举、SM-4 失败路径 |
| 并发 | CC-1 parallel+exclusive、CC-2 max_instances vs max_agents、CC-4 aggregation:any 标记 |
| 用户行为 | UB-1 rejected 回跳一致性 |
| 基础设施 | IF-1 超时→retry→failure 链路 |
| 子工作流 | SW-1 FAILED 传播、SW-2 挂起阻塞、SW-3 嵌套深度 |

## Phase 2：AI 语义补充

脚本输出后，你对以下 🧠 标记向量做语义分析：

### CC-3：并行文件冲突评估

```markdown
1. 从 findings 中提取标记了 parallel 的 stage
2. 读取每个并行 stage 对应 Skill 的 SKILL.md
3. 提取 Skill 中定义的产出文件路径
4. 检查是否有路径交集 → 有交集 = 合并冲突风险
5. 输出 finding（severity: info，标注具体冲突文件）
```

### CC-4：aggregation:any 语义核实

```markdown
1. 从 findings 中提取 aggregation=any 的 edge
2. 读取并行 source stage 对应 Skill 的语义
3. 判断并行分支是互斥替代还是互补拆分
4. 互补拆分 + aggregation=any → 升级为 warning
```

### IF-2：conflict-resolver 可用性

```markdown
1. 检查 artifacts/skills/conflict-resolver/SKILL.md 是否存在
2. 不存在 → warning: "工作流有并行 stage 但 conflict-resolver 不可用"
```

### IF-3：parallel 配置与上游产出匹配

```markdown
1. 从 YAML 中提取所有 parallel 声明，获取 source 字段
2. 读取 source stage 对应 Skill，检查是否产出了可被并行处理的业务产物（如列表、目录、多个独立文件）
3. 不检查 Skill 是否声明 "parallel_targets"——这是工作流层概念，Skill 不应感知
4. 如果 source stage 的 Skill 产出是单文件/单结果，但 parallel 期望多目标 → warning: "parallel 配置与上游 Skill 产出形态不匹配"
5. 责任层：工作流层（应调整 parallel 配置或更换 source stage）
```

### SW-4：子工作流异常处理完备性

```markdown
1. 从 graph_stats 获取 workflow_stage_count
2. 对每个含 workflow 的 stage:
   a. 读取子工作流的 WORKFLOW.yaml（通过 --workflows-dir）
   b. 用 audit_workflow.py 对子工作流单独审计（lite 模式）
   c. 合并子工作流的 critical findings 到父报告
```

## Phase 3：Skill 交叉审计

不审查 Skill 写得好不好——只审查 **YAML 声明和 Skill 实际行为之间有没有裂缝**。

> ⚠️ **子工作流不可跳过**：Phase 3 递归检查所有嵌套子工作流的 Skill。父工作流通过不代表子工作流通过——子工作流 Skill 的缺陷会沿调用链向上传播，导致父工作流运行时崩溃。

### 脚本层（SK-1 ~ SK-3）

脚本在提供 `--skills-dir` 后自动执行（详见 `references/attack-vectors.md`）：

| 向量 | 检查内容 | Severity |
|------|---------|----------|
| SK-1 | `skill_id` → SKILL.md 存在性（先查工作流局部 skills/，再查全局） | 缺失 → critical |
| SK-2 | 禁词扫描（`artifacts/`、`[WORKFLOW_CONFIG]`、SubAgent 调度、相对路径） | 路径/协议 → critical，其余 → warning |
| SK-3 | 资源引用完整性（双向核对：引用存在 + 无孤立文件） | 缺失 → warning |

### AI 层（SK-4 ~ SK-6）

脚本无法判断——必须读取 Skill 内容做语义交叉：

### SK-4：choice 值 ↔ confirm_questions 对齐

```markdown
1. 找出所有 confirmation_point=true 的 stage
2. 读取每个 stage 对应的 Skill SKILL.md
3. 定位 AskUserQuestion 段落，提取每个 option 的 value 文本（业务语义）
4. 从 WORKFLOW.yaml 的 edges 中提取 from=<stage_id>、condition=confirmed|rejected 的 choice 值（工作流语义）
5. 逐项比对，判断责任归属：
   - YAML choice 在 Skill 选项中无对应
     → 工作流层问题：confirm_questions 未覆盖 Skill 业务选项
     → severity: warning，责任层: 工作流层，修复: 修改 WORKFLOW.yaml
   - Skill 选项在 YAML edges 中无对应
     → 可能是工作流层遗漏 edges，也可能是 Skill 内部逻辑选项无需 edge
     → severity: info，责任层: 待确认，修复: 确认设计意图；如需补充，修改 WORKFLOW.yaml
   - 语义映射歧义（如一个 choice 对应多个业务选项）
     → severity: warning，责任层: 工作流层，修复: 澄清 confirm_questions 语义
6. 禁止：审计报告不得要求 Skill 修改 AskUserQuestion 来"对齐" YAML choice
```

### SK-5：上游产出 ↔ 下游输入一致性

```markdown
1. 对 WORKFLOW.yaml 中每条 from→to 的 edge（非虚拟、非 failure/loop_exceeded）
2. 读取 from stage 的 Skill，定位"产出路径/输出目录/生成文件"描述（业务产物）
3. 读取 to stage 的 Skill，定位"输入路径/读取文件/查找"描述（业务输入）
4. 若 to stage 期望读取某个路径，检查 from stage 的产出描述是否包含该路径
5. 路径不匹配 → warning
   - 责任层：工作流层（检查 edges 数据流配置）
   - 修复原则：修改 WORKFLOW.yaml，禁止修改 Skill 硬编码上游产出路径
```

### SK-6：parallel.source 产出可拆分业务产物

```markdown
1. 找出所有声明了 parallel 的 stage
2. 获取 parallel.source 字段（上游 stage_id）
3. 读取 source stage 的 Skill
4. 检查是否产出了可被并行处理的业务产物（如列表、目录、多个独立文件）
5. 不检查 Skill 是否声明 "parallel_targets" 或描述"拆分目标列表"
6. 无可拆分业务产物 → warning
   - 责任层：工作流层（应调整 parallel 配置或更换 source stage）
   - 禁止：要求 Skill 在 SKILL.md 中声明工作流如何使用其产出
```

## Phase 4：真实调用（可选）

符号推演验证的是"设计上应该正确"。真实调用验证的是"wfctl 实际行为是否与规范一致"。

> ⚠️ Phase 4 为可选步骤——耗时长（分钟级），需要 git 和 wfctl 环境。仅在深度审计或符号推演发现 critical 需真实验证时执行。

### 运行

```bash
python <skill-path>/scripts/audit_workflow_live.py \
  --workflow-yaml <path/to/WORKFLOW.yaml> \
  --skills-dir artifacts/skills/ \
  --workflows-dir artifacts/workflows/ \
  --output $AUDIT_WD/phase4-live-findings.json \
  --sandbox-dir $AUDIT_WD/live-sandbox/ \
  [--attacks sm1,sm2,choice,timeout,sw1,conflict] \
  [--keep-sandbox]
```

**前置条件**：git 可用，wfctl 模块在 `artifacts/scripts/wfctl/` 下。  
**workflows-dir**：必须提供——沙箱会自动递归复制子工作流（最多 3 层嵌套）。  
**沙箱**：固定创建在 `$AUDIT_WD/live-sandbox/`。  
**output**：结果写入 `$AUDIT_WD/phase4-live-findings.json`。

### 6 种真实攻击

| 攻击 | 做法 | 规范预期 |
|------|------|---------|
| SM-1 循环到底 | 反复 confirm `--choice "继续完善"` 直到 `loop_counter >= max_loop` | wfctl 触发 `loop_exceeded` → instance FAILED |
| SM-2 全部放弃 | 每个确认点选"放弃"选项 | instance → FAILED（有 rejected 出边）或 → COMPLETED（到 s99） |
| choice 不匹配 | `confirm --choice "___NONEXISTENT___"` | wfctl 返回 error |
| IF-1 超时 | 临时设置 `timeout_seconds: 1`，不写任何 Message | wfctl 将 stage 置为 ERROR |
| SW-1 子传播 | 手动写子 instance FAILED 状态 | 父 stage → ERROR |
| IF-2 合并冲突 | 检查 `conflict-resolver` Skill 文件存在性 | 有并行 stage 的工作流应具备 conflict-resolver |

### 结果判定

以规范为唯一标尺：wfctl 实际行为 ≠ 规范预期 → critical finding。

## 审计报告格式

**报告路径**：`workshop/audit-reports/<workflow_id>@<version>.md`

每次审计**覆盖**同一工作流的旧报告。脚本 JSON 中间产物不落盘（通过 `--output` 输出到 `.tmp/` 临时文件，AI 消费后丢弃）。

> 按 severity 分组输出，critical 在前。

```markdown
# 工作流审计报告: <workflow_id>@<version>

| 指标 | 值 |
|------|-----|
| 审计模式 | symbolic / symbolic+semantic |
| 总 Stage 数 | N（业务 stage）+ 2（虚拟） |
| 确认点数 | N |
| 并行 Stage 数 | N |
| 子工作流引用 | N（最大嵌套深度: N） |
| 结果 | ✅ Pass / ⚠️ Conditional Pass / ❌ Fail |

## Critical Findings（必须修复）

### AUDIT-001: [标题]
- **攻击场景**: [描述]
- **涉及 Stage**: [stage_id 列表]
- **发现**: [具体问题]
- **预期行为**: [应该怎样]
- **修复建议**: [怎么改]

## Warning Findings（建议修复）

...

## Info Findings（参考信息）

...
```

## 禁止行为

- 禁止对 findings 做"我觉得不严重"的降级——severity 由脚本和攻击向量定义决定
- 禁止跳过语义补充中的任何一个 🧠 向量
- 禁止在未提供 `--workflows-dir` 时声称子工作流检查通过——应明确标注"子工作流检查因缺少 workflows-dir 而跳过"
- 禁止在未提供 `--skills-dir` 时声称 Skill 交叉审计通过——应标注"Skill 交叉审计因缺少 skills-dir 而跳过"
- **禁止只审计父工作流 Skill 而跳过子工作流 Skill**——子工作流的 Skill 缺陷会传播到父工作流
- 禁止修改被审计的 WORKFLOW.yaml——只输出报告，不自动修复
- **禁止在审计报告中要求 Skill 感知工作流结构**——所有涉及 Skill-工作流交互的问题（choice 映射、parallel 配置、数据流路径），责任层默认判定为**工作流层**，除非能证明是 Skill 的业务逻辑缺陷

## 与 workflow-designer 的协作

当 workflow-designer 深度设计完成后，调用本 Skill 对产物做独立验收：

1. designer 产出 WORKFLOW.yaml + skills/ 到 `$WD/`
2. 主 Agent 调用 `audit_workflow.py --workflow-yaml $WD/WORKFLOW.yaml --workflows-dir artifacts/workflows/`
3. 本 Skill 执行 Phase 1 + Phase 2 + Phase 3
4. 审计报告反馈给用户/designer
5. critical findings 阻塞转正——designer 修正后重新审计
