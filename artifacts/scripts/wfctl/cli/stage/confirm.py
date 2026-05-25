"""confirm 命令——处理用户确认/拒绝 AWAITING_CONFIRM 的 stage。

所有决策委托给 TransitionPolicy，CLI handler 只负责：
1. 加载/保存状态
2. 调用 TransitionPolicy 纯决策
3. 执行副作用（文件写入、running_agents 清理）
"""

import json

from domain.dag.graph import build_adjacency
from infrastructure.errors import InputError
from infrastructure.project import find_root
from compat.workflow.registry import load_workflow
from domain.transition.policy import TransitionPolicy
from compat.instance.registry import load_instance_state, save_instance_state
from state.model import InstanceStatus, StageState, StageStatus, StateDelta
from state.timeline import append_timeline


def register_confirm(subparsers):
    p = subparsers.add_parser("confirm", help="确认/拒绝 AWAITING_CONFIRM 的 stage")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.add_argument("--choice", required=True, help="用户选择的选项值")
    p.add_argument("--feedback", default="", help="用户反馈文本")
    p.set_defaults(handler=_handle_confirm)


def _handle_confirm(args) -> dict:
    state = load_instance_state(args.instance)

    # 加载 spec 和邻接表
    from services.resolver import find_workflow_dir
    version = state.version
    wf_dir = find_workflow_dir(state.workflow_id, version if version else None)
    spec = load_workflow(wf_dir / "WORKFLOW.yaml")
    adj = build_adjacency(spec)
    stage_order = [s.stage_id for s in spec.stages]

    # __merge__ 伪 stage
    if args.stage == "__merge__":
        return _handle_merge_confirm(args, state)

    # 查找 AWAITING_CONFIRM 的 stage 实例
    candidates = state.stages_by_id(args.stage)
    if not candidates:
        raise InputError(f"Stage not found: {args.stage}", code="STAGE_NOT_FOUND")

    stage = next((s for s in candidates if s.status == StageStatus.AWAITING_CONFIRM), None)
    if stage is None:
        statuses = {s.stage_instance_id: s.status.value for s in candidates}
        raise InputError(
            f"No AWAITING_CONFIRM instance for stage {args.stage}. "
            f"Existing instances: {statuses}",
            code="INVALID_ARGUMENT",
        )

    # 纯决策
    policy = TransitionPolicy.from_adjacency(adj, args.stage)
    result = policy.on_confirm(stage, args.choice, bool(args.feedback), stage_order)

    if result.instance_failed:
        delta = StateDelta(instance_updates={"status": InstanceStatus.FAILED})
        new_state = state.apply_delta(delta)
        save_instance_state(args.instance, new_state)
        return {"status": "instance_failed", "stage_id": args.stage, "reason": result.reason}

    # 构建 StateDelta（委托给 TransitionPolicy）
    delta = TransitionPolicy.build_confirm_delta(result, stage, state, spec.stages)

    # 级联重置
    cascade = None
    if result.cascade_reset_target is not None:
        cascade = TransitionPolicy.compute_cascade_reset(
            state, args.stage, result.cascade_reset_target, stage_order,
        )
        if cascade.removed_stage_instance_ids or cascade.reset_stage_instance_ids:
            delta = delta.merge(cascade.to_state_delta(spec.stages))

    # 应用状态变更
    new_state = state.apply_delta(delta)

    # ── 副作用区 ──
    if result.requires_feedback and args.feedback:
        _write_feedback_message(args.instance, args.stage, stage, args.choice, args.feedback)

    if result.is_relay and stage.requires_parallel_targets:
        from runtime.message.handler import validate_parallel_targets
        validate_parallel_targets(args.instance, args.stage, stage.output_message_id)

    if cascade and cascade.cleanup_running_agent_stage_ids:
        _cleanup_running_agents_for_reset(args.instance, cascade.cleanup_running_agent_stage_ids)

    append_timeline(args.instance, args.stage, result.timeline_event_label, {
        "choice": args.choice,
        "reason": result.reason,
    })

    save_instance_state(args.instance, new_state)

    response: dict = {"status": "ok", "stage_id": args.stage}
    if result.is_relay:
        response.update({"new_status": "PENDING", "matched": args.choice, "loop": stage.loop_counter + 1})
    elif result.is_rejected:
        response.update({"new_status": "DONE", "rejected": True, "target": result.target_stage_id})
    elif result.exit_condition == "confirmed":
        response.update({"new_status": "DONE", "matched": args.choice})
    elif result.exit_condition == "loop_exceeded":
        response.update({"new_status": "DONE", "reason": "loop_exceeded", "target": result.target_stage_id})
    return response


def _handle_merge_confirm(args, state) -> dict:
    result = TransitionPolicy.on_merge_confirm(args.choice)
    remove_ids = [s.stage_instance_id for s in state.stages if s.stage_id == "__merge__"]
    delta = StateDelta(
        remove_stage_instance_ids=remove_ids,
        instance_updates={"merge_confirmed": result.merge_confirmed},
    )
    new_state = state.apply_delta(delta)
    save_instance_state(args.instance, new_state)
    return {"status": "ok", "stage_id": "__merge__", "merge_confirmed": result.merge_confirmed}


def _write_feedback_message(instance_id: str, stage_id: str, stage: StageState,
                            choice: str, feedback: str):
    from runtime.message.handler import write_message
    write_message(
        instance_id=instance_id,
        stage_id=stage_id,
        stage_instance_id=stage.stage_instance_id,
        status="PENDING",
        report=feedback,
        checkpoint_summary=f"用户反馈（选项 {choice}）：{feedback}",
    )


def _cleanup_running_agents_for_reset(instance_id: str, reset_stage_ids: list[str]) -> None:
    root = find_root()
    path = root / ".agent" / "running_agents.json"
    if not path.exists():
        return
    try:
        agents = json.loads(path.read_text(encoding="utf-8"))
        before = len(agents)
        agents = [
            a for a in agents
            if not (a.get("instance_id") == instance_id
                    and a.get("stage_id") in reset_stage_ids)
        ]
        if len(agents) != before:
            path.write_text(json.dumps(agents, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    except Exception:
        pass
