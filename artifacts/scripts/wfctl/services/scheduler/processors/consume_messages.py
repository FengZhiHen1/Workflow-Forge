"""ConsumeMessagesProcessor：消费消息 + AWAITING_CONFIRM 合法性校验。

步骤 1, 1.5：直接操作 InstanceState，零 dict 桥接。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import get_confirmed_edges
from core.project import find_root
from services.message_handler import scan_messages
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import (
    CycleMeta,
    InstanceState,
    StageState,
    StageStatus,
    StateDelta,
)
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import append_deviation, _append_timeline
from services.validator import validate_modified_files


@dataclass
class ConsumeMessagesProcessor:
    """消费消息池并校验 AWAITING_CONFIRM 合法性。

    纯函数风格：读消息 → 生成 StateDelta + CycleMeta → 返回。
    """

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        root = find_root()
        consumed_ids = set(state.consumed_message_ids)
        messages = scan_messages(ctx.instance_id, consumed_ids)

        delta = StateDelta()
        cycle_meta = state.cycle_meta
        new_consumed: set[str] = set(consumed_ids)

        # 构建 stage_instance_id → StageState 索引
        stage_index: dict[str, StageState] = {}
        stage_by_stage_id: dict[str, list[StageState]] = {}
        for st in state.stages:
            stage_index[st.stage_instance_id] = st
            stage_by_stage_id.setdefault(st.stage_id, []).append(st)

        for msg in messages:
            msg_id = msg.get("message_id", "")
            if not msg_id:
                continue

            # 定位 stage：优先 stage_instance_id，回退 stage_id
            st = self._find_stage(msg, stage_index, stage_by_stage_id)
            if st is None:
                new_consumed.add(msg_id)
                continue

            # 校验 modified_files
            try:
                wt = ctx.worktree_map.get(st.stage_id) if ctx.worktree_map else None
                if msg.get("modified_files") and wt:
                    validate_modified_files(wt, msg["modified_files"], st.stage_id)
            except Exception as e:
                import traceback
                delta.stage_updates[st.stage_instance_id] = {}
                _append_timeline(ctx.instance_id, st.stage_id, "running→error", {
                    "reason": str(e),
                    "message_id": msg_id,
                    "intended_status": msg.get("status"),
                    "traceback": traceback.format_exc(),
                })
                cycle_meta = cycle_meta.with_error(st.stage_instance_id)
                new_consumed.add(msg_id)
                continue

            old_status = st.status
            msg_status = msg.get("status", old_status.value)

            # 状态无变化 → 只消费消息 ID
            if old_status.value == msg_status:
                new_consumed.add(msg_id)
                continue

            if msg_status == "DONE":
                routing_choice = msg.get("routing_choice")
                if routing_choice:
                    valid = st.valid_routing_choices
                    if valid and routing_choice not in valid:
                        cycle_meta = cycle_meta.with_error(st.stage_instance_id)
                        _append_timeline(ctx.instance_id, st.stage_id, "running→error", {
                            "message_id": msg_id,
                            "reason": f"非法 routing_choice: '{routing_choice}'，合法值: {valid}",
                        })
                        new_consumed.add(msg_id)
                        continue
                    delta.stage_updates[st.stage_instance_id] = {
                        "exit_condition": "success",
                        "output_message_id": msg_id,
                        "routing_choice": routing_choice,
                    }
                else:
                    delta.stage_updates[st.stage_instance_id] = {
                        "exit_condition": "success",
                        "output_message_id": msg_id,
                    }
                _append_timeline(ctx.instance_id, st.stage_id, "running→done", {"message_id": msg_id})
                cycle_meta = cycle_meta.with_done(st.stage_instance_id)

            elif msg_status == "ERROR":
                delta.stage_updates[st.stage_instance_id] = {
                    "output_message_id": msg_id,
                }
                _append_timeline(ctx.instance_id, st.stage_id, "running→error", {
                    "message_id": msg_id, "reason": msg.get("report", ""),
                })
                cycle_meta = cycle_meta.with_error(st.stage_instance_id)

            elif msg_status == "AWAITING_CONFIRM":
                updates: dict = {
                    "output_message_id": msg_id,
                }
                if msg.get("confirm_questions"):
                    updates["confirm_questions"] = msg["confirm_questions"]
                delta.stage_updates[st.stage_instance_id] = updates
                _append_timeline(ctx.instance_id, st.stage_id, "running→awaiting_confirm", {"message_id": msg_id})
                cycle_meta = cycle_meta.with_awaiting_confirm(st.stage_instance_id)

            elif msg_status == "RUNNING":
                delta.stage_updates[st.stage_instance_id] = {"status": StageStatus.RUNNING}
                _append_timeline(ctx.instance_id, st.stage_id, "scheduled", {"message_id": msg_id})

            new_consumed.add(msg_id)

        # 1.5 AWAITING_CONFIRM 合法性校验
        for sid in list(cycle_meta.newly_awaiting_confirm_ids):
            st = stage_index.get(sid)
            if st is None:
                continue
            if not get_confirmed_edges(ctx.adj, st.stage_id):
                # 从 cycle_meta 移除，标记为 error
                cycle_meta = CycleMeta(
                    newly_done_stage_instance_ids=cycle_meta.newly_done_stage_instance_ids,
                    newly_error_stage_instance_ids=cycle_meta.newly_error_stage_instance_ids | {sid},
                    newly_awaiting_confirm_ids=cycle_meta.newly_awaiting_confirm_ids - {sid},
                    ready_candidates=cycle_meta.ready_candidates,
                )
                # 清除已写入的元数据
                delta.stage_updates.pop(sid, None)
                append_deviation(
                    ctx.instance_id, "INVALID_AWAITING_CONFIRM",
                    f"Stage {st.stage_id} 无 confirmed 边但上报了 AWAITING_CONFIRM，已转为 ERROR",
                    stage_id=st.stage_id,
                )

        # 更新 consumed_message_ids
        delta.instance_updates["consumed_message_ids"] = frozenset(new_consumed)

        # 构建最终 StateDelta（含 cycle_meta，frozen dataclass 不支持直接赋值）
        final_delta = StateDelta(
            stage_updates=delta.stage_updates,
            instance_updates=delta.instance_updates,
            append_stages=delta.append_stages,
            remove_stage_instance_ids=delta.remove_stage_instance_ids,
            cycle_meta=cycle_meta,
        )
        return ProcessorResult(state_delta=final_delta)

    @staticmethod
    def _find_stage(
        msg: dict,
        stage_index: dict[str, StageState],
        stage_by_stage_id: dict[str, list[StageState]],
    ) -> StageState | None:
        sid = msg.get("stage_instance_id") or msg.get("stage_id")
        if not sid:
            return None
        st = stage_index.get(sid)
        if st is not None:
            return st
        # 回退：按 stage_id 查找（取第一个 PENDING/RUNNING/ERROR/AWAITING_CONFIRM）
        candidates = stage_by_stage_id.get(sid, [])
        for s in candidates:
            if s.status in (StageStatus.RUNNING, StageStatus.PENDING, StageStatus.ERROR, StageStatus.AWAITING_CONFIRM):
                return s
        return candidates[0] if candidates else None
