"""MCP 工具集注册入口。"""

from __future__ import annotations

from . import jobs, observability, ops

ALL_MODULES = (jobs, observability, ops)


def register_all(mcp) -> None:
    """把全部工具注册到 FastMCP 实例上。"""
    for module in ALL_MODULES:
        module.register(mcp)
