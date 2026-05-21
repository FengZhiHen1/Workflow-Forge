---
name: git-worktree-sync
description: Git worktree 多实例协作同步工作流。当用户在 git worktree 中完成代码修改并验收通过后，使用本 skill 将更改安全地合并回主分支。适用于多实例（Claude Code / Kimi Code）并行开发同一个项目的场景。触发场景包括：worktree 合并、同步到主分支、提交 worktree 更改、worktree 收尾、合并分支、git worktree 提交、将 worktree 更改同步回主线。即使用户没有明确提到 "worktree" 但处于 worktree 目录中并要求提交/合并更改，也应优先使用本 skill。
---

# Git Worktree 同步 Skill

本 Skill 用于在 git worktree 中完成开发后，将更改安全地同步回项目主分支，并清理 worktree 环境。

## 适用场景

- 你正在使用 git worktree 进行多实例并行开发
- 当前 worktree 中的更改已经验收通过
- 需要将更改合并回主分支（dev / main / master）

## 执行流程

**整个流程必须按顺序执行，每一步成功后才能进入下一步。**

### 步骤 1：前置检查

1. 确认当前目录处于 git worktree 中：
   ```bash
   git rev-parse --git-dir
   git worktree list
   ```
2. 获取当前分支名称：
   ```bash
   git branch --show-current
   ```
3. 确认当前分支不是主分支（dev/main/master），且存在未提交的更改或已提交的更改需要合并。

如果当前不在 worktree 中，或者当前分支就是主分支，停止执行并告知用户。

### 步骤 2：确定目标主分支

检测项目使用的分支策略，优先级如下：

1. **dev** — 如果 `origin/dev` 存在
2. **main** — 如果 `origin/main` 存在
3. **master** — 如果 `origin/master` 存在

使用以下命令检测：
```bash
git branch -r | findstr "origin/dev"
git branch -r | findstr "origin/main"
git branch -r | findstr "origin/master"
```

如果以上分支均不存在，使用 `AskUserQuestion` 询问用户目标分支名称。

### 步骤 3：同步目标主分支到当前分支

在合并前，先将目标主分支的最新代码同步到当前 feature 分支，以减少冲突：

```bash
git fetch origin <target-branch>
git merge origin/<target-branch> --no-edit
```

**冲突处理**：
- 如果合并无冲突，继续下一步。
- 如果出现冲突，尝试自动解决（见下方"冲突解决策略"）。
- 如果冲突无法自动解决，使用 `AskUserQuestion` 向用户确认处理方式，提供冲突文件列表。

### 步骤 4：分阶段提交当前更改

使用 `git status --short` 查看所有未提交的更改（包括未暂存和已暂存）。

#### 变更分组原则

按**功能模块**对更改进行分组提交。分析文件路径和变更内容，将属于同一模块/同一功能的文件分为一组。

**分组逻辑**：
- 同一目录下的相关文件通常属于同一模块
- 配置文件（如 `package.json`、`pyproject.toml`、`Dockerfile`）单独一组，标记为 `chore`
- 测试文件与被测代码放在同一组
- 如果某个模块的改动很小（1-2 个文件），可以与其他相近模块合并为一组
- 如果某个模块改动很大（超过 10 个文件），考虑拆分为更细粒度（如按子功能拆分）

#### Commit Message 规范

使用标准中文 commit message，遵循 Conventional Commits 格式：

```
<type>(<scope>): <subject>
```

- **type**：`feat`（新功能）、`fix`（修复）、`refactor`（重构）、`docs`（文档）、`test`（测试）、`chore`（构建/配置）
- **scope**：功能模块名称，如 `auth`、`payment`、`api`、`ui` 等。若无法确定具体模块，可省略括号部分
- **subject**：简洁描述变更内容，使用中文，句末不加句号

**示例**：
- `feat(auth): 添加 JWT 登录接口`
- `fix(payment): 修复订单状态同步异常`
- `refactor(api): 统一错误响应格式`
- `docs(readme): 更新部署说明`
- `chore(config): 更新依赖版本`

对每一组文件依次执行：
```bash
git add <file1> <file2> ...
git commit -m "<type>(<scope>): <subject>"
```

如果所有文件已经提交（即步骤 3 之前已经 commit 过），则跳过此步骤。

### 步骤 5：合并当前分支到目标主分支

在 git worktree 环境中，目标主分支通常已经被主工作树（如 `main-work`）检出，**不能**在当前 feature worktree 中直接 `git checkout` 到目标分支。

**正确做法**：
1. 先找到主工作树目录。可以通过以下命令查看所有 worktree：
   ```bash
   git worktree list
   ```
   主工作树通常是项目根目录下的原始工作目录（如 `main-work`）。
2. **切换到主工作树目录**，在那里执行后续操作：
   ```bash
   cd <main-worktree-path>
   git pull origin <target-branch>
   git merge <feature-branch> --no-edit
   git push origin <target-branch>
   ```

如果由于某种原因主工作树未检出目标分支，也可以临时回到项目根目录（bare repo 的上级目录）clone 一份再操作，但优先使用已有主工作树。

**冲突处理**：同步骤 3。出现冲突时优先自动解决，无法解决时询问用户。

### 步骤 6：清理 worktree

合并完成后，删除当前 worktree 目录（保留分支的 commit 历史）：

```bash
git worktree remove <worktree-path>
```

**注意**：
- 使用 `git worktree remove` 而不是 `rm -rf`，Git 会自动检查是否有未提交的更改
- 如果 `git worktree remove` 因未提交更改失败，先确认步骤 4 是否已完成
- 分支本身**不要删除**，保留 commit 历史

清理完成后，告知用户 worktree 已删除，当前工作目录已回到主分支。

## 冲突解决策略

当自动合并出现冲突时，按以下顺序尝试解决：

1. **文本文件冲突**：
   - 优先采用"双方保留"策略（在冲突标记中保留双方内容）
   - 对于配置类文件（JSON、YAML、TOML 等），优先保留 feature 分支的更改，同时补充主分支新增的配置项
   - 对于代码文件，检查冲突区域，若双方修改的是不同功能点，合并两者内容；若修改的是同一逻辑，标记为需人工确认

2. **二进制文件冲突**：
   - 优先保留 feature 分支的版本

3. **无法自动判断的冲突**：
   - 使用 `AskUserQuestion` 向用户展示冲突文件列表和冲突片段，询问处理方式
   - 选项可包括：保留当前更改、保留传入更改、手动编辑、中止合并

**安全边界**：任何可能导致原有功能被破坏的自动解决方案，都必须降级为询问用户。

## 注意事项

- 执行过程中如果任何 git 命令返回非零退出码，立即停止并分析错误原因
- 在 Windows 环境下使用 PowerShell，注意路径分隔符和命令兼容性
- 合并前务必执行 `git fetch`，确保基于最新代码进行合并
- 若当前 worktree 中有未跟踪的大文件或敏感文件，提醒用户在提交前检查 `.gitignore`
