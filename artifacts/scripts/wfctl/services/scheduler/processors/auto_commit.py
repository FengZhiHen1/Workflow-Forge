"""AutoCommitProcessor：DONE stage 自动提交 + 补锚。

步骤 2, 2.5：对刚转为 DONE 的 stage 自动提交 git 变更并打锚点。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.errors import GitError
from core.git_ops import git_add_all, git_commit_file, git_status_porcelain, git_tag_exists
from core.project import find_root
from services.scheduler.context import ExecutionContext
from services.scheduler.state_model import InstanceState, StageStatus
from services.scheduler.processors.base import ProcessorResult
from services.worktree_manager import tag_anchor


@dataclass
class AutoCommitProcessor:
    """自动提交 DONE stage 的变更并补锚点。"""

    def process(self, ctx: ExecutionContext, state: InstanceState) -> ProcessorResult:
        self._auto_commit_done_stages(ctx, state)
        self._ensure_anchors_for_done_stages(ctx, state)
        return ProcessorResult()

    def _auto_commit_done_stages(self, ctx: ExecutionContext, state: InstanceState) -> None:
        import json

        for stage_inst_id in state.cycle_meta.newly_done_stage_instance_ids:
            st = state.stage_by_instance_id(stage_inst_id)
            if not st or st.status != StageStatus.DONE:
                continue

            worktree = ctx.worktree_map.get(st.stage_id)
            if not worktree or not worktree.exists():
                continue

            # 从消息文件读取 report
            report = f"stage {st.stage_id} done"
            message_id = st.output_message_id or ""
            if message_id:
                msg_path = (
                    ctx.root / ".agent" / "instances" / ctx.instance_id
                    / "messages" / f"{message_id}.json"
                )
                if msg_path.exists():
                    try:
                        msg_data = json.loads(msg_path.read_text(encoding="utf-8"))
                        report = msg_data.get("report", report)
                    except Exception:
                        pass

            full_msg = (
                f"{report}\n\n"
                f"wf-stage: {stage_inst_id}\n"
                f"wf-instance: {ctx.instance_id}\n"
                f"wf-message: {message_id}\n"
            )

            msg_file = worktree / ".wfctl_commit_msg"
            msg_file.write_text(full_msg, encoding="utf-8")

            rc, _, stderr = git_add_all(worktree)
            if rc != 0:
                msg_file.unlink(missing_ok=True)
                raise GitError(f"auto-commit add failed for stage {st.stage_id}: {stderr}")

            rc, _, stderr = git_commit_file(worktree, msg_file)
            msg_file.unlink(missing_ok=True)
            if rc != 0:
                raise GitError(f"auto-commit failed for stage {st.stage_id}: {stderr}")

            anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{stage_inst_id}"
            try:
                tag_anchor(ctx.instance_id, anchor, worktree=worktree)
            except Exception:
                pass

    def _ensure_anchors_for_done_stages(self, ctx: ExecutionContext, state: InstanceState) -> None:
        root = find_root()
        for st in state.stages:
            if st.status != StageStatus.DONE:
                continue
            stage_id = st.stage_id
            stage_inst = st.stage_instance_id
            anchor_name = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{stage_inst}"

            worktree = ctx.worktree_map.get(stage_id)
            if not worktree or not worktree.exists():
                continue

            if git_tag_exists(worktree, anchor_name):
                continue

            rc, stdout, _ = git_status_porcelain(worktree)
            if rc != 0:
                continue
            if stdout.strip():
                report = f"stage {stage_id} done (confirmed)"
                full_msg = (
                    f"{report}\n\n"
                    f"wf-stage: {stage_inst}\n"
                    f"wf-instance: {ctx.instance_id}\n"
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

            try:
                tag_anchor(ctx.instance_id, anchor_name, worktree=worktree)
            except Exception:
                pass
