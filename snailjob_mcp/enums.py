"""SnailJob 1.8.1 枚举值字典。

字段名与取值均已通过 javap 反编译 1.8.1 jar 确认：
- snail-job-common-core: JobBlockStrategyEnum / JobTaskTypeEnum / JobArgsTypeEnum /
  ExecutorTypeEnum / StatusEnum / JobTaskBatchStatusEnum / JobTaskStatusEnum
- snail-job-client-job-core: TriggerTypeEnum / AllocationAlgorithmEnum

这些映射仅用于工具入参归一化与出参可读化；真正提交给服务端的始终是数字值，
未知值原样透传，避免升级 SnailJob 后映射过期导致误判。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 任务状态
# StatusEnum: NO=0(暂停), YES=1(开启)。PUT /job/status 与创建任务的 jobStatus 同源。
JOB_STATUS = {
    0: "paused",
    1: "enabled",
}
JOB_STATUS_BY_NAME = {v: k for k, v in JOB_STATUS.items()}

# -------------------------------------------------------------- 触发类型
# TriggerTypeEnum: SCHEDULED_TIME=2, CRON=3, POINT_IN_TIME=5, WORK_FLOW=99
TRIGGER_TYPE = {
    2: "fixed_interval",
    3: "cron",
    5: "point_in_time",
    99: "workflow",
}
TRIGGER_TYPE_BY_NAME = {v: k for k, v in TRIGGER_TYPE.items()}

# -------------------------------------------------------------- 路由策略
# AllocationAlgorithmEnum: CONSISTENT_HASH=1, RANDOM=2, LRU=3, ROUND=4, FIRST=5, LAST=6
ROUTE_KEY = {
    1: "consistent_hash",
    2: "random",
    3: "lru",
    4: "round_robin",
    5: "first",
    6: "last",
}
ROUTE_KEY_BY_NAME = {v: k for k, v in ROUTE_KEY.items()}

# -------------------------------------------------------------- 阻塞策略
# JobBlockStrategyEnum: DISCARD=1, OVERLAY=2, CONCURRENCY=3, RECOVERY=4
BLOCK_STRATEGY = {
    1: "discard",
    2: "overlay",
    3: "concurrency",
    4: "recovery",
}
BLOCK_STRATEGY_BY_NAME = {v: k for k, v in BLOCK_STRATEGY.items()}

# -------------------------------------------------------------- 执行器类型
# ExecutorTypeEnum: JAVA=1, PYTHON=2, GO=3
EXECUTOR_TYPE = {
    1: "java",
    2: "python",
    3: "go",
}
EXECUTOR_TYPE_BY_NAME = {v: k for k, v in EXECUTOR_TYPE.items()}

# -------------------------------------------------------------- 任务类型
# JobTaskTypeEnum: UNKNOWN=0, CLUSTER=1, BROADCAST=2, SHARDING=3, MAP=4, MAP_REDUCE=5
TASK_TYPE = {
    0: "unknown",
    1: "cluster",
    2: "broadcast",
    3: "sharding",
    4: "map",
    5: "map_reduce",
}
TASK_TYPE_BY_NAME = {v: k for k, v in TASK_TYPE.items()}

# -------------------------------------------------------------- 参数类型
# JobArgsTypeEnum: TEXT=1, JSON=2
ARGS_TYPE = {
    1: "text",
    2: "json",
}
ARGS_TYPE_BY_NAME = {v: k for k, v in ARGS_TYPE.items()}

# -------------------------------------------------------------- 批次状态
# JobTaskBatchStatusEnum: WAITING=1, RUNNING=2, SUCCESS=3, FAIL=4, STOP=5, CANCEL=6
TASK_BATCH_STATUS = {
    1: "waiting",
    2: "running",
    3: "success",
    4: "fail",
    5: "stop",
    6: "cancel",
}
TASK_BATCH_STATUS_BY_NAME = {v: k for k, v in TASK_BATCH_STATUS.items()}

# -------------------------------------------------------------- 子任务状态
# JobTaskStatusEnum: RUNNING=1, SUCCESS=2, FAIL=3, STOP=4, CANCEL=5
TASK_STATUS = {
    1: "running",
    2: "success",
    3: "fail",
    4: "stop",
    5: "cancel",
}
TASK_STATUS_BY_NAME = {v: k for k, v in TASK_STATUS.items()}


def humanize(mapping: dict[int, str], value) -> str:
    """把数字枚举值翻译成可读名；未知值返回 ``unknown(<v>)``。"""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return mapping.get(value, f"unknown({value})")
    return str(value)


def resolve(mapping: dict[int, str], by_name: dict[str, int], value, field: str):
    """把入参（数字或可读名）归一化为服务端需要的数字枚举值。

    未知可读名直接报错；数字值原样透传（允许服务端新增枚举值）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 应为枚举名或整数，收到布尔值: {value!r}")
    if isinstance(value, int):
        return value
    name = str(value).strip().lower()
    if name in by_name:
        return by_name[name]
    raise ValueError(
        f"{field} 的非法取值 {value!r}，"
        f"可用值: {', '.join(f'{v}={k}' for k, v in mapping.items())}"
    )
