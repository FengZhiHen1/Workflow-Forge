"""wfctl 异常体系。"""


class WfctlError(Exception):
    """wfctl 异常基类。"""

    code: str = "UNKNOWN_ERROR"
    exit_code: int = 1

    def __init__(self, message: str, code: str | None = None, exit_code: int | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code


class StateError(WfctlError):
    """状态文件异常——instance.json 损坏、字段缺失、状态不一致。"""

    def __init__(self, message: str, code: str = "STATE_CORRUPTED"):
        super().__init__(message, code=code, exit_code=1)


class WorktreeError(WfctlError):
    """Worktree 操作异常——创建失败、合并冲突、残留清理失败。"""

    def __init__(self, message: str, code: str = "WORKTREE_CREATE_FAILED"):
        super().__init__(message, code=code, exit_code=1)


class SchemaError(WfctlError):
    """WORKFLOW.yaml 解析异常——格式错误、必填字段缺失、版本不支持。"""

    def __init__(self, message: str, code: str = "SCHEMA_PARSE_ERROR"):
        super().__init__(message, code=code, exit_code=1)


class ValidationError(WfctlError):
    """校验异常——保护区触碰、权限越界、消息字段非法。"""

    def __init__(self, message: str, code: str = "ACCESS_VIOLATION"):
        super().__init__(message, code=code, exit_code=1)


class GitError(WfctlError):
    """git 操作异常——命令失败、仓库损坏。"""

    def __init__(self, message: str, code: str = "GIT_COMMAND_FAILED"):
        super().__init__(message, code=code, exit_code=1)


class InputError(WfctlError):
    """用户输入异常——参数非法、引用不存在。"""

    def __init__(self, message: str, code: str = "INVALID_ARGUMENT"):
        super().__init__(message, code=code, exit_code=2)
