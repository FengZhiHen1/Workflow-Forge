"""confirm 命令——处理用户确认/拒绝 AWAITING_CONFIRM 的 stage。

所有决策委托给 TransitionPolicy，CLI handler 只负责：
1. 加载/保存状态
2. 调用 TransitionPolicy 纯决策
3. 执行副作用（文件写入、running_agents 清理）
"""

import json

from core.dag import build_adjacency
from core.errors import InputError
from core.project import find_root
from core.schema.loader import load_workflow
from domain.transition.policy import TransitionPolicy
from state.persistence import load_instance_state, save_instance_state
from services.scheduler.state_model import InstanceStatus, StageState, StageStatus, StateDelta
from services.state_manager import _append_timeline


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

    stage = _find_awaiting_confirm(candidates)
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

    # 构建 StateDelta
    delta = _build_confirm_delta(result, stage, state, spec, adj)

    # 级联重置
    cascade = None
    if result.cascade_reset_target is not None:
        cascade = TransitionPolicy.compute_cascade_reset(
            state, args.stage, result.cascade_reset_target, stage_order,
        )
        if cascade.removed_stage_instance_ids or cascade.reset_stage_instance_ids:
            cascade_delta = _build_cascade_delta(cascade, spec, state)
            delta = delta.merge(cascade_delta)

    # 应用状态变更
    new_state = state.apply_delta(delta)

    # ── 副作用区 ──
    # 反馈消息写入
    if result.requires_feedback and args.feedback:
        _write_feedback_message(args.instance, args.stage, stage, args.choice, args.feedback)

    # 中继确认需要 parallel_targets 验证
    if result.is_relay and stage.requires_parallel_targets:
        _validate_parallel_targets_in_message(args.instance, args.stage, stage)

    # 级联重置清理 running_agents
    if cascade and cascade.cleanup_running_agent_stage_ids:
        _cleanup_running_agents_for_reset(args.instance, cascade.cleanup_running_agent_stage_ids)

    # timeline
    timeline_event = _pick_timeline_event(result)
    _append_timeline(args.instance, args.stage, timeline_event, {
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
    """__merge__ 伪 stage 确认：yes → 合入，no → 下次再问。"""
    result = TransitionPolicy.on_merge_confirm(args.choice)

    # 移除 __merge__ 伪 stage
    remove_ids = [s.stage_instance_id for s in state.stages if s.stage_id == "__merge__"]
    delta = StateDelta(
        remove_stage_instance_ids=remove_ids,
        instance_updates={"merge_confirmed": result.merge_confirmed},
    )
    new_state = state.apply_delta(delta)
    save_instance_state(args.instance, new_state)

    return {"status": "ok", "stage_id": "__merge__", "merge_confirmed": result.merge_confirmed}


def _find_awaiting_confirm(candidates: list[StageState]) -> StageState | None:
    """从候选列表中取 AWAITING_CONFIRM 状态的实例。"""
    return next((s for s in candidates if s.status == StageStatus.AWAITING_CONFIRM), None)


def _build_confirm_delta(result, stage: StageState, state, spec, adj) -> StateDelta:
    """将 ConfirmResult 转换为 StateDelta。"""
    stage_updates: dict[str, dict] = {}
    instance_updates: dict = {}

    # 当前 stage 的状态变更
    su = dict(result.updates)
    su["status"] = result.next_status
    if result.exit_condition:
        su["exit_condition"] = result.exit_condition
    stage_updates[stage.stage_instance_id] = su

    # rejected 边目标 stage 激活
    if result.is_rejected and result.target_stage_id:
        target = state.first_stage_by_id(result.target_stage_id)
        if target:
            target_updates = {"status": StageStatus.PENDING}
            if result.target_stage_id == stage.stage_id:
                target_updates["loop_counter"] = stage.loop_counter + 1
            stage_updates[target.stage_instance_id] = target_updates

    # loop_exceeded 目标 stage 激活
    if result.exit_condition == "loop_exceeded" and result.target_stage_id:
        target = state.first_stage_by_id(result.target_stage_id)
        if target:
            target_updates = {"status": StageStatus.PENDING}
            if result.target_stage_id != stage.stage_id:
                target_updates["loop_counter"] = target.loop_counter + 1
            stage_updates[target.stage_instance_id] = target_updates
        # 若目标为终态 stage，实例直接 FAILED
        if TransitionPolicy._is_terminal_stage(result.target_stage_id, spec.stages):
            instance_updates["status"] = InstanceStatus.FAILED

    return StateDelta(stage_updates=stage_updates, instance_updates=instance_updates)


def _build_cascade_delta(cascade, spec, state) -> StateDelta:
    """将 CascadeResetResult 转换为 StateDelta。"""
    spec_stage_map = {s.stage_id: s for s in spec.stages}
    append_stages: list[StageState] = []

    for sid in cascade.reset_stage_instance_ids:
        stage_spec = spec_stage_map.get(sid)
        append_stages.append(StageState(
            stage_id=sid,
            stage_instance_id=sid,
            status=StageStatus.PENDING,
            model=stage_spec.model if stage_spec else None,
        ))

    return StateDelta(
        remove_stage_instance_ids=cascade.removed_stage_instance_ids,
        append_stages=append_stages,
    )


def _pick_timeline_event(result) -> str:
    """根据 ConfirmResult 选择 timeline 事件名。"""
    if result.exit_condition == "loop_exceeded":
        return "loop_exceeded"
    if result.is_rejected:
        return "awaiting_confirm→done"
    if result.exit_condition == "confirmed":
        return "awaiting_confirm→done"
    return "awaiting_confirm→pending"


# ── 副作用函数（保留自旧 confirm.py）──

def _write_feedback_message(instance_id: str, stage_id: str, stage: StageState,
                            choice: str, feedback: str):
    """写入反馈 Message，供 SubAgent 重做时读取。"""
    from services.message_handler import write_message
    write_message(
        instance_id=instance_id,
        stage_id=stage_id,
        stage_instance_id=stage.stage_instance_id,
        status="PENDING",
        report=feedback,
        checkpoint_summary=f"用户反馈（选项 {choice}）：{feedback}",
    )


def _cleanup_running_agents_for_reset(instance_id: str, reset_stage_ids: list[str]) -> None:
    """从 running_agents.json 中移除被级联重置的 stage 条目。"""
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


def _validate_parallel_targets_in_message(instance_id: str, stage_id: str,
                                          stage: StageState) -> None:
    """验证 stage 的消息中包含 parallel_targets。"""
    msg_id = stage.output_message_id
    if not msg_id:
        raise InputError(
            f"Stage {stage_id} 需要产出 parallel_targets 但无 output_message_id。"
            f"请使用中继确认（自循环）让 SubAgent 在确认后继续执行并上报 parallel_targets。",
            code="PARALLEL_TARGETS_REQUIRED",
        )
    root = find_root()
    msg_path = root / ".agent" / "instances" / instance_id / "messages" / f"{msg_id}.json"
    if not msg_path.exists():
        raise InputError(
            f"Stage {stage_id} 需要产出 parallel_targets 但消息文件 {msg_id}.json 不存在。",
            code="PARALLEL_TARGETS_REQUIRED",
        )
    try:
        msg = json.loads(msg_path.read_text(encoding="utf-8"))
    except Exception:
        raise InputError(
            f"Stage {stage_id} 的消息文件 {msg_id}.json 解析失败。",
            code="PARALLEL_TARGETS_REQUIRED",
        )
    if not msg.get("parallel_targets"):
        raise InputError(
            f"Stage {stage_id} 需要产出 parallel_targets 但当前消息中未包含。"
            f"请使用中继确认（自循环）让 SubAgent 补交 parallel_targets。",
            code="PARALLEL_TARGETS_REQUIRED",
        )
