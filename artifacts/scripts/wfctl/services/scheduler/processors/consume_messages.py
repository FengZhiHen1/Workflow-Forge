"""ConsumeMessagesProcessor：消费消息 + AWAITING_CONFIRM 合法性校验。

步骤 1, 1.5：消费消息池，更新 stage 状态。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.dag import get_confirmed_edges
from core.errors import InputError
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageState, StateDelta, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.state_manager import consume_messages, append_deviation


@dataclass
class ConsumeMessagesProcessor:
    """消费消息池并校验 AWAITING_CONFIRM 合法性。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        # 将 InstanceState 转换为旧格式 dict 供 consume_messages 使用
        instance_dict = state.to_dict()
        changes = consume_messages(ctx.instance_id, instance_dict, ctx.worktree_map)

        delta = StateDelta()
        for change in changes:
            stage_id = change["stage_id"]
            new_status = change["new_status"]
            old_status = change["old_status"]
            msg = change.get("message", {})

            if new_status == "ERROR":
                append_deviation(
                    ctx.instance_id, "STAGE_ERROR",
                    msg.get("report", ""),
                    stage_id=stage_id,
                )

            # 状态转换映射
            status_map = {
                "DONE": StageStatus.DONE,
                "ERROR": StageStatus.ERROR,
                "AWAITING_CONFIRM": StageStatus.AWAITING_CONFIRM,
                "RUNNING": StageStatus.RUNNING,
            }
            if new_status in status_map:
                updates = {"status": status_map[new_status]}
                if new_status == "DONE":
                    updates["exit_condition"] = "success"
                    updates["output_message_id"] = msg.get("message_id")
                elif new_status == "ERROR":
                    updates["output_message_id"] = msg.get("message_id")
                elif new_status == "AWAITING_CONFIRM":
                    updates["output_message_id"] = msg.get("message_id")
                    updates["confirm_questions"] = msg.get("confirm_questions", [])
                delta.stage_updates[stage_id] = updates

        # 1.5 AWAITING_CONFIRM 合法性校验
        for change in changes:
            if change["new_status"] != "AWAITING_CONFIRM":
                continue
            sid = change["stage_id"]
            if not get_confirmed_edges(ctx.adj, sid):
                # 转为 ERROR
                delta.stage_updates[sid] = {
                    "status": StageStatus.ERROR,
                }
                append_deviation(
                    ctx.instance_id, "INVALID_AWAITING_CONFIRM",
                    f"Stage {sid} 无 confirmed 边但上报了 AWAITING_CONFIRM，已转为 ERROR",
                    stage_id=sid,
                )
                # 写入合成错误消息（由旧代码 _write_synthetic_error_message 处理）
                # 暂时跳过，因为消息写入需要更多上下文

        # 将 changes 存入 context 供后续 Processor 使用
        ctx.extra["message_changes"] = changes
        return ProcessorResult(state_delta=delta)
