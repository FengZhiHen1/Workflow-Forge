"""status 命令。"""

from services.status_builder import build_instance_status, build_project_status


def register_status(subparsers):
    p = subparsers.add_parser("status", help="查询项目全局状态或实例详情")
    p.add_argument("--instance", help="实例 ID（不提供则返回项目级）")
    p.set_defaults(handler=_handle_status)


def _handle_status(args) -> dict:
    if args.instance:
        return build_instance_status(args.instance)
    return build_project_status()
