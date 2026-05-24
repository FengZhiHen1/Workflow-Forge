"""next 调度核心（DAG 计算 + action 生成）。"""

import time
from pathlib import Path

from core.timestamp import iso_timestamp, parse_iso_timestamp

from core.dag import (
    _all_satisfied,
    build_adjacency,
    collect_downstream,
    compute_ready,
    get_confirmed_edges,
    get_failure_edge,
    get_loop_exceeded_edge,
    get_rejected_edges,
)
from core.errors import GitError, InputError, StateError
from core.git_ops import git_add_all, git_commit_file, git_rev_parse, git_status_porcelain
from core.lock import FileLock
from core.project import find_root
from core.schema.interface import EdgeCondition, StageSpec, StageTargetType, WorkflowSpec
from core.schema.loader import load_workflow
from services.message_handler import scan_messages
from services.state_manager import (
    append_deviation,
    consume_messages,
    load_instance,
    save_instance,
)
from services.worktree_manager import (
    create_parallel_worktree,
    create_stage_worktree,
    merge_stage_worktree,
    sync_instance_with_main,
    sync_instance_with_parent,
    sync_stage_with_instance,
    tag_anchor,
)


def run_next(instance_id: str) -> dict:
    """调度核心：消费消息，推进状态，返回 action。

    递归处理整棵实例树（父→子→孙），返回归一化的 flat action 列表。
    子工作流实例的处理对编排器完全透明——编排器只需调一次
    ``wfctl next --instance <root>``，不再需要单独驱动子实例。
    """
    root = find_root()
    lock_path = root / ".agent" / "instances" / instance_id / "instance.json"
    lock = FileLock(lock_path)

    if not lock.acquire(timeout=15.0):
        raise StateError("Could not acquire instance lock", code="STATE_LOCKED")

    try:
        return _run_next_inner(instance_id)
    finally:
        lock.release()


def _run_next_inner(instance_id: str) -> dict:
    """单实例调度核心（不含顶层锁管理，供递归调用）。

    调用方负责获取本实例的 instance.json 锁。
    内部递归处理子实例时，会为每个子实例独立获取/释放锁。
    """
    root = find_root()
    instance = load_instance(instance_id)
    if instance.get("status") != "ACTIVE":
        return {"status": "error", "reason": f"Instance is {instance.get('status')}"}

    # 0. 同步 worktree 与上游（Level 1 / Level 1.5），失败静默跳过
    _sync_worktree_upstream(instance_id, instance)

    spec = _load_workflow_for_instance(instance)
    adj = build_adjacency(spec)
    worktree_map = _build_worktree_map(instance_id, instance)

    # 1. 消费消息
    changes = consume_messages(instance_id, instance, worktree_map)
    for change in changes:
        if change["new_status"] == "ERROR":
            append_deviation(
                instance_id, "STAGE_ERROR",
                change["message"].get("report", ""),
                stage_id=change["stage_id"],
            )

    # 1.5. AWAITING_CONFIRM 合法性校验
    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    for change in changes:
        if change["new_status"] != "AWAITING_CONFIRM":
            continue
        sid = change["stage_id"]
        if not get_confirmed_edges(adj, sid):
            stage = stage_map.get(sid)
            if stage:
                stage["status"] = "ERROR"
                _write_synthetic_error_message(
                    instance_id, sid, stage,
                    f"Stage {sid} 上报了 AWAITING_CONFIRM 但未定义任何 confirmed 边。"
                    f"该 stage 不应设置确认点——请检查 Skill 是否在错误的阶段请求了确认。",
                )
                append_deviation(
                    instance_id, "INVALID_AWAITING_CONFIRM",
                    f"Stage {sid} 无 confirmed 边但上报了 AWAITING_CONFIRM，已转为 ERROR",
                    stage_id=sid,
                )
                change["new_status"] = "ERROR"

    # 2. 自动提交 DONE stage
    _auto_commit_done_stages(instance_id, changes, worktree_map, spec.anchor_prefix)

    # 2.5. 补锚
    _ensure_anchors_for_done_stages(instance_id, instance, worktree_map, spec.anchor_prefix)

    # 3. 并发 stage worktree 合并
    merge_conflict_actions = _merge_done_stage_worktrees(instance_id, instance, changes, worktree_map)

    # 4. 子工作流完成检查（处理上一轮已完成的子实例）
    _check_child_workflows(instance, root)

    # 5. parallel 拆分
    running_agents = _load_running_agents(instance_id)
    parallel_actions = _check_parallel(adj, instance, instance_id, spec, running_agents)

    # 5.5. 子工作流实例创建
    _spawn_child_workflows(instance, instance_id, spec, adj)

    # 5.6. 递归处理所有活跃子实例（统一消费子实例消息池）
    child_results = _recurse_child_instances(instance, instance_id)

    # 5.6.1. 子实例递归后重新检查完成状态（子实例可能在本次递归中完成）
    _check_child_workflows(instance, root)

    # 6. ERROR 分支
    error_actions = _handle_error_stages(instance, adj, spec, instance_id)

    # 6.5. 超时检测
    _check_timeouts(instance, spec, instance_id)

    # 7. CONFLICT 分支
    conflict_actions = _handle_conflict_stages(instance, instance_id)

    # 8. 虚拟 stage 预处理
    _resolve_virtual_stages(adj, instance, instance_id, spec)

    # 9. 就绪计算
    ready = compute_ready(adj, instance)

    # 10. 调度约束
    ready = _apply_scheduling_constraints(ready, instance, spec)

    # 11. worktree 分配 + spawn/continue action（带 instance_id）
    stage_actions = _allocate_and_spawn(ready, instance, instance_id, adj, spec, running_agents)

    # 12. 确认点聚合：本实例 + 所有子实例
    local_confirm_pending = _collect_confirm_pending(instance, adj)
    all_confirm_pending = (local_confirm_pending or []) + child_results.get("confirm_pending", [])
    confirm_action = {"action": "confirm", "pending": all_confirm_pending} if all_confirm_pending else None

    # 13. 组装 actions（不再包含 child_next）
    actions: list[dict] = []
    actions.extend(parallel_actions)
    actions.extend(error_actions)
    actions.extend(conflict_actions)
    actions.extend(merge_conflict_actions)
    actions.extend(child_results.get("error", []))
    actions.extend(child_results.get("conflict", []))
    actions.extend(child_results.get("merge_conflict", []))
    actions.extend(stage_actions)
    actions.extend(child_results.get("spawn_continue", []))
    actions.extend(child_results.get("retry", []))
    actions.extend(child_results.get("reinforce", []))
    if confirm_action:
        actions.append(confirm_action)

    # 14. 全部 DONE？执行实例合并
    if _check_all_done(instance, spec):
        if not instance.get("parent_instance_id") and not instance.get("merge_confirmed"):
            instance.setdefault("stages", []).append({
                "stage_id": "__merge__",
                "stage_instance_id": "__merge__",
                "status": "AWAITING_CONFIRM",
                "confirm_questions": [
                    f"实例 {instance_id}（{instance.get('goal', '')}）全部 stage 已完成，是否合入 main？",
                ],
            })
        else:
            merge_result = _execute_merge_to_main(instance, spec, instance_id)
            actions.append(merge_result)

    if not actions:
        actions.append({"action": "await", "reason": "no ready stages"})

    save_instance(instance_id, instance)
    return {"status": "ok", "actions": actions}


def run_sync(instance_id: str) -> dict:
    """sync：仅消费消息、更新 stage 状态，不计算 next。"""
    root = find_root()
    lock_path = root / ".agent" / "instances" / instance_id / "instance.json"
    lock = FileLock(lock_path)

    if not lock.acquire(timeout=15.0):
        raise StateError("Could not acquire instance lock", code="STATE_LOCKED")

    try:
        instance = load_instance(instance_id)
        worktree_map = _build_worktree_map(instance_id, instance)
        changes = consume_messages(instance_id, instance, worktree_map)
        save_instance(instance_id, instance)
        return {"status": "ok", "changes": changes}
    finally:
        lock.release()


def _sync_worktree_upstream(instance_id: str, instance: dict) -> None:
    """同步实例 worktree 与上游。失败时记录 deviation，不阻塞流程。

    Level 1（根实例）: 本地 main → 实例 worktree
    Level 1.5（子实例）: 父实例 worktree → 子实例 worktree
    二者互斥，根据 parent_instance_id 判断。
    """
    parent_id = instance.get("parent_instance_id")

    if parent_id:
        success, msg = sync_instance_with_parent(instance_id, parent_id)
    else:
        success, msg = sync_instance_with_main(instance_id)

    if not success:
        append_deviation(instance_id, "SYNC_SKIPPED", msg)


def _load_workflow_for_instance(instance: dict) -> "WorkflowSpec":
    """加载工作流 spec。"""
    from services.resolver import find_workflow_dir
    workflow_id = instance["workflow_id"]
    version = instance.get("version", "")
    wf_dir = find_workflow_dir(workflow_id, version if version else None)
    yaml_file = wf_dir / "WORKFLOW.yaml"
    return load_workflow(yaml_file)


def _build_worktree_map(instance_id: str, instance: dict) -> dict[str, Path]:
    """构建 stage_id → worktree 路径映射。

    优先检查 stage 级 worktree 是否存在（文件系统优先），
    不存在时回退到实例 worktree。这比仅依赖 stage_instance_id 命名约定更健壮。
    """
    root = find_root()
    wt_map: dict[str, Path] = {}
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

    for s in instance.get("stages", []):
        sid = s["stage_id"]
        s_inst_id = s.get("stage_instance_id", sid)
        stage_wt = root / ".tmp" / "worktrees" / f"stage-{instance_id}-{s_inst_id}"
        if stage_wt.exists():
            wt_map[sid] = stage_wt
        else:
            wt_map[sid] = inst_wt

    return wt_map


def _auto_commit_done_stages(instance_id: str, changes: list[dict], worktree_map: dict[str, Path], anchor_prefix: str) -> None:
    """
    对刚转为 DONE 的 stage，在其 worktree 中自动提交变更 + 打锚点。
    提交信息 = report + wf-* trailers，通过 git commit -F 临时文件提交。
    锚点按 stage_instance_id 命名，覆盖并行实例和普通 stage。
    """
    done_changes = [c for c in changes if c.get("new_status") == "DONE"]
    for change in done_changes:
        stage_id = change["stage_id"]
        msg = change.get("message", {})
        worktree = worktree_map.get(stage_id)
        if not worktree or not worktree.exists():
            continue

        report = msg.get("report", f"stage {stage_id} done")
        stage_inst = msg.get("stage_instance_id", stage_id)
        message_id = msg.get("message_id", "")

        full_msg = (
            f"{report}\n\n"
            f"wf-stage: {stage_inst}\n"
            f"wf-instance: {instance_id}\n"
            f"wf-message: {message_id}\n"
        )

        msg_file = worktree / ".wfctl_commit_msg"
        msg_file.write_text(full_msg, encoding="utf-8")

        rc, _, stderr = git_add_all(worktree)
        if rc != 0:
            msg_file.unlink(missing_ok=True)
            raise GitError(f"auto-commit add failed for stage {stage_id}: {stderr}")

        rc, _, stderr = git_commit_file(worktree, msg_file)
        msg_file.unlink(missing_ok=True)
        if rc != 0:
            raise GitError(f"auto-commit failed for stage {stage_id}: {stderr}")

        # 打锚点：commit 成功后立即打标，确保 rollback 可定位到该 stage
        anchor = f"{anchor_prefix}-{instance_id}-{stage_inst}"
        try:
            tag_anchor(instance_id, anchor, worktree=worktree)
        except Exception:
            pass


def _ensure_anchors_for_done_stages(instance_id: str, instance: dict, worktree_map: dict[str, Path], anchor_prefix: str) -> None:
    """为所有 DONE 但缺锚点的 stage 补打锚点。

    覆盖 confirm 驱动 DONE 的场景——confirm 直接写入 DONE 状态而不经过
    _auto_commit_done_stages 的 changes 路径，导致锚点缺失。
    本函数扫描所有 DONE stage，对缺少 git tag 的逐一补提交 + 补锚。
    """
    from core.git_ops import git_add_all, git_commit_file, git_tag_exists

    root = find_root()
    for s in instance.get("stages", []):
        if s.get("status") != "DONE":
            continue
        stage_id = s["stage_id"]
        stage_inst = s.get("stage_instance_id", stage_id)
        anchor_name = f"{anchor_prefix}-{instance_id}-{stage_inst}"

        worktree = worktree_map.get(stage_id)
        if not worktree or not worktree.exists():
            continue

        if git_tag_exists(worktree, anchor_name):
            continue

        # 检查 worktree 是否有未提交变更，无变更则只补锚点
        rc, stdout, _ = git_status_porcelain(worktree)
        if rc != 0:
            continue
        if stdout.strip():
            report = f"stage {stage_id} done (confirmed)"
            full_msg = (
                f"{report}\n\n"
                f"wf-stage: {stage_inst}\n"
                f"wf-instance: {instance_id}\n"
            )
            msg_file = worktree / ".wfctl_commit_msg"
            msg_file.write_text(full_msg, encoding="utf-8")

            rc_add, _, stderr = git_add_all(worktree)
            if rc_add != 0:
                msg_file.unlink(missing_ok=True)
                continue

            rc_commit, _, _ = git_commit_file(worktree, msg_file)
            msg_file.unlink(missing_ok=True)
            if rc_commit != 0:
                continue

        # 打锚点
        from services.worktree_manager import tag_anchor
        try:
            tag_anchor(instance_id, anchor_name, worktree=worktree)
        except Exception:
            pass


def _merge_done_stage_worktrees(instance_id: str, instance: dict, changes: list[dict], worktree_map: dict[str, Path]) -> list[dict]:
    """将 DONE stage 的独立 worktree 按 stage_id 字典序依次合入实例 worktree。

    规范 §十三：并发 stage 完成后，wfctl next 将临时分支合并回实例 worktree。
    无冲突 → stage 保持 DONE，stage worktree 被自动清理。
    有冲突 → stage 回退为 CONFLICT，返回 conflict action 供主 Agent 调度冲突消解。
    """
    root = find_root()
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

    # 筛选：刚转为 DONE、且有独立 worktree 的 stage
    merge_candidates: list[dict] = []
    for change in changes:
        if change.get("new_status") != "DONE":
            continue
        stage_id = change["stage_id"]
        worktree = worktree_map.get(stage_id)
        if not worktree or not worktree.exists():
            continue
        if worktree.resolve() == inst_wt.resolve():
            continue
        merge_candidates.append(change)

    if not merge_candidates:
        return []

    # 按 stage_id 字典序排序，确保合并顺序确定性
    merge_candidates.sort(key=lambda c: c["stage_id"])

    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    conflict_actions: list[dict] = []

    for change in merge_candidates:
        stage_id = change["stage_id"]
        stage_inst_id = change.get("message", {}).get("stage_instance_id", stage_id)

        try:
            success, conflict_files = merge_stage_worktree(instance_id, stage_inst_id)
            if not success:
                stage = stage_map.get(stage_id)
                if stage:
                    stage["status"] = "CONFLICT"
                    stage["conflict_files"] = conflict_files
                conflict_actions.append({
                    "action": "conflict",
                    "instance_id": instance_id,
                    "stage_id": stage_id,
                    "worktree": str(worktree_map[stage_id].relative_to(root)),
                    "conflict_files": conflict_files,
                    "source_stage": stage_id,
                })
        except GitError:
            stage = stage_map.get(stage_id)
            if stage:
                stage["status"] = "CONFLICT"
            conflict_actions.append({
                "action": "conflict",
                "instance_id": instance_id,
                "stage_id": stage_id,
                "worktree": str(worktree_map[stage_id].relative_to(root)),
                "conflict_files": [],
                "source_stage": stage_id,
            })

    return conflict_actions


def _check_child_workflows(instance: dict, root: Path) -> None:
    """检查子工作流完成状态。仅检查 RUNNING 的 WORKFLOW stage。"""
    for s in instance.get("stages", []):
        if s.get("status") != "RUNNING":
            continue
        child_id = s.get("child_instance_id")
        if not child_id:
            continue
        child_path = root / ".agent" / "instances" / child_id / "instance.json"
        if not child_path.exists():
            continue
        try:
            import json
            child = json.loads(child_path.read_text(encoding="utf-8"))
            if child.get("status") == "COMPLETED":
                s["status"] = "DONE"
                s["exit_condition"] = "success"
            elif child.get("status") == "FAILED":
                s["status"] = "ERROR"
        except Exception:
            pass


def _check_parallel(adj, instance: dict, instance_id: str, spec,
                    running_agents: list[dict] | None = None) -> list[dict]:
    """检查 parallel 拆分需求，返回 reinforce actions 列表。

    当上游 stage 未产出 parallel_targets 时：
    1. 上游 SubAgent 仍存活 → 发送 reinforce action，要求补交（最多 2 次）
    2. 上游 SubAgent 已终止或重试耗尽 → 置 ERROR，交由现有错误处理链终止流程
    """
    root = find_root()
    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    actions: list[dict] = []

    if running_agents is None:
        running_agents = _load_running_agents(instance_id)

    for stage in spec.stages:
        if not stage.parallel:
            continue
        source_stage_id = stage.parallel.source
        source_stage = stage_map.get(source_stage_id)
        if not source_stage or source_stage.get("status") != "DONE":
            continue

        # 检查是否已拆分（fan_out_target 标识并行实例）
        existing = [s for s in instance["stages"] if s["stage_id"] == stage.stage_id and s.get("fan_out_target")]
        if existing:
            continue

        # 检查是否已置为 ERROR（避免重复处理已终止的并行 stage）
        already_error = any(
            s["stage_id"] == stage.stage_id and s.get("status") == "ERROR"
            for s in instance["stages"]
        )
        if already_error:
            continue

        # 获取上游消息的 parallel_targets
        msg_id = source_stage.get("output_message_id")
        if not msg_id:
            actions.extend(_handle_missing_targets(
                instance, stage, instance_id, source_stage_id, running_agents,
                "上游 stage 已完成但未产出 output_message_id",
            ))
            continue

        msg_path = root / ".agent" / "instances" / instance_id / "messages" / f"{msg_id}.json"
        if not msg_path.exists():
            actions.extend(_handle_missing_targets(
                instance, stage, instance_id, source_stage_id, running_agents,
                f"上游 stage 的消息文件 {msg_id}.json 不存在",
            ))
            continue

        try:
            import json
            msg = json.loads(msg_path.read_text(encoding="utf-8"))
        except Exception:
            actions.extend(_handle_missing_targets(
                instance, stage, instance_id, source_stage_id, running_agents,
                f"上游 stage 的消息文件 {msg_id}.json 解析失败",
            ))
            continue

        if "parallel_targets" not in msg:
            reason = (
                f"上游 stage {source_stage_id} 未产出 parallel_targets"
                f"（SubAgent 上报时未传 --parallel-targets）"
            )
            actions.extend(_handle_missing_targets(
                instance, stage, instance_id, source_stage_id, running_agents, reason,
            ))
            continue

        targets = msg.get("parallel_targets", [])
        if not targets:
            continue

        # 有 targets → 清除重试计数，执行拆分
        _clear_parallel_retry(instance, stage.stage_id)

        max_inst = stage.parallel.max_instances
        if max_inst:
            targets = targets[:max_inst]

        new_stages: list[dict] = []
        for idx, target in enumerate(targets):
            stage_inst_id = f"{stage.stage_id}_{idx}"
            new_stages.append({
                "stage_id": stage.stage_id,
                "stage_instance_id": stage_inst_id,
                "status": "PENDING",
                "agent_id": None,
                "system_agent_id": None,
                "output_message_id": None,
                "loop_counter": 0,
                "attempt_count": 0,
                "confirmed": False,
                "started_at": None,
                "model": stage.model,
                "child_instance_id": None,
                "fan_out_target": target,
            })

        instance["stages"] = [s for s in instance["stages"] if not (s["stage_id"] == stage.stage_id and s.get("stage_instance_id") == stage.stage_id)]
        instance["stages"].extend(new_stages)

    return actions


def _handle_missing_targets(instance: dict, stage, instance_id: str, source_stage_id: str,
                            running_agents: list[dict], reason: str) -> list[dict]:
    """处理缺失 parallel_targets：先 reinforce，失败则置 ERROR。"""
    max_retry = 2
    retry_count = _get_parallel_retry(instance, stage.stage_id)

    source_agent = None
    for a in running_agents:
        if a.get("instance_id") == instance_id and a.get("stage_id") == source_stage_id:
            source_agent = a
            break

    if source_agent and retry_count < max_retry:
        _incr_parallel_retry(instance, stage.stage_id)
        new_count = retry_count + 1
        append_deviation(
            instance_id,
            "PARALLEL_TARGETS_REINFORCE",
            f"stage {stage.stage_id}: 上游 {source_stage_id} 未产出 parallel_targets，"
            f"第 {new_count}/{max_retry} 次强化重试",
            stage_id=stage.stage_id,
        )
        return [{
            "action": "reinforce",
            "instance_id": instance_id,
            "type": "parallel_targets_missing",
            "stage_id": stage.stage_id,
            "source_stage_id": source_stage_id,
            "system_agent_id": source_agent["system_agent_id"],
            "retry_count": new_count,
            "max_retry": max_retry,
            "message": (
                f"你在 stage {source_stage_id} 的上报中未包含 parallel_targets。"
                f"请根据 contracts/output.md 中「parallel_targets 规范」补充产出，"
                f"通过 --parallel-targets 参数重新上报（格式：id:标签:上下文）。"
                f"这是第 {new_count}/{max_retry} 次提醒，超次将终止工作流。"
            ),
        }]

    # SubAgent 已终止或重试耗尽 → 置 ERROR，移除 PENDING 条目防止被调度
    error_msg = (
        f"{reason}，stage {stage.stage_id} 的并行拆分无法执行"
        if retry_count == 0
        else f"{reason}，已强化重试 {retry_count} 次仍无 parallel_targets，"
             f"stage {stage.stage_id} 终止"
    )
    _error_parallel_stage(instance, stage, error_msg, instance_id, source_stage_id)
    return []


def _get_parallel_retry(instance: dict, stage_id: str) -> int:
    """读取 parallel 重试计数（仅 PENDING 且未拆分的单一条目）。"""
    for s in instance["stages"]:
        if s["stage_id"] == stage_id and s.get("status") == "PENDING" and not s.get("fan_out_target"):
            return s.get("parallel_retry_count", 0)
    return 0


def _incr_parallel_retry(instance: dict, stage_id: str) -> None:
    """递增 parallel 重试计数。"""
    for s in instance["stages"]:
        if s["stage_id"] == stage_id and s.get("status") == "PENDING" and not s.get("fan_out_target"):
            s["parallel_retry_count"] = s.get("parallel_retry_count", 0) + 1
            return


def _clear_parallel_retry(instance: dict, stage_id: str) -> None:
    """清除 parallel 重试计数（成功拆分时调用）。"""
    for s in instance["stages"]:
        if s["stage_id"] == stage_id and s.get("status") == "PENDING":
            s.pop("parallel_retry_count", None)


def _error_parallel_stage(instance: dict, stage, reason: str,
                          instance_id: str, source_stage_id: str) -> None:
    """将并行 stage 置为 ERROR 并写入合成错误消息，交由现有错误处理链终止流程。

    不引入新状态值——复用 ERROR 让 _handle_error_stages 按 stage.retry 决定
    是否重试（通常 retry=0 时直接走 failure_edge 或 terminate）。
    """
    # 移除原有的单 stage PENDING 条目（防止被 _spawn_child_workflows 调度）
    instance["stages"] = [s for s in instance["stages"] if not (
        s["stage_id"] == stage.stage_id and s.get("status") == "PENDING"
        and not s.get("fan_out_target")
    )]

    err_msg_id = _write_synthetic_error_message(
        instance_id, stage.stage_id,
        {"stage_instance_id": stage.stage_id}, reason,
    )

    # 添加 ERROR 条目
    instance["stages"].append({
        "stage_id": stage.stage_id,
        "stage_instance_id": stage.stage_id,
        "status": "ERROR",
        "agent_id": None,
        "system_agent_id": None,
        "output_message_id": err_msg_id,
        "loop_counter": 0,
        "attempt_count": 0,
        "confirmed": False,
        "started_at": None,
        "model": stage.model,
        "child_instance_id": None,
    })
    append_deviation(
        instance_id,
        "PARALLEL_TARGETS_MISSING",
        f"stage {stage.stage_id}: 上游 {source_stage_id} 未产出 parallel_targets，已置 ERROR",
        stage_id=stage.stage_id,
    )


def _write_synthetic_error_message(instance_id: str, stage_id: str,
                                     stage: dict, reason: str) -> str:
    """写入一条合成 ERROR 消息到消息池，返回 message_id。"""
    import uuid
    from core.atomic_write import atomic_write_json

    root = find_root()
    messages_dir = root / ".agent" / "instances" / instance_id / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    msg = {
        "schema_version": "3.0.0",
        "message_id": msg_id,
        "instance_id": instance_id,
        "stage_id": stage_id,
        "stage_instance_id": stage.get("stage_instance_id", stage_id),
        "status": "ERROR",
        "report": reason,
        "checkpoint_summary": "",
        "confirm_questions": [],
        "parallel_targets": None,
        "modified_files": [],
        "timestamp": iso_timestamp(),
    }
    atomic_write_json(messages_dir / f"{msg_id}.json", msg)
    stage["output_message_id"] = msg_id
    return msg_id


def _spawn_child_workflows(instance: dict, instance_id: str, spec, adj) -> list[dict]:
    """为 PENDING 的 WORKFLOW 类型 stage 实例创建子工作流 Instance。

    仅当 stage 的 DAG 入边已满足时才创建子实例（与 compute_ready 逻辑一致）。
    子 worktree 基于父实例 worktree HEAD 创建，继承父级所有已完成 stage 的文件产物。
    创建后 stage 状态 → RUNNING，child_instance_id 写入关联。
    返回新创建的子实例信息列表 [{child_instance_id, parent_stage_id}]。
    """
    root = find_root()
    stage_specs = {s.stage_id: s for s in spec.stages}
    stage_states = {s["stage_id"]: s for s in instance["stages"]}
    inst_wt = root / ".tmp" / "worktrees" / f"instance-{instance_id}"
    created: list[dict] = []

    # 获取父实例 worktree HEAD 作为子 worktree 基准
    rc, head_ref, _ = git_rev_parse(inst_wt, "HEAD")
    base_ref = head_ref.strip() if rc == 0 else "HEAD"

    for s in instance["stages"]:
        if s.get("status") != "PENDING":
            continue
        stage_id = s["stage_id"]
        stage_spec = stage_specs.get(stage_id)
        if not stage_spec or stage_spec.target_type != StageTargetType.WORKFLOW:
            continue

        # 检查 DAG 入边是否满足（与 compute_ready 逻辑一致）
        upstream_edges = adj.incoming.get(stage_id, [])
        if not _all_satisfied(upstream_edges, stage_states):
            continue

        # 解析 workflow 引用: "question-solution@1.0.0"
        wf_ref = stage_spec.target
        if "@" in wf_ref:
            child_wf_id, child_version = wf_ref.split("@", 1)
        else:
            child_wf_id, child_version = wf_ref, None

        # 构建子实例 goal（含 fan_out_target 上下文）
        fan_out = s.get("fan_out_target") or {}
        goal_parts = [fan_out.get("label", stage_id)]
        if fan_out.get("context"):
            goal_parts.append(fan_out["context"])
        child_goal = "：".join(goal_parts)

        from services.creator import create_instance as _create_child
        child_result = _create_child(
            workflow_id=child_wf_id,
            version=child_version,
            goal=child_goal,
            parent_instance_id=instance_id,
            worktree_base_ref=base_ref,
        )

        s["child_instance_id"] = child_result["instance_id"]
        s["status"] = "RUNNING"
        s["started_at"] = iso_timestamp()
        created.append({
            "child_instance_id": child_result["instance_id"],
            "parent_stage_id": stage_id,
        })

    return created


def _recurse_child_instances(parent_instance: dict, parent_instance_id: str) -> dict:
    """递归处理所有活跃子工作流实例，合并它们的 actions。

    对每个 RUNNING 的 WORKFLOW 类型 stage（已有 child_instance_id），
    获取子实例锁并调用 _run_next_inner()，合并返回的 actions。

    子实例的 await 不传播——子实例在等自己的 SubAgent，不应阻塞父级。
    子实例的 confirm 由调用方与父级 confirm 合并。

    Returns:
        {
            "spawn_continue": [...],   # spawn + continue actions
            "retry": [...],
            "reinforce": [...],
            "confirm_pending": [...],  # AWAITING_CONFIRM 条目
            "error": [...],
            "conflict": [...],
            "merge_conflict": [...],
            "terminate": [...],
        }
    """
    root = find_root()
    result: dict[str, list[dict]] = {
        "spawn_continue": [],
        "retry": [],
        "reinforce": [],
        "confirm_pending": [],
        "error": [],
        "conflict": [],
        "merge_conflict": [],
        "terminate": [],
    }

    for s in parent_instance.get("stages", []):
        child_id = s.get("child_instance_id")
        if not child_id:
            continue
        if s.get("status") != "RUNNING":
            continue

        try:
            child_instance = load_instance(child_id)
        except Exception:
            continue

        if child_instance.get("status") != "ACTIVE":
            continue

        child_lock_path = root / ".agent" / "instances" / child_id / "instance.json"
        child_lock = FileLock(child_lock_path)
        if not child_lock.acquire(timeout=10.0):
            append_deviation(
                parent_instance_id, "CHILD_LOCK_FAILED",
                f"Could not acquire lock for child instance {child_id}",
                stage_id=s.get("stage_id"),
            )
            continue

        try:
            child_result = _run_next_inner(child_id)
            if child_result.get("status") != "ok":
                continue

            for action in child_result.get("actions", []):
                action_type = action.get("action")
                if action_type in ("spawn", "continue"):
                    result["spawn_continue"].append(action)
                elif action_type == "retry":
                    result["retry"].append(action)
                elif action_type == "reinforce":
                    result["reinforce"].append(action)
                elif action_type == "confirm":
                    result["confirm_pending"].extend(action.get("pending", []))
                elif action_type == "conflict":
                    if action.get("source_stage"):
                        result["merge_conflict"].append(action)
                    else:
                        result["conflict"].append(action)
                elif action_type == "terminate":
                    result["terminate"].append(action)
                elif action_type in ("error",):
                    result["error"].append(action)
                # await 不传播——子实例等待中，父级不受影响
        finally:
            child_lock.release()

    return result


def _check_timeouts(instance: dict, spec, instance_id: str) -> None:
    """检测 RUNNING stage 是否超时，自动写入 ERROR 并追加 deviation。

    宿主平台在 timeout_seconds 到期后终止 SubAgent 并通知主 Agent。
    主 Agent 调用 next，wfctl 发现 RUNNING stage 无新消息且已超时 → 自动 ERROR。
    """
    root = find_root()
    stage_spec_map = {s.stage_id: s for s in spec.stages}

    for s in instance["stages"]:
        if s.get("status") != "RUNNING":
            continue

        started_at = s.get("started_at")
        if not started_at:
            continue

        stage_spec = stage_spec_map.get(s["stage_id"])
        if not stage_spec or not stage_spec.timeout_seconds:
            continue

        try:
            elapsed = time.time() - parse_iso_timestamp(started_at)
        except (ValueError, OSError):
            continue

        if elapsed > stage_spec.timeout_seconds:
            stage_id = s["stage_id"]
            s["status"] = "ERROR"
            s["started_at"] = None

            # 写入超时消息到消息池（由后续 consume_messages 消费并写入 timeline）
            messages_dir = root / ".agent" / "instances" / instance_id / "messages"
            messages_dir.mkdir(parents=True, exist_ok=True)
            import uuid
            msg_id = f"msg-{uuid.uuid4().hex[:8]}"
            msg = {
                "schema_version": "3.0.0",
                "message_id": msg_id,
                "instance_id": instance_id,
                "stage_id": stage_id,
                "stage_instance_id": s.get("stage_instance_id", stage_id),
                "status": "ERROR",
                "report": f"Stage timed out after {stage_spec.timeout_seconds}s",
                "checkpoint_summary": "",
                "confirm_questions": [],
                "parallel_targets": None,
                "modified_files": [],
                "timestamp": iso_timestamp(),
            }
            from core.atomic_write import atomic_write_json
            atomic_write_json(messages_dir / f"{msg_id}.json", msg)

            append_deviation(
                instance_id,
                "STAGE_TIMEOUT",
                f"Stage {stage_id} timed out after {elapsed:.0f}s",
                stage_id=stage_id,
            )


def _resolve_virtual_stages(adj, instance: dict, instance_id: str, spec) -> None:
    """预处理虚拟 stage：在就绪计算前将满足条件的虚拟 stage 标为 DONE。

    虚拟 stage（如 s00-workflow-start）无业务逻辑，上游满足即应立即标记 DONE。
    这样下游真实 stage 可在同一次 next 中进入就绪列表，避免多余的 await 往返。
    循环处理以支持级联虚拟 stage（s00-virtual → s01-virtual → s02-real）。
    """
    from core.schema.interface import StageTargetType

    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    stage_specs = {s.stage_id: s for s in spec.stages}

    changed = True
    while changed:
        changed = False
        for stage_id, stage_spec in stage_specs.items():
            if stage_spec.target_type != StageTargetType.VIRTUAL:
                continue
            state = stage_map.get(stage_id)
            if not state or state.get("status") != "PENDING":
                continue
            upstream_edges = adj.incoming.get(stage_id, [])
            if _all_satisfied_virtual(upstream_edges, stage_map):
                state["status"] = "DONE"
                # 打锚点
                anchor = f"{spec.anchor_prefix}-{instance_id}-{stage_id}"
                try:
                    tag_anchor(instance_id, anchor)
                except Exception:
                    pass
                changed = True


def _all_satisfied_virtual(upstream_edges: list, stage_states: dict) -> bool:
    """虚拟 stage 的就绪判断：按 exit_condition 匹配边条件。

    与 _all_satisfied 对齐：每条边仅在 upstream stage DONE 且 exit_condition
    匹配时生效。FAILURE / REJECTED / LOOP_EXCEEDED 仅由专用 handler 触发，
    不计入就绪。
    """
    if not upstream_edges:
        return True
    from core.schema.interface import EdgeCondition
    for edge in upstream_edges:
        upstream_stage = stage_states.get(edge.from_stage, {})
        upstream_status = upstream_stage.get("status", "PENDING")
        if upstream_status != "DONE":
            continue
        exit_cond = upstream_stage.get("exit_condition", "")

        if edge.condition == EdgeCondition.ALWAYS:
            return True
        if edge.condition == EdgeCondition.SUCCESS and exit_cond in ("success", ""):
            return True
        if edge.condition == EdgeCondition.CONFIRMED and exit_cond in ("confirmed", ""):
            return True
    return False


def _handle_error_stages(instance: dict, adj, spec, instance_id: str) -> list[dict]:
    """处理 ERROR 分支。"""
    actions: list[dict] = []
    stage_map = {s["stage_id"]: s for s in instance["stages"]}

    for s in instance["stages"]:
        if s.get("status") != "ERROR":
            continue

        stage_id = s["stage_id"]
        stage_spec = adj.stages.get(stage_id)
        max_attempts = stage_spec.retry if stage_spec else 0

        attempt_count = s.get("attempt_count", 0)

        if attempt_count < max_attempts:
            s["status"] = "PENDING"
            s["attempt_count"] = attempt_count + 1
            actions.append({
                "action": "retry",
                "instance_id": instance_id,
                "stage_id": stage_id,
                "attempt": s["attempt_count"],
            })
            continue

        # 重试耗尽
        failure_edge = get_failure_edge(adj, stage_id)
        loop_counter = s.get("loop_counter", 0)
        if failure_edge and loop_counter < (failure_edge.max_loop or 0):
            target_stage = stage_map.get(failure_edge.to_stage)
            if not target_stage:
                instance["status"] = "FAILED"
                actions.append({
                    "action": "terminate",
                    "instance_id": instance_id,
                    "status": "FAILED",
                    "reason": "failure edge targets non-existent stage",
                })
                continue
            target_stage["status"] = "PENDING"
            target_stage["loop_counter"] = loop_counter + 1
            actions.append({
                "action": "spawn",
                "instance_id": instance_id,
                "stage_id": failure_edge.to_stage,
                "reason": "failure-edge",
            })
            continue

        # failure edge 也耗尽
        loop_exceeded_edge = get_loop_exceeded_edge(adj, stage_id)
        if loop_exceeded_edge:
            target_stage = stage_map.get(loop_exceeded_edge.to_stage)
            if target_stage:
                target_stage["status"] = "PENDING"
            actions.append({
                "action": "spawn",
                "instance_id": instance_id,
                "stage_id": loop_exceeded_edge.to_stage,
                "reason": "loop-exceeded",
            })
            continue

        # 无可用 handler
        instance["status"] = "FAILED"
        actions.append({
            "action": "terminate",
            "instance_id": instance_id,
            "status": "FAILED",
            "reason": f"no handler for stage {stage_id} error",
        })

    return actions


def _handle_conflict_stages(instance: dict, instance_id: str) -> list[dict]:
    """处理 CONFLICT 分支：尝试重试合并。"""
    actions: list[dict] = []
    for s in instance["stages"]:
        if s.get("status") != "CONFLICT":
            continue

        stage_id = s["stage_id"]
        stage_inst_id = s.get("stage_instance_id", stage_id)

        from services.worktree_manager import resolve_conflicts_and_merge
        try:
            success = resolve_conflicts_and_merge(instance_id, stage_inst_id)
            if success:
                s["status"] = "DONE"
                # 打锚点
                spec = _load_workflow_for_instance(instance)
                anchor = f"{spec.anchor_prefix}-{instance_id}-{stage_inst_id}"
                tag_anchor(instance_id, anchor)
            else:
                actions.append({
                    "action": "conflict",
                    "instance_id": instance_id,
                    "stage_id": stage_id,
                    "conflict_files": s.get("conflict_files", []),
                    "source_stage": stage_id,
                })
        except GitError as e:
            actions.append({
                "action": "conflict",
                "instance_id": instance_id,
                "stage_id": stage_id,
                "conflict_files": s.get("conflict_files", []),
                "source_stage": stage_id,
            })

    return actions


def _apply_scheduling_constraints(ready: list[str], instance: dict, spec) -> list[str]:
    """应用 exclusive 和 max_parallel_agents 约束。"""
    running = [s for s in instance["stages"] if s.get("status") == "RUNNING"]

    # 有 exclusive RUNNING → 过滤掉所有就绪 stage
    running_stage_ids = {s["stage_id"] for s in running}
    stage_spec_map = {s.stage_id: s for s in spec.stages}
    if any(stage_spec_map.get(sid) and stage_spec_map[sid].exclusive for sid in running_stage_ids):
        return []

    # max_parallel_agents
    max_parallel = spec.max_parallel_agents
    if len(running) >= max_parallel:
        return []

    # 最多再启动 max_parallel - len(running) 个
    available_slots = max_parallel - len(running)
    return ready[:available_slots]


def _is_parallel_instance(stage_inst_id: str) -> bool:
    """判断 stage_instance_id 是否为 parallel 拆分实例（含 _<digit> 后缀）。"""
    parts = stage_inst_id.rsplit("_", 1)
    return len(parts) == 2 and parts[1].isdigit()


def _allocate_and_spawn(ready: list[str], instance: dict, instance_id: str, adj, spec,
                        running_agents: list[dict]) -> list[dict]:
    """为就绪 stage 分配 worktree 并生成 spawn/continue action。

    同 Skill 延续检测（§6.5）：对每个就绪 stage，查 running_agents 中是否有
    同 skill_id 的条目。命中 → continue action；未命中 → spawn action。
    parallel 拆分实例不参与映射表。
    """
    if not ready:
        return []

    root = find_root()
    actions: list[dict] = []

    # 构建 stage 状态查找表
    stage_map = {s["stage_id"]: s for s in instance["stages"]}

    # 判断是否需要拆分 worktree
    multi_ready = len(ready) > 1

    for stage_id in ready:
        stage_spec = adj.stages.get(stage_id)
        if not stage_spec:
            continue
        if stage_spec.target_type == StageTargetType.VIRTUAL:
            stage_state = stage_map.get(stage_id)
            if stage_state:
                stage_state["status"] = "DONE"
            continue

        if stage_spec.target_type == StageTargetType.WORKFLOW:
            continue

        stage_state = stage_map.get(stage_id)
        if not stage_state:
            continue

        stage_inst_id = stage_state.get("stage_instance_id", stage_id)
        is_parallel = stage_inst_id != stage_id or stage_state.get("fan_out_target")

        # 6.5 同 Skill 延续检测：parallel 实例不参与映射表
        skill_id = stage_spec.target
        matched_agent = None
        if not is_parallel:
            matched_agent = _lookup_running_agent(running_agents, skill_id)

        # worktree 分配（spawn 和 continue 完全一致）
        if multi_ready or is_parallel:
            if _is_parallel_instance(stage_inst_id):
                base_id, idx_str = stage_inst_id.rsplit("_", 1)
                worktree = create_parallel_worktree(instance_id, base_id, int(idx_str))
            else:
                worktree = create_stage_worktree(instance_id, stage_inst_id)
        else:
            worktree = root / ".tmp" / "worktrees" / f"instance-{instance_id}"

        # 更新 stage 状态
        stage_state["status"] = "RUNNING"
        stage_state["started_at"] = iso_timestamp()

        # 构建 context
        context = _build_context(stage_id, adj, instance, stage_spec)

        needs_targets = any(
            s.parallel and s.parallel.source == stage_id
            for s in spec.stages
        )
        stage_state["requires_parallel_targets"] = needs_targets

        routing_choices = _collect_success_choices(adj, stage_id)
        stage_state["valid_routing_choices"] = routing_choices

        if matched_agent:
            # Level 2: 同步 stage worktree ↔ 实例 worktree（continue 前）
            sync_ok, conflict_files = sync_stage_with_instance(instance_id, stage_inst_id)
            if not sync_ok:
                stage_state["status"] = "CONFLICT"
                stage_state["conflict_files"] = conflict_files
                actions.append({
                    "action": "conflict",
                    "instance_id": instance_id,
                    "stage_id": stage_id,
                    "worktree": str(worktree.relative_to(root)),
                    "conflict_files": conflict_files,
                    "source_stage": stage_id,
                })
                continue

            # 同 Skill 延续：标记上游 stage 的 continued_to
            prev_stage_id = matched_agent["stage_id"]
            prev_stage = stage_map.get(prev_stage_id)
            if prev_stage:
                prev_stage["continued_to"] = stage_id

            sys_id = matched_agent["system_agent_id"]
            stage_state["system_agent_id"] = sys_id

            # 更新 running_agents.json 中的 stage_id
            _save_running_agent(instance_id, skill_id, sys_id, stage_id)

            actions.append({
                "action": "continue",
                "instance_id": instance_id,
                "stage_id": stage_id,
                "skill_id": skill_id,
                "worktree": str(worktree.relative_to(root)),
                "system_agent_id": sys_id,
                "requires_parallel_targets": needs_targets,
                "confirmation_point": stage_spec.confirmation_point,
                "valid_routing_choices": routing_choices,
                "context": context,
            })
        else:
            actions.append({
                "action": "spawn",
                "instance_id": instance_id,
                "stage_id": stage_id,
                "skill_id": skill_id,
                "worktree": str(worktree.relative_to(root)),
                "requires_parallel_targets": needs_targets,
                "confirmation_point": stage_spec.confirmation_point,
                "valid_routing_choices": routing_choices,
                "context": context,
            })

    return actions


def _load_running_agents(instance_id: str) -> list[dict]:
    """从 .agent/running_agents.json 读取项目级存活 SubAgent 列表，
    按 instance_id 过滤，仅返回当前实例的条目。
    """
    root = find_root()
    path = root / ".agent" / "running_agents.json"
    if not path.exists():
        return []
    try:
        import json
        all_agents = json.loads(path.read_text(encoding="utf-8"))
        return [a for a in all_agents if a.get("instance_id") == instance_id]
    except Exception:
        return []


def _save_running_agent(instance_id: str, skill_id: str, system_agent_id: str, stage_id: str) -> None:
    """追加或更新 running_agents.json 中的条目（按 system_agent_id 去重）。"""
    root = find_root()
    path = root / ".agent" / "running_agents.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    import json
    agents: list[dict] = []
    if path.exists():
        try:
            agents = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 按 system_agent_id 去重：移除同 ID 的旧条目
    agents = [a for a in agents if a.get("system_agent_id") != system_agent_id]
    agents.append({
        "skill_id": skill_id,
        "system_agent_id": system_agent_id,
        "stage_id": stage_id,
        "instance_id": instance_id,
    })
    from core.atomic_write import atomic_write_json
    atomic_write_json(path, agents)


def _lookup_running_agent(running_agents: list[dict], skill_id: str) -> dict | None:
    """在 running_agents 中查找同 skill_id 的存活 SubAgent。

    返回命中条目或 None。多条命中时取第一条。
    """
    for agent in running_agents:
        if agent.get("skill_id") == skill_id:
            return agent
    return None


def _build_context(stage_id: str, adj, instance: dict, stage_spec) -> dict:
    """构建传递给 SubAgent 的上下文。"""
    upstream_summaries = []
    for edge in adj.incoming.get(stage_id, []):
        upstream_stage = next((s for s in instance["stages"] if s["stage_id"] == edge.from_stage), None)
        if upstream_stage and upstream_stage.get("output_message_id"):
            upstream_summaries.append({
                "stage_id": edge.from_stage,
                "message_id": upstream_stage["output_message_id"],
            })

    return {
        "upstream": upstream_summaries,
        "stage_name": stage_spec.name or stage_id,
        "successor_stages_block": _build_successor_stages_block(stage_id, stage_spec, adj),
    }


def _build_successor_stages_block(stage_id: str, stage_spec, adj) -> str:
    """按 subagent-prompt-template.md 生成规则构造后继 stage 清单文本。"""
    current_skill_id = stage_spec.target
    current_name = stage_spec.name or stage_id

    successors: list[tuple[str, str, str | None]] = []
    for edge in adj.outgoing.get(stage_id, []):
        if edge.condition.value in ("success", "always"):
            succ_spec = adj.stages.get(edge.to_stage)
            if succ_spec and succ_spec.target_type != StageTargetType.VIRTUAL:
                successors.append((edge.to_stage, succ_spec.name or edge.to_stage, succ_spec.target))

    if not successors:
        return "你是工作流的最后一个 stage，需产出最终交付物。完成后上报 DONE。"

    lines = [
        "本 stage 下游还有以下 stage，它们将由编排器独立调度，**不属于你的职责范围**："
    ]
    for succ_id, succ_name, succ_skill in successors:
        if succ_skill == current_skill_id:
            lines.append(
                f"  - {succ_id}「{succ_name}」⚠️ 与你使用同一 Skill——"
                f"你只需执行 Skill 中属于「{current_name}」的部分"
            )
        else:
            lines.append(f"  - {succ_id}「{succ_name}」")
    lines.append("")
    lines.append(
        f"你只需完成「{current_name}」的工作，产出 checkpoint，"
        f"然后上报 DONE / AWAITING_CONFIRM 并停止。"
        f"后续 stage 由编排器根据你的 checkpoint 和路由选择独立调度。"
    )
    return "\n".join(lines)


def _collect_confirm_pending(instance: dict, adj) -> list[dict] | None:
    """收集当前实例自身的 AWAITING_CONFIRM stage。

    子工作流实例的确认由 _recurse_child_instances() 收集并合并。
    返回 None 表示无待确认项。
    """
    pending: list[dict] = []
    for s in instance.get("stages", []):
        if s.get("status") == "AWAITING_CONFIRM":
            pending.append({
                "stage_id": s["stage_id"],
                "instance_id": instance["instance_id"],
                "questions": s.get("confirm_questions", []),
                "valid_choices": _collect_valid_choices(adj, s["stage_id"]),
            })
    return pending if pending else None


def _collect_valid_choices(adj, stage_id: str) -> list[str]:
    """收集 stage 所有 confirmed + rejected 边的 choice 值（去重）。"""
    from core.dag import get_confirmed_edges, get_rejected_edges
    choices: list[str] = []
    for e in get_confirmed_edges(adj, stage_id) + get_rejected_edges(adj, stage_id):
        if e.choice and e.choice not in choices:
            choices.append(e.choice)
    return choices


def _collect_success_choices(adj, stage_id: str) -> list[str]:
    """收集 stage 所有 SUCCESS 边的 choice 值（去重，不含 None）。
    返回空列表表示该 stage 无路由选择。
    """
    choices: list[str] = []
    for e in adj.outgoing.get(stage_id, []):
        if e.condition.value == "success" and e.choice and e.choice not in choices:
            choices.append(e.choice)
    return choices


def _check_all_done(instance: dict, spec) -> bool:
    """检查是否所有非虚拟 stage 都 DONE。"""
    non_virtual = [s for s in spec.stages if s.target_type != StageTargetType.VIRTUAL]
    if not non_virtual:
        return False
    stage_map = {s["stage_id"]: s for s in instance["stages"]}
    return all(stage_map.get(s.stage_id, {}).get("status") == "DONE" for s in non_virtual)


def _execute_merge_to_main(instance: dict, spec, instance_id: str) -> dict:
    """执行实例 worktree 合入主仓库（wfctl 是 git 操作的唯一执行者）。

    调度计算与副作用分离：此函数仅在所有 stage DONE 后调用，
    不参与 DAG 计算或 action 生成。
    """
    from services.worktree_manager import merge_instance_to_main
    try:
        success, conflict_files = merge_instance_to_main(instance_id)
        if success:
            instance["status"] = "COMPLETED"
            anchor = f"{spec.anchor_prefix}-{instance_id}-final"
            tag_anchor(instance_id, anchor)
            return {"action": "merge_to_main", "status": "completed"}
        else:
            return {
                "action": "conflict",
                "conflict_files": conflict_files,
                "worktree": ".",
            }
    except GitError as e:
        return {"action": "merge_to_main", "status": "error", "reason": str(e)}
