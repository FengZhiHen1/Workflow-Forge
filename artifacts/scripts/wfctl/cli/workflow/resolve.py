"""resolve 命令。"""

from services.resolver import resolve, resolve_workflow


def register_resolve(subparsers):
    p = subparsers.add_parser("resolve", help="工作流发现：扫描可用工作流或解析单个 YAML")
    p.add_argument("--workflow", help="工作流 ID@版本，如 math-model@2.1.0")
    p.set_defaults(handler=_handle_resolve)


def _handle_resolve(args) -> dict:
    if args.workflow:
        wf_id = args.workflow
        version = None
        if "@" in wf_id:
            wf_id, version = wf_id.split("@", 1)
        return resolve_workflow(wf_id, version)
    return {"workflows": resolve()}
