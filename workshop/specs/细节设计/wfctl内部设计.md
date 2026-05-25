# wfctl 内部设计 v1.1.0

---

## 〇、设计决策汇总

| 维度 | 决策 |
|------|------|
| 包结构 | 六层分层架构：cli → domain → state → scheduler → runtime → infrastructure，services 为横向编排层（瘦身） |
| DAG 引擎 | 邻接表 + BFS 就绪计算 + Tarjan SCC 拓扑分析 |
| 状态转换 | `TransitionPolicy` 单一真相源，所有状态变更经 `StateDelta.apply()` |
| 调度核心 | 14 步 Processor 流水线，`CycleMeta` 显式传递轮内事件 |
| Git 抽象 | 薄封装函数（subprocess.run 包装） |
| CLI 框架 | argparse 标准库，按业务域分组（workflow / instance / stage / message） |
| 状态 IO | 原子写入（tmp + os.replace）+ 文件锁 |
| 错误处理 | `WfctlError` 异常体系 |
| Schema 兼容 | 适配器模式，内部统一规范表示 |
| 测试 | 单测（domain/state/scheduler/infrastructure）+ 集成测试（runtime/services）+ 端到端（cli） |
| Python 版本 | ≥ 3.10 |
| 分发方式 | 由 workflow-env-init Skill 分发至 `.claude/scripts/wfctl/` |
| 日志 | stderr 结构化 JSON，stdout 专用于命令结果 |
| 项目发现 | 从工作目录向上查找 `.claude/` 目录作为项目根 |

---

## 一、包结构

```
.claude/scripts/wfctl/           # 消费者项目中的 wfctl 根目录
├── __init__.py
├── __main__.py                  # python -m wfctl 入口
├── main.py                      # CLI 入口：argparse 注册 + 异常捕获
│
├── cli/                         # 接口层 —— 命令入口，无业务逻辑，按业务域分组
│   ├── __init__.py
│   ├── main.py                  # argparse 注册 + 全局异常捕获
│   ├── workflow/                # 工作流管理命令
│   │   ├── create.py            # create 命令
│   │   ├── resolve.py           # resolve 命令
│   │   ├── visualize.py         # visualize 命令
│   │   └── cleanup.py           # cleanup 命令
│   ├── instance/                # 实例生命周期命令
│   │   ├── status.py            # status 命令
│   │   ├── sync.py              # sync 命令
│   │   ├── pause.py             # pause 命令
│   │   ├── resume.py            # resume 命令
│   │   └── terminate.py         # terminate 命令
│   ├── stage/                   # Stage 操作命令（全部调用 TransitionPolicy）
│   │   ├── next_cmd.py          # next 命令
│   │   ├── confirm.py           # confirm 命令
│   │   ├── rollback.py          # rollback 命令
│   │   ├── skip.py              # skip 命令
│   │   └── deviate.py           # deviate 命令
│   └── message/                 # 消息命令
│       └── write.py             # message write 命令
│
├── domain/                      # 领域层：业务概念与规则（最稳定，不依赖其他层）
│   ├── __init__.py
│   ├── workflow/                # 工作流定义
│   │   ├── spec.py              # WorkflowSpec / StageSpec / EdgeSpec / ParallelSpec
│   │   ├── conditions.py        # EdgeCondition / StageStatus / InstanceStatus
│   │   └── parser.py            # YAML → Spec（原 core/schema/v3.py + loader.py）
│   ├── dag/                     # DAG 引擎
│   │   ├── graph.py             # AdjacencyList / build_adjacency / compute_ready
│   │   ├── topology.py          # Tarjan SCC / analyze_topology / 回边检测
│   │   ├── traversal.py         # collect_downstream / collect_ancestors
│   │   └── validator.py         # DAG 静态验证器（15+ 项检查）
│   └── transition/              # 状态转换策略（单一真相源）
│       ├── policy.py            # TransitionPolicy（边处理集中化）
│       └── results.py           # TransitionResult / ConfirmResult / RollbackResult / SkipResult / MergeConfirmResult
│
├── state/                       # 状态层：状态模型与持久化（被 domain / scheduler / cli 共同依赖）
│   ├── __init__.py
│   ├── model.py                 # StageState / InstanceState / StateDelta / CycleMeta
│   ├── persistence.py           # load_instance_state / save_instance_state（原子写入 + v2→v3 自动迁移）
│   └── timeline.py              # append_timeline / append_deviation
│
├── scheduler/                   # 调度层：机械调度核心
│   ├── __init__.py
│   ├── orchestrator.py          # SchedulerOrchestrator（14 步 Processor 流水线）
│   ├── context.py               # ExecutionContext
│   └── processors/              # 14 个 Processor（编号前缀表示流水线顺序）
│       ├── __init__.py
│       ├── base.py              # Processor 协议 + ProcessorResult
│       ├── 01_sync_worktree.py
│       ├── 02_message_consumer.py
│       ├── 03_virtual_stages.py
│       ├── 04_state_transition.py       # 合并 cycle_meta 到 stages
│       ├── 05_auto_commit.py
│       ├── 06_merge_worktrees.py
│       ├── 07_parallel_split.py
│       ├── 08_child_workflow.py         # 递归调度子工作流
│       ├── 09_error_recovery.py         # 基于 TransitionPolicy 的错误恢复
│       ├── 10_conflict_handler.py
│       ├── 11_ready_compute.py
│       ├── 12_allocate_spawn.py
│       ├── 13_confirm_aggregate.py
│       └── 14_finalize.py
│
├── runtime/                     # 运行时层：Agent、Worktree、Message 资源管理
│   ├── __init__.py
│   ├── agent/
│   │   ├── __init__.py
│   │   └── manager.py           # RunningAgentManager（取代散落函数）
│   ├── worktree/
│   │   ├── __init__.py
│   │   ├── manager.py           # WorktreeManager（创建/合并/清理）
│   │   ├── git.py               # Git 薄封装（原 core/git_ops.py）
│   │   └── sync.py              # sync_worktree / SyncResult
│   └── message/
│       ├── __init__.py
│       ├── handler.py           # write_message / scan_messages
│       └── identity.py          # .wfctl_identity.json 校验
│
├── infrastructure/              # 基础设施层：与 wfctl 业务完全无关的工具
│   ├── __init__.py
│   ├── errors.py                # 异常体系（WfctlError / StateError / GitError / InputError...）
│   ├── io.py                    # atomic_write_json / atomic_write_text
│   ├── lock.py                  # FileLock（跨平台）
│   ├── logging.py               # 结构化 stderr 日志
│   ├── project.py               # 项目根目录发现
│   └── timestamp.py             # ISO 8601 时间戳
│
├── services/                    # 应用服务层：跨领域编排（大幅瘦身）
│   ├── __init__.py
│   ├── creator.py               # 实例创建（使用 domain + state + runtime）
│   ├── resolver.py              # 工作流发现
│   ├── status_builder.py        # 状态查询（只读）
│   └── validator.py             # 保护区检测
│
└── tests/                       # 测试层：与源码结构严格对齐
    ├── __init__.py
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

### 依赖方向

```
cli           ← 最上层，面向用户
  ↓ 依赖
domain        ← 业务规则，最稳定，不依赖任何其他层
  ↓ 依赖
state         ← 状态模型与持久化
  ↓ 依赖
scheduler     ← 调度算法
  ↓ 依赖
runtime       ← 运行时资源
  ↓ 依赖
infrastructure ← 最底层，纯工具

services      ← 横向编排层，可依赖 domain + state + runtime
```

**关键约束**：
- `domain/` 不依赖任何其他层（最纯净）
- `infrastructure/` 不依赖任何上层（可被任意层使用）
- `cli/` 可以依赖所有层，但自身不被任何层依赖
- `services/` 只保留跨领域编排逻辑，所有能下沉到 `domain/`、`state/`、`runtime/` 的逻辑都不留在 `services/`

### 消费者项目中的位置

```
<项目根>/
├── .claude/
│   └── scripts/
│       └── wfctl/              # ← wfctl 包，由 workflow-env-init 分发
│           └── ...
├── .agent/
│   └── ...
└── .tmp/
    └── worktrees/
        └── ...
```

wfctl 的运行方式：

```bash
# 项目根目录下执行
python -m wfctl <command> [options]
```

`project.py` 从 cwd 向上查找 `.claude/` 确定项目根，所有路径操作相对于项目根。

---

## 二、Schema 适配器

### 2.1 设计动机

- WORKFLOW.yaml 的 `schema_version` 会随协议演进迭代
- wfctl 内部只认一种规范表示，不散落版本分支逻辑
- 新增版本只需新增适配器

### 2.2 内部规范表示（`domain/workflow/spec.py` + `domain/workflow/conditions.py`）

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class StageStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    DONE = "DONE"
    ERROR = "ERROR"
    CONFLICT = "CONFLICT"

class InstanceStatus(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class EdgeCondition(Enum):
    ALWAYS = "always"
    SUCCESS = "success"
    FAILURE = "failure"
    LOOP_EXCEEDED = "loop_exceeded"

class StageTargetType(Enum):
    SKILL = "skill"
    WORKFLOW = "workflow"
    VIRTUAL = "virtual"

@dataclass
class ParallelSpec:
    source: str              # 上游 stage_id
    max_instances: Optional[int] = None

@dataclass
class StageSpec:
    stage_id: str
    name: str
    target_type: StageTargetType
    target: Optional[str]        # skill_id 或 workflow 引用
    mandatory: bool
    retry: int                   # 默认 0
    timeout_seconds: Optional[int]
    model: Optional[str]
    exclusive: bool
    parallel: Optional[ParallelSpec]

@dataclass
class EdgeSpec:
    from_stage: str
    to_stage: str
    condition: EdgeCondition
    max_loop: Optional[int]
    choice: Optional[str]
    aggregation: str             # "all" | "any"
    cascade_reset_until: Optional[str] = None

@dataclass
class WorkflowSpec:
    schema_version: str
    workflow_id: str
    version: str
    max_parallel_agents: int
    anchor_prefix: str
    stages: list[StageSpec]
    edges: list[EdgeSpec]
```

### 2.3 适配器接口与加载流程

```python
# domain/workflow/parser.py

def load_workflow(yaml_path: Path) -> WorkflowSpec:
    """读取 WORKFLOW.yaml，按 schema_version 选择适配器，返回 WorkflowSpec。"""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    version = raw["schema_version"]
    adapter = _get_adapter(version)     # 按版本号匹配适配器
    return adapter.parse(raw)

# domain/workflow/parser.py（原 v3.py + loader.py 合并）

class V3Adapter:
    """schema_version "3.0.0" 适配器"""
    def parse(self, raw: dict) -> WorkflowSpec:
        # 校验必填字段
        # 转换为 WorkflowSpec + StageSpec + EdgeSpec
        ...
```

新增 `v4.py` 时，只需实现相同的 `parse(raw) -> WorkflowSpec` 接口，在 `_get_adapter` 中注册即可。services 层完全无感。

---

## 三、DAG 引擎（`domain/dag/`）

### 3.1 数据结构

```python
@dataclass
class AdjacencyList:
    """邻接表：stage_id → 从该 stage 出发的所有 EdgeSpec"""
    outgoing: dict[str, list[EdgeSpec]]    # key → 出发边
    incoming: dict[str, list[EdgeSpec]]    # key → 到达边（反向索引，加速查上游）
    stages: dict[str, StageSpec]           # stage_id → StageSpec
```

### 3.2 构建

```python
def build_adjacency(spec: WorkflowSpec) -> AdjacencyList:
    """解析 WorkflowSpec，构建 outgoing + incoming 双索引。"""
```

### 3.3 `next` 就绪计算核心算法

```python
# domain/dag/graph.py

def compute_ready(adj: AdjacencyList, state: InstanceState) -> list[tuple[str, str]]:
    """计算就绪 stage，返回 [(stage_id, stage_instance_id), ...]。"""
    ready: list[tuple[str, str]] = []
    for st in state.stages:
        if st.status != StageStatus.PENDING:
            continue
        upstream = adj.incoming.get(st.stage_id, [])
        if _all_satisfied(upstream, state, st.stage_id):
            ready.append((st.stage_id, st.stage_instance_id))
    return ready

def _all_satisfied(
    upstream_edges: list[EdgeSpec],
    state: InstanceState,
    stage_id: str,
) -> bool:
    """检查上游边是否满足（OR 语义）。使用 InstanceState 原生接口，不再操作 dict。"""
    if not upstream_edges:
        return True
    for edge in upstream_edges:
        upstream = state.stage_by_id(edge.from_stage)
        if not upstream or upstream.status != StageStatus.DONE:
            continue
        exit_cond = upstream.exit_condition
        if edge.condition == EdgeCondition.ALWAYS:
            return True
        if edge.condition == EdgeCondition.SUCCESS:
            if exit_cond == "loop_exceeded":
                continue
            if edge.choice:
                if upstream.routing_choice == edge.choice:
                    return True
                continue
            return True
    return False
```

### 3.4 拓扑分析（`domain/dag/topology.py`）

```python
@dataclass(frozen=True)
class TopologyResult:
    order: list[str]                    # 拓扑序（SCC 压缩后）
    cycles: list[list[str]]             # 所有环（SCC 大小 > 1 或自环）
    back_edges: list[EdgeSpec]          # 回边列表

def analyze_topology(adj: AdjacencyList) -> TopologyResult:
    """Tarjan SCC 算法。"""
```

### 3.5 下游遍历（回退 / 级联清理）

```python
# domain/dag/traversal.py

def collect_downstream(adj: AdjacencyList, stage_id: str,
                       exclude_conditions: set[EdgeCondition]) -> set[str]:
    """BFS 从 stage_id 出发，沿 edges 遍历所有可达 stage，
       排除指定 condition 的边（如 failure、loop_exceeded）。
       返回受影响 stage_id 集合。"""
```

### 3.6 关键点

- 虚拟 stage（`s00-workflow-start`、`s99-workflow-end`）由 wfctl 内部处理，不分配 worktree，不生成 action
- `aggregation=any` 的 parallel 拆分：任一实例 DONE 即解锁下游，其余实例被标记为 SUPERSEDED（写入 stage_history，不参与流转）
- `exclusive` 的调度约束在 scheduler 层处理，dag 只管就绪判断

### 3.7 DAG 验证器（`domain/dag/validator.py`）

验证器在 `load_workflow()` 时自动调用，基于 `analyze_topology()` 的拓扑分析结果执行 15 项静态检查：

| # | 检查项 | 说明 | 严重度 |
|---|--------|------|--------|
| 1 | 自环无 `max_loop` | 自环（`from == to`）的 FAILURE 边必须设置 `max_loop > 0` | ERROR |
| 2 | 多节点环无 `max_loop` | 多节点环上至少有一条边设置 `max_loop > 0` | ERROR |
| 3 | 回边无 `max_loop` | 回边（from 拓扑序 > to 拓扑序）必须设置 `max_loop > 0` | ERROR |
| 4 | `failure_edge` 指向不存在 stage | FAILURE 边的 `to_stage` 必须在 `stages` 中存在 | ERROR |
| 5 | `cascade_reset_until` 指向不存在 stage 或非祖先 | `cascade_reset_until` 必须存在且是 `from_stage` 的祖先（或自身） | ERROR |
| 6 | (已废弃) `confirmation_point` 字段残留 | confirmation_point 字段不再使用，确认是 Skill 内部 AskUserQuestion 行为 | ERROR |
| 7 | SUCCESS 边 choice 不完备 | 多条 SUCCESS 边时，要么全部设置 `choice`，要么全部不设置 | ERROR |
| 8 | SUCCESS 边 choice 重复 | 同一 stage 的多条 SUCCESS 边 `choice` 值必须互斥 | ERROR |
| 9 | SUCCESS 边 choice 混用 | 多条 SUCCESS 边时，不允许部分有 `choice`、部分无 `choice` | ERROR |
| 10 | (合并到 #9) | 同 #9 | - |
| 11 | `failure_edge` 但 `retry=0` | 有 FAILURE 边但 `StageSpec.retry == 0`，failure_edge 永远不会触发 | WARNING |
| 12 | `loop_exceeded_edge` 但无 `failure_edge` | LOOP_EXCEEDED 边存在但无 FAILURE 边，逻辑不连贯 | WARNING |
| 13 | parallel fan-in 一致性 | parallel 拆分的多个实例必须能正确 fan-in 到同一下游 stage | ERROR |
| 14 | 终态 stage 有非 ALWAYS 出边 | DONE 后不应有条件判断，终态 stage 的出边只能是 ALWAYS | WARNING |
| 15 | 歧义路由（多条 choice 边 + 兜底边） | 存在多条有 choice 的边时，若同时存在无 choice 的兜底边，路由逻辑模糊 | WARNING |

---

## 四、状态管理（`state/`）

### 4.1 instance.json 路径

消费者项目中，采用 v3 目录式布局：
```
.agent/instances/<instance_id>/
├── instance.json              # 状态机
├── messages/                  # 消息池
│   └── <message_id>.json
├── logs/
│   ├── deviation.jsonl
│   ├── stage_history.jsonl
│   └── timeline.jsonl
└── children/
    └── <child_id>/            # 子工作流实例（同上结构）
```

兼容读取：若目标路径不存在，回退扫描 `.agent/workflows/instances/<id>.json`（v2 平铺式）和 `.agent/messages/<YYYY-MM-DD>/<message_id>.json`（v2 消息路径），确保旧实例不丢失。写入始终使用 v3 目录式。

### 4.2 数据兼容层（`state/persistence.py`）

```python
class DataVersion(Enum):
    V2 = "2.0.0"
    V3 = "3.0.0"

@dataclass(frozen=True)
class InstanceDataAdapter:
    raw: dict[str, Any]
    declared_version: DataVersion

    @classmethod
    def from_file(cls, path: Path) -> "InstanceDataAdapter": ...
    def to_standard(self) -> dict[str, Any]: ...
    def _migrate_v2_to_v3(self) -> dict[str, Any]: ...

def load_instance_state(instance_id: str) -> InstanceState: ...
def save_instance_state(instance_id: str, state: InstanceState) -> None: ...
```

**关键行为**：
- v2 实例首次加载时自动迁移到 v3 路径，删除旧文件
- 适配器只读，不写回旧格式
- `save_instance_state` 永远输出 v3 格式

### 4.3 状态模型（`state/model.py`）

```python
@dataclass(frozen=True)
class CycleMeta:
    """本次调度周期内的临时状态，用于 Processor 间显式通信。不写入 instance.json，仅在单次 next 调用内有效。"""
    newly_done_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_error_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_awaiting_confirm_ids: frozenset[str] = field(default_factory=frozenset)
    ready_candidates: list[tuple[str, str]] = field(default_factory=list)

@dataclass(frozen=True)
class StateDelta:
    stage_updates: dict[str, dict[str, Any]] = field(default_factory=dict)  # stage_instance_id → {字段: 新值}
    instance_updates: dict[str, Any] = field(default_factory=dict)
    append_stages: list[StageState] = field(default_factory=list)
    remove_stage_instance_ids: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class InstanceState:
    # ... 现有字段 ...
    cycle_meta: CycleMeta = field(default_factory=lambda: CycleMeta())

    def stages_by_id(self, stage_id: str) -> list[StageState]: ...
    def first_stage_by_id(self, stage_id: str) -> StageState | None: ...
    def stage_by_instance_id(self, stage_instance_id: str) -> StageState | None: ...
    def apply_delta(self, delta: StateDelta) -> "InstanceState": ...
```

### 4.4 原子写入

```python
# infrastructure/io.py

def atomic_write_json(path: Path, data: dict) -> None:
    """写入临时文件后 os.replace，保证写入原子性。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # Windows/Unix 均为原子操作
```

### 4.5 文件锁

```python
# infrastructure/lock.py

class FileLock:
    """跨平台互斥锁。

    实现：在目标文件旁创建 .lock 文件，写入 "pid:timestamp"。
    获取前检查 pid 是否存活，死则抢锁。不依赖 fcntl.flock。
    """
    def __init__(self, path: Path): ...
    def acquire(self, timeout: float = 10.0) -> bool: ...
    def release(self) -> None: ...
```

使用场景：`next` 对同一实例加锁，防止主 Agent 并发调用。

### 4.6 消息消费

消息消费由 `scheduler/processors/02_message_consumer.py` 的 `MessageConsumerProcessor` 实现，纯函数，零副作用：

```python
@dataclass
class MessageConsumerProcessor:
    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        # 1. 扫描消息目录
        # 2. 按 stage_instance_id 定位 stage
        # 3. 校验 routing_choice（使用 TransitionPolicy.validate_routing_choice）
        # 4. 生成 StateDelta（标记 cycle_meta.newly_done / newly_error / newly_awaiting_confirm）
        # 5. 幂等：状态无变化时只消费消息 ID，不覆盖已有字段
```

---

## 五、Git 操作（`runtime/worktree/git.py`）

### 5.1 设计原则

- 纯函数，每个函数封装一个 git 命令
- 统一返回 `(returncode: int, stdout: str, stderr: str)`
- 不记录日志（日志由调用方 services 层写入 timeline）
- 不抛异常（调用方根据 returncode 决定是否抛 GitError）

### 5.2 函数清单

```python
def git_worktree_add(path: Path, base_ref: str, branch: Optional[str] = None) -> tuple[int, str, str]: ...
def git_worktree_remove(path: Path, force: bool = False) -> tuple[int, str, str]: ...
def git_worktree_list(repo_root: Path) -> tuple[int, str, str]: ...
def git_fetch(repo: Path, source: Path, refspec: str) -> tuple[int, str, str]: ...
def git_merge(repo: Path, ref: str, no_ff: bool = True) -> tuple[int, str, str]: ...
def git_checkout(repo: Path, ref: str) -> tuple[int, str, str]: ...
def git_tag(repo: Path, tag_name: str, ref: str = "HEAD") -> tuple[int, str, str]: ...
def git_tag_delete(repo: Path, tag_name: str) -> tuple[int, str, str]: ...
def git_status_porcelain(repo: Path) -> tuple[int, str, str]: ...
def git_merge_base(repo: Path, ref_a: str, ref_b: str) -> tuple[int, str, str]: ...
def git_rev_parse(repo: Path, ref: str) -> tuple[int, str, str]: ...
def git_add_all(repo: Path) -> tuple[int, str, str]: ...
def git_commit_file(repo: Path, message_file: Path) -> tuple[int, str, str]: ...
```

### 5.3 调用方使用模式

```python
rc, stdout, stderr = git_worktree_add(path, "HEAD", branch="wf-stage-xxx-xxx")
if rc != 0:
    raise GitError(f"worktree add failed: {stderr}")
```

---

## 六、调度核心（`scheduler/`）

### 6.0 `TransitionPolicy` 详细设计（`domain/transition/policy.py`）

`TransitionPolicy` 是 Stage 出边策略的单一真相源，所有边处理逻辑集中于此。每个 Processor 和 CLI 命令通过 `TransitionPolicy.from_adjacency(adj, stage_id)` 获取策略对象，调用对应方法做纯决策。

#### 三种确认模式

`on_confirm()` 内部根据 edge 类型和 stage 配置，区分三种确认模式：

| 模式 | 触发条件 | 状态转换 | 关键行为 |
|------|----------|----------|----------|
| (已废弃) Confirmation Point | 确认现在是 Skill 内部 AskUserQuestion 行为 | - | 工作流定义不再区分确认类型 |
| **confirm + continue** | SubAgent 上报 AWAITING_CONFIRM，用户确认 | `AWAITING_CONFIRM → PENDING` | `loop_counter++`，`pending_choice=choice`，SubAgent 通过 continue action 继续 |
| **DONE + routing_choice** | SubAgent 完成工作后上报 DONE + routing_choice | `RUNNING → DONE` | 匹配 SUCCESS 边，解锁下游 |
| **loop_exceeded** | confirm 循环次数达到 `loop_exceeded_edge.max_loop` | `AWAITING_CONFIRM → DONE` | 强制退出循环，激活 loop_exceeded 目标 stage |

(已废弃) 确认现在是 Skill 内部行为：SubAgent 直接 AskUserQuestion → AWAITING_CONFIRM → confirm → continue，多轮确认由 Skill 内部控制，不需工作流定义参与。

#### 选择边统一接口

```python
class TransitionPolicy:
    def match_success_edge(self, routing_choice: str | None) -> EdgeSpec | None:
        """根据 SubAgent 上报的 routing_choice 匹配 SUCCESS 边。
        精确匹配 choice → 无匹配时返回无 choice 的兜底 edge → 无兜底返回 None。"""

    # match_confirmed_edge 已删除——确认不再匹配边
        

    # match_rejected_edge 已删除——确认不再匹配边
        

    def validate_routing_choice(self, routing_choice: str | None) -> tuple[bool, str]:
        """校验 SubAgent 上报的 routing_choice 是否合法。
        返回 (is_valid, error_message)。该 stage 无 SUCCESS choice 边时任何值都合法。"""
```

#### 级联重置

```python
class TransitionPolicy:
    def compute_cascade_reset(
        self, state: InstanceState, from_stage_id: str, to_stage_id: str, spec: WorkflowSpec
    ) -> CascadeResetResult:
        """计算回边级联重置范围。

        从 to_stage_id 出发 BFS 遍历下游（排除 failure/loop_exceeded），
        直到遇到 from_stage_id 或 cascade_reset_until 指定的上限 stage。
        返回需要重置的 stage_instance_ids、需要移除的 stage_instance_ids、
        需要清理 running_agents 的 stage_ids。
        """
```

#### `__merge__` 伪 stage

```python
class TransitionPolicy:
    def build_merge_stage(self, instance_id: str, goal: str) -> StageState:
        """创建 __merge__ 伪 stage（status=AWAITING_CONFIRM）。"""

    def on_merge_confirm(self, state: InstanceState, choice: str) -> MergeConfirmResult:
        """处理 __merge__ 确认。choice 为 yes/y/confirm/accept/ok 时 merge_confirmed=true。"""
```

---

### 6.1 Processor 流水线

`next` 命令由 `SchedulerOrchestrator` 驱动 14 个 Processor 顺序执行：

```
01. SyncWorktreeProcessor          # 同步 worktree，检测冲突
02. MessageConsumerProcessor        # 消费消息，标记 cycle_meta
03. VirtualStagesProcessor          # 虚拟 stage 直通，标记 cycle_meta
04. StateTransitionProcessor        # 将 cycle_meta 事件应用到 stages（单一状态变更点）
05. AutoCommitProcessor             # 扫描 cycle_meta.newly_done，执行 git commit
06. MergeWorktreesProcessor         # 扫描 cycle_meta.newly_done，合并 stage worktree
07. ParallelSplitProcessor          # 检查 parallel 需求，拆分
08. ChildWorkflowProcessor          # 子工作流：检查完成 + 创建 + 递归调度
09. ErrorRecoveryProcessor          # 错误恢复（基于 TransitionPolicy.on_error()）
10. ConflictResolveProcessor        # 冲突处理
11. ReadyComputeProcessor           # 就绪计算，写入 cycle_meta.ready_candidates
12. AllocateSpawnProcessor          # 读 ready_candidates，分配 worktree
13. ConfirmAggregateProcessor       # AWAITING_CONFIRM 聚合
14. FinalizeProcessor               # 收尾：终态检测、merge_to_main、清理
```

Processor 间通过 `state.cycle_meta` 显式通信，不再使用 `ctx.extra` 隐式依赖。

### 6.2 action 生成

每个 Processor 返回的 action 追加到数组中，最终返回批量数组。主 Agent 按并发规则批量启动 SubAgent。

```python
def _apply_scheduling_constraints(ready: list[str], running: list[StageState],
                                   spec: WorkflowSpec) -> list[str]:
    """应用 exclusive 和 max_parallel_agents 约束，过滤就绪列表。"""
    # 有 exclusive RUNNING → 过滤掉所有就绪 stage（返回空列表）
    # running 数达 max_parallel_agents → 过滤，等待下一轮
    ...
```

### 6.3 自动提交

SubAgent 不自行执行 git 操作。wfctl 在消费 DONE 消息后，自动将 worktree 中的变更提交为 git commit。

```python
# AutoCommitProcessor（scheduler/processors/05_auto_commit.py）
# 从 state.cycle_meta.newly_done_stage_instance_ids 读取本轮新完成的 stage
# 对每个 stage：在其 worktree 中自动提交
```

**提交信息格式**：

```
<report>

wf-stage: <stage_id>
wf-instance: <instance_id>
wf-message: <message_id>
```

- `report` 由 SubAgent 按 conventional commit subject 行格式书写（如 `feat(s03): 完成选题分析`）
- wfctl 附加三条 trailer，提供完整的可追溯链

**提交时机**：

| 场景 | 时机 | 所在 worktree |
|------|------|-------------|
| 非并发 stage | DONE 后立即提交 | 实例 worktree |
| 并发 stage | DONE 后、合并前提交 | stage worktree（提交后其临时分支有完整历史，fetch + merge --no-ff 才能正常工作） |

### 6.4 worktree 分配逻辑

```python
# AllocateSpawnProcessor（scheduler/processors/12_allocate_spawn.py）

def _allocate_worktrees(ready: list[tuple[str, str]], running: list[StageState],
                         spec: WorkflowSpec) -> dict[str, Path]:
    """为每个就绪 stage 分配 worktree 路径。

    单 stage 就绪 → 实例 worktree（不拆分）
    多 stage 并发就绪 → 每个 stage 独立的 stage 级 worktree
    parallel 拆分 → stage-<id>-<s_id>#<n>
    """
```

### 6.5 子工作流统一路径

`ChildWorkflowProcessor`（08）取代旧 `CheckChildrenProcessor`：

1. 检查 RUNNING 子实例的完成状态（COMPLETED → 父 stage DONE；FAILED → 父 stage ERROR）
2. 创建新的子实例（PENDING WORKFLOW stage）
3. 递归调度活跃子实例：调用 `SchedulerOrchestrator.run(child_ctx, child_state)`
4. 子实例的 await action 不传播到父实例
5. 递归后二次检查

不再调用 `_run_next_inner`（legacy 路径已删除）。

---

## 七、CLI 层（`cli/`）

### 7.1 入口（`cli/main.py`）

```python
import argparse
import sys
from infrastructure.errors import WfctlError
from infrastructure.logging import log_error

def main():
    parser = argparse.ArgumentParser(prog="wfctl", description="工作流机械调度程序")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 按业务域分组注册各子命令
    _register_workflow_commands(subparsers)   # create, resolve, visualize, cleanup
    _register_instance_commands(subparsers)   # status, sync, pause, resume, terminate
    _register_stage_commands(subparsers)      # next, confirm, rollback, skip, deviate
    _register_message_commands(subparsers)    # write

    args = parser.parse_args()

    try:
        result = args.handler(args)          # 每个子命令返回 dict 或 JSON
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    except WfctlError as e:
        log_error(e)
        json.dump({"status": "error", "error": str(e), "code": e.code},
                  sys.stderr, indent=2)
        sys.exit(e.exit_code)
```

### 7.2 CLI 命令统一模式

所有直接操作 `instance.json` 的 CLI 命令（confirm/rollback/skip/pause/resume/terminate）遵循统一模式：

```python
def _handle_command(args):
    state = load_instance_state(args.instance)           # 1. 加载状态
    result = policy.on_xxx(state, args.stage)            # 2. 纯决策（TransitionPolicy）
    new_state = state.apply_delta(result.delta)          # 3. 纯状态转换（StateDelta）
    # 4. 副作用区（Git、文件、running_agents 清理）
    save_instance_state(args.instance, new_state)        # 5. 保存状态
```

| 命令 | 调用 TransitionPolicy |
|------|----------------------|
| `cli/stage/confirm.py` | `TransitionPolicy.on_confirm()` |
| `cli/stage/rollback.py` | `TransitionPolicy.on_rollback()` + Git 副作用分离 |
| `cli/stage/skip.py` | `TransitionPolicy.on_skip()` + `tag_anchor` 副作用 |
| `cli/instance/pause.py` | `TransitionPolicy.on_pause()` |
| `cli/instance/resume.py` | `TransitionPolicy.on_resume()` |
| `cli/instance/terminate.py` | `StateDelta` + `WorktreeManager.terminate_instance()` 副作用事务 |

### 7.3 子命令注册示例

```python
# cli/stage/next_cmd.py
def register_next(subparsers):
    p = subparsers.add_parser("next", help="调度核心：消费消息，推进状态，返回 action")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_next)

def _handle_next(args) -> dict:
    from scheduler.orchestrator import SchedulerOrchestrator
    return SchedulerOrchestrator().run_next(args.instance)
```

### 7.4 命令参数一览

| 命令 | 必填参数 | 可选参数 |
|------|---------|---------|
| `resolve` | — | `--workflow <id>@<ver>` |
| `create` | `--workflow <id>@<ver>` | `--goal "..."` |
| `next` | `--instance <id>` | — |
| `sync` | `--instance <id>` | — |
| `confirm` | `--instance <id> --stage <id> --choice <value>` | `--feedback "..."` |
| `rollback` | `--instance <id> --stage <id>` | — |
| `skip` | `--instance <id> --stage <id>` | `--force` |
| `pause` | `--instance <id>` | — |
| `resume` | `--instance <id>` | — |
| `status` | — | `--instance <id>` |
| `deviate` | `--instance <id> --type <type> --reason "..."` | `--stage <id> --files ...` |
| `terminate` | `--instance <id>` | — |
| `identity` | — | — |
| `message` `write` | `--instance <id> --stage <id> --status <status> --report "..."` | `--checkpoint "..." --questions ... --parallel-targets ...` |

---

## 八、异常体系（`infrastructure/errors.py`）

```python
class WfctlError(Exception):
    """wfctl 异常基类。"""
    code: str          # 机器可读错误码
    exit_code: int     # 进程退出码
    def __init__(self, message: str, code: str, exit_code: int = 1): ...

class StateError(WfctlError):
    """状态文件异常——instance.json 损坏、字段缺失、状态不一致。"""
    # code: "STATE_CORRUPTED" / "STATE_INCONSISTENT" / "STATE_LOCKED"

class WorktreeError(WfctlError):
    """Worktree 操作异常——创建失败、合并冲突、残留清理失败。"""
    # code: "WORKTREE_CREATE_FAILED" / "WORKTREE_MERGE_CONFLICT" / "WORKTREE_ORPHAN"

class SchemaError(WfctlError):
    """WORKFLOW.yaml 解析异常——格式错误、必填字段缺失、版本不支持。"""
    # code: "SCHEMA_PARSE_ERROR" / "SCHEMA_VERSION_UNSUPPORTED" / "SCHEMA_VALIDATION_ERROR"

class ValidationError(WfctlError):
    """校验异常——保护区触碰、权限越界、消息字段非法。"""
    # code: "ACCESS_VIOLATION" / "INVALID_MESSAGE" / "IDENTITY_MISMATCH"

class GitError(WfctlError):
    """git 操作异常——命令失败、仓库损坏。"""
    # code: "GIT_COMMAND_FAILED" / "GIT_REPO_CORRUPTED"

class InputError(WfctlError):
    """用户输入异常——参数非法、引用不存在。"""
    # code: "INVALID_ARGUMENT" / "WORKFLOW_NOT_FOUND" / "STAGE_NOT_FOUND"
    exit_code = 2
```

### 使用原则

- services 层遇到错误直接 `raise`，不返回错误字典
- cli 层 `main.py` 统一捕获 `WfctlError`，映射到退出码和 stderr JSON
- 未预期的 `Exception` 一律视为 wfctl 内部 bug，`exit_code = 1`，stderr 输出完整 traceback（开发期）/ 摘要（发布期）

---

## 九、日志系统（`infrastructure/logging.py`）

### 9.1 两层日志分离

| 层 | 去向 | 格式 | 写入者 |
|----|------|------|--------|
| 运行日志 | stderr | JSON 一行一条 | wfctl 自身 |
| 审计日志 | `.agent/` 下 timeline.jsonl / deviation.jsonl | JSON 一行一条 | wfctl（业务数据） |

### 9.2 运行日志

```python
import json, sys, time

def log(level: str, message: str, **kwargs):
    """写一条结构化日志到 stderr。"""
    entry = {"ts": time.time(), "level": level, "msg": message, **kwargs}
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)
```

不引入 logging 标准库——wfctl 是无状态 CLI，不需要 logger 层级、handler、formatter 的复杂度。一行 `log()` 函数足够。

### 9.3 审计日志

审计日志（timeline、deviation）是 wfctl 业务数据的一部分，由 `state/timeline.py` 在状态变更时追加写入。不走 stderr。

---

## 十、测试策略

### 10.1 单元测试

| 模块 | 测试要点 |
|------|---------|
| `domain/dag/graph.py` | 邻接表构建正确性、单链/多分支/parallel 的就绪判断、下游 BFS 遍历、aggregation=any 行为 |
| `domain/dag/topology.py` | Tarjan SCC、自环检测、回边识别 |
| `domain/dag/validator.py` | 15+ 检查项覆盖 |
| `domain/transition/policy.py` | 决策表覆盖（ALWAYS/SUCCESS/FAILURE/LOOP_EXCEEDED/SUCCESS + choice）、on_confirm / on_rollback / on_skip |
| `domain/workflow/parser.py` | 正常 YAML 解析、必填字段缺失报错、完整示例文件 |
| `state/model.py` | CycleMeta 不序列化、StateDelta apply、查询接口 |
| `state/persistence.py` | v2 实例加载迁移、v3 加载/保存、迁移后旧文件删除 |
| `infrastructure/io.py` | 正常写入、崩溃残留 .tmp 文件、并发写入安全 |
| `infrastructure/lock.py` | 获取-释放、超时、死 pid 抢锁 |
| `infrastructure/errors.py` | 异常 code/exit_code 映射 |

纯 Python，不依赖 git 或文件系统以外的外部资源。

### 10.2 集成测试

使用 `pytest` + `conftest.py` 提供 fixtures：

```python
# tests/conftest.py

@pytest.fixture
def temp_git_repo(tmp_path):
    """创建临时 git 仓库（含 .claude/ 目录结构），返回项目根 Path。"""
    ...

@pytest.fixture
def sample_workflow_yaml(temp_git_repo):
    """在临时仓库中写入示例 WORKFLOW.yaml，返回路径。"""
    ...

@pytest.fixture
def sample_instance_json(temp_git_repo):
    """写入示例 instance.json，返回路径。"""
    ...
```

测试要点：

| 模块 | 测试要点 |
|------|---------|
| `services/resolver.py` | 扫描工作流列表、解析单个 YAML |
| `services/creator.py` | 创建实例 worktree、写入 instance JSON、打初始锚点、fast_forward / clone_from |
| `scheduler/orchestrator.py` | 完整场景：单链推进、parallel 拆分、ERROR→retry→failure edge→terminate、确认流程、合并冲突→CONFLICT |
| `scheduler/processors/02_message_consumer.py` | 消息消费幂等、状态更新、consumed_message_ids 去重、非法 routing_choice |
| `runtime/worktree/manager.py` | 拆分、合并（无冲突/有冲突）、清理 |
| `runtime/message/handler.py` | 消息写入、字段注入、modified_files 检测 |
| `runtime/agent/manager.py` | 注册、查找、移除、过滤 |
| `services/validator.py` | 保护区检测、越界→ERROR |

### 10.3 端到端测试（`tests/cli/`）

少量冒烟测试，验证 CLI 参数解析 + JSON 输出格式。完整业务流程由集成测试覆盖。

---

## 十一、关键算法伪代码

### 11.1 `next` 的就绪计算 + action 生成

```
SchedulerOrchestrator.run_next(instance_id):
  lock = acquire_lock(instance_id)
  state = load_instance_state(instance_id)
  spec = load_workflow(state.workflow_id, state.version)
  adj = build_adjacency(spec)
  ctx = ExecutionContext(instance_id=instance_id, adj=adj, ...)

  # 14 步 Processor 流水线
  for processor in PROCESSORS:
      result = processor.process(ctx, state)
      if result.state_delta:
          state = state.apply_delta(result.state_delta)
      actions.extend(result.actions or [])
      if result.stop:
          break

  save_instance_state(instance_id, state)
  release_lock(instance_id)
  return {"status": "ok", "actions": actions}
```

### 11.2 ERROR 处理（ErrorRecoveryProcessor）

```
ErrorRecoveryProcessor.process(ctx, state):
  delta = StateDelta()
  for st in state.stages where st.status == ERROR:
      policy = TransitionPolicy.from_adjacency(ctx.adj, st.stage_id)
      result = policy.on_error(st)
      delta.stage_updates[st.stage_instance_id] = {
          "status": result.next_status,
          **result.updates,
      }
      # 超时检测：写入合成 ERROR 消息，由下次 MessageConsumerProcessor 统一消费
  return ProcessorResult(state_delta=delta)
```

---

## 十二、`identity`、`message write` 与 `terminate` 命令

### 12.1 `identity`

```bash
wfctl identity
```

从 worktree 根目录的身份元数据文件读取并返回：

```json
{
  "instance_id": "20260517-001",
  "stage_id": "s03",
  "stage_instance_id": "s03",
  "message_target_path": ".agent/instances/20260517-001/messages/"
}
```

不含 `project_root`——SubAgent 不可知主仓库位置。

### 12.2 `message write`

```bash
wfctl message write \
  --instance <id> --stage <id> --status <status> --report "..." \
  [--checkpoint "..."] [--questions "..."] [--parallel-targets "..."]
```

wfctl 内部：
1. 读取 `identity` 元数据校验调用者身份
2. 注入 `message_id`、`timestamp`、`modified_files`（通过 `git status --porcelain` 获取）
3. 原子写入 `.agent/instances/<id>/messages/<message_id>.json`
4. 返回 `{"status": "ok", "message_id": "msg-xxx"}`

### 12.3 `terminate`

```bash
wfctl terminate --instance <id>
```

wfctl 内部：
1. 校验实例存在且状态为 `ACTIVE` 或 `PAUSED`（终态实例不可重复终止）
2. `StateDelta` 将实例状态 → `FAILED`
3. `WorktreeManager.terminate_instance(instance_id)` 原子化执行全部副作用：清理 anchor tags、移除全部 worktree、归档实例目录
4. 写入 deviation 日志（`type: USER_TERMINATED`）
5. 写入 timeline（`active→failed`，标注 `terminated_by: user`）
6. 返回 `{"status": "ok", "terminated_instance": "<id>", "cleaned_worktrees": [...]}`

---

## 十三、与现有规范的衔接

| 本设计章节 | 对应规范 |
|-----------|---------|
| 包结构、CLI 层、异常体系 | 本设计独有 |
| Schema 适配器 | `WORKFLOW.yaml字段规范` |
| DAG 引擎、Processor 流水线 | `wfctl接口与行为规范` §四（next 调度核心） |
| 状态管理 | `Instance状态机规范` + `Message通信协议规范` |
| Git 操作、worktree 分配 | `worktree与git锚点规范` |
| validator | `权限与校验体系规范` |
| status 聚合 | `项目全局状态规范` |
| identity / message write | `Message通信协议规范` §六 + `Skill定义规范` §四 |
| TransitionPolicy / StateDelta | `wfctl重构方案-v2.md` |

---

## 十四、跨平台兼容策略

### 14.1 概述

wfctl 在 Windows 和 Unix（Linux/macOS）上均需正常运行。策略不是"适配两类系统"，而是**从一开始就只用跨平台机制**。

### 14.2 路径

| 机制 | 说明 |
|------|------|
| `pathlib.Path` | 所有路径操作使用 `Path`，禁止字符串拼接 |
| 统一分隔符 | 所有硬编码路径用 `/`（`pathlib` 自动转换） |
| 无 `../` 逃逸 | 禁止相对路径穿越，所有路径相对项目根计算 |

### 14.3 Git ref 命名

- 分支名仅使用 `[a-zA-Z0-9_-]`，禁止 `#`、空格、特殊字符
- parallel 拆分实例使用 `_<n>` 后缀（如 `s03_0`、`s03_1`），`_` 在 git ref 中合法

### 14.4 文件锁

- 跨平台互斥锁：Windows 用 `ctypes.windll.kernel32.OpenProcess` 检测 pid 存活，Unix 用 `os.kill(pid, 0)`
- 锁文件写入 `pid:timestamp`，不依赖 `fcntl.flock`
- 详见 `infrastructure/lock.py`

### 14.5 原子 IO

- `os.replace(src, dst)` — Windows/Unix 均为原子替换操作
- JSON 写入：先写 `.tmp` 文件，完成后 `os.replace(tmp, target)`
- 详见 `infrastructure/io.py`

### 14.6 时间戳

- 所有时间戳使用 `datetime.now().astimezone().isoformat(timespec="seconds")`
- 禁止 `time.strftime("%z")`（Windows 上可能返回空字符串）
- 详见 `infrastructure/timestamp.py`

### 14.7 编码

- 所有文本文件使用 UTF-8（`encoding="utf-8"`）
- JSON 写入使用 `ensure_ascii=False` 保留非 ASCII 字符可读性（如中文 `report`）

### 14.8 子进程

- `subprocess.run(cmd, capture_output=True, text=True)` — 命令参数为 list，不拼接 shell 字符串
- 不调用 shell 脚本（bat / sh），所有逻辑在 Python 内完成

### 14.9 Windows 特有约束

| 约束 | 处理 |
|------|------|
| 路径长度限制（260 字符） | `.tmp/worktrees/` 路径尽量短命名；必要时启用 `\\?\` 前缀 |
| `os.symlink` 不可用 | 使用文件复制替代（Git worktree 本身不依赖 symlink） |
| Git worktree 管理员权限 | worktree 创建需要 `GIT_WORKTREE` 功能，Git for Windows 已包含 |

### 14.10 跨平台测试矩阵

| 平台 | 测试范围 |
|------|---------|
| Windows | 全量（78 个测试） |
| Linux | CI 中运行（后续配套） |
| macOS | CI 中运行（后续配套） |
