# worktree 与 git 锚点规范 v3.0.0

---

## 〇、git 操作分工

所有 git 操作（`worktree add`、`commit`、`merge`、`tag`、`checkout`、`worktree remove`）均由 wfctl 在对应命令内部执行。主 Agent 只调用 wfctl 命令，不直接操作 git。

| 操作 | 执行者 | 触发命令 |
|------|--------|---------|
| 创建实例 worktree | wfctl | `create` |
| 拆分 stage worktree | wfctl | `next`（检测到并发时） |
| **自动提交 worktree 变更** | **wfctl** | **`next`（消费 DONE 消息后）** |
| Stage → 实例 worktree 合并 | wfctl | `next`（消费 DONE 消息时） |
| 冲突消解 | conflict-resolver SubAgent | 主 Agent 收到 conflict action 后启动 |
| 实例 → 主仓库合并 | wfctl | `next`（返回 merge_to_main 时） |
| 打锚点 | wfctl | `create` / `next`（stage DONE 时） |
| 回退 checkout | wfctl | `rollback` |
| 清理 worktree | wfctl | `next`（合并后 / 异常扫描） |

wfctl 能做的 git 操作是确定性的（commit、checkout、merge 无冲突时、tag、clean）。冲突消解不是——它需要看代码内容做语义判断，因此委托给 SubAgent（conflict-resolver）。

---

## 一、目录布局

```
.tmp/worktrees/
├── instance-<instance_id>/         # 实例级 worktree
├── stage-<instance_id>-<s_id>/     # 并发 stage 级 worktree
└── stage-<instance_id>-<s_id>#<n>/ # parallel 拆分 worktree
```

全部在 `.tmp/worktrees/` 下，`.tmp/` 已由 `.gitignore` 排除。`git worktree add` 创建时会自动生成对应的临时分支（`wf-stage-<instance_id>-<s_id>`），合并后随 worktree 一并清理，不残留分支引用。

---

## 二、实例 worktree

### 2.1 创建

`wfctl create` 执行：

1. 以主仓库当前 HEAD 为基点创建 worktree：

   ```
   git worktree add .tmp/worktrees/instance-<instance_id>/ HEAD
   ```

2. 在 worktree 内打初始锚点（见 §五）
3. 在 worktree 根目录写入身份元数据文件（instance_id、project_root、消息投递路径）
4. 生成 instance.json，写入 `.agent/instances/<instance_id>/`

基准始终为主仓库 HEAD，无 `--base` 参数。两个工作流实例需要串行依赖时，由用户确保第一个实例已合入主仓库后再启动第二个。

### 2.2 运行时角色

| 并发场景 | worktree | SubAgent 工作目录 |
|----------|----------|------------------|
| 单 stage 就绪 | 实例 worktree（不拆） | `.tmp/worktrees/instance-<id>/` |
| 多 stage 并发就绪 | 每个 stage 拆出独立 worktree | `.tmp/worktrees/stage-<id>-<s_id>/` |
| parallel 拆分 | 每个拆出实例独立 worktree | `.tmp/worktrees/stage-<id>-<s_id>#<n>/` |

### 2.3 终止清理

实例进入 `COMPLETED` 或 `FAILED` 后，主 Agent 调用清理：

```
git worktree remove .tmp/worktrees/instance-<instance_id>/ --force
```

异常残留处理见 §七。

---

## 三、Stage 级 worktree 拆分

### 3.1 拆分规则

| 场景 | 拆分 | 路径 | 基准 |
|------|------|------|------|
| 单 stage 就绪 | 不拆 | 实例 worktree | — |
| 多 stage 并发 | 拆 | `stage-<id>-<stage_instance_id>/` | 实例 worktree HEAD |
| parallel 拆分 | 拆 | `stage-<id>-<s_id>#<n>/` | 实例 worktree HEAD |

仅并发时拆分——非并发时 SubAgent 直接在实例 worktree 中工作，stage 的提交直接挂在实例 worktree 上，零合并开销。

### 3.2 拆分操作

wfctl `next` 判定需拆分时：

```
git worktree add .tmp/worktrees/stage-<instance_id>-<stage_instance_id>/ \
  -b wf-stage-<instance_id>-<stage_instance_id> \
  .tmp/worktrees/instance-<instance_id>/
```

- 临时分支名：`wf-stage-<instance_id>-<stage_instance_id>`
- 基准点：实例 worktree 当前 HEAD（包含所有已完成 stage 的产出）
- SubAgent 的变更全部留在临时分支上
- 完成后 wfctl 将临时分支合并回实例 worktree

### 3.3 并发分配约束

| 约束 | 行为 |
|------|------|
| `max_parallel_agents` | RUNNING 总数（含子工作流）达上限时不新增 spawn |
| `exclusive: true` | 有 exclusive stage RUNNING 时本轮不新增任何 spawn |
| 就绪顺序 | FIFO，先就绪先分配 |

已就绪但未分配到 worktree 的 stage 保持 `PENDING`。

---

## 四、自动提交

SubAgent 在 worktree 中修改文件后，不自行执行 git commit（权限规范禁止 SubAgent 操作 git）。wfctl 在消费 DONE 消息后、合并前，自动将变更提交。

### 4.1 提交信息格式

Message 的 `report` 字段采用 conventional commit 格式，可以是单行标题也可以包含正文。wfctl 取其完整内容作为 git commit message，并追加程序化 trailers：

```
<report>

wf-stage: <stage_id>
wf-instance: <instance_id>
wf-message: <message_id>
```

单行示例：

```
feat(s03): 完成选题分析，推荐方案 B

wf-stage: s03
wf-instance: 20260517-001
wf-message: msg-a1b2c3d4
```

多行示例（report 包含正文）：

```
feat(s03): 完成选题分析，推荐方案 B

- 分析了 A、B、C 三个候选方案的可行性与数据可得性
- 方案 B 在实现成本和数据质量两个维度均表现最优
- 建议下一步：验证方案 B 的原始数据完整性
- 未决问题：方案 B 依赖的外部 API 需确认 SLA

wf-stage: s03
wf-instance: 20260517-001
wf-message: msg-a1b2c3d4
```

### 4.2 提交时机与位置

| 场景 | 提交时机 | 提交位置 |
|------|---------|---------|
| 非并发 stage（直接在实例 worktree） | `next` 消费 DONE 消息后 | 实例 worktree |
| 并发 stage（独立 stage worktree） | `next` 消费 DONE 消息后，合并前 | stage worktree |

### 4.3 提交操作

将 `<report>` + trailers 写入临时文件，通过 `git commit -F` 提交：

```
git -C <worktree> add -A
git -C <worktree> commit -F <tmpfile>
```

`<tmpfile>` 内容：

```
<report>

wf-stage: <stage_id>
wf-instance: <instance_id>
wf-message: <message_id>
```

非并发 stage 提交后直接走锚点流程。并发 stage 提交后，临时分支上有了完整提交，后续的 fetch + merge --no-ff 才能正常工作。

---

## 五、两级合并

### 5.1 Stage → 实例 worktree

仅并发拆分的 stage 需要合并。非并发 stage 直接在实例 worktree 上提交，无需合并。

**并发 stage 合并流程**：

1. 在 stage worktree 中自动提交变更（见 §四）

2. 将 stage worktree 的临时分支 fetch 到实例 worktree：

   ```
   git -C .tmp/worktrees/instance-<id>/ fetch \
     .tmp/worktrees/stage-<id>-<s_id>/ wf-stage-<id>-<s_id>
   ```

3. 合并：

   ```
   git -C .tmp/worktrees/instance-<id>/ merge FETCH_HEAD --no-ff
   ```

4. **无冲突**：
   - Stage → `DONE`
   - 在实例 worktree 内打锚点
   - 清理 stage worktree
   - 解锁下游

5. **多个 stage 同时 DONE 的合并顺序**：
   - 按 `stage_id` 字典序依次合并到实例 worktree
   - 保证合并顺序确定性，避免因时序差异导致不同冲突结果
   - 开发者可通过 stage_id 命名控制合并优先级

6. **有冲突**：
   - Stage → `CONFLICT`
   - 保留 stage worktree，冲突状态在实例 worktree 中
   - 返回 `{action: "conflict", ...}`
   - 走 conflict-resolver 流程（见 wfctl 接口规范 §十三）

### 5.2 实例 worktree → 主仓库

实例所有 stage DONE 后，`next` 返回 `{action: "merge_to_main", ...}`。主 Agent 调用 wfctl 执行合并——wfctl 是 git 操作的唯一执行者：

1. wfctl 尝试将实例 worktree 合入主仓库
2. **无冲突**：静默合并，打最终锚点，完成
3. **有冲突**：wfctl 返回 `{action: "conflict", conflict_files: [...], worktree: "<主仓库路径>"}`。主 Agent 启动 conflict-resolver SubAgent 在**主仓库**中消解冲突（同 stage 级冲突处理流程）。消解后 wfctl 完成合并，打最终锚点

不使用确认点。只有 git 冲突时才需要人工介入，且通过 conflict-resolver 的 AWAITING_CONFIRM 机制呈现。

---

## 六、Git 锚点

### 5.1 命名规范

| 锚点 | 格式 | 示例 |
|------|------|------|
| 初始 | `wf-<instance_id>-s00-workflow-start` | `wf-20260517-001-s00-workflow-start` |
| Stage | `wf-<instance_id>-<stage_id>` | `wf-20260517-001-s03` |
| parallel 实例 | `wf-<instance_id>-<stage_id>#<n>` | `wf-20260517-001-s03#2` |
| 最终 | `wf-<instance_id>-final` | `wf-20260517-001-final` |

前缀 `wf` 可由 WORKFLOW.yaml 的 `anchor_prefix` 覆盖。

### 5.2 存储位置

Lightweight git tag，仅存在于实例 worktree 的 git 仓库中。不推送远程，不进入主仓库历史。实例 worktree 被删除后锚点随之消失。

### 5.3 打锚点时机

| 时机 | 锚点 |
|------|------|
| `create` 完成 | 初始锚点 |
| Stage → DONE（合并成功或非并发直接完成） | Stage 锚点 |
| 实例 worktree 合入主仓库 | 最终锚点 |

一个 stage 只打一个锚点，粒度 stage 级。SubAgent 在 stage 内部可能做多次 commit，但这些中间态不单独打锚点——回退只能退到 stage 边界。

### 5.4 用途

- **回退定位**：`git checkout wf-<instance_id>-<stage_id>` 重建到指定 stage 完成后的状态
- **审计追溯**：`git diff wf-<id>-s02..wf-<id>-s03` 精确查看单个 stage 的变更集
- **违规恢复**：wfctl 事后校验发现保护区被触碰时，从锚点检出覆盖违规修改

---

## 七、回退

### 6.1 命令

```
wfctl rollback --instance <id> --stage <stage_id>
```

### 6.2 行为

1. 校验 `<stage_id>` 存在且有对应锚点
2. 确定受影响的下游 stage：从 `<stage_id>` 出发，沿 edges（排除 `condition=failure` 和 `loop_exceeded`）BFS 遍历，收集所有可达 stage。包括 parallel 拆分出的所有 stage 实例
3. 重建实例 worktree：

   ```
   git -C .tmp/worktrees/instance-<id>/ checkout wf-<instance_id>-<stage_id>
   ```

   下游 stage 的提交被 git 自然清除（保留在 reflog 中 30 天，可恢复）

4. 移除受影响 stage 的锚点 tags
5. 重置受影响 stage 的状态为 `PENDING`，清零 `attempt_count`、`loop_counter`
6. 级联清理受影响 stage 的 `consumed_message_ids`
7. 写入 timeline

### 6.3 不可回退

| 情况 | 处理 |
|------|------|
| 目标 stage 无锚点 | 报错，拒绝 |
| 实例 worktree 已合入主仓库 | 报错，提示从主仓库历史恢复 |

---

## 八、清理

### 7.1 正常清理

| 时机 | 操作 | 触发者 |
|------|------|--------|
| Stage worktree 合并入实例 worktree | `git worktree remove stage-<id>-<s_id>/ --force` | wfctl `next`（合并成功后立即清理） |
| 实例 COMPLETED / FAILED | `git worktree remove instance-<id>/ --force` | wfctl `next`（检测到终态后自动执行）或 wfctl `terminate`（用户主动取消时） |

### 7.2 异常残留

wfctl `next` 每次调用扫描 `.tmp/worktrees/`：

| 情况 | 处理 |
|------|------|
| worktree 目录存在但对应 instance 不存在 | `git worktree remove --force`，写 deviation |
| worktree 目录存在，instance ACTIVE 但无对应 RUNNING/CONFLICT stage | `git worktree remove --force`，写 deviation |

`wfctl status` 返回的 `active_worktrees` 和 `conflict_worktrees` 供开发者排查。
