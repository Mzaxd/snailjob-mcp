"""错误类型定义。所有错误带上下文与下一步建议，方便 Agent 自助排障。"""

from __future__ import annotations


class SnailJobError(Exception):
    """SnailJob MCP 基础错误。

    Attributes:
        message: 人类可读的错误描述（含 HTTP 状态码 / 响应 msg）。
        hint: 下一步建议（如 token 失效提示检查账号密码）。
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        super().__init__(message)

    def to_dict(self) -> dict:
        out = {"error": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}（建议：{self.hint}）"
        return self.message


class ConfigError(SnailJobError):
    """配置文件缺失、格式错误或字段非法。"""


class ReadOnlyError(SnailJobError):
    """对 read_only 环境执行写操作。"""


class AuthError(SnailJobError):
    """登录失败或 token 无法恢复。"""


class ApiError(SnailJobError):
    """SnailJob 服务端返回业务失败。

    Attributes:
        status: 服务端响应包装结构里的 status 码（如有）。
        http_status: HTTP 状态码（如有）。
    """

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        *,
        status: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message, hint)
        self.status = status
        self.http_status = http_status
