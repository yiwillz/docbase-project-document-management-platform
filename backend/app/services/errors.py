"""业务错误：services 层只抛这些，不依赖 HTTP；由 main.py 统一转成状态码。

code 是稳定的错误代码（如 project.code_taken），前端按它做 i18n 翻译；
message 是给开发者看的说明。
"""

from typing import Any


class ServiceError(Exception):
    # code、message 只能按位置传，params 里才能再用 code 之类的名字
    def __init__(self, code: str, message: str, /, **params: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params


class NotFound(ServiceError):
    """对象不存在（或已停用）。"""


class Conflict(ServiceError):
    """与现有数据冲突，如编号重复、项目已归档。"""


class Invalid(ServiceError):
    """参数不合法。"""


class Unauthorized(ServiceError):
    """没登录或登录过期（401）。"""


class Forbidden(ServiceError):
    """登录了，但没有这个权限（403）。"""
