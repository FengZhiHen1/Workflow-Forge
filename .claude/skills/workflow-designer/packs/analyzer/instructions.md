# Analyzer Pack — 输入分析能力

> 当需要分析旧 Skill、已有工作流或需求描述，以提取结构化信息用于工作流设计时，加载此包。

## 执行前必读规范

分析前必须自行读取以下规范文件：

| 规范文件 | 用途 |
|---------|------|
| `workshop/specs/细节设计/WORKFLOW.yaml字段规范.md` | 理解 stage 字段、edge condition 枚举、choice/aggregation 等——确保 `proposed_stages` 和 `proposed_edges` 与规范兼容 |
| `workshop/specs/细节设计/Instance状态机规范.md` | 理解状态流转——确保 `proposed_edges` 的 condition 映射正确 |
| `workshop/specs/细节设计/Skill定义规范.md` | 理解 Skill 边界、AskUserQuestion 替换——正确判断哪些内容应留在 Skill 内、哪些应外提为 Stage |
| `workshop/specs/工作流思想.md` | 设计哲学——理解"最大化并发"、"确认点为唯一阻塞点"等原则 |

## 输入

1. 输入材料（下列之一）：
   - **旧 Skill 模式**：一个或多个旧 SKILL.md 的完整内容
   - **已有工作流模式**：WORKFLOW.yaml + WORKFLOW.md + 关联 skills/
   - **从零开始模式**：用户的一句话需求描述
2. 用户在设计初期提供的决策摘要（改造方向、合并策略、红线约束等）

## 输出

输出 YAML 格式的分析报告，保存到 `$WD/analysis-report.yaml`：

```yaml
analysis_version: "3.0.0"
mode: single|multi|workflow_upgrade|from_scratch
analysis_depth: standard|deep

source_skills:
  - path: 输入路径
    name: Skill/工作流名称
    line_count: 0

logical_steps:
  - step_id: s1
    skill_index: 0
    name: 步骤名称
    line_range: [1, 50]
    description: 步骤描述
    type: analysis|generation|validation|communication|script_call|subagent_call

askuserquestion_points:
  - point_id: aq1
    skill_index: 0
    location: "位置描述"
    line_range: [100, 120]
    purpose: 此 AskUserQuestion 的目的
    question_count: 1
    options_structure: 单选/多选/二元确认
    is_gate: true
    gate_type: 书写授权|冻结授权|冲突裁决|方向确认|其他
    suggested_stage_id: 建议的 stage_id
    suggested_mapping_reason: 为什么这样映射

subagent_calls:
  - call_id: sub1
    skill_index: 0
    location: "位置描述"
    line_range: [150, 180]
    subagent_role: SubAgent 的角色
    purpose: 调用目的
    inputs: []
    outputs: []
    suggested_stage_id: 建议的 stage_id
    suggested_skill_id: 建议的新 skill_id
    extraction_reason: 为什么必须外提

script_calls:
  - call_id: sc1
    skill_index: 0
    location: 位置描述
    script_name: 脚本名
    purpose: 用途
    retain_in_skill: true
    reason: 保留或外提的理由

bundled_resources:
  references:
    - file: xxx.md
      purpose: 文件用途
      should_migrate: true
      reason: 迁移或不迁移的理由
  scripts:
    - file: xxx.py
      purpose: 文件用途
      should_migrate: true
      reason: 迁移或不迁移的理由
  assets:
    - file: template.html
      purpose: 文件用途
      should_migrate: false
      reason: 迁移或不迁移的理由

io_mapping:
  inputs: []
  outputs: []
  intermediate_files: []

skill_relationships:
  - from_skill_index: 0
    to_skill_index: 1
    relation_type: data_dependency|call_dependency|temporal_dependency|manual_handoff
    description: 关系描述
    output_files: []
    input_files: []
    suggested_edge:
      condition: always|success
      reason: 为什么这样映射

proposed_stages:
  - stage_id: s01-xxx
    name: Stage 名称
    derived_from_skill_index: 0
    derived_from: 源自哪段步骤
    skill_id: 建议的 skill_id
    mandatory: true
    reason: 为什么这样拆分

proposed_edges:
  - from: s01-xxx
    to: s02-yyy
    condition: always|success|failure|loop_exceeded
    reason: 为什么这样连接
    is_cross_skill: false

sub_workflows:
  - parent_stage_id: s03
    workflow_ref: module-design-pipeline@1.1.0
    path: artifacts/workflows/module-design-pipeline@1.1.0/
    stage_count: 0
    confirmation_count: 0
    issues: []
    optimization_notes: ""

risk_notes: []

# workflow_upgrade 模式专用字段
source_workflow:
  path: ""
  workflow_id: ""
  version: ""
  stage_count: 0
  skill_count: 0
```

> 当 `mode` 为 `workflow_upgrade` 时，`source_workflow` 的信息必须同时映射到 `source_skills`（将工作流视为整体输入源），`proposed_stages` 和 `proposed_edges` 必须在根级别提供。

## 分析规则

### 1. 逻辑步骤识别

- **旧 Skill 模式**：按 SKILL.md 的章节结构拆解步骤，`skill_index` 标识归属
- **已有工作流模式**：按 WORKFLOW.yaml 的 stages 拆解，标注每个 stage 的当前状态
- **从零开始模式**：从用户描述中提取隐含的逻辑步骤，推断所需能力
- 每个步骤标记类型：analysis / generation / validation / communication / script_call / subagent_call

### 2. AskUserQuestion 点识别

- 找到所有 AskUserQuestion 调用（显式和隐式）
- 判断是否是"门控"（gate）——后续步骤依赖用户确认
- 门控类型：书写授权、冻结授权、冲突裁决、方向确认、其他
- 为每个点建议 stage_id 和映射理由

### 3. SubAgent 调用识别（旧 Skill 模式）

- 找到所有内部 SubAgent 调用
- 每个必须建议提升为独立 Stage
- 说明输入输出，以便设计 edges 时引用

### 4. 多 Skill 关系识别（多 Skill 模式核心）

- **数据依赖**：Skill A 的输出是否被 Skill B 读取
- **调用依赖**：Skill A 是否明确提到调用 Skill B
- **时序依赖**：Skill B 是否必须在 Skill A 完成后开始
- **用户手动衔接**：旧体系中用户手动在 A 后调用 B，映射为 `always` 还是 `confirmed` edge

**deep 模式额外要求**：
- 对每个依赖关系标注：强依赖（必须）/ 弱依赖（可选）
- 识别潜在的并行机会：哪些 Skill 无依赖可并行
- 标注循环依赖风险（如有）

### 5. Stage 拆分建议

- 遵循"每个确认点一个 Stage"原则
- 连续的非确认业务步骤可以合并
- 多 Skill 模式下 Stage ID 加前缀区分来源
- 为每个 Stage 提供充分的拆分理由
- **proposed_stages 中每个字段必须与 `WORKFLOW.yaml字段规范.md` §二的定义一致**（stage_id 格式、字段名、互斥约束）
- **proposed_edges 中 condition 值必须来自 `WORKFLOW.yaml字段规范.md` §三的枚举**

### 6. 风险标注

- 逻辑过于耦合无法干净拆分 → 标注
- AskUserQuestion 不适合提升为 Stage → 标注替代方案
- Skill 间关系不明确 → 标注"需用户确认"

### 7. 捆绑资源清点

**必须列出旧 Skill 目录下所有捆绑资源文件**，而非仅分析 SKILL.md 正文中提到的。检查以下目录（如存在）：

- `references/` —— 参考文档、设计原则、模板等
- `scripts/` —— 辅助脚本（包括 SKILL.md 正文未显式调用但在目录中的）
- `assets/` —— 模板、图标、字体等

对每个文件评估：
- **用途**：这个文件在新 Skill 的业务逻辑中还有用吗？
- **迁移决策**：`should_migrate: true` 表示必须迁移；`false` 表示可以丢弃
- **理由**：说明为什么迁移或不迁移——后续阶段依赖这个判断来执行实际操作

> ⚠️ 这是防止 references/scripts 在改造过程中丢失的关键步骤。如果旧 Skill 有捆绑资源但没有列出，后续所有阶段都不会知道它们的存在。

### 8. 子工作流检测

当输入为已有工作流（`mode: workflow_upgrade`）或旧 Skill 中包含子流程调度时，**必须检测子工作流**。

**检测步骤**：

1. **扫描 WORKFLOW.yaml**：遍历 `stages[]`，找出所有含 `workflow` 字段的 Stage
2. **读取子工作流**：对每个 `workflow` 引用，读取 `artifacts/workflows/<id>@<ver>/WORKFLOW.yaml`
3. **分析子工作流**：
   - Stage 数量和确认点密度
   - 死 Stage、循环出口完整性、异常路径覆盖
   - 确认点节奏是否合理
   - 与父工作流的衔接（父 Stage 的 `confirmation_point` 与子工作流的DONE 上报是否重叠/冗余）
4. **填写 `sub_workflows[]`**：每个子工作流一条记录，标注发现的问题和优化建议

**子工作流递归**：如果子工作流内部又引用了孙工作流（嵌套），同样检测并记录。标注嵌套深度，超过 3 层标为 critical risk。

**禁止**：检测到子工作流但跳过分析——子工作流的质量直接影响父工作流质量。即使子工作流的 WORKFLOW.yaml 不在本次改造范围内，也必须完整分析并在报告中呈现。

## 禁止行为

- 禁止修改输入材料
- 禁止直接生成 WORKFLOW.yaml 或 SKILL.md
- 禁止省略任何 AskUserQuestion 或 SubAgent 调用点
- 禁止跳过捆绑资源清点——即使旧 Skill 的 references/、scripts/、assets/ 看似"空"或"不相关"，也必须检查并记录
- 禁止检测到子工作流但不分析——含 `workflow` 字段的 Stage 必须触发子工作流检测流程
- 禁止假设输入材料中未明确描述的行为
