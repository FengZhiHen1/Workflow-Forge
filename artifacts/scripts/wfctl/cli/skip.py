"""skip 命令——跳过指定 stage，直接标记为 DONE。"""

from core.errors import StateError
from core.schema.loader import load_workflow
from services.state_manager import (
    _append_timeline,
    append_deviation,
    load_instance,
    save_instance,
)
from services.worktree_manager import tag_anchor

FORCEABLE_STATES = {"PENDING", "RUNNING", "AWAITING_CONFIRM", "ERROR"}


def register_skip(subparsers):
    p = subparsers.add_parser("skip", help="跳过指定 stage（标记为 DONE）")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.add_argument("--reason", default="Manually skipped", help="跳过原因")
    p.add_argument(
        "--force",
        action="store_true",
        help="强制跳过任意非终态 stage（RUNNING / AWAITING_CONFIRM / ERROR）",
    )
    p.set_defaults(handler=_handle_skip)


def _handle_skip(args) -> dict:
    instance = load_instance(args.instance)
    stage_id = args.stage

    if instance.get("status") == "COMPLETED":
        raise StateError("Instance already completed")
    if instance.get("status") == "FAILED":
        raise StateError("Instance already terminated")

    # 查找所有匹配的 stage 实例（parallel fan-out 会产生多个同 stage_id 的条目）
    targets = [s for s in instance["stages"] if s["stage_id"] == stage_id]
    if not targets:
        raise StateError(f"Stage not found: {stage_id}")

    # 校验：全部已 DONE → 拒绝；任一不在可跳过集合 → 拒绝；任一非 PENDING 且未 --force → 拒绝
    if all(s.get("status") == "DONE" for s in targets):
        raise StateError(f"All instances of stage {stage_id} are already DONE")

    for s in targets:
        status = s.get("status", "PENDING")
        if status not in FORCEABLE_STATES:
            raise StateError(
                f"Stage {stage_id} ({s.get('stage_instance_id')}) is {status}, "
                f"only {sorted(FORCEABLE_STATES)} stages can be skipped"
            )
        if status != "PENDING" and not args.force:
            raise StateError(
                f"Stage {stage_id} ({s.get('stage_instance_id')}) is {status}, not PENDING. "
                f"Use --force to skip non-PENDING stages."
            )

    # 加载 workflow spec 获取 anchor_prefix
    from services.resolver import find_workflow_dir
    version = instance.get("version", "")
    wf_dir = find_workflow_dir(instance["workflow_id"], version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")

    # 逐个标记 DONE + 打锚点（按 stage_instance_id）
    old_statuses: dict[str, str] = {}
    for s in targets:
        s_inst_id = s.get("stage_instance_id", stage_id)
        old_statuses[s_inst_id] = s.get("status", "PENDING")
        s["status"] = "DONE"
        s["started_at"] = None
        anchor = f"{spec.anchor_prefix}-{args.instance}-{s_inst_id}"
        tag_anchor(args.instance, anchor)
        _append_timeline(
            args.instance, stage_id,
            f"{old_statuses[s_inst_id]}→done (skipped{' force' if args.force else ''})",
            {"reason": args.reason, "stage_instance_id": s_inst_id},
        )

    append_deviation(
        args.instance,
        "STAGE_SKIPPED_FORCE" if args.force else "STAGE_SKIPPED",
        args.reason,
        stage_id=stage_id,
    )
    save_instance(args.instance, instance)

    return {
        "status": "ok",
        "stage_id": stage_id,
        "instances_skipped": len(targets),
        "old_statuses": old_statuses,
        "forced": args.force,
        "reason": args.reason,
    }
