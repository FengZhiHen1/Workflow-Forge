# wfctl 目录结构重组方案

> 编制日期：2026-05-24
> 版本：v1.0
> 范围：artifacts/scripts/wfctl 全部源码目录

---

## 一、现有目录痛点

### 1.1 文件分布现状

```
wfctl/
├── cli/                    ← 18 个命令平铺，无业务分组
│   ├── confirm.py          (384 行)
│   ├── rollback.py         (88 行)
│   ├── skip.py             (105 行)
│   ├── terminate.py
│   └── ... 14 个其他命令
├── core/                   ← 职责混杂：schema + DAG + Git + 锁 + IO
│   ├── schema/             (数据模型：interface.py / v3.py / loader.py)
│   ├── dag.py              (图引擎)
│   ├── dag_validator.py    (验证器)
│   ├── git_ops.py          (Git 薄封装)
│   ├── lock.py             (文件锁)
│   ├── atomic_write.py     (原子写入)
│   └── logging.py          (日志)
├── services/               ← 分类不清：调度/worktree/消息/状态/验证/查询/创建 全混在一起
│   ├── scheduler/
│   │   ├── processors/     (13 个 Processor)
│   │   ├── orchestrator.py
│   │   ├── context.py
│   │   └── state_model.py
│   ├── scheduler_legacy.py (1459 行遗留文件)
│   ├── worktree_manager.py
│   ├── creator.py
│   ├── message_handler.py
│   ├── validator.py
│   ├── status_builder.py
│   └── state_manager.py
└── tests/                  ← 与源码结构不完全对齐
    ├── cli/
    ├── core/
    ├── services/
    └── quality/
```

### 1.2 核心痛点

| 痛点 | 说明 |
|------|------|
| **CLI 命令平铺** | 18 个命令文件平铺在 `cli/` 下，无业务域分组。confirm/rollback/skip/pause/resume/terminate 都属于"stage 操作"，但散落在不同文件中，无法一眼看出命令体系。 |
| **core/ 职责混杂** | `core/` 既是"领域核心"（schema、DAG）又是"基础设施"（Git、锁、原子写入、日志）。一个修改基础设施的 PR 会触发对整个 core/ 的审查。 |
| **services/ 是大杂烩** | 调度器、worktree 管理、消息处理、状态持久化、查询构建、实例创建全部平铺。不同业务域之间没有边界。 |
| **scheduler/ 嵌套混乱** | `orchestrator.py` 和 `context.py` 在 `scheduler/` 下，但 13 个 Processor 在 `scheduler/processors/` 下，而状态模型又在 `scheduler/state_model.py`。一个调度器的完整视图需要跨 3 个层级查看。 |
| **新增模块无归宿** | 重构方案中引入的大量新模块——`TransitionPolicy`、`DAGTopology`、`RunningAgentManager`、`CycleMeta`、`ConfirmResult`、`RollbackResult`、`SkipResult`、`SyncResult`——没有明确的目录位置。如果继续塞进现有目录，会加剧混乱。 |
| **测试与源码不对齐** | `tests/services/` 下没有 `scheduler/` 子目录，Processor 的测试散落在 `tests/services/` 或 `tests/core/` 中。 |
| **遗留文件污染** | `services/scheduler_legacy.py`（1459 行）与 `services/scheduler/` 新架构并存，目录名本身就传达了"新旧交替"的混乱信号。 |

---

## 二、新目录结构设计

```
wfctl/
├── cli/                          # 命令层：用户接口
│   ├── __init__.py
│   ├── main.py                   # argparse 注册 + 全局异常捕获
│   ├── workflow/                 # 工作流管理命令
│   │   ├── create.py             # 创建实例
│   │   ├── resolve.py            # 工作流发现
│   │   ├── visualize.py          # 可视化
│   │   └── cleanup.py            # 清理僵尸/孤儿
│   ├── instance/                 # 实例生命周期命令
│   │   ├── status.py             # 状态查询
│   │   ├── sync.py               # 同步消息
│   │   ├── pause.py              # 暂停
│   │   ├── resume.py             # 恢复
│   │   └── terminate.py          # 终止
│   ├── stage/                    # Stage 操作命令（全部调用 TransitionPolicy）
│   │   ├── next_cmd.py           # 调度核心
│   │   ├── confirm.py            # 确认/拒绝
│   │   ├── rollback.py           # 回退
│   │   ├── skip.py               # 跳过
│   │   └── deviate.py            # 偏差记录
│   └── message/                  # 消息命令
│       └── write.py              # SubAgent 写入消息
│
├── domain/                       # 领域层：业务概念与规则（最稳定）
│   ├── __init__.py
│   ├── workflow/                 # 工作流定义
│   │   ├── __init__.py
│   │   ├── spec.py               # WorkflowSpec / StageSpec / EdgeSpec / ParallelSpec
│   │   ├── conditions.py         # EdgeCondition / StageStatus / InstanceStatus
│   │   └── parser.py             # YAML → Spec（原 core/schema/v3.py + loader.py）
│   ├── dag/                      # DAG 引擎
│   │   ├── __init__.py
│   │   ├── graph.py              # AdjacencyList / build_adjacency / compute_ready
│   │   ├── topology.py           # Tarjan SCC / analyze_topology / 回边检测
│   │   ├── traversal.py          # collect_downstream / collect_ancestors
│   │   └── validator.py          # DAG 静态验证器（12+ 项检查）
│   └── transition/               # 状态转换策略（单一真相源）
│       ├── __init__.py
│       ├── policy.py             # TransitionPolicy（边处理集中化）
│       └── results.py            # TransitionResult / ConfirmResult / RollbackResult / SkipResult / MergeConfirmResult
│
├── state/                        # 状态层：状态模型与持久化（被 domain / scheduler / cli 共同依赖）
│   ├── __init__.py
│   ├── model.py                  # StageState / InstanceState / StateDelta / CycleMeta
│   ├── persistence.py            # load_instance_state / save_instance_state（原子写入）
│   └── timeline.py               # append_timeline / append_deviation
│
├── scheduler/                    # 调度层：机械调度核心
│   ├── __init__.py
│   ├── orchestrator.py           # SchedulerOrchestrator（Processor 流水线）
│   ├── context.py                # ExecutionContext
│   └── processors/               # 14 个 Processor（编号前缀表示流水线顺序）
│       ├── __init__.py
│       ├── base.py               # Processor 协议 + ProcessorResult
│       ├── 01_sync_worktree.py
│       ├── 02_message_consumer.py
│       ├── 03_virtual_stages.py
│       ├── 04_state_transition.py       # 新增：合并 cycle_meta 到 stages
│       ├── 05_auto_commit.py
│       ├── 06_merge_worktrees.py
│       ├── 07_parallel_split.py
│       ├── 08_child_workflow.py         # 取代 check_children.py
│       ├── 09_error_recovery.py         # 取代 error_handler.py
│       ├── 10_conflict_handler.py
│       ├── 11_ready_compute.py
│       ├── 12_allocate_spawn.py
│       ├── 13_confirm_aggregate.py
│       └── 14_finalize.py
│
├── runtime/                      # 运行时层：Agent、Worktree、Message 资源管理
│   ├── __init__.py
│   ├── agent/
│   │   ├── __init__.py
│   │   └── manager.py            # RunningAgentManager（取代散落函数）
│   ├── worktree/
│   │   ├── __init__.py
│   │   ├── manager.py            # WorktreeManager（创建/合并/清理）
│   │   ├── git.py                # Git 薄封装（原 core/git_ops.py）
│   │   └── sync.py               # sync_worktree / SyncResult
│   └── message/
│       ├── __init__.py
│       ├── handler.py            # write_message / scan_messages
│       └── identity.py           # .wfctl_identity.json 校验
│
├── infrastructure/               # 基础设施层：与 wfctl 业务完全无关的工具
│   ├── __init__.py
│   ├── errors.py                 # 异常体系（WfctlError / StateError / GitError / InputError...）
│   ├── io.py                     # atomic_write_json / atomic_write_text
│   ├── lock.py                   # FileLock（跨平台）
│   ├── logging.py                # 结构化 stderr 日志
│   ├── project.py                # 项目根目录发现
│   └── timestamp.py              # ISO 8601 时间戳
│
├── services/                     # 应用服务层：跨领域编排（大幅瘦身）
│   ├── __init__.py
│   ├── creator.py                # 实例创建（使用 domain + state + runtime）
│   ├── resolver.py               # 工作流发现
│   ├── status_builder.py         # 状态查询（只读）
│   └── validator.py              # 保护区检测
│
└── tests/                        # 测试层：与源码结构严格对齐
    ├── cli/
    │   ├── workflow/
    │   ├── instance/
    │   ├── stage/
    │   └── message/
    ├── domain/
    │   ├── workflow/
    │   ├── dag/
    │   └── transition/
    ├── state/
    ├── scheduler/
    │   └── processors/
    ├── runtime/
    │   ├── agent/
    │   ├── worktree/
    │   └── message/
    ├── infrastructure/
    └── services/
```

---

## 三、重组原则

### 3.1 按依赖方向分层（单向依赖）

```
cli/           ← 最上层，面向用户
  ↓ 依赖
domain/        ← 业务规则，最稳定
  ↓ 依赖
state/         ← 状态模型与持久化
  ↓ 依赖
scheduler/     ← 调度算法
  ↓ 依赖
runtime/       ← 运行时资源
  ↓ 依赖
infrastructure/ ← 最底层，纯工具

services/      ← 横向编排层，可依赖 domain + state + runtime
```

**关键约束**：
- `domain/` 不依赖任何其他层（最纯净）
- `infrastructure/` 不依赖任何上层（可被任意层使用）
- `cli/` 可以依赖所有层，但自身不被任何层依赖

### 3.2 影子状态机统一归宿

所有直接操作 `instance.json` 的 CLI 命令（confirm/rollback/skip/pause/resume/terminate）归入 `cli/stage/`，统一调用 `domain/transition/policy.py`，消除"命令层也有状态机"的碎片化。

### 3.3 状态模型独立提取

`state/` 独立为一级目录，被 `domain/`、`scheduler/`、`cli/` 共同依赖：
- `domain/transition/policy.py` 需要 `StateDelta` 表达变更
- `scheduler/orchestrator.py` 需要 `InstanceState` 驱动流水线
- `cli/stage/confirm.py` 需要 `StateDelta` 应用用户确认

避免 `scheduler/state_model.py` 被非调度模块 import 导致的层级混乱。

### 3.4 运行时资源分离

原 `services/` 大杂烩拆分为三个运行时域：
- `runtime/agent/`：SubAgent 生命周期（running_agents.json）
- `runtime/worktree/`：Git worktree 管理
- `runtime/message/`：消息池读写

### 3.5 基础设施纯粹化

`infrastructure/` 只放与 wfctl 业务无关的工具：
- 锁、原子写入、日志 → 任何 Python 项目都可能有
- Git 操作（`git_ops.py`）移入 `runtime/worktree/git.py`，因为 Git 是 wfctl 的业务依赖（worktree 管理），不是通用基础设施

### 3.6 Processor 编号化

`01_sync_worktree.py` ~ `14_finalize.py` 用两位编号前缀：
- 一眼看出流水线顺序
- 文件排序即执行顺序
- 新增 Processor 时编号自动指示插入位置

### 3.7 测试严格对齐

`tests/` 目录与源码一一对应：
- `tests/domain/dag/test_validator.py` ← `domain/dag/validator.py`
- `tests/scheduler/processors/test_05_auto_commit.py` ← `scheduler/processors/05_auto_commit.py`
- `tests/runtime/agent/test_manager.py` ← `runtime/agent/manager.py`

---

## 四、完整文件映射表

### 4.1 新增文件

| 新文件 | 说明 | 来源 |
|--------|------|------|
| `domain/transition/policy.py` | TransitionPolicy 单一真相源 | 新增（重构方案 §5、§13） |
| `domain/transition/results.py` | 各类状态转换结果类型 | 新增 |
| `domain/dag/topology.py` | Tarjan SCC 拓扑分析 | 新增（重构方案 §6） |
| `domain/dag/traversal.py` | 下游/祖先收集 | 从 `core/dag.py` 拆分 |
| `state/model.py` | StageState / InstanceState / StateDelta / CycleMeta | 从 `services/scheduler/state_model.py` 上移 |
| `state/persistence.py` | load/save_instance_state | 从 `services/state_manager.py` 重构 |
| `state/timeline.py` | append_timeline / append_deviation | 从 `services/state_manager.py` 拆分 |
| `scheduler/processors/04_state_transition.py` | 合并 cycle_meta 到 stages | 新增 |
| `scheduler/processors/08_child_workflow.py` | 子工作流统一路径 | 取代 `check_children.py` |
| `scheduler/processors/09_error_recovery.py` | 错误恢复（基于 TransitionPolicy） | 取代 `error_handler.py` |
| `runtime/agent/manager.py` | RunningAgentManager | 新增 |
| `runtime/worktree/sync.py` | sync_worktree / SyncResult | 从 `worktree_manager.py` 拆分 |
| `runtime/message/identity.py` | .wfctl_identity.json 校验 | 从 `cli/message_write.py` 提取 |

### 4.2 移动文件

| 原文件 | 新文件 | 说明 |
|--------|--------|------|
| `core/schema/interface.py` | `domain/workflow/spec.py` + `domain/workflow/conditions.py` | 拆分数据模型与枚举 |
| `core/schema/v3.py` | `domain/workflow/parser.py` | 重命名 |
| `core/schema/loader.py` | `domain/workflow/parser.py` | 合并到 parser |
| `core/dag.py` | `domain/dag/graph.py` + `domain/dag/traversal.py` | 拆分图引擎与遍历工具 |
| `core/dag_validator.py` | `domain/dag/validator.py` | 重命名 |
| `core/git_ops.py` | `runtime/worktree/git.py` | Git 是业务依赖，非基础设施 |
| `core/atomic_write.py` | `infrastructure/io.py` | 通用工具 |
| `core/lock.py` | `infrastructure/lock.py` | 通用工具 |
| `core/logging.py` | `infrastructure/logging.py` | 通用工具 |
| `core/project.py` | `infrastructure/project.py` | 通用工具 |
| `core/timestamp.py` | `infrastructure/timestamp.py` | 通用工具 |
| `core/errors.py` | `infrastructure/errors.py` | 通用工具 |
| `services/scheduler/state_model.py` | `state/model.py` | 上移 |
| `services/state_manager.py` | `state/persistence.py` + `state/timeline.py` | 拆分 |
| `services/scheduler/orchestrator.py` | `scheduler/orchestrator.py` | 上移 |
| `services/scheduler/context.py` | `scheduler/context.py` | 上移 |
| `services/scheduler/processors/*.py` | `scheduler/processors/*.py` | 平移，编号重命名 |
| `services/worktree_manager.py` | `runtime/worktree/manager.py` | 移入运行时层 |
| `services/message_handler.py` | `runtime/message/handler.py` | 移入运行时层 |
| `cli/confirm.py` | `cli/stage/confirm.py` | 归入 stage 操作 |
| `cli/rollback.py` | `cli/stage/rollback.py` | 归入 stage 操作 |
| `cli/skip.py` | `cli/stage/skip.py` | 归入 stage 操作 |
| `cli/pause.py` | `cli/instance/pause.py` | 归入实例生命周期 |
| `cli/resume.py` | `cli/instance/resume.py` | 归入实例生命周期 |
| `cli/terminate.py` | `cli/instance/terminate.py` | 归入实例生命周期 |
| `cli/status.py` | `cli/instance/status.py` | 归入实例生命周期 |
| `cli/sync.py` | `cli/instance/sync.py` | 归入实例生命周期 |
| `cli/create.py` | `cli/workflow/create.py` | 归入工作流管理 |
| `cli/resolve.py` | `cli/workflow/resolve.py` | 归入工作流管理 |
| `cli/visualize.py` | `cli/workflow/visualize.py` | 归入工作流管理 |
| `cli/cleanup.py` | `cli/workflow/cleanup.py` | 归入工作流管理 |
| `cli/message_write.py` | `cli/message/write.py` | 归入消息命令 |
| `cli/deviate.py` | `cli/stage/deviate.py` | 归入 stage 操作 |
| `cli/next_cmd.py` | `cli/stage/next_cmd.py` | 归入 stage 操作 |
| `cli/identity.py` | `cli/message/identity.py` 或删除 | 功能极小，可归入 message write |
| `cli/restore.py` | `cli/workflow/restore.py` | 归入工作流管理 |

### 4.3 删除文件

| 文件 | 删除原因 |
|------|----------|
| `services/scheduler_legacy.py` | Phase 5 删除，功能已被新 Orchestrator 覆盖 |
| `services/scheduler/processors/consume_messages.py` | 功能合并到 `02_message_consumer.py` |
| `services/scheduler/processors/error_handler.py` | 功能合并到 `09_error_recovery.py` |
| `services/scheduler/processors/check_children.py` | 功能合并到 `08_child_workflow.py` |
| `core/schema/__init__.py` | 目录已合并到 `domain/workflow/` |

---

## 五、迁移路径

目录重组是**破坏性操作**（import 路径全部变更），必须一次性完成。

### 5.1 执行时机

**建议在 Phase 5（最后清理阶段）执行**，理由：
- legacy 代码已删除，文件移动量最小
- 所有新模块已创建，可以一次性放入正确位置
- 测试已全部通过，移动后有明确基线

### 5.2 执行步骤

```bash
# 步骤 1：确保所有测试通过（移动前的基线）
pytest wfctl/tests/ -x

# 步骤 2：创建新目录结构
mkdir -p wfctl/{cli/{workflow,instance,stage,message},domain/{workflow,dag,transition},state,scheduler/processors,runtime/{agent,worktree,message},infrastructure,services}
mkdir -p tests/{cli/{workflow,instance,stage,message},domain/{workflow,dag,transition},state,scheduler/processors,runtime/{agent,worktree,message},infrastructure,services}

# 步骤 3：使用 git mv 移动文件（保留 Git 历史）
git mv core/schema/interface.py       domain/workflow/spec.py
git mv core/schema/v3.py              domain/workflow/parser.py
# ... 其余文件按映射表逐个 git mv ...

# 步骤 4：批量修正 import 路径
# 方法 A：使用 ruff（推荐）
ruff check --select I001 wfctl/ --fix
ruff check --select F401 wfctl/ --fix

# 方法 B：使用 sed 批量替换
# sed -i 's/from core\.dag import/from domain.dag.graph import/g' wfctl/**/*.py
# sed -i 's/from services\.scheduler\.state_model import/from state.model import/g' wfctl/**/*.py

# 步骤 5：运行全部测试验证
pytest wfctl/tests/ -x

# 步骤 6：提交
# git commit -m "refactor: restructure directories for single source of truth"
```

### 5.3 风险与回退

| 风险 | 缓解措施 |
|------|----------|
| import 路径遗漏 | 使用 `ruff check --select F401` 检测未使用的 import，反推遗漏的修正 |
| 循环导入 | 严格遵循单向依赖原则：`domain` → `state` → `scheduler` → `runtime` → `infrastructure` |
| 测试路径失效 | `tests/` 同步重组，pytest 的 `conftest.py` 放在 `tests/` 根目录 |
| IDE 缓存混乱 | 重启 IDE / 清除 `.pytest_cache` / `__pycache__` |

---

## 六、关键设计决策

### Q1：为什么把 `state/` 提升为一级目录，而不是放在 `scheduler/` 下？

因为 `state/` 被三层同时依赖：
- `domain/transition/policy.py` 需要 `StateDelta` 表达变更
- `scheduler/orchestrator.py` 需要 `InstanceState` 驱动流水线
- `cli/stage/confirm.py` 需要 `StateDelta` 应用用户确认

如果 `state/` 放在 `scheduler/` 下，`domain/` 和 `cli/` import `scheduler.state_model` 会造成层级混乱。

### Q2：为什么把 `git_ops.py` 从 `core/` 移到 `runtime/worktree/`？

因为 Git 操作不是通用基础设施——它与 wfctl 的 worktree 管理强耦合（`git worktree add/remove`、`git tag` 的命名规范包含 `{anchor_prefix}-{instance_id}-{stage_id}`）。

真正的基础设施是 `atomic_write`、`FileLock`、`timestamp` 这类任何项目都需要的工具。

### Q3：Processor 编号前缀（`01_`、`02_`）是否会影响 Python import？

不会。Python 模块名可以以数字开头：

```python
from scheduler.processors import 01_sync_worktree  # 不合法！数字不能作为标识符

# 正确做法：在 __init__.py 中映射
# scheduler/processors/__init__.py
from .sync_worktree import SyncWorktreeProcessor
from .message_consumer import MessageConsumerProcessor
# ...
```

编号仅用于**文件系统排序**，模块内部类名保持无前缀：

```python
# scheduler/processors/01_sync_worktree.py
class SyncWorktreeProcessor: ...
```

### Q4：`services/` 瘦身到什么程度？

`services/` 只保留**跨领域编排**逻辑：
- `creator.py`：协调 `domain/`（workflow 解析）+ `state/`（初始状态）+ `runtime/worktree/`（worktree 创建）
- `resolver.py`：工作流发现，不修改状态
- `status_builder.py`：状态聚合，只读
- `validator.py`：保护区检测，只读

所有能下沉到 `domain/`、`state/`、`runtime/` 的逻辑都不留在 `services/`。

---

> **总结**：新目录结构的核心目标是"看一眼目录就知道代码在哪里"。`domain/` 放业务规则，`state/` 放状态模型，`scheduler/` 放调度算法，`runtime/` 放资源管理，`infrastructure/` 放纯工具，`cli/` 按业务域分组。任何新功能都有唯一正确的归宿。
