---
name: module-lifecycle-preflight
description: >
  工作流流水线前置环境同步与就绪检查。检测 git worktree 状态，同步主分支最新代码，
  检查 docs/ 目录设计文档是否有未同步变更，运行 preflight_check.py 验证 Python 版本、
  脚本完整性和 SubAgent 可用性。不通过则阻断流水线。
  使用场景：(1) 模块生命周期工作流的入口阶段——对抗验证流水线启动前的环境就绪检查；
  (2) pipeline orchestrator 调度本 skill 验证所有前置条件；(3) git worktree 环境中
  同步主分支代码并检测未同步变更；(4) 验证流水线依赖的基础设施（Python 版本、脚本、契约目录）是否就绪。
  核心工作方式：通过 3 步串行检查（git 代码同步 → docs/ 文档变更检测 → preflight_check.py 脚本验证）
  确保流水线环境合格，任一检查不通过则阻断流水线。
  必须优先使用本 skill 当编排器需要执行模块实现流水线的入口就绪检查时。
---

# 模块生命周期前置检查 (Preflight)

编排流水线入口阶段的环境就绪检查。在契约提取、实现落地、测试生成等任何后续阶段启动前，
确保运行环境满足所有前置条件。

## 外部对接协议 (Protocol)

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-preflight/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-preflight/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）
4. 工作流级共享参考（可选）：若 `workflow_refs` 非空，按需读取其中列出的文件

> **零侵入原则**：若本 Skill 无专用契约且 `workflow_refs` 为空，通用契约自动兜底，无需因此上报 ERROR。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `module_id` — 目标模块编号（必填）
- `upstream_files`, `upstream_message_ids`（可选，本 skill 为入口阶段，通常为空）
- `workflow_ref_dir`, `workflow_refs`（可选）— 工作流级共享参考目录和文件列表
- `special_instructions`（可选）
- `stage_direction`（工作方向指令，优先级最高）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`，`report` 中说明缺失字段。
- `skill_id` 与自身 `skill_id`（`module-lifecycle-preflight`）不一致：立即终止，上报 `ERROR`。
- `module_id` 缺失：立即终止，上报 `ERROR`，说明流水线无法在缺少模块编号时继续。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

禁止直接手写 JSON 到 `.agent/messages/`。

### 4. 降级熔断

- **方案级降级**（跳过某项检查、降低检查标准、以警告替代错误）：**禁止自主执行**。必须在 `report` 中说明原因，上报 `PENDING_CONFIRM`，等待用户确认。
- **资源级降级**（超时后缩短检查范围、脚本缺失时跳过语法检查）：可自主执行，但必须在 `report` 中说明具体措施和影响。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle` 中 Stage `orch-preflight` 的执行器，
属于 `core-adversarial` 功能组。

**上游 Stage**：无（流水线入口点）

**下游 Stage**：`orch-contract`（进入 Skill `module-lifecycle-contract`）
- 本 Skill 完成就绪检查后，下游将进入契约提取与设计仲裁阶段
- 若本 Skill 以 ERROR 终止，下游全部阻塞

**可选下游**：`gitsync-check`（可选触发 `module-lifecycle-git-sync`）
- 若检测到 worktree 未同步且存在未提交变更，编排器可选择插入 Git 同步作为环境准备

---

## 核心逻辑：三步前置检查

此阶段无确认点（`confirmation_point: false`），检查完毕后直接上报结果。

### 先决条件：定位共享脚本

本 Skill 依赖工作流级共享脚本 `preflight_check.py`。按以下优先级定位：

1. 若编排器注入了 `workflow_ref_dir`，在其下的 `scripts/preflight_check.py` 查找
2. 若编排器注入了 `workflow_refs` 且包含脚本路径列表，从中筛选 `preflight_check.py`
3. Fallback：在项目根目录的 `scripts/` 下查找

定位失败则上报 `ERROR`，说明缺失文件。

### Step 1: Git Worktree 状态检测与代码同步

检测当前工作目录的 git 环境状态，确保代码与主分支同步。

**1.1 检测 git 环境**

```bash
git rev-parse --git-dir
```

非零退出码则上报 `ERROR`：当前工作目录不在 git 仓库中。

**1.2 检测 worktree 状态**

```bash
git worktree list
```

解析输出，确认：
- 当前目录是否为 worktree（列表中必须包含当前路径）
- 主 worktree（bare）是否存在

**1.3 确定主分支并同步**

主分支优先级：`dev` > `main` > `master`。

```bash
# 获取远程最新
git fetch origin <target-branch>

# 合并到当前分支
git merge origin/<target-branch> --no-edit
```

合并冲突时处理策略：
- 文本文件冲突：双方保留（保留冲突标记，由用户后续处理）
- 配置文件冲突：远程优先生效

冲突无法自动解决时，记录为警告但**不阻断**（后续阶段可以由 gitsync-check 处理）。

**1.4 检测未同步变更**

```bash
git status --porcelain
```

分析输出，标记所有未跟踪或已修改文件。特别关注：
- `docs/` 目录下的设计文档变更
- 若 `docs/` 下有未提交变更，记录警告：设计文档存在未同步修改，可能影响契约提取阶段的一致性

**输出**：`preflight_git_status` — 包含分支信息、同步状态、未提交变更列表。

### Step 2: docs/ 目录设计文档检查

检查 `docs/` 目录下设计文档的完整性和同步状态。

**检查项**：

1. **目录存在性**：`docs/` 是否存在且非空
   - 不存在：上报 `ERROR`
2. **模块设计文档定位**：按四优先级搜索 `{module_id}` 相关设计文档
   - P0：独立双文件 `docs/功能设计/{group}/{module_id}-*/{module_id}-*-设计文档.md` + `{module_id}-*-落地规范.md`
   - P1：旧版单文件 `docs/功能设计/{group}/{module_id}-*/{module_id}-*-功能设计文档.md`
   - P2：总设计文档 `docs/功能设计/{module_id}-总设计文档.md`
   - P3：其他路径
   - 至少定位到一份设计文档（设计文档或落地规范），否则上报 `ERROR`
3. **git 变更检测**（基于 Step 1.4 的结果）：
   - 若设计文档目录下的文件存在未提交变更，记录警告并列出具体文件
   - 此警告不阻断，但下游 `orch-contract` 阶段应知晓此风险

**输出**：`preflight_docs_status` — 包含文档目录存在性、定位到的文档路径列表、未同步变更列表。

### Step 3: 运行 preflight_check.py 验证

运行共享脚本进行自动化环境验证。

```bash
python <workflow_ref_dir>/scripts/preflight_check.py \
  --module-id <module_id> \
  --check-sub-skills
```

脚本执行以下检查：
- Python 版本 >= 3.8
- 工作流共享 `scripts/` 下所有必需脚本存在且语法正确
- `docs/contracts/{module_id}/` 目录存在性（若指定 `--module-id`）
- 依赖的 SubAgent skill 存在性（若指定 `--check-sub-skills`）

**退出码判定**：

| 退出码 | 含义 | 处理 |
|:---|:---|:---|
| 0 | 全部通过 | 上报 `DONE` |
| 1 | 存在警告 | 上报 `DONE`，`report` 中列出警告项 |
| 2 | 存在阻断性错误 | 上报 `ERROR`，`report` 中列出错误项 |

**退出码 2 时**：不得继续执行后续阶段。`report` 必须包含：
- 具体失败的检查项
- 修复建议（如"请升级 Python 到 3.8+"、"请安装缺失的脚本文件"）

---

## 综合判定与上报

三步检查完成后，汇总结果：

| 场景 | 状态 | 说明 |
|:---|:---|:---|
| 三步全部通过 | `DONE` | 流水线环境就绪 |
| 仅 Step 1/2 产生警告，Step 3 退出码 0 | `DONE` | `report` 中记录警告项 |
| Step 3 退出码 1（警告） | `DONE` | `report` 中汇总全部警告，下游可自行决定是否继续 |
| 任一步产生 ERROR | `ERROR` | `report` 中列出所有阻断性错误及修复建议 |

**report 格式**：

```json
{
  "preflight_summary": {
    "passed": true_or_false,
    "total_checks": 3,
    "failed_checks": [],
    "warnings": []
  },
  "step_details": {
    "git_sync": { "status": "pass|warn|error", "details": "..." },
    "docs_check": { "status": "pass|warn|error", "details": "...", "design_docs_found": ["path1", "path2"] },
    "preflight_script": { "status": "pass|warn|error", "exit_code": 0, "output": "..." }
  }
}
```

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务：
   - 在 `.tmp/<workflow_instance_id>/` 下生成你的 message 草稿 JSON；
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`；
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用；
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止。
3. `message_id` 由脚本自动生成，你无需提供。
4. 本 Skill 的 `confirmation_point=false`：任务完成后直接上报 `status: "DONE"`（或 `status: "ERROR"` 若检查失败）。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

---

## [WORKFLOW_CONFIG]
```json
{
  "skill_id": "module-lifecycle-preflight",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "optional"
}
```
