"""confirm 命令。"""

import json

from core.dag import build_adjacency, get_confirmed_edges, get_loop_exceeded_edge, get_rejected_edges
from core.errors import InputError
from core.project import find_root
from core.schema.loader import load_workflow
from services.state_manager import _append_timeline, load_instance, save_instance


def register_confirm(subparsers):
    p = subparsers.add_parser("confirm", help="确认/拒绝 AWAITING_CONFIRM 的 stage")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.add_argument("--choice", required=True, help="用户选择的选项值")
    p.add_argument("--feedback", default="", help="用户反馈文本")
    p.set_defaults(handler=_handle_confirm)


def _handle_confirm(args) -> dict:
    instance = load_instance(args.instance)

    # __merge__ 伪 stage：确认一级实例合入 main
    if args.stage == "__merge__":
        return _handle_merge_confirm(args, instance)

    candidates = [s for s in instance["stages"] if s["stage_id"] == args.stage]
    if not candidates:
        raise InputError(f"Stage not found: {args.stage}", code="STAGE_NOT_FOUND")

    # 优先取 AWAITING_CONFIRM 的实例（并行 fan-out 时可能多个实例同 stage_id 不同状态）
    stage = next((s for s in candidates if s.get("status") == "AWAITING_CONFIRM"), None)
    if not stage:
        statuses = {s.get("stage_instance_id"): s.get("status") for s in candidates}
        raise InputError(
            f"No AWAITING_CONFIRM instance for stage {args.stage}. "
            f"Existing instances: {statuses}",
            code="INVALID_ARGUMENT",
        )

    from services.resolver import find_workflow_dir
    version = instance.get("version", "")
    wf_dir = find_workflow_dir(instance["workflow_id"], version if version else None)
    yaml_file = wf_dir / "WORKFLOW.yaml"
    spec = load_workflow(yaml_file)
    adj = build_adjacency(spec)

    choice = args.choice
    stage_id = args.stage

    # 查找 confirmed edges：先精确匹配 choice，无匹配时用无 choice 的兜底 edge
    confirmed_edges = get_confirmed_edges(adj, stage_id)
    matched_confirmed = _match_edges(confirmed_edges, choice)

    if matched_confirmed:
        edge = matched_confirmed[0]  # 多条匹配时取第一条

        # 中继确认：edge 回指自身 → stage 继续执行
        if edge.to_stage == edge.from_stage:
            loop_counter = stage.get("loop_counter", 0)
            max_loop = edge.max_loop or 0

            if max_loop and loop_counter >= max_loop:
                # 循环已超限，走 loop_exceeded
                exceeded_edge = get_loop_exceeded_edge(adj, stage_id)
                if exceeded_edge:
                    _apply_edge_target(instance, exceeded_edge, stage_id)
                    stage["status"] = "DONE"
                    stage["exit_condition"] = "loop_exceeded"
                    # 若目标为终态 stage，直接终止实例
                    if _is_terminal_stage(exceeded_edge.to_stage, spec):
                        instance["status"] = "FAILED"
                    _append_timeline(args.instance, stage_id, "loop_exceeded",
                                     {"choice": choice, "loop_counter": loop_counter})
                    save_instance(args.instance, instance)
                    return {"status": "ok", "stage_id": stage_id, "new_status": "DONE",
                            "reason": "loop_exceeded", "target": exceeded_edge.to_stage}
                else:
                    instance["status"] = "FAILED"
                    save_instance(args.instance, instance)
                    return {"status": "instance_failed", "stage_id": stage_id,
                            "reason": f"loop exceeded for choice: {choice}"}

            stage["status"] = "PENDING"
            stage["loop_counter"] = loop_counter + 1
            stage["system_agent_id"] = None
            if args.feedback:
                _write_feedback_message(args.instance, stage_id, stage, choice, args.feedback)
            _append_timeline(args.instance, stage_id, "awaiting_confirm→pending",
                             {"confirmed_by": "user", "choice": choice, "loop": stage["loop_counter"]})
            save_instance(args.instance, instance)
            return {"status": "ok", "stage_id": stage_id, "new_status": "PENDING",
                    "matched": choice, "loop": stage["loop_counter"]}

        # 终局确认：edge 指向下游 → stage 结束
        # 前置拦截：requires_parallel_targets 的 stage 必须在消息中携带 parallel_targets
        if stage.get("requires_parallel_targets"):
            _validate_parallel_targets_in_message(args.instance, stage_id, stage)

        stage["status"] = "DONE"
        stage["exit_condition"] = "confirmed"
        stage["confirmed_choice"] = choice
        _append_timeline(args.instance, stage_id, "awaiting_confirm→done",
                         {"confirmed_by": "user", "choice": choice})

        # 回边检测：若 to_stage 拓扑序早于 from_stage，级联重置中间 Stage
        _cascade_reset_on_backward_edge(instance, spec, stage_id, edge.to_stage, args.instance)

        save_instance(args.instance, instance)
        return {"status": "ok", "stage_id": stage_id, "new_status": "DONE", "matched": choice}

    # 查找 rejected edges
    rejected_edges = get_rejected_edges(adj, stage_id)
    matched_rejected = _match_edges(rejected_edges, choice)

    if matched_rejected:
        rejected_edge = matched_rejected[0]

        # 自循环 rejected 边：检查 max_loop，超限走 loop_exceeded
        if rejected_edge.to_stage == rejected_edge.from_stage:
            loop_counter = stage.get("loop_counter", 0)
            max_loop = rejected_edge.max_loop or 0
            if max_loop and loop_counter >= max_loop:
                exceeded_edge = get_loop_exceeded_edge(adj, stage_id)
                if exceeded_edge:
                    _apply_edge_target(instance, exceeded_edge, stage_id)
                    stage["status"] = "DONE"
                    stage["exit_condition"] = "loop_exceeded"
                    if _is_terminal_stage(exceeded_edge.to_stage, spec):
                        instance["status"] = "FAILED"
                    _append_timeline(args.instance, stage_id, "loop_exceeded",
                                     {"choice": choice, "loop_counter": loop_counter,
                                      "trigger": "rejected"})
                    save_instance(args.instance, instance)
                    return {"status": "ok", "stage_id": stage_id, "new_status": "DONE",
                            "reason": "loop_exceeded", "target": exceeded_edge.to_stage}
                else:
                    instance["status"] = "FAILED"
                    save_instance(args.instance, instance)
                    return {"status": "instance_failed", "stage_id": stage_id,
                            "reason": f"loop exceeded for rejected choice: {choice}"}

        stage["status"] = "DONE"
        stage["exit_condition"] = "rejected"
        stage["attempt_count"] = 0
        # 激活 rejected edge 目标 stage（使其在下次 next 时进入就绪列表）
        rejected_target = next((s for s in instance["stages"] if s["stage_id"] == rejected_edge.to_stage), None)
        if rejected_target:
            rejected_target["status"] = "PENDING"
            # 自循环 rejected：递增 loop_counter
            if rejected_edge.to_stage == rejected_edge.from_stage:
                rejected_target["loop_counter"] = stage.get("loop_counter", 0) + 1
        # 若目标为终态 stage，直接终止实例
        if _is_terminal_stage(rejected_edge.to_stage, spec):
            instance["status"] = "FAILED"
        if args.feedback:
            _write_feedback_message(args.instance, stage_id, stage, choice, args.feedback)
        _append_timeline(args.instance, stage_id, "awaiting_confirm→done",
                         {"rejected_by": "user", "choice": choice, "target": rejected_edge.to_stage})
        save_instance(args.instance, instance)
        return {"status": "ok", "stage_id": stage_id, "new_status": "DONE",
                "rejected": True, "target": rejected_edge.to_stage}

    # 无匹配 edge → instance FAILED，列出合法选项辅助排查
    all_choices: list[str] = []
    for e in confirmed_edges + rejected_edges:
        if e.choice and e.choice not in all_choices:
            all_choices.append(e.choice)
    hint = f" 合法选项：{all_choices}" if all_choices else "（该 stage 未定义任何带 choice 的边）"
    instance["status"] = "FAILED"
    save_instance(args.instance, instance)
    return {
        "status": "instance_failed",
        "stage_id": stage_id,
        "reason": f"未知的选项：'{choice}'。{hint}",
    }


def _handle_merge_confirm(args, instance: dict) -> dict:
    """处理 __merge__ 伪 stage 的确认：yes → 允许合入，no → 下次再问。"""
    choice = args.choice.lower()
    instance_id = instance["instance_id"]

    # 移除 __merge__ 伪 stage
    instance["stages"] = [s for s in instance["stages"] if s["stage_id"] != "__merge__"]

    if choice in ("yes", "y", "confirm", "accept", "ok"):
        instance["merge_confirmed"] = True
        save_instance(args.instance, instance)
        return {"status": "ok", "stage_id": "__merge__", "merge_confirmed": True}

    # no 或其他：不设置 merge_confirmed，下次 next 继续提示
    save_instance(args.instance, instance)
    return {"status": "ok", "stage_id": "__merge__", "merge_confirmed": False}


def _match_edges(edges, choice: str):
    """精确匹配 choice 的 edge，若无则返回无 choice 的兜底 edge。"""
    exact = [e for e in edges if e.choice == choice]
    if exact:
        return exact
    return [e for e in edges if not e.choice]


def _is_terminal_stage(stage_id: str, spec) -> bool:
    """判断 stage 是否为终态虚拟 stage（如 s99-workflow-end）。

    终态 stage 被 rejected / loop_exceeded 边指向时，实例应立即 FAILED，
    而非等待其他 PENDING stage。
    """
    from core.schema.interface import StageTargetType
    for s in spec.stages:
        if s.stage_id == stage_id and s.target_type == StageTargetType.VIRTUAL:
            # 确认是"工作流终止"语义的结束 stage（而非 s00 起始 stage）
            if "workflow-end" in stage_id or "终结" in s.name or "终止" in s.name:
                return True
    return False


def _apply_edge_target(instance: dict, edge, source_stage_id: str):
    """将 edge 的目标 stage 设为 PENDING（供 loop_exceeded 使用）。"""
    target = next((s for s in instance["stages"] if s["stage_id"] == edge.to_stage), None)
    if target:
        target["status"] = "PENDING"
        target["loop_counter"] = target.get("loop_counter", 0) + 1


def _write_feedback_message(instance_id: str, stage_id: str, stage: dict, choice: str, feedback: str):
    """写入反馈 Message，供 SubAgent 重做时读取。"""
    from services.message_handler import write_message
    write_message(
        instance_id=instance_id,
        stage_id=stage_id,
        stage_instance_id=stage.get("stage_instance_id", stage_id),
        status="PENDING",
        report=feedback,
        checkpoint_summary=f"用户反馈（选项 {choice}）：{feedback}",
    )


def _cascade_reset_on_backward_edge(instance: dict, spec, from_stage_id: str,
                                     to_stage_id: str, instance_id: str) -> None:
    """回边级联重置：当确认边指向拓扑序更早的 Stage 时，
    将起止 Stage 之间的所有中间 Stage 重置为 PENDING。

    判断依据：to_stage 在 WORKFLOW.yaml stages 列表中的位置早于 from_stage。
    重置范围：[to_stage, from_stage)，不含 from_stage（它已 DONE）。

    每个中间 Stage 的所有实例（含 parallel fan-out）被折叠为单一 PENDING 条目，
    确保 downstream 的 _check_parallel 可以重新创建正确的 parallel 实例。
    """
    stage_order = [s.stage_id for s in spec.stages]
    try:
        from_idx = stage_order.index(from_stage_id)
        to_idx = stage_order.index(to_stage_id)
    except ValueError:
        return

    if to_idx >= from_idx:
        return  # 非回边，无需处理

    spec_stage_map = {s.stage_id: s for s in spec.stages}

    for i in range(to_idx, from_idx):
        sid = stage_order[i]
        stage_spec = spec_stage_map.get(sid)

        # 收集该 stage 的所有现有实例（含 parallel），确认是否需要重置
        existing = [s for s in instance.get("stages", []) if s["stage_id"] == sid]
        needs_reset = any(e.get("status") in ("DONE", "ERROR") for e in existing)

        if not needs_reset:
            continue

        # 移除所有现有实例，替换为单一 PENDING 条目
        instance["stages"] = [s for s in instance["stages"] if s["stage_id"] != sid]
        instance["stages"].append({
            "stage_id": sid,
            "stage_instance_id": sid,
            "status": "PENDING",
            "agent_id": None,
            "system_agent_id": None,
            "output_message_id": None,
            "loop_counter": 0,
            "attempt_count": 0,
            "started_at": None,
            "model": stage_spec.model if stage_spec else None,
            "child_instance_id": None,
            "fan_out_target": None,
        })
        _append_timeline(instance_id, sid, "done→pending",
                         {"reason": "backward_edge_cascade",
                          "from_stage": from_stage_id,
                          "to_stage": to_stage_id})


def _validate_parallel_targets_in_message(instance_id: str, stage_id: str, stage: dict) -> None:
    """验证 stage 的消息中包含 parallel_targets。缺失时抛出 InputError。"""
    msg_id = stage.get("output_message_id")
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
            f"Stage {stage_id} 需要产出 parallel_targets 但消息文件 {msg_id}.json 不存在。"
            f"请使用中继确认（自循环）让 SubAgent 重新上报。",
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
            f"请使用中继确认（自循环，choice 配 '重新选择' 等）"
            f"让 SubAgent 在确认后补交 parallel_targets，"
            f"或在 AWAITING_CONFIRM 消息中预先附带 parallel_targets 后再确认。",
            code="PARALLEL_TARGETS_REQUIRED",
        )
