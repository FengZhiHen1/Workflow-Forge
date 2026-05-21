"""实例创建。"""

import json
import shutil
import time
from pathlib import Path

from core.atomic_write import atomic_write_json
from core.dag import collect_ancestors
from core.errors import InputError
from core.git_ops import git_rev_parse
from core.project import find_root
from core.schema.interface import StageTargetType
from core.schema.loader import load_workflow
from services.resolver import find_workflow_dir
from services.state_manager import load_instance, save_instance, append_deviation
from services.worktree_manager import (
    create_instance_worktree,
    tag_anchor,
)


def _compute_nesting_depth(instance_id: str, root: Path) -> int:
    """沿 parent_instance_id 链向上计算嵌套深度（含自身）。"""
    depth = 1
    current_id = instance_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        # 在所有实例目录中查找父实例
        instances_dir = root / ".agent" / "instances"
        found = False
        if instances_dir.exists():
            for d in instances_dir.iterdir():
                json_file = d / "instance.json"
                if not json_file.exists():
                    continue
                try:
                    import json as _json
                    data = _json.loads(json_file.read_text(encoding="utf-8"))
                    if data.get("instance_id") == current_id:
                        parent = data.get("parent_instance_id")
                        if parent:
                            depth += 1
                            current_id = parent
                            found = True
                        else:
                            current_id = ""
                            found = True
                        break
                except Exception:
                    continue
        if not found:
            break
    return depth


def create_instance(
    workflow_id: str,
    version: str | None = None,
    goal: str = "",
    parent_instance_id: str | None = None,
    clone_from: str | None = None,
    fast_forward_to: str | None = None,
    worktree_base_ref: str | None = None,
) -> dict:
    """生成 Instance JSON，分配实例 worktree，写入身份元数据，打初始锚点。

    当 clone_from 指定时，从旧实例克隆：继承 DONE stage 状态、复制 worktree 文件、
    保留消息记录，旧实例标记 FAILED。

    当 fast_forward_to 指定时，将该 stage 的拓扑前驱自动标记为 DONE，
    实例创建后首个 next 直接进入目标 stage。

    当 worktree_base_ref 指定时，实例 worktree 基于该 ref 创建（用于子工作流继承父 worktree 状态）。
    clone_from 和 fast_forward_to 互斥。
    """
    root = find_root()

    # 互斥校验
    if clone_from and fast_forward_to:
        raise InputError(
            "--clone and --fast-forward-to are mutually exclusive",
            code="INVALID_ARGUMENT",
        )

    # ── clone 分支 ────────────────────────────────────────────
    if clone_from:
        return _create_from_clone(
            root=root,
            clone_from=clone_from,
            workflow_id=workflow_id,
            version=version,
            goal=goal,
            parent_instance_id=parent_instance_id,
        )

    # ── 正常创建 ──────────────────────────────────────────────
    # 嵌套深度检测（上限 3 层）
    if parent_instance_id:
        depth = _compute_nesting_depth(parent_instance_id, root)
        if depth >= 3:
            raise InputError(
                f"Maximum nesting depth (3) exceeded: current depth is {depth}",
                code="INVALID_ARGUMENT",
            )

    # 查找工作流目录（目录命名规范：<workflow_id>@<version>/）
    wf_dir = find_workflow_dir(workflow_id, version)
    yaml_file = wf_dir / "WORKFLOW.yaml"

    spec = load_workflow(yaml_file)

    # 生成 instance_id
    instance_id = time.strftime("%Y%m%d") + "-001"
    instances_dir = root / ".agent" / "instances"
    if instances_dir.exists():
        existing = [d.name for d in instances_dir.iterdir() if d.is_dir()]
        prefix = time.strftime("%Y%m%d") + "-"
        nums = [int(n.split("-")[1]) for n in existing if n.startswith(prefix) and n.split("-")[1].isdigit()]
        next_num = max(nums, default=0) + 1
        instance_id = f"{prefix}{next_num:03d}"

    # 创建前清理残留的 git worktree 注册（目录已丢失但注册还在）
    from core.git_ops import git_worktree_prune
    git_worktree_prune(root)

    # 创建实例 worktree
    worktree = None
    anchor_name = f"{spec.anchor_prefix}-{instance_id}-s00-workflow-start"
    inst_dir = instances_dir / instance_id

    try:
        base_ref = worktree_base_ref or "HEAD"
        worktree = create_instance_worktree(instance_id, base_ref=base_ref)

        # 构建 stages 初始状态
        stages: list[dict] = []
        for s in spec.stages:
            stages.append({
                "stage_id": s.stage_id,
                "stage_instance_id": s.stage_id,
                "status": "PENDING",
                "agent_id": None,
                "system_agent_id": None,
                "output_message_id": None,
                "loop_counter": 0,
                "attempt_count": 0,
                "confirmed": False,
                "started_at": None,
                "model": s.model,
                "child_instance_id": None,
                "fan_out_target": None,
            })

        # ── fast-forward：将目标 stage 的拓扑前驱标为 DONE ──
        fast_forwarded: list[str] = []
        if fast_forward_to:
            from core.dag import build_adjacency as _build_adj
            adj = _build_adj(spec)
            ancestors = collect_ancestors(adj, fast_forward_to)
            if fast_forward_to not in adj.stages or adj.stages[fast_forward_to].target_type == StageTargetType.VIRTUAL:
                raise InputError(
                    f"fast-forward target '{fast_forward_to}' is not a valid non-virtual stage",
                    code="INVALID_ARGUMENT",
                )
            stage_map = {s["stage_id"]: s for s in stages}
            for stage_id in ancestors:
                target = stage_map.get(stage_id)
                if not target:
                    continue
                stage_spec = adj.stages.get(stage_id)
                if stage_spec and stage_spec.target_type == StageTargetType.VIRTUAL:
                    continue
                target["status"] = "DONE"
                fast_forwarded.append(stage_id)

        instance = {
            "schema_version": "3.0.0",
            "instance_id": instance_id,
            "workflow_id": spec.workflow_id,
            "version": spec.version,
            "goal": goal,
            "status": "ACTIVE",
            "parent_instance_id": parent_instance_id,
            "consumed_message_ids": [],
            "stages": stages,
        }

        # 打初始锚点（在写入 instance.json 之前，锚点只依赖 worktree 状态）
        tag_anchor(instance_id, anchor_name, worktree=worktree)

        # 为 fast-forwarded DONE stage 打锚点
        for ff_id in fast_forwarded:
            ff_anchor = f"{spec.anchor_prefix}-{instance_id}-{ff_id}"
            try:
                tag_anchor(instance_id, ff_anchor, worktree=worktree)
            except Exception:
                pass

        # 保存 instance.json
        inst_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(inst_dir / "instance.json", instance)

        # 写入身份元数据到 worktree
        identity = {
            "instance_id": instance_id,
            "stage_id": None,
            "stage_instance_id": None,
            "message_target_path": str(inst_dir / "messages"),
        }
        identity_file = worktree / ".wfctl_identity.json"
        identity_file.write_text(json.dumps(identity, indent=2, ensure_ascii=False), encoding="utf-8")

        result = {
            "status": "ok",
            "instance_id": instance_id,
            "workflow_id": spec.workflow_id,
            "version": spec.version,
            "worktree": str(worktree.relative_to(root)),
        }
        if fast_forwarded:
            result["fast_forwarded"] = fast_forwarded
        return result
    except Exception:
        # 回滚：清理已创建的资源
        from services.worktree_manager import remove_anchor, remove_instance_worktree
        if worktree is not None and worktree.exists():
            try:
                remove_anchor(instance_id, anchor_name, worktree=worktree)
            except Exception:
                pass
            try:
                remove_instance_worktree(instance_id)
            except Exception:
                pass
        if inst_dir.exists():
            import shutil
            shutil.rmtree(inst_dir, ignore_errors=True)
        raise


def _create_from_clone(
    *,
    root: Path,
    clone_from: str,
    workflow_id: str,
    version: str | None,
    goal: str,
    parent_instance_id: str | None,
) -> dict:
    """从旧实例克隆新实例。

    行为：
    1. 校验旧实例存在且非终态
    2. 获取旧 worktree HEAD 作为新 worktree 的 base_ref（保留所有 DONE stage 文件）
    3. 复制旧实例所有消息文件到新实例
    4. 继承 DONE stage（含 parallel fan-out 信息），非 DONE stage 重置为 PENDING
    5. 旧实例标记 FAILED
    """
    old_inst = load_instance(clone_from)

    # 校验：旧实例不能是 COMPLETED
    if old_inst.get("status") == "COMPLETED":
        raise InputError(
            f"Cannot clone a COMPLETED instance: {clone_from}",
            code="INVALID_ARGUMENT",
        )

    # 使用旧实例的 workflow_id 和 version（除非调用方显式覆盖）
    actual_wf_id = workflow_id if workflow_id else old_inst.get("workflow_id", "")
    actual_version = version if version else old_inst.get("version", "")

    wf_dir = find_workflow_dir(actual_wf_id, actual_version)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")

    # 生成新 instance_id
    instance_id = time.strftime("%Y%m%d") + "-001"
    instances_dir = root / ".agent" / "instances"
    if instances_dir.exists():
        existing = [d.name for d in instances_dir.iterdir() if d.is_dir()]
        prefix = time.strftime("%Y%m%d") + "-"
        nums = [
            int(n.split("-")[1])
            for n in existing
            if n.startswith(prefix) and n.split("-")[1].isdigit()
        ]
        next_num = max(nums, default=0) + 1
        instance_id = f"{prefix}{next_num:03d}"

    goal = goal or old_inst.get("goal", "")

    # 创建前清理残留的 git worktree 注册
    from core.git_ops import git_worktree_prune
    git_worktree_prune(root)

    # 获取旧 worktree HEAD 作为新 worktree 基准
    old_wt = root / ".tmp" / "worktrees" / f"instance-{clone_from}"
    worktree_source = "old-head"
    if old_wt.exists():
        rc, old_head, _ = git_rev_parse(old_wt, "HEAD")
        base_ref = old_head.strip() if rc == 0 else "HEAD"
    else:
        base_ref = "HEAD"
        worktree_source = "main-head"

    anchor_name = f"{spec.anchor_prefix}-{instance_id}-s00-workflow-start"
    inst_dir = instances_dir / instance_id
    worktree = None

    try:
        # 创建新 worktree，基于旧 worktree 的 HEAD（继承所有文件变更）
        worktree = create_instance_worktree(instance_id, base_ref=base_ref)

        # 构建 stages：继承 DONE，其余重置
        old_stages_by_id: dict[str, list[dict]] = {}
        for s in old_inst.get("stages", []):
            old_stages_by_id.setdefault(s["stage_id"], []).append(s)

        stages: list[dict] = []
        for s in spec.stages:
            old_entries = old_stages_by_id.get(s.stage_id, [])
            all_old_done = old_entries and all(e.get("status") == "DONE" for e in old_entries)

            if all_old_done:
                # 继承旧的 DONE stage（含 parallel fan_out 信息）
                for entry in old_entries:
                    stages.append({
                        "stage_id": entry["stage_id"],
                        "stage_instance_id": entry.get("stage_instance_id", entry["stage_id"]),
                        "status": "DONE",
                        "agent_id": entry.get("agent_id"),
                        "system_agent_id": entry.get("system_agent_id"),
                        "output_message_id": entry.get("output_message_id"),
                        "loop_counter": entry.get("loop_counter", 0),
                        "attempt_count": entry.get("attempt_count", 0),
                        "confirmed": entry.get("confirmed", False),
                        "started_at": entry.get("started_at"),
                        "model": entry.get("model"),
                        "child_instance_id": entry.get("child_instance_id"),
                        "fan_out_target": entry.get("fan_out_target"),
                    })
            else:
                # 非 DONE stage → PENDING
                stages.append({
                    "stage_id": s.stage_id,
                    "stage_instance_id": s.stage_id,
                    "status": "PENDING",
                    "agent_id": None,
                    "system_agent_id": None,
                    "output_message_id": None,
                    "loop_counter": 0,
                    "attempt_count": 0,
                    "confirmed": False,
                    "started_at": None,
                    "model": s.model,
                    "child_instance_id": None,
                    "fan_out_target": None,
                })

        # 只复制被继承的 DONE stage 对应的消息文件，并改写 instance_id
        old_msgs_dir = root / ".agent" / "instances" / clone_from / "messages"
        new_msgs_dir = inst_dir / "messages"
        consumed_message_ids: list[str] = []
        if old_msgs_dir.exists():
            inherited_msg_ids = {
                s["output_message_id"]
                for s in stages
                if s.get("status") == "DONE" and s.get("output_message_id")
            }
            if inherited_msg_ids:
                new_msgs_dir.mkdir(parents=True, exist_ok=True)
                for msg_id in inherited_msg_ids:
                    src = old_msgs_dir / f"{msg_id}.json"
                    if src.exists():
                        data = json.loads(src.read_text(encoding="utf-8"))
                        data["instance_id"] = instance_id
                        atomic_write_json(new_msgs_dir / f"{msg_id}.json", data)
                        consumed_message_ids.append(msg_id)

        instance = {
            "schema_version": "3.0.0",
            "instance_id": instance_id,
            "workflow_id": spec.workflow_id,
            "version": spec.version,
            "goal": goal,
            "status": "ACTIVE",
            "parent_instance_id": parent_instance_id,
            "consumed_message_ids": consumed_message_ids,
            "stages": stages,
        }

        # 打初始锚点
        tag_anchor(instance_id, anchor_name, worktree=worktree)

        # 为每个已继承的 DONE stage 打锚点（跳过 s00-workflow-start，已在上面打过）
        for s in stages:
            if s.get("status") == "DONE" and s["stage_instance_id"] != "s00-workflow-start":
                anchor = f"{spec.anchor_prefix}-{instance_id}-{s['stage_instance_id']}"
                try:
                    tag_anchor(instance_id, anchor, worktree=worktree)
                except Exception:
                    pass

        # 保存 instance.json
        inst_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(inst_dir / "instance.json", instance)

        # 写入身份元数据
        identity = {
            "instance_id": instance_id,
            "stage_id": None,
            "stage_instance_id": None,
            "message_target_path": str(inst_dir / "messages"),
        }
        identity_file = worktree / ".wfctl_identity.json"
        identity_file.write_text(
            json.dumps(identity, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # 旧实例标记 FAILED
        old_status = old_inst.get("status")
        if old_status != "FAILED":
            old_inst["status"] = "FAILED"
            save_instance(clone_from, old_inst)
            append_deviation(
                clone_from,
                "INSTANCE_CLONED",
                f"Cloned to new instance {instance_id}",
            )

        return {
            "status": "ok",
            "instance_id": instance_id,
            "workflow_id": spec.workflow_id,
            "version": spec.version,
            "worktree": str(worktree.relative_to(root)),
            "cloned_from": clone_from,
            "worktree_source": worktree_source,
            "inherited_done_stages": [
                s["stage_id"] for s in stages if s.get("status") == "DONE"
            ],
        }

    except Exception:
        # 回滚
        from services.worktree_manager import remove_anchor, remove_instance_worktree
        if worktree is not None and worktree.exists():
            try:
                remove_anchor(instance_id, anchor_name, worktree=worktree)
            except Exception:
                pass
            try:
                remove_instance_worktree(instance_id)
            except Exception:
                pass
        if inst_dir.exists():
            shutil.rmtree(inst_dir, ignore_errors=True)
        raise
