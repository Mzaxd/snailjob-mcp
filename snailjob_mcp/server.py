"""FastMCP Server 组装：stdio transport，工具集见 snailjob_mcp.tools。"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import __version__
from .tools import register_all

logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    mcp = FastMCP(
        "snailjob-mcp",
        instructions=(
            "管理自建 SnailJob 1.8.x 分布式任务调度平台。每个工具的第一个参数 env "
            "选择目标环境（config.yml 的 environments 键），留空用 default_env。"
            "read_only 环境会拒绝全部写操作。危险操作（delete_job / "
            "dead_letter_rollback / dead_letter_delete / stop_batch）执行前必须"
            "先用对应的查询工具确认目标存在且符合预期。"
        ),
    )
    register_all(mcp)
    logger.info("snailjob-mcp v%s ready (stdio)", __version__)
    return mcp


def run() -> None:
    # stdio MCP 约定：stdout 只能走 MCP 协议，日志一律输出 stderr
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    create_server().run(transport="stdio")


if __name__ == "__main__":
    run()
