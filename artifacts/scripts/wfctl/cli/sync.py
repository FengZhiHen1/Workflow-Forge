"""sync 命令。"""

from services.scheduler import run_sync


def register_sync(subparsers):
    p = subparsers.add_parser("sync", help="同步：仅消费消息，不计算 next")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.set_defaults(handler=_handle_sync)


def _handle_sync(args) -> dict:
    return run_sync(args.instance)
