> ⚠️ **已归档**：本 v1.0 文档已被 `wfctl重构方案-v2.md` 取代。
> 现行重构路线图、文件路径、阶段划分、验收标准均以 v2 为准。
> 本文档保留仅作历史参考，不再代表当前实现状态。

# wfctl 重构方案：从碎片化到单一真相源

> 编制日期：2026-05-24
> 版本：v1.0（已归档）
> 范围：DAG 引擎、Processor 流水线、状态模型、DAG 静态验证器

---

## 一、现状诊断：六大 Bug 根因

### 根因 1：新旧格式桥接层是 Bug 的绝对温床

当前代码存在**两套状态格式**并行运行：

| 场景 | 使用的格式 |
|------|-----------|
| `Processor.process(ctx, state)` | `InstanceState`（不可变 dataclass） |
| `compute_ready(ctx.adj, instance_dict)` | `dict`（旧格式，`instance.json` 原生） |
| `consume_messages(id, instance_dict, ...)` | `dict`（直接 mutate dict） |
| `state.to_dict()` → 再传给旧函数 | 来回转换 |

**具体 Bug 模式：**

```python
# ReadyComputeProcessor
instance_dict = state.to_dict()          # ① 新→旧
ready = compute_ready(ctx.adj, instance_dict)  # ② 旧函数

# consume_messages 直接 mutate dict
stage = stage_map.get(stage_id)          # ③ 拿到 dict 引用
stage["status"] = "DONE"                 # ④ 直接修改！
```

- `state.to_dict()` 的往返导致 `frozenset` → `list` 等类型变化
- `consume_messages` 直接修改 dict，与 Processor "纯函数"的设计假设矛盾
- `compute_ready` 返回 `list[str]`（stage_id），但 parallel 拆分后一个 stage_id 对应多个 instance

### 根因 2：`stage_id` vs `stage_instance_id` 标识混乱

这是**最致命的 Bug 来源**：

```python
# 90% 的 Processor 用 stage_id 做 delta key
delta.stage_updates[stage_id] = {"status": StageStatus.DONE}

# 但 ParallelSplitProcessor 突然用 stage_instance_id
delta = StateDelta(stage_updates={
    s.stage_instance_id: {"parallel_retry_count": new_count}
})

# state.stage_map() "同 stage_id 取最后一条"
def stage_map(self) -> dict[str, StageState]:
    for s in self.stages:
        m[s.stage_id] = s      # ← parallel 拆分后，后面的覆盖前面的！
    return m
```

**后果：**
- Parallel 拆分后，`AllocateSpawnProcessor` 只处理最后一个 parallel instance
- `ErrorHandlerProcessor` 对 parallel instance 的 retry 可能作用到错误的 instance 上
- `VirtualStagesProcessor` 的 `stage_map[stage_id] = st.replace(...)` 在循环中直接修改局部 dict，不影响真实状态

### 根因 3：边处理逻辑碎片化 + 语义不一致

边的条件判断散落在至少 **5 个地方**，且逻辑不完全一致：

| 文件 | 处理的边 | 行为 |
|------|----------|------|
| `core/dag.py:_all_satisfied` | ALWAYS/SUCCESS/CONFIRMED | SUCCESS 接受 `exit_cond not in ("loop_exceeded",)`，含 `""` |
| `core/dag.py:_all_satisfied_virtual` | ALWAYS/SUCCESS/CONFIRMED | 虚拟 stage 的副本，但 `exit_cond` 默认值处理不同 |
| `ErrorHandlerProcessor` | FAILURE/LOOP_EXCEEDED | 运行时错误恢复链 |
| `dag_validator.py` | 死锁/悬空/确认点缺口 | 静态检查，不验证运行时语义 |
| `FinalizeProcessor` | SUCCESS（all_done 检查） | 只检查非虚拟 stage |

**缺失的验证：**
- `EdgeSpec.aggregation`（`"all"` \| `"any"`）**定义了但从未被使用**
- `cascade_reset_until` 指向的 stage 是否存在？未验证
- `loop_counter_stage` 指向的 stage 是否存在？未验证
- `failure_edge` 的目标 stage 是否在拓扑序下游？未验证
- 回边（非自环的回边）是否有 `max_loop` 限制？未验证

### 根因 4：Processor 间的隐式依赖导致顺序脆弱

当前通过 `ctx.extra` 传递中间结果，形成**隐式数据流**：

```
ConsumeMessagesProcessor ──message_changes──► AutoCommitProcessor
                                      └────► MergeWorktreesProcessor
                                              └────► ErrorHandlerProcessor（超时写入消息）
```

**后果：**
- 如果 Processor 顺序调整，`AutoCommitProcessor` 会崩溃（拿不到 `message_changes`）
- `CheckChildrenProcessor` 直接调用 `_run_next_inner`（legacy 调度器），子实例走旧路径，父实例走新路径，行为不一致
- `VirtualStagesProcessor` 和 `AllocateSpawnProcessor` **都做 VIRTUAL → DONE**，重复逻辑

### 根因 5：DAG 验证器覆盖严重不足

```python
# _check_unbounded_loops 只检查自环
if edge.from_stage == edge.to_stage:   # ← 回边呢？
    if edge.max_loop is None:

# _check_confirmation_gaps 只检查 CONFIRMED，不检查 REJECTED
confirmed_edges = [e for e in adj.outgoing.get(...)
                   if e.condition == EdgeCondition.CONFIRMED]  # ← rejected 边呢？
```

**缺少的检查项：**
1. 回边（from 拓扑序 > to 拓扑序）无 `max_loop` 限制
2. `choice` 完备性：多条 SUCCESS 边但 choice 未覆盖所有情况
3. `failure_edge` 指向不存在的 stage
4. `cascade_reset_until` 指向不存在的 stage
5. `confirmation_point=true` 但无 `REJECTED` 边（用户可能 reject）
6. `parallel.source` 的 stage 产出 `parallel_targets` 后，下游 stage 是否能正确处理 fan-in

### 根因 6：Parallel 拆分的边界情况

```python
# ParallelSplitProcessor 移除原有 stage 的条件
remove_stage_instance_ids=[
    s.stage_instance_id for s in state.stages
    if s.stage_id == stage_spec.stage_id and not s.fan_out_target
]
```

- 如果 stage 被手动 `skip` 过，`fan_out_target` 为 `None`，但状态不是 `PENDING`，可能被误移除
- `_handle_missing_targets` 的 reinforce action 中的 `source_agent` 查找逻辑脆弱：`running_agents` 中的 `stage_id` 可能已不是当前 source stage

---

## 二、重构原则

> **"一个概念只在一个地方被处理，一种状态只有一种表示形式。"**

---

## 三、重构 1：彻底消灭旧 dict 格式（单一真相源）

**目标**：所有模块统一使用 `InstanceState`/`StageState`，`instance.json` 只是序列化格式。

### 3.1 重写 `compute_ready`

```python
# core/dag.py
from services.scheduler.state_model import InstanceState, StageStatus

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
    """检查上游边是否满足（OR 语义）。

    使用 InstanceState 原生接口，不再操作 dict。
    """
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
        if edge.condition == EdgeCondition.CONFIRMED:
            if exit_cond not in ("confirmed", ""):
                continue
            if edge.choice:
                if upstream.confirmed_choice == edge.choice:
                    return True
                continue
            return True
        # FAILURE / REJECTED / LOOP_EXCEEDED 由 TransitionPolicy 处理

    return False
```

### 3.2 重写消息消费为纯函数

```python
# services/scheduler/processors/message_consumer.py
@dataclass
class MessageConsumerProcessor:
    """消费消息池，生成 StateDelta。零副作用。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        root = find_root()
        messages_dir = root / ".agent" / "instances" / ctx.instance_id / "messages"
        consumed_ids = state.consumed_message_ids
        delta = StateDelta()
        new_consumed: set[str] = set()

        for msg_path in sorted(messages_dir.glob("*.json")):
            msg = json.loads(msg_path.read_text(encoding="utf-8"))
            msg_id = msg.get("message_id")
            if not msg_id or msg_id in consumed_ids:
                continue

            stage_inst_id = msg.get("stage_instance_id") or msg.get("stage_id")
            stage = state.stage_by_instance_id(stage_inst_id)
            if not stage:
                continue

            # 校验 routing_choice
            new_status = msg.get("status", stage.status.value)
            updates: dict[str, Any] = {}

            if new_status == "DONE":
                choice = msg.get("routing_choice")
                if choice and stage.valid_routing_choices and choice not in stage.valid_routing_choices:
                    updates["status"] = StageStatus.ERROR
                    updates["output_message_id"] = msg_id
                else:
                    updates["status"] = StageStatus.DONE
                    updates["exit_condition"] = "success"
                    updates["output_message_id"] = msg_id
                    if choice:
                        updates["routing_choice"] = choice
            elif new_status == "ERROR":
                updates["status"] = StageStatus.ERROR
                updates["output_message_id"] = msg_id
            elif new_status == "AWAITING_CONFIRM":
                updates["status"] = StageStatus.AWAITING_CONFIRM
                updates["output_message_id"] = msg_id
                updates["confirm_questions"] = msg.get("confirm_questions", [])

            if updates:
                delta.stage_updates[stage_inst_id] = updates
            new_consumed.add(msg_id)

        if new_consumed:
            delta.instance_updates["consumed_message_ids"] = consumed_ids | new_consumed

        return ProcessorResult(state_delta=delta)
```

### 3.3 删除旧实现

- `services/state_manager.py` 中的 `consume_messages` 旧实现 → **删除**
- `services/state_manager.py` 中的 `load_instance` / `save_instance` 保留，但增加 `load_instance_state` / `save_instance_state` 封装

---

## 四、重构 2：`stage_instance_id` 作为唯一标识

**所有 `StateDelta.stage_updates` 的 key 统一为 `stage_instance_id`。**

### 4.1 修改 `StateDelta` 文档与使用约定

```python
@dataclass(frozen=True)
class StateDelta:
    """显式状态变更描述。

    - stage_updates: **stage_instance_id** → {字段: 新值}
    - instance_updates: 顶层 instance 字段变更
    - append_stages: 新增 stage（parallel 拆分用）
    - remove_stage_instance_ids: 移除 stage（按 stage_instance_id）
    """
    stage_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
```

### 4.2 修改 `InstanceState` 查询接口

```python
@dataclass(frozen=True)
class InstanceState:
    # ... 现有字段 ...

    # ── 查询辅助 ──

    def stages_by_id(self, stage_id: str) -> list[StageState]:
        """返回所有匹配 stage_id 的 stage（用于 parallel 场景）。

        取代旧的 stage_map() "取最后一条" 的歧义行为。
        """
        return [s for s in self.stages if s.stage_id == stage_id]

    def first_stage_by_id(self, stage_id: str) -> StageState | None:
        """按 stage_id 查找第一条（兼容旧行为，但显式命名）。"""
        for s in self.stages:
            if s.stage_id == stage_id:
                return s
        return None

    def stage_by_instance_id(self, stage_instance_id: str) -> StageState | None:
        """按 stage_instance_id 精确查找。"""
        for s in self.stages:
            if s.stage_instance_id == stage_instance_id:
                return s
        return None
```

### 4.3 逐 Processor 修改

| Processor | 修改点 |
|-----------|--------|
| `ConsumeMessagesProcessor` | `delta.stage_updates[stage_inst_id]` |
| `AutoCommitProcessor` | 扫描 `state.stages`，按 `stage_instance_id` 定位 |
| `MergeWorktreesProcessor` | 按 `stage_instance_id` 更新 |
| `VirtualStagesProcessor` | 按 `stage_instance_id` 更新 |
| `ErrorHandlerProcessor` | `delta.stage_updates[st.stage_instance_id]` |
| `AllocateSpawnProcessor` | ready list 携带 `stage_instance_id` |
| `ParallelSplitProcessor` | 统一使用 `stage_instance_id` |
| `CheckChildrenProcessor` | 子实例状态更新用 `stage_instance_id` |

---

## 五、重构 3：引入 `TransitionPolicy` —— 边处理单一真相源

将边的所有处理逻辑集中到一个对象中。

### 5.1 新增 `core/transition.py`

```python
"""TransitionPolicy：Stage 出边策略的单一真相源。

将分散在 dag.py、ErrorHandlerProcessor、AllocateSpawnProcessor 中的
边处理逻辑统一封装。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.dag import AdjacencyList
from core.schema.interface import EdgeCondition, EdgeSpec, StageSpec
from services.scheduler.state_model import StageState, StageStatus


@dataclass(frozen=True)
class TransitionResult:
    """状态转换决策结果。"""
    next_status: StageStatus
    target_stage_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    action: str = ""  # "retry" | "spawn" | "terminate" | "none"


@dataclass(frozen=True)
class TransitionPolicy:
    """一个 stage 的所有出边策略。

    集中管理：就绪条件、错误恢复、确认分支、循环限制。
    """
    stage_id: str
    spec: StageSpec

    # 各类边缓存
    ready_edges: list[EdgeSpec] = field(default_factory=list)
    confirmed_edges: list[EdgeSpec] = field(default_factory=list)
    rejected_edges: list[EdgeSpec] = field(default_factory=list)
    failure_edge: EdgeSpec | None = None
    loop_exceeded_edge: EdgeSpec | None = None

    @classmethod
    def from_adjacency(cls, adj: AdjacencyList, stage_id: str) -> TransitionPolicy:
        """从邻接表构建 TransitionPolicy。"""
        spec = adj.stages.get(stage_id)
        if not spec:
            raise ValueError(f"Stage {stage_id} not found in adjacency")

        outgoing = adj.outgoing.get(stage_id, [])
        return cls(
            stage_id=stage_id,
            spec=spec,
            ready_edges=[e for e in outgoing
                        if e.condition in (EdgeCondition.ALWAYS, EdgeCondition.SUCCESS, EdgeCondition.CONFIRMED)],
            confirmed_edges=[e for e in outgoing if e.condition == EdgeCondition.CONFIRMED],
            rejected_edges=[e for e in outgoing if e.condition == EdgeCondition.REJECTED],
            failure_edge=next((e for e in outgoing if e.condition == EdgeCondition.FAILURE), None),
            loop_exceeded_edge=next((e for e in outgoing if e.condition == EdgeCondition.LOOP_EXCEEDED), None),
        )

    def is_upstream_satisfied(self, upstream_state: StageState, edge: EdgeSpec) -> bool:
        """判断单条上游边是否满足。"""
        if upstream_state.status != StageStatus.DONE:
            return False
        exit_cond = upstream_state.exit_condition

        if edge.condition == EdgeCondition.ALWAYS:
            return True
        if edge.condition == EdgeCondition.SUCCESS:
            if exit_cond == "loop_exceeded":
                return False
            if edge.choice:
                return upstream_state.routing_choice == edge.choice
            return True
        if edge.condition == EdgeCondition.CONFIRMED:
            if exit_cond not in ("confirmed", ""):
                return False
            if edge.choice:
                return upstream_state.confirmed_choice == edge.choice
            return True
        return False

    def on_error(self, state: StageState) -> TransitionResult:
        """Stage 进入 ERROR 后的恢复决策。"""
        max_attempts = self.spec.retry

        if state.attempt_count < max_attempts:
            return TransitionResult(
                next_status=StageStatus.PENDING,
                updates={"attempt_count": state.attempt_count + 1},
                action="retry",
            )

        if self.failure_edge and state.loop_counter < (self.failure_edge.max_loop or 0):
            return TransitionResult(
                next_status=StageStatus.PENDING,
                target_stage_id=self.failure_edge.to_stage,
                updates={"loop_counter": state.loop_counter + 1},
                action="spawn",
            )

        if self.loop_exceeded_edge:
            return TransitionResult(
                next_status=StageStatus.PENDING,
                target_stage_id=self.loop_exceeded_edge.to_stage,
                action="spawn",
            )

        return TransitionResult(
            next_status=StageStatus.ERROR,
            action="terminate",
        )

    def valid_routing_choices(self) -> list[str]:
        """收集所有 SUCCESS 边的 choice 值。"""
        choices: list[str] = []
        for e in self.ready_edges:
            if e.condition == EdgeCondition.SUCCESS and e.choice and e.choice not in choices:
                choices.append(e.choice)
        return choices

    def valid_confirm_choices(self) -> list[str]:
        """收集所有 confirmed + rejected 边的 choice 值。"""
        choices: list[str] = []
        for e in self.confirmed_edges + self.rejected_edges:
            if e.choice and e.choice not in choices:
                choices.append(e.choice)
        return choices
```

### 5.2 收益

- `_all_satisfied` / `_all_satisfied_virtual` / `get_failure_edge` / `get_loop_exceeded_edge` / `get_confirmed_edges` / `get_rejected_edges` 等 6 个分散函数合并为 1 个对象
- 新增边条件时只需改 `TransitionPolicy` 一处
- 单元测试可以针对 `TransitionPolicy` 做完整的决策表覆盖，无需启动完整调度器

---

## 六、重构 4：DAG 验证器重构 —— 拓扑分析引擎

引入真正的**拓扑排序**和**强连通分量检测**。

### 6.1 新增拓扑分析工具

```python
# core/dag_topology.py
"""DAG 拓扑分析：排序、循环检测、强连通分量。"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import AdjacencyList
from core.schema.interface import EdgeSpec


@dataclass(frozen=True)
class TopologyResult:
    order: list[str]                    # 拓扑序（不含循环节点）
    cycles: list[list[str]]             # 所有环（SCC 大小 > 1 或自环）
    back_edges: list[EdgeSpec]          # 回边列表


def analyze_topology(adj: AdjacencyList) -> TopologyResult:
    """使用 Tarjan SCC 算法分析 DAG 拓扑结构。

    返回拓扑序、循环列表、回边列表。
    """
    index_counter = [0]
    stack: list[str] = []
    lowlinks: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str):
        index[v] = index_counter[0]
        lowlinks[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for edge in adj.outgoing.get(v, []):
            w = edge.to_stage
            if w not in index:
                strongconnect(w)
                lowlinks[v] = min(lowlinks[v], lowlinks[w])
            elif w in on_stack:
                lowlinks[v] = min(lowlinks[v], index[w])

        if lowlinks[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for stage_id in adj.stages:
        if stage_id not in index:
            strongconnect(stage_id)

    # 自环检测
    self_loops: list[list[str]] = []
    for stage_id in adj.stages:
        for edge in adj.outgoing.get(stage_id, []):
            if edge.from_stage == edge.to_stage:
                self_loops.append([stage_id])

    # 回边检测（基于拓扑序）
    # 先计算无环图的拓扑序（移除所有 SCC 内部边后的图）
    order = []
    scc_map: dict[str, int] = {}
    for i, scc in enumerate(sccs):
        for node in scc:
            scc_map[node] = i

    # 简化的拓扑序：按 SCC 的拓扑序排列
    for scc in reversed(sccs):
        for node in scc:
            order.append(node)

    # 回边 = from 在拓扑序中出现在 to 之后的边（且非自环）
    pos = {sid: i for i, sid in enumerate(order)}
    back_edges = []
    for stage_id in adj.stages:
        for edge in adj.outgoing.get(stage_id, []):
            if edge.from_stage == edge.to_stage:
                continue
            if pos.get(edge.from_stage, 0) > pos.get(edge.to_stage, 0):
                back_edges.append(edge)

    cycles = [scc for scc in sccs if len(scc) > 1] + self_loops

    return TopologyResult(order=order, cycles=cycles, back_edges=back_edges)
```

### 6.2 重写 DAG 验证器

```python
# core/dag_validator.py

def validate_workflow(spec: WorkflowSpec) -> ValidationResult:
    adj = build_adjacency(spec)
    topo = analyze_topology(adj)

    issues: list[ValidationIssue] = []
    issues.extend(_check_cycles(adj, topo))
    issues.extend(_check_back_edges(adj, topo))
    issues.extend(_check_reachability(adj, topo))
    issues.extend(_check_edge_completeness(adj))
    issues.extend(_check_confirmation_coverage(adj))
    issues.extend(_check_parallel_consistency(adj))
    issues.extend(_check_cascade_reset_validity(adj, topo))
    issues.extend(_check_failure_chain(adj))

    return ValidationResult(issues=issues)


def _check_cycles(adj: AdjacencyList, topo: TopologyResult) -> list[ValidationIssue]:
    """检查所有循环（SCC 大小 > 1 或自环）是否有 max_loop。"""
    issues: list[ValidationIssue] = []
    cycle_edges = set()

    # 收集所有参与循环的边
    for cycle in topo.cycles:
        if len(cycle) == 1:
            # 自环
            stage_id = cycle[0]
            for edge in adj.outgoing.get(stage_id, []):
                if edge.to_stage == stage_id and edge.condition in (
                    EdgeCondition.FAILURE, EdgeCondition.CONFIRMED
                ):
                    if edge.max_loop is None or edge.max_loop <= 0:
                        issues.append(ValidationIssue(
                            "UNBOUNDED_LOOP",
                            f"Stage '{stage_id}' 的自环 {edge.condition.value} 边缺少 max_loop",
                            stage_id=stage_id,
                            edge_from=stage_id,
                            edge_to=stage_id,
                        ))
        else:
            # 多节点环：收集环上所有边
            cycle_set = set(cycle)
            for stage_id in cycle:
                for edge in adj.outgoing.get(stage_id, []):
                    if edge.to_stage in cycle_set:
                        cycle_edges.add((edge.from_stage, edge.to_stage, edge.condition))

    # 多节点环要求至少有一条边有 max_loop（或所有边都有）
    for cycle in topo.cycles:
        if len(cycle) <= 1:
            continue
        cycle_set = set(cycle)
        has_loop_limit = False
        for stage_id in cycle:
            for edge in adj.outgoing.get(stage_id, []):
                if edge.to_stage in cycle_set and edge.max_loop is not None and edge.max_loop > 0:
                    has_loop_limit = True
                    break
            if has_loop_limit:
                break
        if not has_loop_limit:
            issues.append(ValidationIssue(
                "UNBOUNDED_LOOP",
                f"循环 {' → '.join(cycle)} 没有任何边设置 max_loop 限制",
            ))

    return issues


def _check_back_edges(adj: AdjacencyList, topo: TopologyResult) -> list[ValidationIssue]:
    """检查所有回边是否有 max_loop。"""
    issues: list[ValidationIssue] = []
    for edge in topo.back_edges:
        if edge.max_loop is None or edge.max_loop <= 0:
            issues.append(ValidationIssue(
                "UNBOUNDED_BACK_EDGE",
                f"回边 {edge.from_stage} → {edge.to_stage}（条件: {edge.condition.value}）缺少 max_loop",
                edge_from=edge.from_stage,
                edge_to=edge.to_stage,
            ))
    return issues


def _check_edge_completeness(adj: AdjacencyList) -> list[ValidationIssue]:
    """检查 choice 完备性：多条 SUCCESS 边必须有互斥且完备的 choice 覆盖。"""
    issues: list[ValidationIssue] = []
    for stage_id in adj.stages:
        success_edges = [e for e in adj.outgoing.get(stage_id, [])
                        if e.condition == EdgeCondition.SUCCESS]
        if len(success_edges) <= 1:
            continue

        choices = [e.choice for e in success_edges if e.choice]
        if len(choices) != len(success_edges):
            issues.append(ValidationIssue(
                "INCOMPLETE_CHOICES",
                f"Stage '{stage_id}' 有 {len(success_edges)} 条 SUCCESS 边，但仅 {len(choices)} 条设置了 choice",
                stage_id=stage_id,
            ))
        if len(set(choices)) != len(choices):
            issues.append(ValidationIssue(
                "DUPLICATE_CHOICE",
                f"Stage '{stage_id}' 的 SUCCESS 边 choice 值重复",
                stage_id=stage_id,
            ))
    return issues


def _check_failure_chain(adj: AdjacencyList) -> list[ValidationIssue]:
    """检查错误恢复链的完整性。"""
    issues: list[ValidationIssue] = []
    for stage_id, spec in adj.stages.items():
        has_retry = spec.retry > 0
        has_failure = any(
            e.condition == EdgeCondition.FAILURE
            for e in adj.outgoing.get(stage_id, [])
        )
        has_loop_exceeded = any(
            e.condition == EdgeCondition.LOOP_EXCEEDED
            for e in adj.outgoing.get(stage_id, [])
        )

        if has_failure and not has_retry:
            issues.append(ValidationIssue(
                "SUSPICIOUS_FAILURE_EDGE",
                f"Stage '{stage_id}' 有 failure_edge 但 retry=0，failure_edge 永远不会触发",
                stage_id=stage_id,
            ))

        if has_loop_exceeded and not has_failure:
            issues.append(ValidationIssue(
                "ORPHAN_LOOP_EXCEEDED",
                f"Stage '{stage_id}' 有 loop_exceeded_edge 但无 failure_edge",
                stage_id=stage_id,
            ))
    return issues


def _check_cascade_reset_validity(
    adj: AdjacencyList, topo: TopologyResult
) -> list[ValidationIssue]:
    """检查 cascade_reset_until 指向的 stage 是否在祖先路径上。"""
    issues: list[ValidationIssue] = []
    for stage_id in adj.stages:
        for edge in adj.outgoing.get(stage_id, []):
            if not edge.cascade_reset_until:
                continue
            target = edge.cascade_reset_until
            if target not in adj.stages:
                issues.append(ValidationIssue(
                    "INVALID_CASCADE_TARGET",
                    f"Stage '{stage_id}' 的 cascade_reset_until 指向不存在的 stage '{target}'",
                    stage_id=stage_id,
                ))
                continue
            # cascade_reset_until 必须在 stage_id 的祖先路径上
            ancestors = collect_ancestors(adj, stage_id)
            if target not in ancestors and target != stage_id:
                issues.append(ValidationIssue(
                    "INVALID_CASCADE_TARGET",
                    f"Stage '{stage_id}' 的 cascade_reset_until '{target}' 不在其祖先路径上",
                    stage_id=stage_id,
                ))
    return issues
```

---

## 七、重构 5：Processor 流水线去隐式化

### 7.1 消除 `ctx.extra` 隐式依赖

**方案：将"本次调度周期内发生的事件"编码在状态模型中。**

```python
@dataclass(frozen=True)
class InstanceState:
    # ... 现有字段 ...

    # 新增：本次调度周期元数据（不持久化到 instance.json，仅内存传递）
    cycle_meta: CycleMeta = field(default_factory=lambda: CycleMeta())


@dataclass(frozen=True)
class CycleMeta:
    """本次调度周期内的临时状态，用于 Processor 间显式通信。

    不写入 instance.json，仅在单次 next 调用内有效。
    """
    newly_done_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_error_stage_instance_ids: frozenset[str] = field(default_factory=frozenset)
    newly_awaiting_confirm_ids: frozenset[str] = field(default_factory=frozenset)
    ready_candidates: list[tuple[str, str]] = field(default_factory=list)  # (stage_id, stage_instance_id)

    def with_done(self, stage_instance_id: str) -> CycleMeta:
        return replace(self, newly_done_stage_instance_ids=
                       self.newly_done_stage_instance_ids | {stage_instance_id})
```

### 7.2 调整 Processor 顺序

```
BEFORE（隐式依赖，通过 ctx.extra 传递）:
  01. SyncWorktreeProcessor
  02. ConsumeMessagesProcessor → ctx.extra["message_changes"]
  03. AutoCommitProcessor      → 读 message_changes
  04. MergeWorktreesProcessor  → 读 message_changes
  05. CheckChildrenProcessor
  06. ParallelSplitProcessor
  07. ErrorHandlerProcessor
  08. ConflictHandlerProcessor
  09. VirtualStagesProcessor
  10. ReadyComputeProcessor    → ctx.extra["ready_stage_ids"]
  11. AllocateSpawnProcessor   → 读 ready_stage_ids
  12. ConfirmAggregateProcessor
  13. FinalizeProcessor

AFTER（显式状态传递，通过 state.cycle_meta）:
  01. SyncWorktreeProcessor
  02. MessageConsumerProcessor → 消费消息，标记 newly_done / newly_error
  03. VirtualStagesProcessor   → 虚拟 stage 直通，标记 newly_done
  04. StateTransitionProcessor → 应用所有状态转换（合并 cycle_meta 到 stages）
  05. AutoCommitProcessor      → 扫描 newly_done，执行 git commit
  06. MergeWorktreesProcessor  → 扫描 newly_done，合并 worktree
  07. ParallelSplitProcessor   → 检查 parallel 需求，拆分
  08. ChildWorkflowProcessor   → 子工作流（递归调用新 Orchestrator）
  09. ErrorRecoveryProcessor   → 错误恢复 + 超时检测
  10. ConflictResolveProcessor → 冲突处理
  11. ReadyComputeProcessor    → 就绪计算，写入 state.cycle_meta.ready_candidates
  12. AllocateSpawnProcessor   → 读 ready_candidates，分配 worktree
  13. ConfirmAggregateProcessor → 确认点聚合
  14. FinalizeProcessor        → 收尾
```

### 7.3 关键改动说明

- **MessageConsumerProcessor**：取代 `services/state_manager.py` 的 `consume_messages`，纯函数，不 mutate dict
- **StateTransitionProcessor**：新增，负责将 `cycle_meta` 中的事件应用到 `stages`（如 newly_done → stage.status = DONE），作为单一的状态变更点
- **AutoCommitProcessor / MergeWorktreesProcessor**：不再依赖 `ctx.extra["message_changes"]`，而是扫描 `state.cycle_meta.newly_done_stage_instance_ids`
- **ChildWorkflowProcessor**：取代 `CheckChildrenProcessor` 中的子工作流逻辑，递归调用 `SchedulerOrchestrator.run()`，不再调用 `_run_next_inner`

---

## 八、重构 6：子工作流统一路径

### 8.1 修改 `ChildWorkflowProcessor`

```python
# services/scheduler/processors/child_workflow.py
@dataclass
class ChildWorkflowProcessor:
    """子工作流：创建、检查完成、递归调度。

    递归调用 SchedulerOrchestrator.run()，不再调用 legacy _run_next_inner。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        actions: list[dict] = []

        # 1. 检查 RUNNING 子实例的完成状态
        for st in state.stages:
            if st.status != StageStatus.RUNNING or not st.child_instance_id:
                continue
            child_state = self._load_child_state(st.child_instance_id)
            if not child_state:
                continue
            if child_state.status == InstanceStatus.COMPLETED:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.DONE,
                    "exit_condition": "success",
                }
            elif child_state.status == InstanceStatus.FAILED:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.ERROR,
                }

        # 2. 创建新的子实例（PENDING WORKFLOW stage）
        for st in state.stages:
            if st.status != StageStatus.PENDING:
                continue
            spec = ctx.adj.stages.get(st.stage_id)
            if not spec or spec.target_type != StageTargetType.WORKFLOW:
                continue

            # 检查上游就绪
            upstream = ctx.adj.incoming.get(st.stage_id, [])
            if not self._upstream_satisfied(upstream, state):
                continue

            child_result = self._create_child(ctx, st, spec)
            delta.stage_updates[st.stage_instance_id] = {
                "child_instance_id": child_result["instance_id"],
                "status": StageStatus.RUNNING,
                "started_at": iso_timestamp(),
            }

        # 3. 递归调度活跃子实例
        child_actions = self._recurse_children(state, ctx)
        actions.extend(child_actions)

        return ProcessorResult(state_delta=delta, actions=actions)

    def _recurse_children(self, state: InstanceState, ctx: ExecutionContext) -> list[dict]:
        """递归调度所有活跃子实例。"""
        from services.scheduler.orchestrator import SchedulerOrchestrator

        actions: list[dict] = []
        for st in state.stages:
            child_id = st.child_instance_id
            if not child_id or st.status != StageStatus.RUNNING:
                continue

            child_state = self._load_child_state(child_id)
            if not child_state or child_state.status != InstanceStatus.ACTIVE:
                continue

            # 构建子实例的 ExecutionContext
            child_ctx = self._build_child_context(ctx, child_id)
            orchestrator = SchedulerOrchestrator()
            result = orchestrator.run(child_ctx, child_state)

            # 扁平化合并子实例 actions
            for action in result.get("actions", []):
                actions.append(self._flatten_action(action, st.stage_id))

        return actions
```

### 8.2 删除 legacy 调用

- `CheckChildrenProcessor._recurse_child_instances` 中的 `from services.scheduler_legacy import _run_next_inner` → **删除**
- `ParallelSplitProcessor` 中的 `from services.scheduler_legacy import _load_running_agents` → **改为直接读取 `.agent/running_agents.json`**
- `AllocateSpawnProcessor` 中的 `from services.scheduler_legacy import _load_running_agents` → **同上**

---

## 九、文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `core/dag_topology.py` | Tarjan SCC 拓扑分析 |
| `core/transition.py` | `TransitionPolicy` 单一真相源 |
| `services/scheduler/processors/message_consumer.py` | 取代 `state_manager.consume_messages` |
| `services/scheduler/processors/state_transition.py` | 状态转换单一入口 |
| `services/scheduler/processors/child_workflow.py` | 取代 `CheckChildrenProcessor` 的子工作流逻辑 |
| `services/scheduler/processors/error_recovery.py` | 取代 `ErrorHandlerProcessor`（基于 TransitionPolicy） |

### 重写文件

| 文件 | 说明 |
|------|------|
| `core/dag.py` | `compute_ready` 改为 `InstanceState` 原生；删除 `_all_satisfied_virtual` |
| `core/dag_validator.py` | 完整重写，增加拓扑分析 |
| `services/scheduler/state_model.py` | 增加 `CycleMeta`，`stage_map()` 改为 `stages_by_id()` |

### 修改文件

| 文件 | 修改点 |
|------|--------|
| `services/scheduler/processors/consume_messages.py` | **删除**（功能合并到 `message_consumer.py`） |
| `services/scheduler/processors/virtual_stages.py` | 使用 `stage_instance_id`，移除副作用（tag_anchor 移到 AutoCommit） |
| `services/scheduler/processors/auto_commit.py` | 从 `cycle_meta.newly_done` 读取 |
| `services/scheduler/processors/merge_worktrees.py` | 从 `cycle_meta.newly_done` 读取 |
| `services/scheduler/processors/ready_compute.py` | 使用 `InstanceState`，返回 `(stage_id, stage_instance_id)` 列表 |
| `services/scheduler/processors/allocate_spawn.py` | 使用 `stage_instance_id`，读取 `cycle_meta.ready_candidates` |
| `services/scheduler/processors/check_children.py` | **删除**（功能合并到 `child_workflow.py`） |
| `services/scheduler/processors/error_handler.py` | **删除**（功能合并到 `error_recovery.py`） |
| `services/scheduler/orchestrator.py` | 更新 PROCESSORS 列表 |
| `services/state_manager.py` | 删除 `consume_messages`，增加 `load_instance_state` 封装 |

### 删除文件

| 文件 | 说明 |
|------|------|
| `services/scheduler_legacy.py` | **整个删除**（确认新架构覆盖所有功能后执行） |

---

## 十、实施路线图

### Phase 1：基础设施（风险：低，预计 2 天）

1. 新增 `core/dag_topology.py` + 单元测试
2. 新增 `core/transition.py` + 单元测试
3. 修改 `services/scheduler/state_model.py`（增加 `CycleMeta`，保留向后兼容）
4. **验证**：所有现有测试通过

### Phase 2：消灭旧格式桥接（风险：中，预计 3 天）

1. 重写 `core/dag.py`：`compute_ready` 改为 `InstanceState` 原生
2. 新增 `services/scheduler/processors/message_consumer.py`
3. 修改 `services/scheduler/processors/virtual_stages.py`
4. 修改 `services/scheduler/processors/ready_compute.py`
5. 删除 `services/state_manager.py` 中的 `consume_messages`
6. **验证**：手动测试标准工作流 + parallel 工作流

### Phase 3：`stage_instance_id` 统一化（风险：高，预计 3 天）

1. 逐 Processor 修改 `delta.stage_updates` 的 key 为 `stage_instance_id`
2. 修改 `InstanceState.stage_map()` 为 `stages_by_id()` / `first_stage_by_id()`
3. 修改所有调用 `stage_map()` 的代码
4. **验证**：重点测试 parallel 拆分 + 子工作流场景

### Phase 4：Processor 去隐式化 + 子工作流统一（风险：中，预计 2 天）

1. 新增 `StateTransitionProcessor`
2. 修改 `AutoCommitProcessor` / `MergeWorktreesProcessor` 从 `cycle_meta` 读取
3. 新增 `ChildWorkflowProcessor`，替换 `CheckChildrenProcessor`
4. 新增 `ErrorRecoveryProcessor`，替换 `ErrorHandlerProcessor`
5. 更新 `orchestrator.py` 的 PROCESSORS 列表
6. **验证**：完整端到端测试

### Phase 5：DAG 验证器重构 + 清理（风险：低，预计 2 天）

1. 重写 `core/dag_validator.py`
2. 删除 `services/scheduler_legacy.py`
3. 删除 `services/scheduler/processors/consume_messages.py`
4. 删除 `services/scheduler/processors/error_handler.py`
5. 删除 `services/scheduler/processors/check_children.py`
6. **验证**：所有测试通过，workflow-auditor 运行通过

---

## 十一、关键决策点

| 决策 | 选项 | 建议 |
|------|------|------|
| 是否删除 `scheduler_legacy.py`？ | A. 保留双轨制 B. **彻底删除** | **建议 B**。新架构已完整覆盖 legacy 功能，双轨制是 bug 的持续来源。但需在 Phase 4 完成后执行。 |
| `stage_instance_id` 统一化是否值得？ | A. 只做补丁 B. **彻底统一** | **建议 B**。虽然改动大，但它是解决 parallel 场景 bug 的唯一根本方法。不做则 bug 会持续出现。 |
| `CycleMeta` 是否过度设计？ | A. 继续用 `ctx.extra` B. **显式 CycleMeta** | **建议 B**。`ctx.extra` 是隐式依赖的温床，`CycleMeta` 是编译器可检查的类型安全替代方案。 |
| 是否引入 Pydantic？ | A. 保持 dataclass B. **逐步引入 Pydantic v2** | 建议 Phase 5 后评估。当前 dataclass + `frozen=True` 已足够，Pydantic 可改善验证和序列化。 |

---

## 十二、预期收益

| 指标 | 重构前 | 重构后（预期） |
|------|--------|---------------|
| 边处理函数分散度 | 6 个文件，8 个函数 | 1 个文件，1 个类 |
| 状态格式种类 | 2 种（dict + dataclass） | 1 种（dataclass） |
| `stage_id`/`stage_instance_id` 混用处 | 15+ 处 | 0 处 |
| DAG 静态检查覆盖率 | 5 项 | 12+ 项 |
| Processor 隐式依赖数 | 4 个 `ctx.extra` 键 | 0 个（全部显式） |
| 子工作流调度路径 | 2 条（新 + legacy） | 1 条（统一 Orchestrator） |
| 代码总行数 | ~3500 行 | ~2800 行（删除 legacy + 消除重复） |

---

> **总结**：`wfctl` 的核心设计（Git-native、Processor-Orchestrator、不可变状态）是优雅的。Bug 频发的根源不在于架构方向错误，而在于**实现层面的碎片化**——新旧格式桥接、标识混用、边逻辑分散、隐式依赖。本次重构的目标是**让实现配得上设计**。


---

## 十三、补充设计：多轮提问与选择边

> 本章节补充两个关键场景的详细设计：
> 1. **多轮提问（confirmation_point）**：同一个 stage 经过不确定轮数的人机交互
> 2. **选择边（choice routing）**：通过 `choice` 属性将 stage 导向不同下游
>
> 这两个场景在原始方案中覆盖不足，经代码审查（`cli/confirm.py` 384 行）后补充。

---

### 13.1 当前实现的盲区

#### 盲区 A：`cli/confirm.py` 是游离在 Processor 架构之外的"影子状态机"

`cli/confirm.py` 直接执行 `load_instance()` → mutate dict → `save_instance()`，**完全不经过 Processor 流水线**，也不使用 `StateDelta`。

它内部包含了完整的状态转换逻辑：
- `AWAITING_CONFIRM → PENDING`（confirmation_point 继续）
- `AWAITING_CONFIRM → DONE`（终局确认）
- `AWAITING_CONFIRM → DONE`（rejected）
- 自循环 confirmed 边的 `loop_counter` 递增
- `loop_exceeded` 回退
- `_cascade_reset_on_backward_edge`（回边级联重置）
- `_cleanup_running_agents_for_reset`
- `_write_feedback_message`

这意味着：**wfctl 实际上有两套状态机**——一套是 Processor-Orchestrator（调度器内部），另一套是 `cli/confirm.py`（CLI 命令层）。两套状态机语法不同（dict vs dataclass）、行为可能不一致、bug 修复需要改两个地方。

#### 盲区 B："选择边"是隐式概念，没有一等建模

当前"选择边"通过 `SUCCESS`/`CONFIRMED` 边 + `choice` 属性隐式实现：

```yaml
edges:
  - from: s01-design
    to: s02a-frontend
    condition: success
    choice: "frontend"      # 隐式选择边

  - from: s01-design
    to: s02b-backend
    condition: success
    choice: "backend"       # 隐式选择边
```

运行时处理路径分裂：
- SUCCESS-choice：`core/dag.py:_all_satisfied` 检查 `routing_choice`
- CONFIRMED-choice：`cli/confirm.py:_match_edges()` 匹配 choice

两种选择边的**验证规则、匹配逻辑、错误处理**完全不同，但用户视角下它们是同一概念。

---

### 13.2 多轮提问（confirmation_point）的完整状态机

#### 13.2.1 两种多轮确认模式

| 模式 | YAML 配置 | 触发条件 | 每轮行为 | 终止条件 |
|------|----------|----------|----------|----------|
| **Confirmation Point** | `confirmation_point: true` + 任意 confirmed 边 | SubAgent 上报 `AWAITING_CONFIRM` | 确认后 `status=PENDING`，`confirmed_choice=choice`，同 SubAgent 继续 | SubAgent 最终上报 `DONE` |
| **中继确认（Relay）** | `confirmed` 自循环边（`to_stage == from_stage`） | SubAgent 上报 `AWAITING_CONFIRM` | 确认后 `status=PENDING`，`loop_counter++`，`system_agent_id=None`，重新 spawn | `max_loop` 超限 → `loop_exceeded` |

两种模式可以**叠加使用**：一个 stage 既设置 `confirmation_point: true`，又配置自循环 confirmed 边，实现"多轮确认 + 每轮重新 spawn"。

#### 13.2.2 状态流转图

```
                    SubAgent 上报 AWAITING_CONFIRM
                              │
                              ▼
                        ┌─────────────┐
                        │ AWAITING    │
                        │ _CONFIRM    │
                        └──────┬──────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
        用户 confirm      用户 reject     无匹配 choice
              │                │                │
              ▼                ▼                ▼
    ┌─────────────────┐  ┌──────────┐  ┌──────────────┐
    │ confirmation_   │  │ rejected │  │ instance     │
    │ point=true?     │  │ edge     │  │ FAILED       │
    └───────┬─────────┘  └────┬─────┘  └──────────────┘
            │                 │
      ┌─────┴─────┐           │
      ▼           ▼           ▼
    是           否      ┌──────────┐
      │           │      │  DONE    │
      ▼           ▼      │ rejected │
  ┌───────┐   ┌───────┐  └────┬─────┘
  │PENDING│   │ DONE  │       │
  │continue│   │confirmed│     │ 回边？
  └───┬───┘   └───┬───┘       ▼
      │           │      ┌──────────┐
      │           │      │ cascade  │
      ▼           ▼      │ reset    │
  SubAgent    下游 stage  └──────────┘
  继续执行    就绪
```

#### 13.2.3 `TransitionPolicy.on_confirm()` 设计

将 `cli/confirm.py` 中的全部状态转换逻辑纳入 `TransitionPolicy`：

```python
# core/transition.py

@dataclass(frozen=True)
class ConfirmResult:
    """用户确认命令的状态转换决策结果。"""
    next_status: StageStatus
    exit_condition: str = ""
    target_stage_id: str | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    requires_feedback: bool = False      # 是否需要写入 feedback message
    cascade_reset_target: str | None = None  # 回边级联重置目标
    action: str = ""                     # "continue" | "done" | "rejected" | "loop_exceeded" | "error"
    reason: str = ""


@dataclass(frozen=True)
class TransitionPolicy:
    # ... 现有字段和方法 ...

    # ── 确认命令处理 ──

    def on_confirm(self, state: StageState, choice: str) -> ConfirmResult:
        """处理用户确认命令，取代 cli/confirm.py 中直接 mutate instance.json 的逻辑。

        决策顺序：
        1. 匹配 confirmed edges（精确 choice → 兜底无 choice）
        2. 匹配 rejected edges
        3. 无匹配 → ERROR
        """
        matched_confirmed = self._match_edges(self.confirmed_edges, choice)
        if matched_confirmed:
            return self._apply_confirmed_edge(state, matched_confirmed[0], choice)

        matched_rejected = self._match_edges(self.rejected_edges, choice)
        if matched_rejected:
            return self._apply_rejected_edge(state, matched_rejected[0], choice)

        # 无匹配 edge
        valid_choices = self._all_choice_values()
        return ConfirmResult(
            next_status=StageStatus.ERROR,
            action="error",
            reason=f"未知选项: '{choice}'。合法选项: {valid_choices}",
        )

    def _apply_confirmed_edge(self, state: StageState, edge: EdgeSpec, choice: str) -> ConfirmResult:
        """应用 confirmed edge 的状态转换。"""
        # 1. 中继确认：自循环 confirmed 边
        if edge.to_stage == self.stage_id:
            max_loop = edge.max_loop or 0
            if max_loop and state.loop_counter >= max_loop:
                # 超限 → loop_exceeded
                return self._apply_loop_exceeded(state, choice, trigger="confirmed")

            return ConfirmResult(
                next_status=StageStatus.PENDING,
                action="continue",
                updates={
                    "loop_counter": state.loop_counter + 1,
                    "system_agent_id": None,  # 强制重新 spawn
                },
                requires_feedback=True,
            )

        # 2. confirmation_point：确认后继续执行（非终局）
        if self.spec.confirmation_point:
            return ConfirmResult(
                next_status=StageStatus.PENDING,
                action="continue",
                updates={"confirmed_choice": choice},
                exit_condition="",  # 不设置 exit_condition，等 SubAgent 最终上报 DONE
                requires_feedback=True,
            )

        # 3. 终局确认：confirmed edge 指向下游 stage
        # 前置检查：requires_parallel_targets
        if state.requires_parallel_targets and not state.output_message_id:
            return ConfirmResult(
                next_status=StageStatus.ERROR,
                action="error",
                reason=f"Stage '{self.stage_id}' 需要产出 parallel_targets 但消息缺失",
            )

        return ConfirmResult(
            next_status=StageStatus.DONE,
            action="done",
            exit_condition="confirmed",
            updates={"confirmed_choice": choice},
            target_stage_id=edge.to_stage,
            cascade_reset_target=edge.to_stage,  # 若回边则触发级联重置
        )

    def _apply_rejected_edge(self, state: StageState, edge: EdgeSpec, choice: str) -> ConfirmResult:
        """应用 rejected edge 的状态转换。"""
        # 自循环 rejected + max_loop 超限
        if edge.to_stage == self.stage_id:
            max_loop = edge.max_loop or 0
            if max_loop and state.loop_counter >= max_loop:
                return self._apply_loop_exceeded(state, choice, trigger="rejected")

        return ConfirmResult(
            next_status=StageStatus.DONE,
            action="rejected",
            exit_condition="rejected",
            target_stage_id=edge.to_stage,
            updates={"attempt_count": 0},
            cascade_reset_target=edge.to_stage if edge.to_stage != self.stage_id else None,
        )

    def _apply_loop_exceeded(self, state: StageState, choice: str, trigger: str) -> ConfirmResult:
        """loop_exceeded 的统一处理。"""
        if self.loop_exceeded_edge:
            return ConfirmResult(
                next_status=StageStatus.DONE,
                action="loop_exceeded",
                exit_condition="loop_exceeded",
                target_stage_id=self.loop_exceeded_edge.to_stage,
                updates={"loop_counter": state.loop_counter},
                reason=f"{trigger} 循环超限，choice='{choice}'",
            )
        return ConfirmResult(
            next_status=StageStatus.ERROR,
            action="error",
            reason=f"{trigger} 循环超限且无 loop_exceeded_edge",
        )

    def _match_edges(self, edges: list[EdgeSpec], choice: str) -> list[EdgeSpec]:
        """精确匹配 choice 的 edge，若无则返回无 choice 的兜底 edge。"""
        exact = [e for e in edges if e.choice == choice]
        if exact:
            return exact
        return [e for e in edges if not e.choice]

    def _all_choice_values(self) -> list[str]:
        """收集所有 confirmed + rejected 边的 choice 值。"""
        choices: list[str] = []
        for e in self.confirmed_edges + self.rejected_edges:
            if e.choice and e.choice not in choices:
                choices.append(e.choice)
        return choices
```

#### 13.2.4 `cli/confirm.py` 重构为纯 `StateDelta` 驱动

重构后的 `cli/confirm.py` 不再直接 mutate dict，而是成为 `TransitionPolicy` 的调用者：

```python
# cli/confirm.py（重构后）

def _handle_confirm(args) -> dict:
    # 1. 加载状态（新格式）
    state = load_instance_state(args.instance)
    
    # __merge__ 伪 stage 单独处理
    if args.stage == "__merge__":
        return _handle_merge_confirm(args, state)
    
    # 2. 定位 stage
    candidates = state.stages_by_id(args.stage)
    stage = next((s for s in candidates if s.status == StageStatus.AWAITING_CONFIRM), None)
    if not stage:
        raise InputError(f"No AWAITING_CONFIRM instance for stage {args.stage}")
    
    # 3. 构建 TransitionPolicy
    adj = build_adjacency(load_workflow(...))
    policy = TransitionPolicy.from_adjacency(adj, args.stage)
    
    # 4. 决策
    result = policy.on_confirm(stage, args.choice)
    
    # 5. 构建 StateDelta
    delta = StateDelta()
    delta.stage_updates[stage.stage_instance_id] = {
        "status": result.next_status,
        **result.updates,
    }
    if result.exit_condition:
        delta.stage_updates[stage.stage_instance_id]["exit_condition"] = result.exit_condition
    
    # 6. 处理 rejected / loop_exceeded 的目标 stage 激活
    if result.target_stage_id and result.target_stage_id != args.stage:
        target = state.first_stage_by_id(result.target_stage_id)
        if target:
            delta.stage_updates[target.stage_instance_id] = {"status": StageStatus.PENDING}
    
    # 7. 回边级联重置（作为副作用，不纳入 StateDelta）
    if result.cascade_reset_target:
        _cascade_reset(args.instance, state, args.stage, result.cascade_reset_target)
    
    # 8. 写入 feedback message（副作用）
    if result.requires_feedback and args.feedback:
        _write_feedback_message(args.instance, stage, args.choice, args.feedback)
    
    # 9. 应用状态并保存
    new_state = state.apply_delta(delta)
    save_instance_state(args.instance, new_state)
    
    # 10. 清理 running_agents（若级联重置）
    if result.cascade_reset_target:
        _cleanup_running_agents_for_reset(args.instance, [args.stage, result.cascade_reset_target])
    
    return {
        "status": "ok",
        "stage_id": args.stage,
        "new_status": result.next_status.value,
        "action": result.action,
    }
```

**关键收益**：
- `cli/confirm.py` 从 384 行 → ~80 行
- 所有状态转换逻辑集中在 `TransitionPolicy.on_confirm()`
- `StateDelta` 统一表达变更，支持时间旅行调试
- 级联重置、running_agents 清理等副作用与状态变更分离

---

### 13.3 选择边（choice routing）的完整建模

#### 13.3.1 选择边的分类

| 类型 | 边条件 | choice 来源 | 决策时机 | 当前实现位置 |
|------|--------|------------|----------|-------------|
| **运行时选择** | `SUCCESS` | SubAgent 上报 `routing_choice` | 调度器 `next` 时 | `core/dag.py:_all_satisfied` |
| **确认选择** | `CONFIRMED` | 用户 `wfctl confirm --choice` | CLI 命令时 | `cli/confirm.py:_match_edges` |
| **拒绝选择** | `REJECTED` | 用户 `wfctl confirm --choice` | CLI 命令时 | `cli/confirm.py:_match_edges` |

#### 13.3.2 `TransitionPolicy` 统一选择边接口

```python
# core/transition.py

@dataclass(frozen=True)
class TransitionPolicy:
    # ... 现有字段 ...

    # ── 选择边统一接口 ──

    @property
    def has_selective_routing(self) -> bool:
        """该 stage 是否配置了选择边（SUCCESS choice 或 CONFIRMED choice）。"""
        return bool(self.valid_routing_choices() or self.valid_confirm_choices())

    def match_success_edge(self, routing_choice: str | None) -> EdgeSpec | None:
        """根据 SubAgent 上报的 routing_choice 匹配 SUCCESS 边。"""
        success_edges = [e for e in self.ready_edges if e.condition == EdgeCondition.SUCCESS]
        if not success_edges:
            return None
        
        # 无 choice 的边作为兜底
        fallback = next((e for e in success_edges if not e.choice), None)
        
        if not routing_choice:
            return fallback
        
        matched = next((e for e in success_edges if e.choice == routing_choice), None)
        return matched or fallback

    def match_confirmed_edge(self, choice: str) -> EdgeSpec | None:
        """根据用户 choice 匹配 CONFIRMED 边。"""
        return self._match_edge_by_choice(self.confirmed_edges, choice)

    def match_rejected_edge(self, choice: str) -> EdgeSpec | None:
        """根据用户 choice 匹配 REJECTED 边。"""
        return self._match_edge_by_choice(self.rejected_edges, choice)

    def _match_edge_by_choice(self, edges: list[EdgeSpec], choice: str) -> EdgeSpec | None:
        """精确匹配 choice，无匹配时返回无 choice 的兜底 edge。"""
        exact = next((e for e in edges if e.choice == choice), None)
        if exact:
            return exact
        return next((e for e in edges if not e.choice), None)

    def validate_routing_choice(self, routing_choice: str | None) -> tuple[bool, str]:
        """校验 SubAgent 上报的 routing_choice 是否合法。"""
        valid = self.valid_routing_choices()
        if not valid:
            return True, ""  # 无选择边，任何值都合法
        if routing_choice and routing_choice in valid:
            return True, ""
        return False, f"非法 routing_choice: '{routing_choice}'，合法值: {valid}"
```

#### 13.3.3 DAG 验证器新增选择边完备性检查

```python
# core/dag_validator.py

def _check_choice_consistency(adj: AdjacencyList) -> list[ValidationIssue]:
    """检查选择边的语义一致性。

    规则：
    1. 同一组边（SUCCESS / CONFIRMED）中，要么全有 choice，要么全无 choice
    2. choice 值必须互斥（不重复）
    3. confirmation_point=true 的 stage 必须有 rejected 边
    4. 有 choice 的边组必须至少有一条兜底边（无 choice）或覆盖所有可能值
    """
    issues: list[ValidationIssue] = []

    for stage_id in adj.stages:
        spec = adj.stages[stage_id]
        outgoing = adj.outgoing.get(stage_id, [])

        # ── SUCCESS 边 choice 一致性 ──
        success_edges = [e for e in outgoing if e.condition == EdgeCondition.SUCCESS]
        success_with_choice = [e for e in success_edges if e.choice]
        success_without_choice = [e for e in success_edges if not e.choice]

        if success_with_choice and success_without_choice:
            issues.append(ValidationIssue(
                "MIXED_CHOICE_EDGES",
                f"Stage '{stage_id}' 的 SUCCESS 边部分有 choice、部分无 choice，"
                f"无 choice 的边会成为意外兜底",
                stage_id=stage_id,
            ))

        if success_with_choice:
            choices = [e.choice for e in success_with_choice]
            if len(set(choices)) != len(choices):
                issues.append(ValidationIssue(
                    "DUPLICATE_CHOICE",
                    f"Stage '{stage_id}' 的 SUCCESS 边 choice 值重复",
                    stage_id=stage_id,
                ))

        # ── CONFIRMED 边 choice 完备性 ──
        confirmed_edges = [e for e in outgoing if e.condition == EdgeCondition.CONFIRMED]
        if len(confirmed_edges) > 1:
            confirmed_with_choice = [e for e in confirmed_edges if e.choice]
            confirmed_without_choice = [e for e in confirmed_edges if not e.choice]
            
            if confirmed_with_choice and confirmed_without_choice:
                issues.append(ValidationIssue(
                    "MIXED_CONFIRMED_CHOICES",
                    f"Stage '{stage_id}' 的 CONFIRMED 边部分有 choice、部分无 choice",
                    stage_id=stage_id,
                ))
            
            if len(confirmed_edges) > 1 and not all(e.choice for e in confirmed_edges):
                issues.append(ValidationIssue(
                    "INCOMPLETE_CONFIRMED_CHOICES",
                    f"Stage '{stage_id}' 有多条 CONFIRMED 边但部分缺少 choice",
                    stage_id=stage_id,
                ))

        # ── REJECTED 边 choice 完备性 ──
        rejected_edges = [e for e in outgoing if e.condition == EdgeCondition.REJECTED]
        if rejected_edges:
            rejected_with_choice = [e for e in rejected_edges if e.choice]
            rejected_without_choice = [e for e in rejected_edges if not e.choice]
            
            if rejected_with_choice and rejected_without_choice:
                issues.append(ValidationIssue(
                    "MIXED_REJECTED_CHOICES",
                    f"Stage '{stage_id}' 的 REJECTED 边部分有 choice、部分无 choice",
                    stage_id=stage_id,
                ))

        # ── confirmation_point 必须有 rejected 边 ──
        if spec.confirmation_point and not rejected_edges:
            issues.append(ValidationIssue(
                "MISSING_REJECTED_EDGE",
                f"Stage '{stage_id}' 设置了 confirmation_point 但无 rejected 边，"
                f"用户无法拒绝确认",
                stage_id=stage_id,
            ))

        # ── 选择边与无选择边混用检查 ──
        # 如果一个 stage 有 SUCCESS choice 边，就不应该有纯 SUCCESS 边（兜底除外）
        if len(success_with_choice) >= 2 and success_without_choice:
            issues.append(ValidationIssue(
                "AMBIGUOUS_ROUTING",
                f"Stage '{stage_id}' 有 {len(success_with_choice)} 条 SUCCESS 选择边，"
                f"但同时存在无 choice 的兜底边，路由逻辑模糊",
                stage_id=stage_id,
            ))

    return issues
```

#### 13.3.4 消费消息处理器中的选择边校验

```python
# services/scheduler/processors/message_consumer.py

@dataclass
class MessageConsumerProcessor:
    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        # ... 消息扫描逻辑 ...
        
        for msg in messages:
            # ... 前置处理 ...
            
            if new_status == "DONE":
                stage_spec = ctx.adj.stages.get(stage.stage_id)
                policy = TransitionPolicy.from_adjacency(ctx.adj, stage.stage_id)
                
                # 校验 routing_choice
                routing_choice = msg.get("routing_choice")
                valid, error_msg = policy.validate_routing_choice(routing_choice)
                if not valid:
                    delta.stage_updates[stage.stage_instance_id] = {
                        "status": StageStatus.ERROR,
                        "output_message_id": msg_id,
                    }
                    append_deviation(ctx.instance_id, "INVALID_ROUTING_CHOICE", error_msg, 
                                   stage_id=stage.stage_id)
                    continue
                
                # 匹配具体的 SUCCESS 边
                matched_edge = policy.match_success_edge(routing_choice)
                updates = {
                    "status": StageStatus.DONE,
                    "exit_condition": "success",
                    "output_message_id": msg_id,
                }
                if routing_choice:
                    updates["routing_choice"] = routing_choice
                
                delta.stage_updates[stage.stage_instance_id] = updates
            
            # ... ERROR / AWAITING_CONFIRM 处理 ...
```

---

### 13.4 文件变更补充清单

| 变更类型 | 文件 | 补充说明 |
|----------|------|----------|
| **修改** | `core/transition.py` | 新增 `on_confirm()`、`ConfirmResult`、选择边统一接口 |
| **修改** | `core/dag_validator.py` | 新增 `_check_choice_consistency()` |
| **重写** | `cli/confirm.py` | 从 384 行直写 dict 改为调用 `TransitionPolicy.on_confirm()` + `StateDelta` |
| **修改** | `services/scheduler/processors/message_consumer.py` | 增加 `routing_choice` 校验，使用 `policy.match_success_edge()` |
| **修改** | `services/scheduler/state_model.py` | `StageState` 增加 `requires_parallel_targets` 字段（用于 confirm 前置检查） |

---

### 13.5 预期收益补充

| 指标 | 重构前 | 重构后（补充后） |
|------|--------|-----------------|
| 状态机实现数量 | 2 套（Processor + cli/confirm.py） | 1 套（统一 `TransitionPolicy`） |
| `cli/confirm.py` 代码量 | ~384 行 | ~80 行 |
| 选择边处理分散度 | 4 个文件（dag.py、confirm.py、allocate_spawn.py、message_consumer.py） | 1 个文件（`transition.py`） |
| confirmation 状态转换可测试性 | 需模拟 CLI 命令 | `TransitionPolicy.on_confirm()` 纯函数单元测试 |
| 多轮提问 max_loop 检查 | 仅自环 | 自环 + 回边 + confirmation_point 循环 |
| confirmation_point 覆盖率 | 无 rejected 边检查 | 强制要求 rejected 边 |

---

> **总结**：补充后的方案将 `cli/confirm.py` 这个"影子状态机"彻底纳入 `TransitionPolicy`，实现了**所有状态转换的统一建模**。选择边从隐式属性提升为一等概念，DAG 验证器可以拦截 choice 相关的配置错误。


---

## 十四、补充设计：遗漏的散乱机制

> 本章节补充 14 个在原始方案和第一轮补充中遗漏的散乱机制。这些机制在全面代码扫描（`cli/*.py`、`services/*.py`、`core/*.py` 共 40+ 文件）后发现，它们同样是"直接操作 dict"、"绕过 Processor 流水线"、"副作用与状态变更混合"的碎片化根源。

---

### 14.1 遗漏清单总览

| # | 机制 | 位置 | 当前问题 | 方案覆盖状态 |
|---|------|------|----------|-------------|
| 1 | **rollback / skip / pause / resume / terminate** 命令 | `cli/rollback.py` 等 | 都是直接 mutate dict 的"影子状态机"，不经过 Processor 或 `StateDelta` | ❌ 未覆盖 |
| 2 | **`InstanceStatus.PAUSED` 缺失** | `core/schema/interface.py` | `InstanceStatus` 枚举无 `PAUSED`，但 `pause.py` 直接设置 `"PAUSED"` | ❌ 未覆盖 |
| 3 | **`running_agents.json` 代码重复** | `services/scheduler/processors/allocate_spawn.py` | 直接复制了 legacy 的三个函数，非导入复用 | ⚠️ 提及但未提重构方案 |
| 4 | **`__merge__` 伪 stage** | `FinalizeProcessor` + `cli/confirm.py` | 运行时创建的虚拟 stage，不在 `WorkflowSpec` 中，无建模 | ❌ 未覆盖 |
| 5 | **`creator.py` 的 `fast_forward_to` / `clone_from`** | `services/creator.py` | 直接构建 dict 写入 `instance.json`，未使用 `InstanceState` | ❌ 未覆盖 |
| 6 | **`StageState.confirmed` 幽灵字段** | `scheduler_legacy.py` 模板 | dict 中有 `"confirmed": False`，但 `StageState` dataclass 无此字段 | ❌ 未覆盖 |
| 7 | **`EdgeSpec.loop_counter_stage` 死字段** | `core/schema/interface.py` | 定义了但从未被任何代码读取 | ❌ 未覆盖 |
| 8 | **`validator.py` 保护区检测** | `services/validator.py` | 每次检查遍历所有 worktree，效率低，重构未提及 | ❌ 未覆盖 |
| 9 | **`message_handler.py` 重复逻辑** | `services/message_handler.py` | `write_message` 和 `inject_modified_files` 内联重复 | ❌ 未覆盖 |
| 10 | **`_sync_worktree` 静默跳过** | `services/worktree_manager.py` | worktree 同步失败但返回 `True, []`，下游在错误状态执行 | ❌ 未覆盖 |
| 11 | **`terminate.py` 复杂副作用链** | `cli/terminate.py` | 抢救文件+清理 tag+备份+删目录，副作用极长 | ❌ 未覆盖 |
| 12 | **`cleanup.py` / `restore.py`** | `cli/cleanup.py` 等 | 维护命令直接解析 `instance.json`，格式变更后易出错 | ❌ 未覆盖 |
| 13 | **`WFCTL_USE_ORCHESTRATOR` 切换** | `services/scheduler_legacy.py` | 环境变量控制新旧路径，移除策略未提及 | ❌ 未覆盖 |
| 14 | **`status_builder.py` 直接读 dict** | `services/status_builder.py` | 只读但绕过 `InstanceState`，格式变更后易出错 | ❌ 未覆盖 |

---

### 14.2 其他 CLI 命令的影子状态机

`cli/confirm.py` 已在 §13 中覆盖，但**其余 5 个命令**同样是直接 `load_instance` → mutate dict → `save_instance`，完全不经过 `StateDelta`：

#### `cli/rollback.py`（88 行）

```python
def _handle_rollback(args):
    instance = load_instance(args.instance)          # ① 读 dict
    # ... 计算下游 ...
    checkout_to_anchor(args.instance, anchor_name)    # ② Git 副作用
    for s_id in reset_stages:
        s = stage_map.get(s_id)
        if s:
            s["status"] = "PENDING"                   # ③ 直接 mutate
            s["attempt_count"] = 0
            s["loop_counter"] = 0
            s["system_agent_id"] = None
            s.pop("continued_to", None)
            # 清理 consumed_message_ids
            if s.get("output_message_id"):
                consumed.remove(msg_id)               # ④ 直接 mutate list
    save_instance(args.instance, instance)            # ⑤ 写回
```

**散乱点**：
- 状态转换（PENDING 重置）与 Git 副作用（checkout、remove_anchor）混合
- `consumed_message_ids` 清理逻辑与 `MessageConsumerProcessor` 不统一
- `continued_to` 字段清理是隐式约定，无统一重置规则

**重构方向**：

```python
# core/transition.py
@dataclass(frozen=True)
class TransitionPolicy:
    def on_rollback(self, state: InstanceState, stage_id: str) -> RollbackResult:
        """生成回退的 StateDelta，不执行 Git 副作用。"""
        downstream = self._collect_downstream_for_reset(stage_id)
        delta = StateDelta()
        for sid in downstream:
            st = state.first_stage_by_id(sid)
            if st and st.status == StageStatus.DONE:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.PENDING,
                    "attempt_count": 0,
                    "loop_counter": 0,
                    "system_agent_id": None,
                    "continued_to": None,
                    "output_message_id": None,
                }
        # 清理 consumed_message_ids
        new_consumed = state.consumed_message_ids - {
            st.output_message_id for st in state.stages
            if st.stage_id in downstream and st.output_message_id
        }
        delta.instance_updates["consumed_message_ids"] = new_consumed
        return RollbackResult(delta=delta, reset_stages=downstream)
```

`cli/rollback.py` 重构为：
1. `load_instance_state()`
2. `policy.on_rollback(state, stage_id)` → `RollbackResult`
3. `new_state = state.apply_delta(result.delta)`
4. 执行 Git 副作用（`checkout_to_anchor`、`remove_anchor`）
5. `save_instance_state()`
6. `append_timeline()`（副作用）

#### `cli/skip.py`（105 行）

**散乱点**：
- 直接标记 `DONE` 但不触发 `AutoCommitProcessor` 的提交逻辑
- `tag_anchor` 是副作用，但状态转换与副作用混合
- "隔离未消费消息"逻辑与 `MessageConsumerProcessor` 不统一

**重构方向**：

```python
# core/transition.py
def on_skip(self, state: InstanceState, stage_id: str, force: bool = False) -> SkipResult:
    targets = state.stages_by_id(stage_id)
    delta = StateDelta()
    newly_consumed: set[str] = set()
    for st in targets:
        if st.status not in {StageStatus.PENDING, StageStatus.RUNNING,
                             StageStatus.AWAITING_CONFIRM, StageStatus.ERROR}:
            continue
        delta.stage_updates[st.stage_instance_id] = {
            "status": StageStatus.DONE,
            "exit_condition": "skipped",
            "started_at": None,
        }
        # 隔离未消费消息（只读计算，不直接 mutate）
        for msg in self._scan_unconsumed_messages(state.instance_id):
            if msg.get("stage_id") == stage_id:
                newly_consumed.add(msg["message_id"])
    if newly_consumed:
        delta.instance_updates["consumed_message_ids"] = state.consumed_message_ids | newly_consumed
    return SkipResult(delta=delta, skipped_count=len(targets))
```

`cli/skip.py` 重构为：
1. `load_instance_state()`
2. `policy.on_skip(state, stage_id, args.force)`
3. `new_state = state.apply_delta(result.delta)`
4. 执行 `tag_anchor`（副作用）
5. `save_instance_state()` + `append_timeline()` + `append_deviation()`

#### `cli/pause.py` / `cli/resume.py`

**散乱点**：
- `pause` 直接设置 `instance["status"] = "PAUSED"`，但 `InstanceStatus` 枚举中**没有这个值**
- `pause` 重置 `RUNNING → PENDING`，与 `rollback` 的重置逻辑重复

**重构方向**：

```python
# core/schema/interface.py
class InstanceStatus(Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"          # ← 补充

# core/transition.py
def on_pause(self, state: InstanceState) -> StateDelta:
    delta = StateDelta()
    for st in state.stages:
        if st.status == StageStatus.RUNNING:
            delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.PENDING}
    delta.instance_updates["status"] = InstanceStatus.PAUSED
    return delta

def on_resume(self, state: InstanceState) -> StateDelta:
    if state.status != InstanceStatus.PAUSED:
        raise StateError("Instance is not paused")
    return StateDelta(instance_updates={"status": InstanceStatus.ACTIVE})
```

#### `cli/terminate.py`

这是**最复杂的维护命令**，包含 10+ 个步骤的副作用链。它不涉及状态机转换（只是置 `FAILED`），但副作用极长：

```
1. 安全检查（未合入 main 需 --force）
2. 创建备份分支 wf-backup-{id}
3. 归档实例目录 .agent/instances/{id} → .agent/archive/{id}
4. 置 instance.status = FAILED
5. 抢救未提交文件（git add + commit）
6. 清理所有 anchor tag
7. 移除 instance worktree
8. 删除 stage worktree
9. 删除 .agent/instances/{id} 目录
10. 但 save_instance + append_deviation 会重新创建 .agent/instances/{id}
```

**重构方向**：
- 状态转换（`FAILED`）→ `StateDelta`
- 其余全部委托给 `WorktreeManager.terminate_instance()`，作为一个**原子事务**

---

### 14.3 `InstanceStatus.PAUSED` 缺失

当前代码：

```python
# core/schema/interface.py
class InstanceStatus(Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # PAUSED 缺失！

# cli/pause.py
instance["status"] = "PAUSED"  # ← 字符串赋值，枚举不识别
```

**后果**：
- `InstanceState.from_dict()` 解析 PAUSED 实例时会抛出 `ValueError`
- `pause` 后的实例无法被新 Orchestrator 路径正确处理

**修复**：在 `InstanceStatus` 中补充 `PAUSED = "PAUSED"`。

---

### 14.4 `running_agents.json` 管理的代码重复

当前 `AllocateSpawnProcessor` 直接内嵌了三个函数的**复制代码**：

```python
# services/scheduler/processors/allocate_spawn.py（第 158-182 行）
def _lookup_running_agent(self, running_agents: list[dict], skill_id: str) -> dict | None: ...
def _save_running_agent(self, instance_id: str, skill_id: str, ...) -> None: ...
```

原版在 `services/scheduler_legacy.py` 中。这不是 import，是**复制粘贴**。

**重构方向**：提取为独立的管理类：

```python
# services/running_agent_manager.py
class RunningAgentManager:
    """running_agents.json 的单一真相源。"""

    _path: Path = field(init=False)

    def __post_init__(self):
        self._path = find_root() / ".agent" / "running_agents.json"

    def load(self) -> list[dict]: ...

    def save(self, agents: list[dict]) -> None: ...

    def lookup(self, skill_id: str) -> dict | None: ...

    def register(self, agent: dict) -> None: ...

    def remove_for_instance(self, instance_id: str, stage_ids: list[str] | None = None) -> None: ...

    def remove_by_system_agent_id(self, system_agent_id: str) -> None: ...
```

`AllocateSpawnProcessor`、`scheduler_legacy.py`、`cli/confirm.py` 都统一调用 `RunningAgentManager`。

---

### 14.5 `__merge__` 伪 stage 建模

`__merge__` 是一个**运行时才创建的虚拟 stage**，用于根实例全部 DONE 后询问用户是否合入 main。

当前处理分散在：
- `FinalizeProcessor._check_all_done`：创建 `__merge__` stage
- `cli/confirm.py:_handle_merge_confirm`：处理 `__merge__` 的确认/拒绝

**重构方向**：在 `TransitionPolicy` 中显式建模：

```python
# core/transition.py
@dataclass(frozen=True)
class TransitionPolicy:
    def build_merge_stage(self, instance_id: str, goal: str) -> StageState:
        """创建 __merge__ 伪 stage。"""
        return StageState(
            stage_id="__merge__",
            stage_instance_id="__merge__",
            status=StageStatus.AWAITING_CONFIRM,
            confirm_questions=[
                f"实例 {instance_id}（{goal}）全部 stage 已完成，是否合入 main？",
            ],
        )

    def on_merge_confirm(self, state: InstanceState, choice: str) -> MergeConfirmResult:
        """处理 __merge__ 确认。"""
        if choice.lower() in ("yes", "y", "confirm", "accept", "ok"):
            return MergeConfirmResult(
                merge_confirmed=True,
                remove_merge_stage=True,
            )
        return MergeConfirmResult(merge_confirmed=False, remove_merge_stage=True)
```

---

### 14.6 `services/creator.py` 的 `fast_forward_to` / `clone_from`

创建实例时的两种特殊模式：

#### `fast_forward_to`
- 用户指定 `--fast-forward-to s03-impl`
- `creator.py` 调用 `collect_ancestors` 找出所有拓扑前驱
- 将这些前驱 stage 直接标为 `DONE`（**直接构建 dict**）

#### `clone_from`
- 用户指定 `--clone old-instance-id`
- 读取旧实例的 `instance.json`
- 继承旧实例的 `DONE` stage 和消息文件
- 旧实例被标记为 `FAILED`

**重构方向**：

```python
# services/creator.py（重构后）
def create_instance_state(
    workflow_id: str,
    spec: WorkflowSpec,
    goal: str = "",
    fast_forward_to: str | None = None,
    clone_from: str | None = None,
) -> InstanceState:
    """返回初始 InstanceState，不使用 dict 中间态。"""
    stages = _build_initial_stages(spec)  # 返回 list[StageState]
    state = InstanceState(
        instance_id=_generate_instance_id(),
        workflow_id=workflow_id,
        goal=goal,
        stages=stages,
    )

    if fast_forward_to:
        state = _apply_fast_forward(state, spec, fast_forward_to)
    elif clone_from:
        state = _apply_clone_from(state, clone_from)

    return state

def _build_initial_stages(spec: WorkflowSpec) -> list[StageState]:
    return [
        StageState(
            stage_id=s.stage_id,
            stage_instance_id=s.stage_id,
            status=StageStatus.PENDING,
            model=s.model,
        )
        for s in spec.stages
    ]

def _apply_fast_forward(state: InstanceState, spec: WorkflowSpec, target_stage_id: str) -> InstanceState:
    """使用 StateDelta 应用 fast_forward。"""
    adj = build_adjacency(spec)
    ancestors = collect_ancestors(adj, target_stage_id)
    delta = StateDelta()
    for st in state.stages:
        if st.stage_id in ancestors or st.stage_id == target_stage_id:
            delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.DONE}
    return state.apply_delta(delta)
```

---

### 14.7 `StageState.confirmed` 幽灵字段

`scheduler_legacy.py` 的 stage 模板中有 `"confirmed": False`，但 `StageState` dataclass 没有这个字段。

**结论**：这是 legacy 代码的残留字段，新架构从未使用。应在删除 `scheduler_legacy.py` 时一并清除，无需在 `StageState` 中添加。

---

### 14.8 `EdgeSpec.loop_counter_stage` 死字段

```python
# core/schema/interface.py
class EdgeSpec:
    loop_counter_stage: Optional[str] = None  # 从未使用
```

当前循环计数全部存在 stage 自身的 `loop_counter` 上。这个字段是 dead code。

**建议**：Phase 5 清理时从 `EdgeSpec` 中删除，或在 DAG 验证器中标记为 `DEPRECATED_FIELD` 警告。

---

### 14.9 `services/validator.py` 保护区检测

```python
def validate_modified_files(wt: Path, modified_files: list[str], stage_id: str) -> None:
    # 每次检查都要遍历 .tmp/worktrees/stage-*
    for sibling in wt.parent.glob("stage-*"):
        ...
```

**问题**：时间复杂度 O(n) 每消息，n = worktree 数量。

**重构方向**：
- 将保护区规则编码为常量集合
- 使用 `pathlib.Path` 的 `relative_to` 替代字符串拼接
- 缓存 worktree 列表（worktree 不会在单次 `next` 中频繁变化）

```python
# services/validator.py
_PROTECTED_PREFIXES = {".agent", ".claude", ".git", ".tmp"}

def validate_modified_files(wt: Path, modified_files: list[str], stage_id: str) -> None:
    for f in modified_files:
        p = Path(f)
        parts = p.parts
        if any(part in _PROTECTED_PREFIXES for part in parts):
            raise InputError(...)
```

---

### 14.10 `services/message_handler.py` 重复逻辑

`write_message` 和 `inject_modified_files` 有内联重复的 `modified_files` 注入逻辑。

**重构方向**：删除 `inject_modified_files`，将其逻辑内联到 `write_message` 中作为唯一入口。

---

### 14.11 `services/worktree_manager.py:_sync_worktree` 静默跳过

```python
def _sync_worktree(source, target):
    if not _is_clean(target):
        return True, []   # ← 静默成功！
    if _fetch_failed(source):
        return True, []   # ← 静默成功！
```

**后果**：worktree 同步失败但返回成功，下游 Processor 可能在**错误的代码状态**上执行。

**重构方向**：

```python
def _sync_worktree(source, target) -> SyncResult:
    if not _is_clean(target):
        return SyncResult(success=False, reason="target worktree not clean", conflict_files=[...])
    if _fetch_failed(source):
        return SyncResult(success=False, reason="fetch failed")
    return SyncResult(success=True)
```

调用方（`SyncWorktreeProcessor`）应根据 `success=False` 生成 `CONFLICT` action 或终止实例。

---

### 14.12 `cli/terminate.py` 复杂副作用链

终止命令应分解为：**状态转换（`StateDelta`）+ 副作用事务（`WorktreeManager`）**。

```python
# cli/terminate.py（重构后）
def _handle_terminate(args):
    state = load_instance_state(args.instance)

    # 1. 安全检查
    if not args.force and not state.parent_instance_id and not state.merge_confirmed:
        raise InputError("Instance not merged. Use --force to terminate.")

    # 2. 状态转换（StateDelta）
    delta = StateDelta(instance_updates={"status": InstanceStatus.FAILED})
    new_state = state.apply_delta(delta)

    # 3. 副作用事务（全部委托给 WorktreeManager）
    WorktreeManager().terminate_instance(
        instance_id=args.instance,
        anchor_prefix=spec.anchor_prefix,
        backup=True,
        salvage_uncommitted=True,
    )

    # 4. 保存状态
    save_instance_state(args.instance, new_state)
    append_deviation(args.instance, "INSTANCE_TERMINATED", "User terminated instance")
```

---

### 14.13 `WFCTL_USE_ORCHESTRATOR` 切换逻辑

```python
# services/scheduler_legacy.py
if os.environ.get("WFCTL_USE_ORCHESTRATOR") == "1":
    from services.scheduler.orchestrator import SchedulerOrchestrator
    orchestrator = SchedulerOrchestrator()
    return orchestrator.run(ctx, state)
```

**问题**：新旧路径通过环境变量切换，增加了测试复杂度和认知负担。

**移除策略**：
1. Phase 4 完成后，所有测试通过新旧路径的**一致性验证**
2. Phase 5 删除 `scheduler_legacy.py` 时一并移除切换逻辑
3. `SchedulerOrchestrator.run()` 成为唯一入口

---

### 14.14 `services/status_builder.py` 直接读 dict

```python
def _get_child_summary(child_id: str) -> dict:
    child_path = root / ".agent" / "instances" / child_id / "instance.json"
    child = json.loads(child_path.read_text(encoding="utf-8"))  # ← 直接读 dict
```

**风险**：如果 `instance.json` 格式变更（如新增 `CycleMeta`、字段重命名），这里会静默出错。

**重构方向**：统一使用 `load_instance_state()`：

```python
def _get_child_summary(child_id: str) -> dict:
    state = load_instance_state(child_id)
    return {
        "instance_id": state.instance_id,
        "status": state.status.value,
        "goal": state.goal,
        "stage_count": len(state.stages),
        "done_count": sum(1 for s in state.stages if s.status == StageStatus.DONE),
    }
```

---

### 14.15 新增文件变更清单（第二轮补充）

| 变更类型 | 文件 | 说明 |
|----------|------|------|
| **新增** | `services/running_agent_manager.py` | 提取 `running_agents.json` 管理类 |
| **重写** | `cli/rollback.py` | `TransitionPolicy.on_rollback()` + `StateDelta` |
| **重写** | `cli/skip.py` | `TransitionPolicy.on_skip()` + `StateDelta` |
| **重写** | `cli/pause.py` / `resume.py` | 补全 `InstanceStatus.PAUSED` + `StateDelta` |
| **重写** | `cli/terminate.py` | 分解为状态转换 + `WorktreeManager.terminate_instance()` |
| **修改** | `services/creator.py` | `create_instance_state()` 返回 `InstanceState`，使用 `StateDelta` 应用 fast_forward/clone |
| **修改** | `core/schema/interface.py` | `InstanceStatus` 增加 `PAUSED`；`EdgeSpec` 删除 `loop_counter_stage` |
| **修改** | `services/validator.py` | 保护区检测优化，使用集合匹配替代遍历 |
| **修改** | `services/message_handler.py` | 删除 `inject_modified_files`，合并到 `write_message` |
| **修改** | `services/worktree_manager.py` | `_sync_worktree` 返回 `SyncResult` 而非静默 `True, []` |
| **修改** | `services/status_builder.py` | 使用 `load_instance_state()` 替代 `json.loads()` |
| **删除** | `services/scheduler_legacy.py` | Phase 5 删除，移除 `WFCTL_USE_ORCHESTRATOR` 切换逻辑 |

---

### 14.16 实施路线图更新

| 阶段 | 新增内容 |
|------|----------|
| **P1** | 新增 `RunningAgentManager`；修改 `InstanceStatus` 补全 `PAUSED`；删除 `EdgeSpec.loop_counter_stage` |
| **P2** | 重写 `creator.py` 使用 `InstanceState` + `StateDelta`；修改 `message_handler.py` 合并重复逻辑 |
| **P3** | 重写 `cli/rollback.py` / `skip.py` / `pause.py` / `resume.py` 使用 `TransitionPolicy` + `StateDelta` |
| **P4** | 重写 `cli/terminate.py`；修改 `worktree_manager.py:_sync_worktree`；修改 `status_builder.py` |
| **P5** | 删除 `scheduler_legacy.py`；移除 `WFCTL_USE_ORCHESTRATOR`；清理 `StageState.confirmed` 幽灵字段 |

---

> **两轮补充后的完整图景**：重构方案从最初覆盖 Processor 流水线内部，扩展到**所有 CLI 命令层**、**实例创建**、**状态查询**、**辅助服务**。目标是确保 wfctl 的**每一个状态变更**都经过 `StateDelta`，**每一个状态查询**都经过 `InstanceState`，**每一个副作用**都显式声明在 Processor 或 CLI 命令的副作用区，彻底消除"影子状态机"。


---

## 十五、补充设计：第三轮复盘遗漏的 20 个机制

> 经全面扫描代码（70+ 个机制）并与方案逐条交叉核对后，发现仍有 20 处遗漏或覆盖不足。本章按严重程度分级补充。

---

### 15.1 严重遗漏（🔴 方案中完全未提及，重构后会功能缺失或行为变更）

#### 遗漏 1：V2 实例兼容层

```python
# services/state_manager.py:20-26
v2_path = root / ".agent" / "workflows" / "instances" / f"{instance_id}.json"
if v2_path.exists():
    data = json.loads(v2_path.read_text(encoding="utf-8"))
    data["schema_version"] = "3.0.0"   # ← 自动注入
    return data
```

`load_instance` 在找不到 v3 路径时会**自动回退到 v2 平铺格式**并注入 `schema_version`。新架构若假设所有文件都是 v3 格式，会导致旧实例无法加载。

**用户建议的「读取时适配」设计**：

```python
# state/persistence.py
from enum import Enum

class DataVersion(Enum):
    V2 = "2.0.0"
    V3 = "3.0.0"


@dataclass(frozen=True)
class InstanceDataAdapter:
    """读取时适配器：根据实例声明的版本标准化为统一数据模型。

    原则：只读适配，不写回旧格式。旧格式实例在首次 save 时自动升级为 v3。
    """
    raw: dict[str, Any]
    declared_version: DataVersion

    @classmethod
    def from_file(cls, path: Path) -> "InstanceDataAdapter":
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data.get("schema_version", "2.0.0")
        return cls(raw=data, declared_version=DataVersion(version))

    def to_standard(self) -> dict[str, Any]:
        """标准化为 v3 格式的 dict（供 InstanceState.from_dict 使用）。"""
        if self.declared_version == DataVersion.V3:
            return self.raw
        return self._migrate_v2_to_v3()

    def _migrate_v2_to_v3(self) -> dict[str, Any]:
        """v2 → v3 迁移规则。"""
        data = dict(self.raw)
        data["schema_version"] = "3.0.0"
        # v2 的 stages 是平铺 dict，v3 是 list[dict]
        if "stages" in data and isinstance(data["stages"], dict):
            data["stages"] = [
                {"stage_id": k, **v}
                for k, v in data["stages"].items()
            ]
        # v2 缺少的字段填充默认值
        data.setdefault("consumed_message_ids", [])
        data.setdefault("merge_confirmed", False)
        data.setdefault("parent_instance_id", None)
        return data


def load_instance_state(instance_id: str) -> InstanceState:
    """加载实例状态，自动适配旧格式。"""
    root = find_root()
    
    # 1. 尝试 v3 路径
    v3_path = root / ".agent" / "instances" / instance_id / "instance.json"
    if v3_path.exists():
        adapter = InstanceDataAdapter.from_file(v3_path)
        return InstanceState.from_dict(adapter.to_standard())
    
    # 2. 尝试 v2 路径（兼容层）
    v2_path = root / ".agent" / "workflows" / "instances" / f"{instance_id}.json"
    if v2_path.exists():
        adapter = InstanceDataAdapter.from_file(v2_path)
        standard = adapter.to_standard()
        # 迁移后自动写入 v3 路径（一次性升级）
        save_instance_state(instance_id, InstanceState.from_dict(standard))
        v2_path.unlink(missing_ok=True)  # 删除旧文件
        return InstanceState.from_dict(standard)
    
    raise InputError(f"Instance not found: {instance_id}")
```

**核心原则**：
- **读取时适配**：根据实例自身声明的版本（`schema_version` 或默认值）读取，然后标准化
- **只读兼容**：适配器只做读取转换，不写回旧格式
- **一次性升级**：v2 实例在首次加载时自动迁移到 v3 路径，删除旧文件
- **后续代码永远只处理标准化后的 `InstanceState`**

---

#### 遗漏 2：`""` exit_condition 兼容

```python
# core/dag.py:81, 88, 94
if edge.condition == EdgeCondition.SUCCESS and exit_cond not in ("loop_exceeded",):
    # "" 兼容旧实例（升级前已 DONE 的 stage）
    return True
if edge.condition == EdgeCondition.CONFIRMED and exit_cond in ("confirmed", ""):
    # "" 兼容旧实例
    return True
```

`_all_satisfied` 将空字符串视为兼容旧实例的合法值。`TransitionPolicy.is_upstream_satisfied()` 必须保留此语义。

**修复**：在 `TransitionPolicy` 中显式处理：

```python
def is_upstream_satisfied(self, upstream_state: StageState, edge: EdgeSpec) -> bool:
    if upstream_state.status != StageStatus.DONE:
        return False
    exit_cond = upstream_state.exit_condition

    if edge.condition == EdgeCondition.ALWAYS:
        return True
    if edge.condition == EdgeCondition.SUCCESS:
        # "" 兼容旧实例（升级前已 DONE 的 stage 无 exit_condition）
        if exit_cond in ("", "success"):
            if edge.choice:
                return upstream_state.routing_choice == edge.choice
            return True
        if exit_cond == "loop_exceeded":
            return False
        # 其他 exit_condition 不匹配
        return False
    if edge.condition == EdgeCondition.CONFIRMED:
        if exit_cond in ("", "confirmed"):
            if edge.choice:
                return upstream_state.confirmed_choice == edge.choice
            return True
        return False
    return False
```

---

#### 遗漏 3：回边级联重置的具体算法

§13.2 的 `ConfirmResult.cascade_reset_target` 只是标记了目标，但没有展开级联重置的具体操作。

当前算法（`cli/confirm.py:256-347`）：
1. 遍历 `stage_order` 索引确定范围 `[to_idx, from_idx]`
2. 将范围内所有 stage 的现有实例（含 parallel fan-out）**全部移除**
3. 替换为**单一 PENDING 条目**
4. 清理 `running_agents.json` 中被重置 stage 的条目

**重构后的级联重置设计**：

```python
# domain/transition/policy.py
@dataclass(frozen=True)
class CascadeResetResult:
    """回边级联重置的详细描述。"""
    reset_stage_instance_ids: list[str]   # 需要重置的 stage_instance_id 列表
    removed_stage_instance_ids: list[str] # 需要移除的 stage_instance_id 列表（parallel fan-out）
    cleanup_running_agent_stage_ids: list[str]  # 需要清理 running_agents.json 的 stage_id 列表


@dataclass(frozen=True)
class TransitionPolicy:
    def compute_cascade_reset(
        self,
        state: InstanceState,
        from_stage_id: str,
        to_stage_id: str,
        spec: WorkflowSpec,
    ) -> CascadeResetResult:
        """计算回边级联重置的范围。

        范围：[to_stage 在 stage_order 中的位置, from_stage 的位置]（含端点）。
        该范围内的所有 stage（含 parallel fan-out 产生的多个实例）被折叠为单一 PENDING 条目。
        """
        stage_order = [s.stage_id for s in spec.stages]
        try:
            from_idx = stage_order.index(from_stage_id)
            to_idx = stage_order.index(to_stage_id)
        except ValueError:
            return CascadeResetResult([], [], [])

        if to_idx >= from_idx:
            return CascadeResetResult([], [], [])  # 非回边，无需处理

        reset_ids: list[str] = []
        removed_ids: list[str] = []
        cleanup_stage_ids: list[str] = []

        for i in range(to_idx, from_idx + 1):
            sid = stage_order[i]
            # 收集该 stage 的所有实例（含 parallel fan-out）
            instances = state.stages_by_id(sid)
            needs_reset = any(
                s.status in (StageStatus.DONE, StageStatus.ERROR)
                for s in instances
            )
            if not needs_reset:
                continue

            cleanup_stage_ids.append(sid)
            for inst in instances:
                removed_ids.append(inst.stage_instance_id)
            # 新增单一 PENDING 条目
            spec_stage = next((s for s in spec.stages if s.stage_id == sid), None)
            reset_ids.append(sid)  # stage_instance_id = stage_id（重置后）

        return CascadeResetResult(
            reset_stage_instance_ids=reset_ids,
            removed_stage_instance_ids=removed_ids,
            cleanup_running_agent_stage_ids=cleanup_stage_ids,
        )
```

`cli/rollback.py` 和 `cli/confirm.py` 的回边处理统一调用此方法。

---

#### 遗漏 4：消息消费的幂等性

```python
# services/state_manager.py:84-88
if old_status == new_status:
    consumed_ids.add(msg["message_id"])
    continue   # ← 状态无变化时仅消费消息 ID，不覆盖已有字段
```

`MessageConsumerProcessor` 必须明确保留此语义：

```python
# scheduler/processors/02_message_consumer.py
for msg in messages:
    stage = state.stage_by_instance_id(msg_stage_inst_id)
    if not stage:
        continue

    new_status_str = msg.get("status", stage.status.value)
    if stage.status.value == new_status_str:
        # 幂等：状态无变化，只消费消息 ID，不覆盖已有字段
        new_consumed.add(msg_id)
        continue

    # 状态有变化，执行转换...
```

**关键场景**：用户 confirm 后 stage 变为 PENDING，SubAgent 的延迟 RUNNING 消息到达时，若 stage 已经是 PENDING，则只消费消息不覆盖。

---

#### 遗漏 5：CONFLICT 自动重试合并（自愈机制）

`ConflictHandlerProcessor` 每次 `next` 都会尝试对 CONFLICT stage 重新执行 `resolve_conflicts_and_merge`。**用户解决冲突后无需额外命令**，下次 `next` 自动恢复。

方案中只是简单提及，需要强调这是**自愈机制**：

```python
# scheduler/processors/10_conflict_handler.py
@dataclass
class ConflictHandlerProcessor:
    """冲突自愈处理器。

    每次 next 都会尝试对 CONFLICT stage 重新合并。
    用户手动解决冲突文件后，无需任何命令，下次 next 自动检测并恢复。
    """
    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        actions: list[dict] = []

        for st in state.stages:
            if st.status != StageStatus.CONFLICT:
                continue

            success, conflict_files = resolve_conflicts_and_merge(
                ctx.instance_id, st.stage_instance_id
            )
            if success:
                delta.stage_updates[st.stage_instance_id] = {
                    "status": StageStatus.DONE,
                    "conflict_files": [],
                }
                actions.append({
                    "action": "resolved",
                    "instance_id": ctx.instance_id,
                    "stage_id": st.stage_id,
                })
            else:
                # 冲突仍存在，保持 CONFLICT 状态，等待用户下次解决
                if conflict_files != st.conflict_files:
                    delta.stage_updates[st.stage_instance_id] = {
                        "conflict_files": conflict_files,
                    }
                # 不生成 action，静默等待下次 next

        return ProcessorResult(state_delta=delta, actions=actions)
```

---

#### 遗漏 6：虚拟 Stage 级联预处理

`VirtualStagesProcessor` 使用 **`while changed` 循环**支持级联：s00-virtual → s01-virtual → s02-real 可在**同一次 next** 中全部完成。

```python
# scheduler/processors/03_virtual_stages.py
@dataclass
class VirtualStagesProcessor:
    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        delta = StateDelta()
        # 使用循环而非单次遍历，支持级联
        changed = True
        while changed:
            changed = False
            for st in state.stages:
                spec = ctx.adj.stages.get(st.stage_id)
                if not spec or spec.target_type != StageTargetType.VIRTUAL:
                    continue
                if st.status != StageStatus.PENDING:
                    continue
                upstream = ctx.adj.incoming.get(st.stage_id, [])
                if _all_satisfied(upstream, state, st.stage_id):
                    delta.stage_updates[st.stage_instance_id] = {
                        "status": StageStatus.DONE,
                    }
                    # 标记 cycle_meta，供 AutoCommit 使用
                    # 注意：虚拟 stage 的锚点可以省略（无代码产出）
                    changed = True
            if changed:
                # 应用 delta 后继续循环，检测下游虚拟 stage
                state = state.apply_delta(delta)

        return ProcessorResult(state_delta=delta)
```

---

#### 遗漏 7：合并策略差异（`--no-ff` vs `ff`）

```python
# core/git_ops.py
def git_merge(repo, branch, no_ff=True): ...

# Stage → Instance：默认 --no-ff（强制产生 merge commit）
# Instance → Main：允许 fast-forward（no_ff=False）
```

需要在 `WorktreeManager` 中显式保留此差异，避免统一为同一策略：

```python
# runtime/worktree/manager.py
def merge_stage_into_instance(self, instance_id: str, stage_inst_id: str) -> tuple[bool, list[str]]:
    """Stage worktree 合并回实例 worktree。使用 --no-ff 保留 stage 边界。"""
    return self._merge(instance_id, stage_inst_id, no_ff=True)

def merge_instance_to_main(self, instance_id: str) -> tuple[bool, list[str]]:
    """实例 worktree 合并入 main。允许 fast-forward。"""
    return self._merge(instance_id, "main", no_ff=False)
```

---

#### 遗漏 8：`_is_terminal_stage` 的硬编码语义

当前终态判断依赖字符串匹配，建议改为显式配置：

```python
# domain/workflow/spec.py
@dataclass
class StageSpec:
    # ... 现有字段 ...
    terminal: bool = False   # ← 新增：显式标记终态 stage

# domain/transition/policy.py
def _is_terminal_stage(self, stage_id: str) -> bool:
    spec = self.adj.stages.get(stage_id)
    return spec.terminal if spec else False
```

DAG 验证器新增检查：

```python
def _check_terminal_stages(adj: AdjacencyList) -> list[ValidationIssue]:
    """检查终态 stage 的配置合法性。"""
    issues = []
    for stage_id, spec in adj.stages.items():
        if spec.terminal:
            # 终态 stage 不应有出边（除了 VIRTUAL 的结束标记语义）
            outgoing = adj.outgoing.get(stage_id, [])
            if any(e.condition not in (EdgeCondition.ALWAYS,) for e in outgoing):
                issues.append(ValidationIssue(
                    "TERMINAL_STAGE_HAS_OUTGOING_EDGES",
                    f"终态 stage '{stage_id}' 不应有非 ALWAYS 出边",
                    stage_id=stage_id,
                ))
    return issues
```

---

#### 遗漏 9：Create 异常回滚

```python
# services/creator.py:236-251
# 创建实例过程中任何异常都会清理已创建的 worktree、anchor tag、instance 目录
```

重构后的 `creator.py` 需要保留此异常回滚语义：

```python
# services/creator.py
def create_instance(...):
    created_resources: list[Callable] = []  # 资源创建栈
    try:
        # 1. 创建 worktree
        wt = WorktreeManager().create_instance_worktree(instance_id)
        created_resources.append(lambda: WorktreeManager().remove_worktree(wt))
        
        # 2. 打初始锚点
        anchor = tag_anchor(instance_id, ...)
        created_resources.append(lambda: remove_anchor(instance_id, anchor))
        
        # 3. 写入 instance.json
        save_instance_state(instance_id, state)
        created_resources.append(lambda: delete_instance_dir(instance_id))
        
        return {"instance_id": instance_id, ...}
    except Exception:
        # 异常回滚：按逆序清理已创建资源
        for cleanup in reversed(created_resources):
            try:
                cleanup()
            except Exception:
                pass  # 回滚过程中的异常静默忽略
        raise
```

---

#### 遗漏 10：Cleanup 对一级未合并实例的保护

```python
# cli/cleanup.py:150-154
# 一级（无 parent）FAILED / ACTIVE 僵尸实例未合入 main 时，非 --force 跳过清理
```

重构后的 cleanup 命令需要保留此安全边界：

```python
# cli/workflow/cleanup.py
def _should_protect_instance(instance: InstanceState) -> tuple[bool, str]:
    """判断实例是否应受保护（防止误删未合并成果）。"""
    if instance.parent_instance_id:
        return False, ""  # 子实例不受保护
    if instance.status in (InstanceStatus.ACTIVE, InstanceStatus.FAILED):
        if not instance.merge_confirmed:
            return True, f"实例 {instance.instance_id} 未合入 main，跳过清理（使用 --force 强制清理）"
    return False, ""
```

---

### 15.2 覆盖不足（🟡 方案中提及但未详细展开）

#### 遗漏 11：子实例 `await` 不传播

子实例的 `await` action 不应合并到父实例结果：

```python
# scheduler/processors/08_child_workflow.py
for action in child_result.get("actions", []):
    if action.get("action") == "await":
        continue  # ← 子实例的 await 不传播
    actions.append(self._flatten_action(action, st.stage_id))
```

---

#### 遗漏 12：子实例递归后二次检查

```python
# scheduler/processors/08_child_workflow.py
def process(self, ctx, state):
    # 1. 首次检查子实例状态
    self._check_child_workflows(state, ctx, delta)
    
    # 2. 递归调度子实例
    child_actions = self._recurse_children(state, ctx)
    actions.extend(child_actions)
    
    # 3. 递归后二次检查（捕获本次 next 中完成的子实例）
    self._check_child_workflows(state, ctx, delta)
```

---

#### 遗漏 13：消息文件解析失败容错

```python
# runtime/message/handler.py
@dataclass
class MessageScanner:
    def scan(self, instance_id: str, consumed_ids: frozenset[str]) -> list[dict]:
        messages: list[dict] = []
        for msg_path in sorted(messages_dir.glob("*.json")):
            try:
                msg = json.loads(msg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # 单条消息损坏不中断整体消费
                import logging
                logging.warning(f"损坏的消息文件，跳过: {msg_path.name}")
                continue
            # ...
        return messages
```

---

#### 遗漏 14：超时自动 ERROR（合成消息机制）

```python
# scheduler/processors/09_error_recovery.py
def _check_timeouts(self, ctx, state) -> StateDelta:
    for st in state.stages:
        if st.status != StageStatus.RUNNING or not st.started_at:
            continue
        elapsed = time.time() - parse_iso_timestamp(st.started_at)
        if elapsed > timeout:
            # 不是直接修改状态，而是写入合成 ERROR 消息
            # 由下次 next 的 MessageConsumerProcessor 统一消费
            self._write_synthetic_error_message(ctx.instance_id, st, elapsed)
            delta.stage_updates[st.stage_instance_id] = {
                "started_at": None,  # 重置 started_at，防止重复超时
            }
    return delta
```

**为什么用合成消息而不是直接修改状态？**
- 保证所有状态变更都经过 `MessageConsumerProcessor` 的统一路径
- timeline 可以记录消息来源（`message_id`）
- 支持消息审计和重放

---

### 15.3 建议补充（🟢 非功能性遗漏）

#### 遗漏 15：硬编码约定集中梳理

| 魔法值 | 当前位置 | 重构后归属 |
|--------|---------|-----------|
| 实例 ID 格式 `YYYYMMDD-NNN` | `creator.py` | `services/creator.py:_generate_instance_id()` |
| 消息 ID 格式 `msg-{uuid[:8]}` | `message_handler.py` | `runtime/message/handler.py:_generate_message_id()` |
| Anchor 命名 `{prefix}-{id}-{stage}` | 多处 | `runtime/worktree/naming.py:AnchorNaming` |
| Worktree 目录命名 | 多处 | `runtime/worktree/naming.py:WorktreeNaming` |
| Parallel reinforce `max_retry=2` | `parallel_split.py` | `scheduler/processors/07_parallel_split.py:MAX_REINFORCE_RETRY` |
| 嵌套深度上限 `3` | `creator.py` | `services/creator.py:MAX_NESTING_DEPTH` |
| 锁超时 `15s` / `10s` | `scheduler_legacy.py` | `infrastructure/lock.py:DEFAULT_LOCK_TIMEOUT` |
| 提交信息 Trailer | `auto_commit.py` | `scheduler/processors/05_auto_commit.py:COMMIT_TRAILERS` |

#### 遗漏 16：测试 Fixtures 的目录结构假设

```python
# tests/services/conftest.py
temp_git_repo 创建 .claude/、.agent/、.tmp/worktrees/、.claude/workflows/
```

目录重组后需要同步更新。建议在 `tests/infrastructure/conftest.py` 中提供统一的 `temp_project_root` fixture。

#### 遗漏 17：全局异常捕获与 JSON 输出契约

```python
# cli/main.py:30-79
```

所有 CLI 统一输出 `{"status": "error", "code": ...}` 到 stderr。重构后的 CLI 命令层需要保持此契约。

#### 遗漏 18：Identity 文件权限隔离

```python
# cli/identity.py:28-29
# 过滤掉 project_root 字段，防止 SubAgent 通过 identity 文件逃逸到项目根目录
```

安全边界，重构时若改 identity schema 易遗漏。

#### 遗漏 19：Parallel Targets CLI 解析

```python
# cli/message/write.py:58-69
# --parallel-targets 按 id:label:context 格式拆分
```

格式错误时静默使用默认值，这个容错行为需要在消息协议文档中明确。

#### 遗漏 20：提交信息 Trailer

```python
# auto-commit 的 commit message 包含 wf-stage: / wf-instance: / wf-message:
```

用于 Git 历史追踪的元数据约定，在 `AutoCommitProcessor` 中保留。

---

### 15.4 数据兼容层的「读取时适配」设计总结

> 用户核心建议：**根据实例自身声明的版本读取数据，然后进行标准化（统一数据模型）。这样只需要对输入做兼容处理。**

这个设计比「来回转换」优雅得多：

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────┐
│  instance.json  │────▶│  读取时适配器 │────▶│  InstanceState  │
│  (v2 或 v3)     │     │  (标准化)    │     │  (统一模型)     │
└─────────────────┘     └──────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌──────────────┐
                        │  后续代码     │
                        │  永远只处理   │
                        │  InstanceState│
                        └──────────────┘
```

**关键收益**：
- **单向适配**：只读转换，不写回旧格式
- **一次性升级**：v2 实例首次加载时自动迁移到 v3 路径
- **后续代码零负担**：所有 Processor、CLI 命令、验证器都只处理 `InstanceState`
- **新增版本时**：只需在 `InstanceDataAdapter` 中增加 `_migrate_vN_to_v3()` 方法

---

> **三轮补充后的完整图景**：方案覆盖了 Processor 流水线、DAG 引擎、状态模型、CLI 命令层、数据兼容层、运行时资源、安全边界。核心原则是：
> 1. **读取时适配**：旧格式实例在加载时标准化
> 2. **统一状态模型**：所有代码只处理 `InstanceState`
> 3. **统一状态转换**：所有变更都经过 `StateDelta`
> 4. **显式副作用**：副作用与状态变更分离，副作用在 Processor/CLI 的副作用区执行
> 5. **自愈机制保留**：CONFLICT 自动重试、超时合成消息、级联虚拟 stage 等隐式行为显式建模
