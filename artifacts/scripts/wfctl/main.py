"""CLI 入口：argparse 注册 + 异常捕获。"""

import argparse
import json
import sys
import traceback

from core.errors import WfctlError
from core.logging import log_error

from cli.create import register_create
from cli.confirm import register_confirm
from cli.deviate import register_deviate
from cli.identity import register_identity
from cli.message_write import register_message_write
from cli.next_cmd import register_next
from cli.pause import register_pause
from cli.resolve import register_resolve
from cli.resume import register_resume
from cli.rollback import register_rollback
from cli.skip import register_skip
from cli.status import register_status
from cli.sync import register_sync
from cli.cleanup import register_cleanup
from cli.restore import register_restore
from cli.terminate import register_terminate


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
