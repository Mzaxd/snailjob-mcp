"""MCP 工具层公共辅助：环境解析、只读校验、错误包装、出参可读化。"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from ..config import EnvironmentConfig
from ..enums import (
    ARGS_TYPE,
    BLOCK_STRATEGY,
    EXECUTOR_TYPE,
    JOB_STATUS,
    ROUTE_KEY,
    TASK_BATCH_STATUS,
    TASK_STATUS,
    TASK_TYPE,
    TRIGGER_TYPE,
    humanize,
)
from ..errors import ReadOnlyError, SnailJobError
from ..registry import EnvContext, registry

logger = logging.getLogger(__name__)


def get_context(env: str | None) -> EnvContext:
    """按 env 参数取运行时上下文；空值回退 default_env。"""
    return registry.context(env or None)


def ensure_writable(env_cfg: EnvironmentConfig, action: str) -> None:
    """read_only 环境直接拒绝写操作。"""
    if env_cfg.read_only:
        raise ReadOnlyError(
            f"环境 {env_cfg.name!r} 配置为 read_only=true，已拒绝{action}操作",
            hint="该环境保护策略不可通过工具绕过；确需变更请修改 config.yml 后重启 MCP Server",
        )


def tool_error_safe(func: Callable) -> Callable:
    """把 SnailJobError/ValueError 转成结构化返回（不抛出），Agent 可读 hint 自助排障。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SnailJobError as exc:
            logger.warning("tool %s 失败: %s", func.__name__, exc)
            return exc.to_dict()
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 兜底，MCP 工具不应崩溃
            logger.exception("tool %s 未预期异常", func.__name__)
            return {
                "error": f"未预期异常: {type(exc).__name__}: {exc}",
                "hint": "请查看 MCP Server 的 stderr 日志定位问题",
            }

    return wrapper


def decode_job(row: dict) -> dict:
    """给任务行追加可读字段（*_name），原始字段保留不动。"""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    out["job_status_name"] = humanize(JOB_STATUS, row.get("jobStatus"))
    out["trigger_type_name"] = humanize(TRIGGER_TYPE, row.get("triggerType"))
    out["route_key_name"] = humanize(ROUTE_KEY, row.get("routeKey"))
    out["block_strategy_name"] = humanize(BLOCK_STRATEGY, row.get("blockStrategy"))
    out["executor_type_name"] = humanize(EXECUTOR_TYPE, row.get("executorType"))
    out["task_type_name"] = humanize(TASK_TYPE, row.get("taskType"))
    out["args_type_name"] = humanize(ARGS_TYPE, row.get("argsType"))
    return out


def decode_batch(row: dict) -> dict:
    """给批次行追加可读字段。"""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    out["task_batch_status_name"] = humanize(TASK_BATCH_STATUS, row.get("taskBatchStatus"))
    return out


def decode_task(row: dict) -> dict:
    """给批次执行树节点追加可读字段。"""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    out["task_status_name"] = humanize(TASK_STATUS, row.get("taskStatus"))
    return out


def normalize_page(data: Any) -> dict:
    """把 PageResult 统一成 {page,size,total,rows}；非分页数据原样包一层。"""
    if isinstance(data, dict) and "data" in data and ("total" in data or "size" in data):
        return {
            "page": data.get("page"),
            "size": data.get("size"),
            "total": data.get("total"),
            "rows": data.get("data") or [],
        }
    if isinstance(data, list):
        return {"rows": data}
    return {"data": data}
