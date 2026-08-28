"""运行观测工具：在线机器、执行器、批次、执行树、大盘。"""

from __future__ import annotations

from ..enums import TASK_BATCH_STATUS, TASK_BATCH_STATUS_BY_NAME, TASK_STATUS_BY_NAME, resolve
from .common import (
    decode_batch,
    decode_task,
    get_context,
    normalize_page,
    tool_error_safe,
)


def register(mcp) -> None:
    @mcp.tool()
    @tool_error_safe
    def list_pods(env: str = "", group_name: str = "", page: int = 1, page_size: int = 100) -> dict:
        """查看在线机器列表（服务端 + 客户端，含 IP、端口、类型、版本、状态）。

        Args:
            env: 环境名，留空用 default_env。
            group_name: 按组过滤在线客户端；留空返回全部 Pod 并附上该环境
                命名空间下的组及各组在线客户端汇总。
            page: 页码。
            page_size: 每页条数。
        """
        ctx = get_context(env)
        console = ctx.console()
        data = console.list_pods(page=page, size=page_size, group_name=group_name or None)
        result = normalize_page(data)

        summary: list[dict] = []
        resolved_group: str | None = None
        try:
            resolved_group = ctx.resolve_group_name(group_name or None)
        except Exception:  # noqa: BLE001 组不可解析时仅影响汇总，不影响 Pod 列表
            pass
        for name in ctx.group_names():
            try:
                online = console.list_online_pods_by_group(name)
                summary.append({"group_name": name, "online_clients": online})
            except Exception as exc:  # noqa: BLE001
                summary.append({"group_name": name, "error": str(exc)})
        result["group_online_summary"] = summary
        if resolved_group:
            result["group_name"] = resolved_group
        return result

    @mcp.tool()
    @tool_error_safe
    def list_executors(env: str = "", keyword: str = "", group_name: str = "") -> dict:
        """查看已注册的执行器列表（客户端 @JobExecutor 注册的任务执行器）。

        创建任务前先用本工具确认 executor_name 已注册。

        Args:
            env: 环境名，留空用 default_env。
            keyword: 执行器名称模糊过滤。
            group_name: 组名，留空自动发现。
        """
        ctx = get_context(env)
        group = ctx.resolve_group_name(group_name or None)
        data = ctx.console().page_executors(
            group_name=group,
            executor_info=keyword or None,
            page=1,
            size=200,
        )
        result = normalize_page(data)
        result["group_name"] = group
        return result

    @mcp.tool()
    @tool_error_safe
    def query_batches(
        env: str = "",
        job_id: int = 0,
        group_name: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """查询任务执行批次列表（分页，按触发时间倒序）。

        手动触发任务后，用本工具确认新批次产生与执行结果。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID，留空查该组全部批次。
            group_name: 组名，留空自动发现。
            status: 批次状态过滤：waiting|running|success|fail|stop|cancel。
            page: 页码。
            page_size: 每页条数。
        """
        ctx = get_context(env)
        group = ctx.resolve_group_name(group_name or None)
        status_val = resolve(
            TASK_BATCH_STATUS,
            TASK_BATCH_STATUS_BY_NAME,
            status or None,
            "status",
        )
        data = ctx.console().page_batches(
            job_id=int(job_id) if job_id else None,
            group_name=group,
            task_batch_status=[status_val] if status_val is not None else None,
            page=page,
            size=page_size,
        )
        result = normalize_page(data)
        result["rows"] = [decode_batch(r) for r in result.get("rows", [])]
        result["group_name"] = group
        return result

    @mcp.tool()
    @tool_error_safe
    def get_batch_tree(env: str = "", task_batch_id: int = 0, task_status: str = "") -> dict:
        """查询执行批次的执行树（分片/Map 子任务的明细与结果）。

        用于排查某个批次失败的具体子任务、执行客户端与结果消息。

        Args:
            env: 环境名，留空用 default_env。
            task_batch_id: 批次 ID（query_batches 返回的 id）。
            task_status: 子任务状态过滤：running|success|fail|stop|cancel。
        """
        if not task_batch_id:
            return {"error": "task_batch_id 必填"}
        ctx = get_context(env)
        status_val = resolve(
            {1: "running", 2: "success", 3: "fail", 4: "stop", 5: "cancel"},
            TASK_STATUS_BY_NAME,
            task_status or None,
            "task_status",
        )
        rows = ctx.console().batch_task_tree(
            task_batch_id=int(task_batch_id),
            task_status=status_val,
        )
        tasks = [decode_task(r) for r in rows]
        fail_count = sum(1 for t in tasks if t.get("taskStatus") == 3)
        return {
            "task_batch_id": int(task_batch_id),
            "task_count": len(tasks),
            "fail_count": fail_count,
            "tasks": tasks,
            "hint": "tasks 为扁平列表，parentId 指向父任务 id（分片/Map 任务存在父子层级）",
        }

    @mcp.tool()
    @tool_error_safe
    def dashboard(env: str = "") -> dict:
        """查看环境大盘统计（任务/工作流/重试任务的成败汇总、在线服务数）。

        Args:
            env: 环境名，留空用 default_env。
        """
        ctx = get_context(env)
        return ctx.console().dashboard_card()
