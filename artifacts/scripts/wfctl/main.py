"""CLI 入口：argparse 注册 + 异常捕获。"""

import argparse
import json
import sys
import traceback

from infrastructure.errors import WfctlError
from infrastructure.logging import log_error

from cli.workflow.create import register_create
from cli.stage.confirm import register_confirm
from cli.stage.deviate import register_deviate
from cli.message.identity import register_identity
from cli.message.write import register_message_write
from cli.stage.next_cmd import register_next
from cli.instance.pause import register_pause
from cli.workflow.resolve import register_resolve
from cli.instance.resume import register_resume
from cli.stage.rollback import register_rollback
from cli.stage.skip import register_skip
from cli.instance.status import register_status
from cli.instance.sync import register_sync
from cli.workflow.cleanup import register_cleanup
from cli.workflow.restore import register_restore
from cli.instance.terminate import register_terminate
from cli.workflow.visualize import register_visualize


def main():
    parser = argparse.ArgumentParser(prog="wfctl", description="工作流机械调度程序")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_resolve(subparsers)
    register_create(subparsers)
    register_pause(subparsers)
    register_resume(subparsers)
    register_next(subparsers)
    register_sync(subparsers)
    register_confirm(subparsers)
    register_rollback(subparsers)
    register_skip(subparsers)
    register_status(subparsers)
    register_deviate(subparsers)
    register_identity(subparsers)
    register_message_write(subparsers)
    register_cleanup(subparsers)
    register_restore(subparsers)
    register_terminate(subparsers)
    register_visualize(subparsers)

    args = parser.parse_args()

    try:
        result = args.handler(args)
        if result is None:
            result = {"status": "ok"}
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    except WfctlError as e:
        log_error(e)
        json.dump(
            {"status": "error", "error": str(e), "code": e.code},
            sys.stderr,
            indent=2,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        sys.exit(e.exit_code)
    except Exception as e:
        log_error(e)
        json.dump(
            {"status": "error", "error": str(e), "exception": traceback.format_exc()},
            sys.stderr,
            indent=2,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
