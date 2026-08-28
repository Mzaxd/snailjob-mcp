"""任务管理工具：查任务、建任务、改配置、触发、启停、删除。"""

from __future__ import annotations

from ..enums import (
    BLOCK_STRATEGY_BY_NAME,
    ROUTE_KEY,
    ROUTE_KEY_BY_NAME,
    TASK_TYPE_BY_NAME,
    TRIGGER_TYPE_BY_NAME,
    resolve,
)
from ..errors import SnailJobError
from .common import (
    decode_job,
    ensure_writable,
    get_context,
    normalize_page,
    tool_error_safe,
)


def register(mcp) -> None:
    @mcp.tool()
    @tool_error_safe
    def list_jobs(
        env: str = "",
        group_name: str = "",
        keyword: str = "",
        job_status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """查询任务列表（分页）。

        Args:
            env: 环境名（config.yml 的 environments 键），留空用 default_env。
            group_name: 组名，留空自动发现。
            keyword: 任务名称模糊过滤（jobName）。
            job_status: 状态过滤："enabled"=开启 / "paused"=暂停，或数字 0/1。
            page: 页码，从 1 开始。
            page_size: 每页条数。
        """
        ctx = get_context(env)
        status_val = resolve(
            {0: "paused", 1: "enabled"},
            {"paused": 0, "enable": 1, "enabled": 1, "start": 1},
            job_status or None,
            "job_status",
        ) if job_status else None
        group = ctx.resolve_group_name(group_name or None)
        data = ctx.console().page_jobs(
            group_name=group,
            job_name=keyword or None,
            job_status=status_val,
            page=page,
            size=page_size,
        )
        result = normalize_page(data)
        result["rows"] = [decode_job(r) for r in result.get("rows", [])]
        result["group_name"] = group
        return result

    @mcp.tool()
    @tool_error_safe
    def get_job(env: str = "", job_id: int = 0) -> dict:
        """查询单个任务详情（含 cron、参数、路由、阻塞策略等全部配置）。

        删除或修改任务前必须先调用本工具确认任务存在且配置符合预期。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID（sj_job.id）。
        """
        if not job_id:
            return {"error": "job_id 必填"}
        ctx = get_context(env)
        return decode_job(ctx.console().get_job(int(job_id)))

    @mcp.tool()
    @tool_error_safe
    def create_job(
        env: str = "",
        executor_name: str = "",
        job_desc: str = "",
        cron: str = "",
        job_name: str = "",
        job_params: str = "",
        trigger_type: str = "cron",
        route_key: str = "round_robin",
        block_strategy: str = "discard",
        executor_timeout: int = 60,
        max_retry_times: int = 3,
        retry_interval: int = 1,
        task_type: str = "cluster",
        parallel_num: int = 1,
        labels: str = "",
        start_now: bool = False,
    ) -> dict:
        """新增任务（创建后默认暂停，除非 start_now=true）。

        创建前会先校验 executor_name 已在该环境的组下注册，未注册会拒绝并
        提示可用执行器。cron 仅在 trigger_type="cron" 时生效；trigger_type=
        "fixed_interval" 时请把 cron 参数填成秒数字符串（如 "60" 表示每 60 秒）。

        Args:
            env: 环境名，留空用 default_env。
            executor_name: 执行器名称（客户端 @JobExecutor 注解的 name）。
            job_desc: 任务描述（中文说明任务用途）。
            cron: CRON 表达式（trigger_type=fixed_interval 时填秒数）。
            job_name: 任务名称，留空自动取 job_desc 前 40 字符。
            job_params: 任务参数字符串（执行器 argsStr，JSON 或文本）。
            trigger_type: 触发类型：cron（默认）| fixed_interval。
            route_key: 路由策略：consistent_hash|random|lru|round_robin|first|last。
            block_strategy: 阻塞策略：discard(丢弃)|overlay(覆盖)|concurrency(并行)|recovery(恢复)。
            executor_timeout: 单次执行超时时间（秒）。
            max_retry_times: 失败最大重试次数。
            retry_interval: 重试间隔（秒）。
            task_type: 任务类型：cluster(集群)|broadcast(广播)|sharding(分片)。
            parallel_num: 并行数（广播/分片任务生效）。
            labels: 标签 JSON 字符串，如 '{"owner":"ops"}'。
            start_now: true=创建后立即启动；默认 false 创建为暂停状态。
        """
        if not executor_name or not job_desc:
            return {"error": "executor_name 与 job_desc 必填"}
        if not cron:
            return {"error": "cron 必填（CRON 表达式，或 fixed_interval 模式下的秒数字符串）"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "创建任务")

        group = ctx.resolve_group_name(None)
        registered = ctx.console().list_executor_names(group)
        if executor_name not in registered:
            return {
                "error": f"执行器 {executor_name!r} 未在组 {group!r} 下注册",
                "registered_executors": sorted(registered),
                "hint": "请确认客户端已上线且 @JobExecutor(name=...) 拼写一致；也可调用 list_executors 查看",
            }

        trigger_type_num = resolve(
            {2: "fixed_interval", 3: "cron"},
            TRIGGER_TYPE_BY_NAME,
            trigger_type,
            "trigger_type",
        )
        payload = {
            "groupName": group,
            "jobName": job_name or job_desc[:40],
            "jobStatus": 1 if start_now else 0,
            "argsStr": job_params or "",
            "argsType": 1,
            "routeKey": resolve(ROUTE_KEY, ROUTE_KEY_BY_NAME, route_key, "route_key"),
            "executorType": 1,
            "executorInfo": executor_name,
            "triggerType": trigger_type_num,
            "triggerInterval": str(cron),
            "blockStrategy": resolve(
                {1: "discard", 2: "overlay", 3: "concurrency", 4: "recovery"},
                BLOCK_STRATEGY_BY_NAME,
                block_strategy,
                "block_strategy",
            ),
            "executorTimeout": int(executor_timeout),
            "maxRetryTimes": int(max_retry_times),
            "retryInterval": int(retry_interval),
            "taskType": resolve(
                {1: "cluster", 2: "broadcast", 3: "sharding"},
                TASK_TYPE_BY_NAME,
                task_type,
                "task_type",
            ),
            "parallelNum": int(parallel_num),
            "description": job_desc,
        }
        if labels:
            payload["labels"] = labels
        job_id = ctx.console().create_job(payload)
        return {
            "job_id": job_id,
            "job_status": "enabled" if start_now else "paused",
            "group_name": group,
            "hint": "创建后可调用 trigger_job 立即验证一次执行，或 set_job_status 启停",
        }

    @mcp.tool()
    @tool_error_safe
    def update_job(
        env: str = "",
        job_id: int = 0,
        cron: str = "",
        job_params: str = "",
        job_desc: str = "",
        job_name: str = "",
        route_key: str = "",
        block_strategy: str = "",
        executor_timeout: int = 0,
        max_retry_times: int = 0,
        retry_interval: int = 0,
        labels: str = "",
    ) -> dict:
        """修改任务配置（局部更新：先查现状，合并后整体提交）。

        只传需要修改的字段，其余保持原值。修改前建议先 get_job 确认现状；
        修改不会改变任务的启停状态。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID。
            cron: 新的 CRON 表达式（或 fixed_interval 的秒数字符串）。
            job_params: 新的任务参数（整体覆盖 argsStr）。
            job_desc: 新的任务描述。
            job_name: 新的任务名称。
            route_key: 路由策略（同 create_job 的取值）。
            block_strategy: 阻塞策略（同 create_job 的取值）。
            executor_timeout: 超时时间（秒）。
            max_retry_times: 最大重试次数。
            retry_interval: 重试间隔（秒）。
            labels: 标签 JSON 字符串。
        """
        if not job_id:
            return {"error": "job_id 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "修改任务")
        console = ctx.console()

        current = console.get_job(int(job_id))
        if not isinstance(current, dict) or not current.get("id"):
            return {
                "error": f"任务 {job_id} 不存在或查询失败",
                "hint": "请先用 get_job 或 list_jobs 确认任务 ID",
            }

        merged = dict(current)
        if cron:
            merged["triggerInterval"] = str(cron)
        if job_params != "":
            merged["argsStr"] = job_params
        if job_desc:
            merged["description"] = job_desc
        if job_name:
            merged["jobName"] = job_name
        if route_key:
            merged["routeKey"] = resolve(ROUTE_KEY, ROUTE_KEY_BY_NAME, route_key, "route_key")
        if block_strategy:
            merged["blockStrategy"] = resolve(
                {1: "discard", 2: "overlay", 3: "concurrency", 4: "recovery"},
                BLOCK_STRATEGY_BY_NAME,
                block_strategy,
                "block_strategy",
            )
        if executor_timeout:
            merged["executorTimeout"] = int(executor_timeout)
        if max_retry_times:
            merged["maxRetryTimes"] = int(max_retry_times)
        if retry_interval:
            merged["retryInterval"] = int(retry_interval)
        if labels:
            merged["labels"] = labels

        # 服务端整体更新要求非空字段齐全，补齐创建态必填字段的兜底值
        merged.setdefault("jobName", merged.get("description", "")[:40] or f"job-{job_id}")
        merged.setdefault("argsType", 1)
        merged.setdefault("executorType", 1)
        merged.setdefault("executorInfo", merged.get("executorInfo", ""))
        merged.setdefault("parallelNum", 1)
        merged.setdefault("jobStatus", current.get("jobStatus", 0))

        console.update_job(merged)
        return {
            "job_id": int(job_id),
            "updated": True,
            "before": decode_job(current),
            "after": decode_job(console.get_job(int(job_id))),
        }

    @mcp.tool()
    @tool_error_safe
    def trigger_job(env: str = "", job_id: int = 0, job_params: str = "") -> dict:
        """手动触发一次任务执行（不改任务状态）。

        触发后可用 query_batches 传入 job_id 查看新产生的执行批次。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID。
            job_params: 本次触发的临时参数（tmpArgsStr，仅本次生效，不落库）。
        """
        if not job_id:
            return {"error": "job_id 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "手动触发")
        ok = ctx.console().trigger_job(int(job_id), job_params or None)
        return {
            "job_id": int(job_id),
            "triggered": ok,
            "hint": "稍后用 query_batches(job_id=...) 查看执行批次结果",
        }

    @mcp.tool()
    @tool_error_safe
    def set_job_status(env: str = "", job_id: int = 0, status: str = "") -> dict:
        """启动或暂停任务。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID。
            status: "start"=启动 / "pause"=暂停。
        """
        if not job_id:
            return {"error": "job_id 必填"}
        status_num = resolve(
            {0: "paused", 1: "enabled"},
            {"start": 1, "enabled": 1, "pause": 0, "paused": 0, "stop": 0},
            status or None,
            "status",
        )
        if status_num is None:
            return {"error": 'status 必填: "start" 或 "pause"'}
        ctx = get_context(env)
        ensure_writable(ctx.config, "启停任务")
        console = ctx.console()
        before = decode_job(console.get_job(int(job_id)))
        ok = console.update_job_status(int(job_id), status_num)
        return {
            "job_id": int(job_id),
            "before_status": before.get("job_status_name"),
            "new_status": "enabled" if status_num == 1 else "paused",
            "updated": ok,
        }

    @mcp.tool()
    @tool_error_safe
    def delete_job(env: str = "", job_id: int = 0) -> dict:
        """【危险】删除任务。删除前必须先 get_job 确认任务存在且配置正确。

        Args:
            env: 环境名，留空用 default_env。
            job_id: 任务 ID。
        """
        if not job_id:
            return {"error": "job_id 必填"}
        ctx = get_context(env)
        ensure_writable(ctx.config, "删除任务")
        console = ctx.console()
        current = console.get_job(int(job_id))
        if not isinstance(current, dict) or not current.get("id"):
            return {
                "error": f"任务 {job_id} 不存在，未执行删除",
                "hint": "删除前必须先 get_job 确认，防止误删",
            }
        console.delete_jobs([int(job_id)])
        return {
            "job_id": int(job_id),
            "deleted": True,
            "job_name": current.get("jobName"),
            "job_desc": current.get("description"),
        }
