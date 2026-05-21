---
name: module-lifecycle-git-sync
description: >
  模块开发生命周期中的 Git Worktree 同步执行器。当用户在 git worktree 中完成代码修改并验收通过后，使用本 Skill 将更改安全地合并回主分支。
  适用于多实例（Claude Code / Kimi Code）并行开发同一个项目的场景。
  触发场景包括：worktree 合并、同步到主分支、worktree 收尾、合并分支、git worktree 提交、将 worktree 更改同步回主线。
  即使用户没有明确提到 "worktree" 但处于 worktree 目录中并要求提交/合并更改，也应优先使用本 Skill。
  由 module-implementation-orchestrator 或用户直接调度。
  必须优先使用本 Skill 当用户提及 worktree 同步、git 合并、代码提交、分支合并、worktree 清理时。
---

# 模块生命周期 Git 同步 Skill

本 Skill 用于在 git worktree 中完成模块开发后，将更改安全地同步回项目主分支，并清理 worktree 环境。

## 外部对接协议（Protocol）

### 1. 契约读取义务

作为 SubAgent 被调度时，执行内部任务前必须依次读取：
1. `.claude/contracts/common.md`（通用契约）
2. 输入契约（优先 `.claude/skills/module-lifecycle-git-sync/references/contract-input.md`，缺失则读取 `.claude/contracts/input.md`）
3. 输出契约（优先 `.claude/skills/module-lifecycle-git-sync/references/contract-output.md`，缺失则读取 `.claude/contracts/output.md`）

> **Git 操作授权**：本 Skill 是专用的 Git 同步执行器，对其执行的 Shell 命令（git commit / git merge / git push / git fetch / git checkout / git worktree remove）拥有显式授权。通用契约中的 Git 操作禁令不适用于本 Skill 的核心业务 —— 这是本 Skill 的唯一职责。但禁止执行 `git reset --hard`、`git push --force`、`git rebase` 等破坏性命令。

### 2. 输入接收与校验

从编排器注入的 prompt 中提取以下字段：
- `workflow_instance_id`, `agent_id`, `skill_id`, `stage_id`
- `upstream_files`, `upstream_message_ids`（可选）
- `stage_direction`（工作方向指令，优先级最高，其中 `stage_id` 决定执行哪个阶段）
- `special_instructions`（可选）

**校验规则**：
- 必填身份字段缺失任意一项：立即终止，上报 `ERROR`。
- `skill_id` 与 `module-lifecycle-git-sync` 不一致：立即终止，上报 `ERROR`。

### 3. 输出上报

完成后必须调用：
```bash
python .claude/scripts/write_message.py \
  --input <草稿路径> \
  --workflow <workflow_instance_id> \
  --agent-id <agent_id> \
  --skill-id <skill_id>
```

### 4. 降级熔断

- **方案级降级**（跳过某个 stage、变更合并策略、强制推送）：**禁止自主执行**。上报 `PENDING_CONFIRM`。
- **资源级降级**（分批提交大量文件）：可自主执行，但必须在 `report` 中说明。

---

## 工作流上下文

本 Skill 是工作流 `module-lifecycle@1.0.0` 中 Group 5（Git Worktree Sync）的执行器，覆盖以下 5 个 Stage（按顺序执行）：

| Stage ID | 名称 | confirmation_point | 条件 |
|----------|------|-------------------|------|
| `gitsync-check` | 前置检查与分支确定 | CONDITIONAL | 默认分支均不存在时触发 |
| `gitsync-sync` | 同步主分支到当前分支 | CONDITIONAL | merge 冲突无法自动解决时触发 |
| `gitsync-commit` | 分阶段提交 | false | -- |
| `gitsync-merge` | 合并到主分支并推送 | CONDITIONAL | merge 到主分支冲突时触发 |
| `gitsync-cleanup` | 清理 Worktree | false | -- |

编排器每次调度本 Skill 时，通过 `stage_direction` 中的 `stage_id` 指定当前执行的阶段。Skill 根据 `stage_id` 选择对应的执行路径。

**入口**：`gitsync-check`（Group 5 入口），也可从 `orch-preflight` 通过可选 edge 进入。

---

## 内部执行规范

### 通用前置操作

每个 Stage 执行前，先确认工作目录处于 git 仓库中：

```bash
git rev-parse --git-dir
```

若不在 git 仓库中，上报 `ERROR`，`report` 说明当前目录不是 git 仓库。

### Stage: gitsync-check（前置检查与分支确定）

**任务**：确认当前处于 git worktree 中且在 feature 分支上，检测目标主分支。

1. 确认处于 git worktree 中：
   ```bash
   git worktree list
   git branch --show-current
   ```
2. 若当前分支是 `dev`、`main` 或 `master`，说明已在主分支上，无需同步，上报 `DONE`，`report` 中说明"已在主分支上，跳过同步"。
3. 若不在 worktree 中（`git worktree list` 仅显示一个条目且就是当前目录），上报 `DONE`，`report` 中说明"不在 worktree 环境中，跳过同步"。

4. 检测目标主分支（优先级 `dev` > `main` > `master`）：
   ```bash
   git branch -r | grep "origin/dev"
   git branch -r | grep "origin/main"
   git branch -r | grep "origin/master"
   ```
   注：Windows 环境下使用 `grep`，若不可用则用 PowerShell 的 `Select-String`。

5. **确认点条件**：若 `dev`、`main`、`master` 均不存在于 `origin`：
   - 生成 message 草稿，`status: "PENDING_CONFIRM"`，`confirm_required: true`
   - `confirm_questions`: `["未检测到默认主分支（dev/main/master），请指定目标主分支名称："]`
   - 调用 `write_message.py` 上报，终止等待恢复
   - 恢复后从 `metadata.confirm_responses` 中提取用户指定的分支名称

6. 若找到目标分支，在 `report` 中记录检测到的目标分支名称，上报 `DONE`。

### Stage: gitsync-sync（同步主分支到当前分支）

**任务**：将目标主分支的最新代码合并到当前 feature 分支。

1. 从上游（gitsync-check）获取目标分支名称。
2. 执行同步：
   ```bash
   git fetch origin <target-branch>
   git merge origin/<target-branch> --no-edit
   ```

3. **冲突处理**：
   - 若合并无冲突：上报 `DONE`。
   - 若出现冲突，尝试自动解决（见下方"冲突解决策略"）。
   - **确认点条件**：若冲突无法自动解决：
     - 生成 message 草稿，`status: "PENDING_CONFIRM"`，`confirm_required: true`
     - `confirm_questions`:
       ```
       [
         "merge origin/<target-branch> 时出现以下冲突文件，无法自动解决：[列出冲突文件路径]。请选择处理方式：",
         "1=保留当前分支内容  2=保留主分支内容  3=手动编辑后告知  4=中止合并"
       ]
       ```
     - 调用 `write_message.py` 上报，终止等待恢复
     - 恢复后根据 `metadata.confirm_responses` 执行对应操作

4. 合并完成后，上报 `DONE`。

### Stage: gitsync-commit（分阶段提交）

**任务**：分析未提交的更改，按功能模块分组提交。

1. 查看未提交更改：
   ```bash
   git status --short
   ```
2. 若所有文件已提交（无输出），上报 `DONE`，`report` 中说明"无可提交更改"。

3. **变更分组**：按功能模块对文件进行分组：
   - 同一目录下的相关文件分为一组
   - 配置文件（`package.json`、`pyproject.toml`、`Dockerfile` 等）单独一组，标记为 `chore`
   - 测试文件与被测代码放在同一组
   - 1-2 个文件的小改动可合并到相近模块组
   - 超过 10 个文件的大模块可拆分为子功能组

4. **Commit Message 格式**：使用中文 Conventional Commits：
   ```
   <type>(<scope>): <subject>
   ```
   - `type`：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`
   - `scope`：功能模块名称（如 `auth`、`api`），无法确定时可省略括号部分
   - `subject`：简洁中文描述，句末不加句号

5. 逐组提交：
   ```bash
   git add <file1> <file2> ...
   git commit -m "<type>(<scope>): <subject>"
   ```
   每组一个 commit。

6. 提交完成后，`report` 中列出所有 commit 的摘要（类型、范围、描述），上报 `DONE`。

### Stage: gitsync-merge（合并到主分支并推送）

**任务**：切换到主 worktree，将 feature 分支合并到主分支并推送。

1. 找到主 worktree 目录：
   ```bash
   git worktree list
   ```
   主 worktree 通常是项目根目录下的原始工作目录（如 `main-work` 或项目根目录），其特征是检出主分支（`dev`/`main`/`master`）。

2. 在主 worktree 中执行：
   ```bash
   cd <main-worktree-path>
   git pull origin <target-branch>
   git merge <feature-branch> --no-edit
   git push origin <target-branch>
   ```

3. **确认点条件**：若 `git merge` 出现冲突：
   - 生成 message 草稿，`status: "PENDING_CONFIRM"`，`confirm_required: true`
   - `confirm_questions`:
     ```
     [
       "合并 feature 分支到主分支时出现冲突：[列出冲突文件]。请选择处理方式：",
       "1=保留 feature 分支内容  2=保留主分支内容  3=手动编辑后告知  4=中止合并"
     ]
     ```
   - 调用 `write_message.py` 上报，终止等待恢复

4. 合并并推送成功后，上报 `DONE`。

### Stage: gitsync-cleanup（清理 Worktree）

**任务**：删除当前 worktree 目录，保留分支。

1. 获取当前 worktree 路径：
   ```bash
   git worktree list
   ```
2. 删除 worktree（保留分支）：
   ```bash
   git worktree remove <worktree-path>
   ```
   - 若因未提交更改失败，先检查 gitsync-commit 是否已完成
   - 分支**不删除**，保留 commit 历史
3. 上报 `DONE`，`report` 中说明已清理的 worktree 路径。

---

## 通用：冲突解决策略

当 `git merge` 出现冲突时，按以下顺序尝试自动解决：

1. **文本文件冲突**：采用"双方保留"策略 —— 在冲突标记中保留双方内容，让开发者后续手动选择。
2. **配置文件冲突**（JSON、YAML、TOML 等）：优先保留 feature 分支的更改，同时补充主分支新增的配置项（合并双方键值）。
3. **代码文件冲突**：检查冲突区域：
   - 若双方修改的是不同函数/区域，合并两者内容
   - 若修改同一逻辑，标记为需人工确认，触发 PENDING_CONFIRM
4. **二进制文件冲突**：优先保留 feature 分支的版本。

**安全边界**：任何可能导致原有功能被破坏的自动解决方案，都必须降级为 PENDING_CONFIRM。

---

## 确认点设计

本 Skill 的三个条件确认点对应的问题设计：

| Stage | 条件 | 问题 ID | 问题内容 |
|-------|------|---------|---------|
| gitsync-check | 无默认分支 | AQ-012 | 未检测到默认主分支，请指定目标分支名称 |
| gitsync-sync | 同步冲突不可解 | AQ-013 | merge 冲突文件列表 + 4 个处理选项 |
| gitsync-merge | 合并到主分支冲突 | AQ-014 | 合并到主分支冲突文件 + 4 个处理选项 |

所有 `confirm_questions` 必须包含 4 个以内具体问题，每项非空且语义明确。

---

## 注意事项

- 任何 git 命令返回非零退出码，检查错误原因后再决定下一步
- 在 Windows 环境下注意路径分隔符和命令兼容性（优先使用 `bash` shell）
- 合并前务必执行 `git fetch`，确保基于最新代码
- 若 worktree 中有未跟踪的大文件或敏感文件，在 gitsync-commit 阶段提醒检查 `.gitignore`

---

## [WORKFLOW_CONFIG]

```json
{
  "skill_id": "module-lifecycle-git-sync",
  "version": "1.0.0",
  "contract_paths": {
    "common": ".claude/contracts/common.md",
    "input": ".claude/contracts/input.md",
    "output": ".claude/contracts/output.md"
  },
  "task_modes": ["core"],
  "autonomous_degradation": false,
  "checkpoint_policy": "conditional"
}
```

---

## Message 上报契约

1. 你的 `agent_id`、`workflow_instance_id`、`skill_id` 已由编排器注入，请在 message 中原样使用，禁止自行编造。
2. 当你完成阶段任务或需要用户确认时：
   - 在 `.tmp/<workflow_instance_id>/` 下生成 message 草稿 JSON
   - 调用 `python .claude/scripts/write_message.py --input <草稿路径> --workflow <instance_id> --agent-id <你的agent_id> --skill-id <你的skill_id>`
   - 若脚本返回错误（非零退出码），根据 stderr 修正后重新调用
   - 若连续失败 3 次，将 `status` 改为 `ERROR`，`report` 中说明校验失败详情，并终止
3. `message_id` 由脚本自动生成，你无需提供。
4. `confirm_questions` 必须是字符串数组，长度 1-4。若你有多项待确认，一次性全部列出，不要分多次终止。
5. 终止前，你的最终回答必须包含脚本返回的 message 文件路径。

### 确认点上报规则

根据当前 `stage_id` 对应的 `confirmation_point` 行为：

- **gitsync-check / gitsync-sync / gitsync-merge**（conditional 确认点）：
  - 条件触发时：生成 message 草稿，`status: "PENDING_CONFIRM"`，`confirm_required: true`，调用 `write_message.py`，终止等待恢复
  - 条件未触发时：正常完成任务，`status: "DONE"`，`confirm_required: false`
- **gitsync-commit / gitsync-cleanup**（无确认点）：
  - 正常完成任务后直接上报 `status: "DONE"`，`confirm_required: false`

### Draft JSON 模板

**DONE 示例**（无冲突的正常完成）：
```json
{
  "workflow_instance_id": "<注入值>",
  "agent_id": "<注入值>",
  "skill_id": "module-lifecycle-git-sync",
  "status": "DONE",
  "report": "阶段完成摘要（纯文本，禁止 # 标题）",
  "upstream_files": [],
  "modified_files": [],
  "draft_files": [],
  "output_files": [],
  "checkpoint_summary": "已完成：...；待处理：...；关键上下文：...",
  "confirm_required": false,
  "confirm_questions": []
}
```

**PENDING_CONFIRM 示例**（冲突无法自动解决）：
```json
{
  "workflow_instance_id": "<注入值>",
  "agent_id": "<注入值>",
  "skill_id": "module-lifecycle-git-sync",
  "status": "PENDING_CONFIRM",
  "report": "merge origin/dev 时出现冲突，以下文件无法自动解决：...",
  "upstream_files": [],
  "modified_files": [],
  "draft_files": [],
  "output_files": [],
  "checkpoint_summary": "已完成：fetch + merge 尝试；待处理：用户确认冲突解决方式；关键上下文：冲突文件列表...",
  "confirm_required": true,
  "confirm_questions": [
    "merge origin/dev 时以下文件出现冲突无法自动解决：src/a.py, src/b.py。请选择处理方式：",
    "1=保留当前分支内容  2=保留主分支内容  3=手动编辑后告知  4=中止合并"
  ]
}
```
