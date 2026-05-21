"""deviate 命令。"""

from services.state_manager import append_deviation


def register_deviate(subparsers):
    p = subparsers.add_parser("deviate", help="记录偏差/非标行为")
    p.add_argument("--instance", required=True, help="实例 ID")
    p.add_argument("--type", required=True, help="偏差类型")
    p.add_argument("--reason", required=True, help="原因说明")
    p.add_argument("--stage", default=None, help="相关 stage_id")
    p.add_argument("--files", nargs="*", default=[], help="相关文件")
    p.set_defaults(handler=_handle_deviate)


def _handle_deviate(args) -> dict:
    append_deviation(
        args.instance,
        args.type,
        args.reason,
        stage_id=args.stage,
        files=args.files,
    )
    return {"status": "ok"}
