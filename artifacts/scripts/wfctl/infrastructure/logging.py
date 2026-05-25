"""stderr 结构化日志。"""

import json
import sys
import time


def log(level: str, message: str, **kwargs):
    """写一条结构化日志到 stderr。"""
    entry = {"ts": time.time(), "level": level, "msg": message, **kwargs}
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr, flush=True)


def log_error(error: Exception, **kwargs):
    """记录错误日志。"""
    from infrastructure.errors import WfctlError

    if isinstance(error, WfctlError):
        log("error", str(error), code=error.code, **kwargs)
    else:
        log("error", str(error), exception_type=type(error).__name__, **kwargs)
