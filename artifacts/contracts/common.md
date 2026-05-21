# 通用工作流契约（Common Contract） v3.0.0

> SubAgent 被工作流调度后，必须遵守以下契约。主 Agent 在启动 prompt 中注入本契约的关键条款，并强制 SubAgent 执行前自行读取完整版本。

---

## 〇、wfctl 调用方式

wfctl 是纯 Python 调度程序，工作流中所有机械操作均通过它完成。调用格式：

```
python .claude/scripts/wfctl/main.py <command> [options]
```

- 编排器（主 Agent）从项目根目录执行，`find_root()` 自动向上查找 `.claude/` 或 `.agent/`
- SubAgent 从 worktree 内部执行 `identity` 和 `message write`（需要就近找到 `.wfctl_identity.json`）
- 不要 cd 进 wfctl 目录再执行——保持 CWD 在正确的上下文（编排器在项目根，SubAgent 在 worktree）
- 下文所有 `wfctl <cmd>` 均为此调用方式的简写

---

## 一、文件系统禁令

以下路径**绝对禁止**读取或写入：

- `.agent/workflows/instances/`（状态机、消息池、日志）
- 其他 stage 的产物目录
- 其他 stage 的 worktree 工作目录

**允许操作**：
- 当前 worktree 内的项目文件
- 本 Skill 的 `references/` 目录
- `.claude/contracts/` 目录下的契约文件（只读）

---

## 二、Git 操作禁令

- **禁止**：`git commit`、`git push`、`git checkout`、`git reset`、`git merge`、`git rebase`
- **允许**：`git status`、`git diff`、`git log`、`git branch`

---

## 三、Message 上报规范

1. 终止前必须通过 `wfctl message write` 写入 Message
2. 禁止直接手写 JSON 到 `.agent/workflows/instances/` 下的消息池
3. `status` 取以下之一：
   - `DONE` — 阶段完成
   - `ERROR` — 失败
   - `AWAITING_CONFIRM` — 需要用户确认
4. `confirm_questions` 长度 ∈ [1, 4]，一次性全部列出
5. 上报即终态——每次上报代表明确的阶段结局

---

## 四、变更与降级说明

SubAgent 不自行判定是否需要用户确认——该决策由 WORKFLOW.yaml 的 `confirmation_point` 字段控制，编排器通过 `wfctl next` 感知后负责呈现确认。

### 方案级降级（禁止自主执行，必须上报 AWAITING_CONFIRM）

以下变更**绝对禁止** SubAgent 自行执行——必须先上报 `AWAITING_CONFIRM`，在 `report` 中说明原因和影响，等待用户确认：

- 算法变更
- 精度降低
- 功能裁剪
- 核心方案简化或替换

### 资源级降级（自主执行，report 中说明）

- OOM 后分批计算或降采样
- 超时后减少迭代次数
- 依赖缺失时降级到标准库等价实现

---

## 五、契约读取义务

主 Agent 在 prompt 中注入以下指令：

> 执行任务前，先读取 `.claude/contracts/common.md`，确认理解所有禁令和上报规范。未读即执行视为违规。
