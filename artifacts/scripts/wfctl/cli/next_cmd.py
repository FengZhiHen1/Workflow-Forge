"""next 命令。"""

from services.scheduler import run_next


def register_next(subparsers):
    p = subparsers.add_parser("next", help="调度核心：消费消息，推进状态，返回 action")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_next)


def _handle_next(args) -> dict:
    return run_next(args.instance)
