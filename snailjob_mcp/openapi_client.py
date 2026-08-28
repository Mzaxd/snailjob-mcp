"""SnailJob OpenAPI 接口 client（官方任务管理契约）。

契约从 1.8.1 jar 反编译确认：

- 每个请求三个 header（HeadersEnum 实际值）：``namespace``、``group-name``、``token``
- 端点：POST /api/job/add、PUT /api/job/update、DELETE /api/job/delete、
  POST /api/job/trigger、PUT /api/job/update/status、
  GET /api/job/detail/id?id={id}（注意 id 是查询参数，不在路径里）、
  GET /api/job-batch/detail/{id}
- 请求体复用 common-model 的 JobRequest / JobTriggerRequest / StatusUpdateRequest 字段。
- 组 token 不需要人工配置：从控制台组配置列表取明文。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .errors import ApiError

logger = logging.getLogger(__name__)

HEADER_NAMESPACE = "namespace"
HEADER_GROUP_NAME = "group-name"
HEADER_TOKEN = "token"


class OpenApiClient:
    """单个环境 + 组 的 OpenAPI client。

    namespace / group-name / token 三个值由 :mod:`snailjob_mcp.registry`
    通过控制台自动发现后注入，用户无需在 config.yml 里配置 token。
    """

    def __init__(
        self,
        env_config,
        *,
        namespace_id: str,
        group_name: str,
        token: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cfg = env_config
        self._client = httpx.Client(
            base_url=env_config.base_url,
            timeout=env_config.timeout,
            verify=getattr(env_config, "verify_ssl", True),
            headers={
                "Content-Type": "application/json",
                HEADER_NAMESPACE: namespace_id,
                HEADER_GROUP_NAME: group_name,
                HEADER_TOKEN: token,
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        resp = self._client.request(method, path, params=params, json=json)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ApiError(
                f"OpenAPI 接口响应非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}",
                hint="确认 base_url 的 context-path 是否为 /snail-job",
                http_status=resp.status_code,
            ) from exc
        status = body.get("status", body.get("code")) if isinstance(body, dict) else None
        message = str(body.get("message", body.get("msg", ""))) if isinstance(body, dict) else str(body)
        # common-core Result 与控制台一致：1=成功，0=失败（线上实测）
        if resp.status_code >= 400 or (status is not None and status != 1):
            hint = None
            if "token" in message.lower() or resp.status_code == 401:
                hint = "组 token 校验失败，运行 doctor 自检核对自动获取的 token 是否与控制台页面一致"
            raise ApiError(
                f"OpenAPI 接口返回失败（HTTP {resp.status_code}）: [{status}] {message}",
                hint=hint,
                status=status if isinstance(status, int) else None,
                http_status=resp.status_code,
            )
        return body.get("data") if isinstance(body, dict) else body

    # ================================================================ 端点封装
    def add_job(self, payload: dict) -> int:
        """POST /api/job/add → 新增任务，返回 jobId。"""
        data = self.request("POST", "/api/job/add", json=payload)
        return int(data)

    def update_job(self, payload: dict) -> bool:
        """PUT /api/job/update → 修改任务（需带 id）。"""
        return bool(self.request("PUT", "/api/job/update", json=payload))

    def delete_jobs(self, job_ids: list[int]) -> bool:
        """DELETE /api/job/delete → 批量删除（body 传 ids 集合）。"""
        return bool(self.request("DELETE", "/api/job/delete", json=job_ids))

    def trigger_job(self, job_id: int, tmp_args: str | None = None) -> bool:
        """POST /api/job/trigger → 手动触发。"""
        return bool(self.request("POST", "/api/job/trigger", json={"jobId": job_id, "tmpArgsStr": tmp_args}))

    def update_job_status(self, job_id: int, status: int) -> bool:
        """PUT /api/job/update/status → 启动(1)/暂停(0)。"""
        return bool(self.request("PUT", "/api/job/update/status", json={"id": job_id, "status": status}))

    def get_job(self, job_id: int) -> dict:
        """GET /api/job/detail/id?id={id} → 任务详情。"""
        return self.request("GET", "/api/job/detail/id", params={"id": job_id})

    def get_job_batch(self, task_batch_id: int) -> dict:
        """GET /api/job-batch/detail/{id} → 批次详情。"""
        return self.request("GET", f"/api/job-batch/detail/{task_batch_id}")
