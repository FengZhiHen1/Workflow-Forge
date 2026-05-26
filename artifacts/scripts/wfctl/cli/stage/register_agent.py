"""register-agent 命令——将 SubAgent 的 system_agent_id 写入 stage state。

编排器 spawn 后拿到 system_agent_id，通过此命令写入实例状态。
后续 next 直接读取 stage state，无需 running_agents.json。
"""

from infrastructure.errors import InputError
from compat.instance.registry import load_instance_state, save_instance_state
from state.model import StageStatus, StateDelta


def register_register_agent(subparsers):
    p = subparsers.add_parser("register-agent", help="将 system_agent_id 写入 stage state")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--stage", required=True, help="目标 stage_id")
    p.add_argument("--agent-id", required=True, help="SubAgent 的 system_agent_id")
    p.set_defaults(handler=_handle_register_agent)


def _handle_register_agent(args) -> dict:
    state = load_instance_state(args.instance)

    # 找到匹配的 RUNNING stage
    candidates = state.stages_by_id(args.stage)
    if not candidates:
        raise InputError(f"Stage not found: {args.stage}", code="STAGE_NOT_FOUND")

    stage = next((s for s in candidates if s.status == StageStatus.RUNNING), None)
    if stage is None:
        raise InputError(
            f"No RUNNING instance for stage {args.stage}",
            code="INVALID_ARGUMENT",
        )

    delta = StateDelta(
        stage_updates={stage.stage_instance_id: {"system_agent_id": args.agent_id}},
    )
    new_state = state.apply_delta(delta)
    save_instance_state(args.instance, new_state)

    return {
        "status": "ok",
        "instance_id": args.instance,
        "stage_id": args.stage,
        "system_agent_id": args.agent_id,
    }
