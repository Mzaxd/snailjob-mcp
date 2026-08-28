"""运维工具：停止批次、死信查询与回滚。"""

from __future__ import annotations

from .common import decode_batch, ensure_writable, get_context, tool_error_safe

def register(mcp) -> None:
    @mcp.tool()
    @tool_error_safe
    def stop_batch(env: str = "", task_batch_id: int = 0) -> dict:
        """停止一个执行中的任务批次（对卡死/误触发的批次止损用）。

        只对 running 状态的批次有意义；已完成的批次会返回失败。

        Args:
            env: 环境名，留空用 default_env。
            task_batch_id: 批次 ID（query_batches 返回的 id）。
        """
        if not task_batch_id:
            return {"error": "task_batch_id 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "停止批次")
        ok = ctx.console().stop_batch(int(task_batch_id))
        return {
            "task_batch_id": int(task_batch_id),
            "stopped": ok,
            "hint": "可用 query_batches(status=...) 复核批次状态",
        }

    @mcp.tool()
    @tool_error_safe
    def retry_batch(env: str = "", task_batch_id: int = 0) -> dict:
        """重试一个批次（重新执行其中失败的子任务）。

        Args:
            env: 环境名，留空用 default_env。
            task_batch_id: 批次 ID。
        """
        if not task_batch_id:
            return {"error": "task_batch_id 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "重试批次")
        ok = ctx.console().retry_batch(int(task_batch_id))
        return {"task_batch_id": int(task_batch_id), "retry_submitted": ok}

    @mcp.tool()
    @tool_error_safe
    def dead_letter_query(
        env: str = "",
        group_name: str = "",
        scene_name: str = "",
        biz_no: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """查询重试死信列表（重试次数耗尽仍失败的任务快照）。

        死信不会自动重试，需要人工评估后用 dead_letter_rollback 回滚重试，
        或确认无用后删除。

        Args:
            env: 环境名，留空用 default_env。
            group_name: 组名，留空自动发现。
            scene_name: 场景名（重试场景）过滤。
            biz_no: 业务编号过滤。
            page: 页码。
            page_size: 每页条数。
        """
        ctx = get_context(env)
        group = ctx.resolve_group_name(group_name or None)
        data = ctx.console().page_dead_letters(
            group_name=group,
            scene_name=scene_name or None,
            biz_no=biz_no or None,
            page=page,
            size=page_size,
        )
        result = normalize_page(data)
        result["group_name"] = group
        return result

    @mcp.tool()
    @tool_error_safe
    def dead_letter_rollback(env: str = "", ids: list[int] | None = None) -> dict:
        """【危险】把死信批量回滚为待重试数据（会重新投递执行）。

        回滚前必须先 dead_letter_query 查清楚 ids 对应的死信内容，确认业务
        上允许重放。回滚后数据立即进入重试队列。

        Args:
            env: 环境名，留空用 default_env。
            ids: 死信 ID 列表（dead_letter_query 返回的 id 字段）。
        """
        if not ids:
            return {"error": "ids 必填（dead_letter_query 返回的 id 列表）"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "死信回滚")
        console = ctx.console()
        count = console.rollback_dead_letters([int(i) for i in ids])
        return {
            "requested": len(ids),
            "rolled_back": count,
            "hint": "回滚后数据进入重试队列，可用重试任务查询确认执行情况",
        }

    @mcp.tool()
    @tool_error_safe
    def dead_letter_delete(env: str = "", ids: list[int] | None = None) -> dict:
        """【危险】批量删除死信（删除后不可恢复）。

        仅适用于确认业务上不再需要的死信；如需重放请用 dead_letter_rollback。

        Args:
            env: 环境名，留空用 default_env。
            ids: 死信 ID 列表。
        """
        if not ids:
            return {"error": "ids 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "删除死信")
        ok = ctx.console().delete_dead_letters([int(i) for i in ids])
        return {"ids": ids, "deleted": ok}

    @mcp.tool()
    @tool_error_safe
    def get_batch_detail(env: str = "", task_batch_id: int = 0) -> dict:
        """查询单个执行批次详情（OpenAPI 通道，含调度参数与执行上下文）。

        Args:
            env: 环境名，留空用 default_env。
            task_batch_id: 批次 ID。
        """
        if not task_batch_id:
            return {"error": "task_batch_id 必填"}
        ctx = get_context(env)
        return decode_batch(ctx.openapi().get_job_batch(int(task_batch_id)))
