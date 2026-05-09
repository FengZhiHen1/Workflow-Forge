# wfctl 内部设计 v1.0.0

---

## 〇、设计决策汇总

| 维度 | 决策 |
|------|------|
| 包结构 | 三层分层架构：cli → services → core |
| DAG 引擎 | 邻接表 + BFS 就绪计算 |
| Git 抽象 | 薄封装函数（subprocess.run 包装） |
| CLI 框架 | argparse 标准库 |
| 状态 IO | 原子写入（tmp + os.replace）+ 文件锁 |
| 错误处理 | WfctlError 异常体系 |
| Schema 兼容 | 适配器模式，内部统一规范表示 |
| 测试 | 单测（core）+ 集成测试（services/git） |
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
├── cli/                         # 接口层 —— 命令入口，无业务逻辑
│   ├── __init__.py
│   ├── resolve.py               # resolve 命令
│   ├── create.py                # create 命令
│   ├── next_cmd.py              # next 命令
│   ├── sync.py                  # sync 命令
│   ├── confirm.py               # confirm 命令
│   ├── rollback.py              # rollback 命令
│   ├── terminate.py             # terminate 命令
│   ├── status.py                # status 命令
│   ├── deviate.py               # deviate 命令
│   ├── identity.py              # identity 命令
│   └── message_write.py         # message write 命令
│
├── services/                    # 业务层 —— 编排逻辑
│   ├── __init__.py
│   ├── resolver.py              # 工作流发现与解析
│   ├── creator.py               # 实例创建
│   ├── scheduler.py             # next 调度核心（DAG 计算 + action 生成）
│   ├── state_manager.py         # instance.json 读写 + 消息消费
│   ├── worktree_manager.py      # worktree 生命周期管理
│   ├── terminator.py            # 实例终止与清理
│   ├── message_handler.py       # 消息写入、校验、消费
│   ├── validator.py             # 权限校验、保护区检测
│   └── status_builder.py        # status 命令的聚合视图构建
│
├── core/                        # 基础层 —— 零业务语义
│   ├── __init__.py
│   ├── schema/                  # Schema 适配器
│   │   ├── __init__.py
│   │   ├── interface.py         # WorkflowSpec / StageSpec / EdgeSpec 内部规范表示
│   │   ├── v3.py                # schema_version "3.0.0" 适配器
│   │   └── loader.py            # 根据 schema_version 自动选择适配器
│   ├── dag.py                   # DAG 引擎：邻接表构建、BFS 就绪计算、下游遍历
│   ├── git_ops.py               # Git 薄封装函数
│   ├── lock.py                  # 跨平台文件锁
│   ├── atomic_write.py          # 原子写入（tmp + os.replace）
│   ├── project.py               # 项目根发现（向上查找 .claude/）
│   ├── errors.py                # 异常体系定义
│   └── logging.py               # stderr 结构化日志
│
└── tests/                       # 测试
    ├── __init__.py
    ├── core/                    # 单元测试（不涉及 git / 文件锁）
    │   ├── test_dag.py
    │   ├── test_schema_v3.py
    │   ├── test_schema_loader.py
    │   ├── test_atomic_write.py
    │   ├── test_lock.py
    │   ├── test_project.py
    │   └── test_errors.py
    ├── services/                # 集成测试（临时 git 仓库）
    │   ├── conftest.py          # pytest fixtures：临时 git repo、示例 WORKFLOW.yaml
    │   ├── test_resolver.py
    │   ├── test_creator.py
    │   ├── test_scheduler.py
    │   ├── test_state_manager.py
    │   ├── test_worktree_manager.py
    │   ├── test_message_handler.py
    │   └── test_validator.py
    └── cli/                     # 端到端冒烟测试
        ├── test_resolve_cmd.py
        ├── test_create_cmd.py
        └── test_next_cmd.py
```

### 依赖方向

```
cli ──→ services ──→ core
        (services 不依赖 cli)
        (core 不依赖 services 和 cli)
```

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

### 2.2 内部规范表示（`core/schema/interface.py`）

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
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class EdgeCondition(Enum):
    ALWAYS = "always"
    SUCCESS = "success"
    FAILURE = "failure"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
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
    confirmation_point: bool
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
    loop_counter_stage: Optional[str]
    choice: Optional[str]
    aggregation: str             # "all" | "any"

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
# core/schema/loader.py

def load_workflow(yaml_path: Path) -> WorkflowSpec:
    """读取 WORKFLOW.yaml，按 schema_version 选择适配器，返回 WorkflowSpec。"""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    version = raw["schema_version"]
    adapter = _get_adapter(version)     # 按版本号匹配适配器
    return adapter.parse(raw)

# core/schema/v3.py

class V3Adapter:
    """schema_version "3.0.0" 适配器"""
    def parse(self, raw: dict) -> WorkflowSpec:
        # 校验必填字段
        # 转换为 WorkflowSpec + StageSpec + EdgeSpec
        ...
```

新增 `v4.py` 时，只需实现相同的 `parse(raw) -> WorkflowSpec` 接口，在 `_get_adapter` 中注册即可。services 层完全无感。

---

## 三、DAG 引擎（`core/dag.py`）

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

```
输入：AdjacencyList, instance.json (各 stage 当前状态), consumed_message_ids
输出：就绪的 stage_id 列表

compute_ready(adj, instance):
  ready = []
  for each stage in adj.stages:
    if stage.status != PENDING:
      continue
    upstream = adj.incoming[stage.stage_id]
    if all_satisfied(upstream, instance):   # 检查每个上游是否已满足解锁条件
      ready.append(stage.stage_id)
  return ready

all_satisfied(upstream_edges, instance):
  for edge in upstream_edges:
    upstream_stage = instance.stages[edge.from_stage]
    if edge.condition == ALWAYS:
      continue                             # 无条件边总满足
    if edge.condition == SUCCESS and upstream_stage.status != DONE:
      return False
    if edge.condition == CONFIRMED and upstream_stage.status != DONE:
      return False                          # confirm 命令已将 stage 置为 DONE，无需额外 confirmed 标志
    if edge.condition == FAILURE:
      # failure 边只在 ERROR 且重试耗尽时触发，不影响正常依赖
      continue
    if edge.condition == REJECTED:
      continue
    if edge.condition == LOOP_EXCEEDED:
      continue
  return True
```

### 3.4 下游遍历（回退 / 级联清理）

```python
def collect_downstream(adj: AdjacencyList, stage_id: str,
                       exclude_conditions: set[EdgeCondition]) -> set[str]:
    """BFS 从 stage_id 出发，沿 edges 遍历所有可达 stage，
       排除指定 condition 的边（如 failure、loop_exceeded）。
       返回受影响 stage_id 集合。"""
```

### 3.5 关键点

- 虚拟 stage（`s00-workflow-start`、`s99-workflow-end`）由 wfctl 内部处理，不分配 worktree，不生成 action
- `aggregation=any` 的 parallel 拆分：任一实例 DONE 即解锁下游，其余实例被标记为 SUPERSEDED（写入 stage_history，不参与流转）
- `exclusive` 的调度约束在 scheduler 层处理，dag 只管就绪判断

---

## 四、状态管理（`services/state_manager.py`）

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

兼容读取：若目标路径不存在，回退扫描 `.agent/workflows/instances/<id>.json`（v2 平铺式）和
`.agent/messages/<YYYY-MM-DD>/<message_id>.json`（v2 消息路径），
确保旧实例不丢失。写入始终使用 v3 目录式。

### 4.2 原子写入

```python
# core/atomic_write.py

def atomic_write_json(path: Path, data: dict) -> None:
    """写入临时文件后 os.replace，保证写入原子性。"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # Windows/Unix 均为原子操作
```

### 4.3 文件锁

```python
# core/lock.py

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

### 4.4 消息消费

```
state_manager.consume_messages(instance_id):
  1. 获取实例级文件锁
  2. 读 instance.json → consumed_message_ids 列表
  3. 扫描 `.agent/instances/<id>/messages/` 下所有消息（兼容扫描 v2 `.agent/messages/`）
  4. 过滤出 message.instance_id == instance_id 且未消费的消息
  5. 对每条消息：
     a. 调用 validator 校验 modified_files
     b. 通过 → 按 message.status 更新对应 stage 状态
     c. 违规 → stage → ERROR，写 deviation
     d. 追加 message_id 到 consumed_message_ids
  6. 原子写入更新后的 instance.json
  7. 释放锁
  8. 返回状态变更摘要
```

---

## 五、Git 操作（`core/git_ops.py`）

### 5.1 设计原则

- 纯函数，每个函数封装一个 git 命令
- 统一返回 `(returncode: int, stdout: str, stderr: str)`
- 不记录日志（日志由调用方 services 层写入 timeline）
- 不抛异常（调用方根据 returncode 决定是否抛 GitError）

### 5.2 函数清单

```python
def git_worktree_add(path: Path, base_ref: str, branch: Optional[str] = None) -> tuple[int, str, str]:
    """git worktree add <path> [ -b <branch> ] <base_ref>"""

def git_worktree_remove(path: Path, force: bool = False) -> tuple[int, str, str]:
    """git worktree remove <path> [--force]"""

def git_worktree_list(repo_root: Path) -> tuple[int, str, str]:
    """git worktree list --porcelain"""

def git_fetch(repo: Path, source: Path, refspec: str) -> tuple[int, str, str]:
    """git -C <repo> fetch <source> <refspec>"""

def git_merge(repo: Path, ref: str, no_ff: bool = True) -> tuple[int, str, str]:
    """git -C <repo> merge <ref> [--no-ff]"""

def git_checkout(repo: Path, ref: str) -> tuple[int, str, str]:
    """git -C <repo> checkout <ref>"""

def git_tag(repo: Path, tag_name: str, ref: str = "HEAD") -> tuple[int, str, str]:
    """git -C <repo> tag <tag_name> <ref>"""

def git_tag_delete(repo: Path, tag_name: str) -> tuple[int, str, str]:
    """git -C <repo> tag -d <tag_name>"""

def git_status_porcelain(repo: Path) -> tuple[int, str, str]:
    """git -C <repo> status --porcelain"""

def git_merge_base(repo: Path, ref_a: str, ref_b: str) -> tuple[int, str, str]:
    """git -C <repo> merge-base <ref_a> <ref_b>"""

def git_rev_parse(repo: Path, ref: str) -> tuple[int, str, str]:
    """git -C <repo> rev-parse <ref>"""

def git_add_all(repo: Path) -> tuple[int, str, str]:
    """git -C <repo> add -A"""

def git_commit_file(repo: Path, message_file: Path) -> tuple[int, str, str]:
    """git -C <repo> commit -F <message_file>"""
```

### 5.3 调用方使用模式

```python
rc, stdout, stderr = git_worktree_add(path, "HEAD", branch="wf-stage-xxx-xxx")
if rc != 0:
    raise GitError(f"worktree add failed: {stderr}")
```

---

## 六、调度核心（`services/scheduler.py`）

### 6.1 `next` 命令处理流程

```
next --instance <id>
  │
  ├─ 1. project.find_root() 确定项目根
  ├─ 2. state_manager.load_instance(id)         # 读 instance.json + 获取锁
  ├─ 3. schema.loader.load_workflow(yaml_path)  # 解析 WORKFLOW.yaml
  ├─ 4. state_manager.consume_messages(id)      # 消费消息池 → 更新 stage 状态
  ├─ 5. _auto_commit_done_stages()             # 对刚 DONE 的 stage 自动提交 worktree 变更（见 § 自动提交）
  ├─ 5.5 _merge_concurrent_stages()            # 多 stage 同时 DONE 时按 stage_id 字典序依次合并
  ├─ 6. dag.build_adjacency(spec)               # 构建邻接表
  ├─ 7. _check_parallel()                       # 检查 parallel 拆分需求
  ├─ 8. _check_child_workflows()                # 检查子工作流完成状态
  ├─ 9. _handle_error_stages()                  # 处理 ERROR 分支（retry/failure edge/terminate）
  ├─10. _handle_conflict_stages()               # 处理 CONFLICT 分支
  ├─11. _compute_ready_stages()                 # 就绪计算
  ├─12. _apply_scheduling_constraints()          # exclusive / max_parallel_agents 约束
  ├─13. _allocate_worktrees()                   # worktree 分配（单/多/parallel）
  ├─14. _check_merge_to_main()                  # 全部 DONE → 返回 merge_to_main
  ├─15. _collect_confirm_actions()              # 聚合 AWAITING_CONFIRM
  ├─16. _cleanup_if_terminal()                  # 检测实例终态（COMPLETED/FAILED）→ 自动清理 worktree
  ├─17. state_manager.save_instance(id)         # 原子写回
  ├─18. 释放锁
  └─19. 返回 action 数组
```

### 6.2 action 生成

每个步骤返回的 action 追加到数组中，最终返回批量数组。主 Agent 按并发规则批量启动 SubAgent。

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
def _auto_commit_done_stages(instance: dict, instance_id: str,
                              changes: list[dict], worktree_map: dict[str, Path]) -> None:
    """对刚转为 DONE 的 stage，在其 worktree 中自动提交。"""
    done_changes = [c for c in changes if c["new_status"] == "DONE"]
    for change in done_changes:
        stage_id = change["stage_id"]
        msg = change["message"]
        worktree = worktree_map.get(stage_id)
        if not worktree:
            continue

        report = msg.get("report", f"stage {stage_id} done")
        stage_inst = msg.get("stage_instance_id", stage_id)
        message_id = msg.get("message_id", "")

        # 组装完整 commit message
        full_msg = f"{report}\n\nwf-stage: {stage_inst}\nwf-instance: {instance_id}\nwf-message: {message_id}"

        # 写入临时文件，通过 git commit -F 提交
        msg_file = worktree / ".wfctl_commit_msg"
        msg_file.write_text(full_msg, encoding="utf-8")

        rc, _, stderr = git_add_all(worktree)
        if rc != 0:
            msg_file.unlink(missing_ok=True)
            raise GitError(f"add failed for stage {stage_id}: {stderr}")

        rc, _, stderr = git_commit_file(worktree, msg_file)
        msg_file.unlink(missing_ok=True)
        if rc != 0:
            raise GitError(f"commit failed for stage {stage_id}: {stderr}")
```

**提交信息格式**：

```
<report>

wf-stage: s03
wf-instance: 20260517-001
wf-message: msg-a1b2c3d4
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
def _allocate_worktrees(ready: list[str], running: list[StageState],
                         spec: WorkflowSpec) -> dict[str, Path]:
    """为每个就绪 stage 分配 worktree 路径。

    单 stage 就绪 → 实例 worktree（不拆分）
    多 stage 并发就绪 → 每个 stage 独立的 stage 级 worktree
    parallel 拆分 → stage-<id>-<s_id>#<n>
    """
```

---

## 七、CLI 层（`cli/`）

### 7.1 入口（`main.py`）

```python
import argparse
import sys
from core.errors import WfctlError
from core.logging import log_error

def main():
    parser = argparse.ArgumentParser(prog="wfctl", description="工作流机械调度程序")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 注册各子命令
    register_resolve(subparsers)
    register_create(subparsers)
    register_next(subparsers)
    register_sync(subparsers)
    register_confirm(subparsers)
    register_rollback(subparsers)
    register_terminate(subparsers)
    register_status(subparsers)
    register_deviate(subparsers)
    register_identity(subparsers)
    register_message_write(subparsers)

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

### 7.2 子命令注册示例

```python
# cli/next_cmd.py
def register_next(subparsers):
    p = subparsers.add_parser("next", help="调度核心：消费消息，推进状态，返回 action")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_next)

def _handle_next(args) -> dict:
    from services.scheduler import run_next
    return run_next(args.instance)
```

### 7.3 命令参数一览

| 命令 | 必填参数 | 可选参数 |
|------|---------|---------|
| `resolve` | — | `--workflow <id>@<ver>` |
| `create` | `--workflow <id>@<ver>` | `--goal "..."` |
| `next` | `--instance <id>` | — |
| `sync` | `--instance <id>` | — |
| `confirm` | `--instance <id> --stage <id> --choice <value>` | `--feedback "..."` |
| `rollback` | `--instance <id> --stage <id>` | — |
| `status` | — | `--instance <id>` |
| `deviate` | `--instance <id> --type <type> --reason "..."` | `--stage <id> --files ...` |
| `terminate` | `--instance <id>` | — |
| `identity` | — | — |
| `message` `write` | `--instance <id> --stage <id> --status <status> --report "..."` | `--checkpoint "..." --questions ... --parallel-targets ...` |

---

## 八、异常体系（`core/errors.py`）

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

## 九、日志系统（`core/logging.py`）

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

审计日志（timeline、deviation）是 wfctl 业务数据的一部分，由 `services/state_manager.py` 在状态变更时追加写入。不走 stderr。

---

## 十、测试策略

### 10.1 单元测试（`tests/core/`）

| 模块 | 测试要点 |
|------|---------|
| `dag.py` | 邻接表构建正确性、单链/多分支/parallel 的就绪判断、下游 BFS 遍历、aggregation=any 行为 |
| `schema/v3.py` | 正常 YAML 解析、必填字段缺失报错、完整示例文件（WORKFLOW.yaml 字段规范中的示例） |
| `schema/loader.py` | 版本识别、适配器选择、不支持的版本报错 |
| `atomic_write.py` | 正常写入、崩溃残留 .tmp 文件、并发写入安全 |
| `lock.py` | 获取-释放、超时、死 pid 抢锁 |
| `errors.py` | 异常 code/exit_code 映射 |

纯 Python，不依赖 git 或文件系统以外的外部资源。

### 10.2 集成测试（`tests/services/`）

使用 `pytest` + `conftest.py` 提供 fixtures：

```python
# tests/services/conftest.py

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
| `resolver.py` | 扫描工作流列表、解析单个 YAML |
| `creator.py` | 创建实例 worktree、写入 instance JSON、打初始锚点 |
| `scheduler.py` | 完整场景：单链推进、parallel 拆分、ERROR→retry→failure edge→terminate、确认流程、合并冲突→CONFLICT |
| `state_manager.py` | 消息消费幂等、状态更新、consumed_message_ids 去重 |
| `worktree_manager.py` | 拆分、合并（无冲突/有冲突）、清理 |
| `message_handler.py` | 消息写入、字段注入、modified_files 检测 |
| `validator.py` | 保护区检测、越界→ERROR |

### 10.3 端到端测试（`tests/cli/`）

少量冒烟测试，验证 CLI 参数解析 + JSON 输出格式。完整业务流程由集成测试覆盖。

---

## 十一、关键算法伪代码

### 11.1 `next` 的就绪计算 + action 生成

```
run_next(instance_id):
  lock = acquire_lock(instance_id)
  instance = load_instance(instance_id)
  spec = load_workflow(instance.workflow_id, instance.version)
  adj = build_adjacency(spec)

  # 1. 消费消息
  changes = consume_messages(instance, adj)
  for change in changes:
    # change: (stage_id, old_status, new_status, message)
    append_timeline(change)
    if change.new_status == ERROR:
      write_deviation(change)

  # 2. 子工作流检查
  for stage in instance.stages where stage.child_instance_id:
    child = load_instance(stage.child_instance_id)
    if child.status == COMPLETED:
      stage.status = DONE
    elif child.status == FAILED:
      stage.status = ERROR

  # 3. ERROR 分支
  actions = []
  for stage in instance.stages where stage.status == ERROR:
    action = handle_error(stage, spec, instance)
    actions.append(action)
    # action 可能是 retry / spawn(failure edge) / terminate

  # 4. CONFLICT 分支
  for stage in instance.stages where stage.status == CONFLICT:
    actions.append({"action": "conflict", "stage_id": stage.stage_id, ...})

  # 5. 就绪计算
  ready = compute_ready(adj, instance)     # BFS 就绪计算

  # 5.5 并发 stage 合并（按 stage_id 字典序）
  _merge_concurrent_stages(done_changes, instance)

  # 6. 调度约束
  ready = apply_constraints(ready, instance, spec)  # exclusive / max_parallel

  # 7. 为就绪 stage 生成 spawn action
  worktree_assignments = allocate_worktrees(ready, instance)
  for stage_id in ready:
    actions.append({
      "action": "spawn",
      "stage_id": stage_id,
      "skill_id": spec.stages[stage_id].target,
      "worktree": worktree_assignments[stage_id],
      "requires_parallel_targets": ...,
      "context": build_context(stage_id, adj, instance),
    })

  # 8. 确认点聚合
  confirm_stages = [s for s in instance.stages if s.status == AWAITING_CONFIRM]
  if confirm_stages:
    actions.append({"action": "confirm", "pending": [...]})

  # 9. 全部 DONE？
  if all_done(instance, spec):
    actions.append({"action": "merge_to_main", ...})

  # 9.5 终态自动清理
  if instance.status in ("COMPLETED", "FAILED"):
    worktree_manager.cleanup_instance(instance_id)

  # 10. 全部都无需等待？
  if not actions:
    actions.append({"action": "await", "reason": "no ready stages"})

  save_instance(instance)
  release_lock(instance_id)
  return {"status": "ok", "actions": actions}
```

### 11.2 ERROR 处理

```
handle_error(stage, spec, instance):
  if stage.attempt_count < max_attempts:
    stage.status = PENDING
    stage.attempt_count += 1
    return {"action": "retry", "stage_id": stage.stage_id, "attempt": stage.attempt_count}

  # 重试耗尽
  failure_edge = find_edge(spec.edges, from=stage.stage_id, condition=FAILURE)
  if failure_edge and stage.loop_counter < failure_edge.max_loop:
    target_stage = instance.stages[failure_edge.to_stage]
    if not target_stage or target_stage.is_virtual:
      return {"action": "terminate", "status": "FAILED", "reason": "failure edge targets virtual stage"}
    target_stage.status = PENDING
    target_stage.loop_counter = stage.loop_counter + 1
    return {"action": "spawn", "stage_id": target_stage.stage_id, "reason": "failure-edge"}

  # failure edge 也耗尽
  loop_exceeded_edge = find_edge(spec.edges, from=stage.stage_id, condition=LOOP_EXCEEDED)
  if loop_exceeded_edge:
    return handle_loop_exceeded(...)

  # 无可用 handler
  instance.status = FAILED
  return {"action": "terminate", "status": "FAILED", "reason": "no handler"}
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
1. 校验实例存在且状态为 `ACTIVE`（终态实例不可重复终止）
2. 实例状态 → `FAILED`
3. 调用 `worktree_manager.cleanup_instance(instance_id)` 清理所有 stage worktree 和实例 worktree
4. 写入 deviation 日志（`type: USER_TERMINATED`）
5. 写入 timeline（`active→failed`，标注 `terminated_by: user`）
6. 返回 `{"status": "ok", "terminated_instance": "<id>", "cleaned_worktrees": [...]}`

---

## 十三、与现有规范的衔接

| 本设计章节 | 对应规范 |
|-----------|---------|
| 包结构、CLI 层、异常体系 | 本设计独有 |
| Schema 适配器 | `WORKFLOW.yaml字段规范` |
| DAG 引擎 | `wfctl接口与行为规范` §四（next 调度核心） |
| 状态管理 | `Instance状态机规范` + `Message通信协议规范` |
| Git 操作、worktree 分配 | `worktree与git锚点规范` |
| validator | `权限与校验体系规范` |
| status 聚合 | `项目全局状态规范` |
| identity / message write | `Message通信协议规范` §六 + `Skill定义规范` §四 |

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
- 详见 `core/lock.py`

### 14.5 原子 IO

- `os.replace(src, dst)` — Windows/Unix 均为原子替换操作
- JSON 写入：先写 `.tmp` 文件，完成后 `os.replace(tmp, target)`
- 详见 `core/atomic_write.py`

### 14.6 时间戳

- 所有时间戳使用 `datetime.now().astimezone().isoformat(timespec="seconds")`
- 禁止 `time.strftime("%z")`（Windows 上可能返回空字符串）
- 详见 `core/timestamp.py`

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

