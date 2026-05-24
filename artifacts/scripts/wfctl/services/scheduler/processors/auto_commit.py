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
        changes = ctx.extra.get("message_changes", [])
        self._auto_commit_done_stages(ctx, changes)
        self._ensure_anchors_for_done_stages(ctx, state)
        return ProcessorResult()

    def _auto_commit_done_stages(self, ctx: ExecutionContext, changes: list[dict]) -> None:
        done_changes = [c for c in changes if c.get("new_status") == "DONE"]
        for change in done_changes:
            stage_id = change["stage_id"]
            msg = change.get("message", {})
            worktree = ctx.worktree_map.get(stage_id)
            if not worktree or not worktree.exists():
                continue

            report = msg.get("report", f"stage {stage_id} done")
            stage_inst = msg.get("stage_instance_id", stage_id)
            message_id = msg.get("message_id", "")

            full_msg = (
                f"{report}\n\n"
                f"wf-stage: {stage_inst}\n"
                f"wf-instance: {ctx.instance_id}\n"
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

            anchor = f"{ctx.spec.anchor_prefix}-{ctx.instance_id}-{stage_inst}"
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
