"""rollback 命令。"""

from core.dag import build_adjacency, collect_downstream
from core.errors import InputError
from core.project import find_root
from core.schema.interface import EdgeCondition
from core.schema.loader import load_workflow
from services.state_manager import load_instance, save_instance
from services.worktree_manager import checkout_to_anchor, remove_anchor


def register_rollback(subparsers):
    p = subparsers.add_parser("rollback", help="回退到指定 stage 锚点")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.set_defaults(handler=_handle_rollback)


def _handle_rollback(args) -> dict:
    instance = load_instance(args.instance)
    stage_id = args.stage

    if instance.get("status") == "COMPLETED":
        raise InputError("Instance already merged to main", code="INVALID_ARGUMENT")
    if instance.get("status") == "FAILED":
        raise InputError("Instance already terminated", code="INVALID_ARGUMENT")

    root = find_root()
    from services.resolver import find_workflow_dir
    version = instance.get("version", "")
    wf_dir = find_workflow_dir(instance["workflow_id"], version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    adj = build_adjacency(spec)

    anchor_prefix = spec.anchor_prefix
    anchor_name = f"{anchor_prefix}-{args.instance}-{stage_id}"

    # 校验锚点存在
    from core.git_ops import git_rev_parse
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{args.instance}"
    if not inst_wt.exists():
        raise InputError("Instance worktree not found", code="INVALID_ARGUMENT")
    rc, _, _ = git_rev_parse(inst_wt, anchor_name)
    if rc != 0:
        raise InputError(f"No anchor for stage {stage_id}", code="STAGE_NOT_FOUND")

    # 确定受影响下游
    downstream = collect_downstream(adj, stage_id, {EdgeCondition.FAILURE, EdgeCondition.LOOP_EXCEEDED})

    # 重建实例 worktree
    checkout_to_anchor(args.instance, anchor_name)

    # 移除受影响 stage 的锚点
    reset_stages = list(downstream)
    for s_id in reset_stages:
        anchor = f"{anchor_prefix}-{args.instance}-{s_id}"
        remove_anchor(args.instance, anchor)

    # 重置状态
    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    for s_id in reset_stages:
        s = stage_map.get(s_id)
        if s:
            s["status"] = "PENDING"
            s["attempt_count"] = 0
            s["loop_counter"] = 0
            s["system_agent_id"] = None
            s.pop("continued_to", None)
            # 级联清理 consumed_message_ids：移除该 stage 产出的消息 ID
            if s.get("output_message_id"):
                msg_id = s["output_message_id"]
                consumed = instance.get("consumed_message_ids", [])
                if msg_id in consumed:
                    consumed.remove(msg_id)
                    instance["consumed_message_ids"] = consumed
            s["output_message_id"] = None

    # 写入 timeline
    from services.state_manager import _append_timeline
    _append_timeline(args.instance, stage_id, "rollback", {"reset_stages": reset_stages})

    save_instance(args.instance, instance)

    return {
        "status": "ok",
        "reset_stages": reset_stages,
        "worktree": str(inst_wt.relative_to(root)),
    }
