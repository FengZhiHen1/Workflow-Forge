"""create 命令。"""

from services.creator import create_instance


def register_create(subparsers):
    p = subparsers.add_parser("create", help="创建实例 worktree 和状态机")
    p.add_argument("--workflow", required=True, help="工作流 ID@版本")
    p.add_argument("--goal", default="", help="实例目标声明")
    p.add_argument(
        "--clone",
        dest="clone_from",
        default=None,
        metavar="OLD_INSTANCE_ID",
        help="从失败/暂停的旧实例克隆：继承 DONE stage、复制 worktree 文件、保留消息记录",
    )
    p.add_argument(
        "--fast-forward-to",
        dest="fast_forward_to",
        default=None,
        metavar="STAGE_ID",
        help="创建实例后，将目标 stage 的所有拓扑前驱自动标记为 DONE，直接从该 stage 开始",
    )
    p.set_defaults(handler=_handle_create)


def _handle_create(args) -> dict:
    wf_id = args.workflow
    version = None
    if "@" in wf_id:
        wf_id, version = wf_id.split("@", 1)
    return create_instance(
        wf_id,
        version=version,
        goal=args.goal,
        clone_from=getattr(args, "clone_from", None),
        fast_forward_to=getattr(args, "fast_forward_to", None),
    )
