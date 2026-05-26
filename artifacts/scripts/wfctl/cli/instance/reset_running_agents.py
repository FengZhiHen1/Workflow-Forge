"""reset-running-agents 命令——清空 running_agents.json。

会话重启后所有 SubAgent 必然已死，编排器应在进入调度循环前调用此命令。
"""

from runtime.agent.manager import RunningAgentManager


def register_reset_running_agents(subparsers):
    p = subparsers.add_parser("reset-running-agents", help="清空 SubAgent 映射表")
    p.add_argument("--instance", default=None, help="仅清理指定实例的条目（可选）")
    p.set_defaults(handler=_handle_reset_running_agents)


def _handle_reset_running_agents(args) -> dict:
    mgr = RunningAgentManager()

    if args.instance:
        mgr.remove_for_instance(args.instance)
        return {"status": "ok", "action": "cleared_instance", "instance_id": args.instance}

    before = len(mgr.load())
    mgr.clear_all()
    return {"status": "ok", "action": "cleared_all", "removed_count": before}
