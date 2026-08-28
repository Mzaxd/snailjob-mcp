"""SnailJob 控制台接口 client（覆盖控制台页面全部能力）。

契约均从 1.8.1 jar 反编译确认：

- 登录 ``POST /auth/login``，body ``{"username","password"}``，
  其中 **password 为 MD5(明文) 的十六进制小写**（前端登录提交前用 js-md5
  的 ``hashAsciiStr`` 摘要，服务端再 ``sha256(收到的值)`` 与库中哈希比对，
  即 ``stored = sha256(md5(明文))``）。
  响应 data 里的 ``token`` 字段即会话 token（SystemUserResponseVO.token）。
- 之后所有请求带 ``SNAIL-JOB-AUTH`` 与 ``SNAIL-JOB-NAMESPACE-ID`` 两个 header
  （AuthenticationInterceptor 中的常量字符串）。
- 响应统一为 ``{status, message, data}`` 包装（common-core Result），
  **成功码 1、失败码 0**（线上 1.8.1 实测）；PageResult 额外携带 page/size/total。
- token 过期时服务端返回 "Login expired, please log in again"（HTTP 层可能是
  401/500），此时自动重登一次并重试，只重试一次防止死循环。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any

import httpx

from .errors import ApiError, AuthError

logger = logging.getLogger(__name__)

AUTH_HEADER = "SNAIL-JOB-AUTH"
NAMESPACE_HEADER = "SNAIL-JOB-NAMESPACE-ID"
# common-core Result：1=成功，0=失败（线上 1.8.1 实测，不是 200）
SUCCESS_STATUS = 1

# 服务端 token 失效时的 message（英文 1.8.1 原文 + 常见中文翻译兜底）
_AUTH_FAIL_RE = re.compile(r"login expired|please log in|登录(已)?过期|认证失败|未登录|无(效)?token", re.IGNORECASE)


class ConsoleClient:
    """单个环境的控制台 HTTP client，线程安全（登录加锁）。"""

    def __init__(self, env_config, *, transport: httpx.BaseTransport | None = None) -> None:
        self._cfg = env_config
        self._namespace_header = ""
        self._client = httpx.Client(
            base_url=env_config.base_url,
            timeout=env_config.timeout,
            verify=getattr(env_config, "verify_ssl", True),
            headers={"Content-Type": "application/json"},
            transport=transport,
        )
        self._token: str | None = None
        self._login_data: dict | None = None
        self._login_lock = threading.Lock()
        # 由 EnvContext 注入：首次业务请求前 header 为空时惰性解析命名空间
        self._namespace_resolver: Callable[[], str] | None = None

    # ------------------------------------------------------------- 生命周期
    def close(self) -> None:
        self._client.close()

    def set_namespace_header(self, value: str) -> None:
        """登录后由 registry 解析出命名空间 id 时注入。"""
        self._namespace_header = value or ""

    # ------------------------------------------------------------------ 登录
    def login(self) -> dict:
        """登录，缓存 token 并返回登录响应 data（含 namespaceIds 等信息）。

        密码摘要与前端一致：MD5(明文) 十六进制小写，服务端 sha256 后与库中哈希比对。
        """
        password_digest = hashlib.md5(self._cfg.password.encode("utf-8")).hexdigest()
        try:
            resp = self._client.post(
                "/auth/login",
                json={"username": self._cfg.username, "password": password_digest},
            )
        except httpx.HTTPError as exc:
            raise AuthError(
                f"登录请求失败: {exc}",
                hint=f"请检查 base_url（当前 {self._cfg.base_url}）是否可达、context-path 是否正确",
            ) from exc
        body = self._parse_body(resp)
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        token = data.get("token")
        if not token:
            raise AuthError(
                f"登录响应中未找到 token: {self._safe_message(body)}",
                hint="请确认账号密码正确；若服务端版本变更了响应结构，需更新本 client",
            )
        self._token = token
        self._login_data = data
        logger.info("snailjob console login ok (env=%s)", self._cfg.name)
        return data

    def invalidate(self) -> None:
        self._token = None

    # ---------------------------------------------------------------- 请求层
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        _retried: bool = False,
    ) -> Any:
        """发起控制台请求并解包 Result；token 失效自动重登重试一次。"""
        if self._token is None:
            with self._login_lock:
                if self._token is None:
                    self.login()
        if (
            path != "/auth/login"
            and not self._namespace_header
            and self._namespace_resolver is not None
        ):
            # SNAIL-JOB-NAMESPACE-ID 为空时所有受保护接口都会 5001，
            # 这里保证任何调用顺序下 header 都先于请求就绪
            self._namespace_resolver()
        resp = self._client.request(
            method,
            path,
            params=params,
            json=json,
            headers={AUTH_HEADER: self._token or "", NAMESPACE_HEADER: self._namespace_header},
        )
        body = self._parse_body(resp)
        if not _retried and self._looks_like_auth_failure(resp, body):
            logger.warning("console token 失效（env=%s），重新登录后重试 %s", self._cfg.name, path)
            with self._login_lock:
                self.invalidate()
                self.login()
            return self.request(method, path, params=params, json=json, _retried=True)
        return self._unwrap(body)

    @property
    def login_data(self) -> dict | None:
        """最近一次登录的响应 data（含 namespaceIds 完整命名空间对象）。"""
        return self._login_data

    def get(self, path: str, *, params: dict | None = None) -> Any:
        return self.request("GET", path, params=self._clean(params))

    def post(self, path: str, json: Any = None) -> Any:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: Any = None) -> Any:
        return self.request("PUT", path, json=json)

    def delete(self, path: str, json: Any = None) -> Any:
        return self.request("DELETE", path, json=json)

    # ---------------------------------------------------------------- 解析层
    @staticmethod
    def _clean(params: dict | None) -> dict | None:
        """去掉 None 值参数，避免服务端把空串当过滤条件。"""
        if not params:
            return None
        return {k: v for k, v in params.items() if v is not None} or None

    @staticmethod
    def _as_page(data: Any, page: int, size: int) -> dict:
        """分页形状归一化：线上 1.8.1 的 *page/list 端点返回裸数组而非 PageResult。

        统一包装为 ``{"data": [...], "total": n, "page": p, "size": s}``；
        若服务端本身返回 dict（其他版本/部署）则原样透传。
        """
        if isinstance(data, list):
            return {"data": data, "total": len(data), "page": page, "size": size}
        if isinstance(data, dict):
            return data
        return {"data": [], "total": 0, "page": page, "size": size}

    @staticmethod
    def _is_auth_failure(resp: httpx.Response) -> bool:
        if resp.status_code in (401,):
            return True
        if resp.status_code == 200:
            return False
        text = resp.text or ""
        return bool(_AUTH_FAIL_RE.search(text))

    @classmethod
    def _looks_like_auth_failure(cls, resp: httpx.Response, body: dict) -> bool:
        """HTTP 层或业务层的 token 失效都算（部分版本业务失败时 HTTP 仍是 200）。"""
        if cls._is_auth_failure(resp):
            return True
        status = body.get("status", body.get("code"))
        if status in (401, 403):
            return True
        message = str(body.get("message", body.get("msg", "")))
        return bool(status != SUCCESS_STATUS and _AUTH_FAIL_RE.search(message))

    @staticmethod
    def _safe_message(body: dict | None) -> str:
        if not isinstance(body, dict):
            return str(body)[:200]
        return f"[status={body.get('status')}] {body.get('message', '')}"[:300]

    def _parse_body(self, resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise ApiError(
                    f"HTTP {resp.status_code}，响应非 JSON: {resp.text[:200]}",
                    hint="确认 base_url 的 context-path 是否为 /snail-job",
                    http_status=resp.status_code,
                )
            raise ApiError(
                f"HTTP {resp.status_code}，响应不是合法 JSON",
                hint="服务端版本可能不兼容，请运行 doctor 自检",
                http_status=resp.status_code,
            )
        if not isinstance(body, dict):
            return {"status": resp.status_code, "message": "", "data": body}
        return body

    def _unwrap(self, body: dict) -> Any:
        status = body.get("status", body.get("code"))
        message = str(body.get("message", body.get("msg", "")))
        if status == SUCCESS_STATUS:
            return body.get("data")
        hint = None
        if message and ("namespace" in message.lower() or "命名空间" in message):
            hint = "命名空间不存在或未授权，请核对配置里的 namespace 名称是否与控制台一致"
        if message and ("group" in message.lower() or "组" in message):
            hint = "组不存在或未同步，请核对 group_name 或让其自动发现"
        raise ApiError(
            f"控制台接口返回失败: [{status}] {message}",
            hint=hint,
            status=status if isinstance(status, int) else None,
        )

    # ================================================================ 端点封装
    # 以下端点路径均以 1.8.1 jar 反编译结果为准。

    def list_namespaces(self) -> list[dict]:
        """GET /namespace/all → 命名空间列表 [{id, name, uniqueId}]。"""
        return self.get("/namespace/all") or []

    def list_group_configs(self, *, namespace_id: str) -> list[dict]:
        """GET /group/list → 组配置列表（含 token 明文）。

        注意：``/group/all/group-config/list`` 在线上 1.8.1 部署中 GET 返回 405，
        实测 ``/group/list`` 可用且返回字段齐全（groupName/namespaceId/token）。
        """
        return self.get("/group/list", params={"page": 1, "size": 100}) or []

    def list_online_pods_by_group(self, group_name: str) -> list:
        """GET /group/on-line/pods/{groupName} → 该组在线客户端列表。"""
        return self.get(f"/group/on-line/pods/{group_name}") or []

    def list_pods(self, *, group_name: str | None = None, page: int = 1, size: int = 100) -> dict:
        """GET /dashboard/pods → 在线机器分页（服务端+客户端）。"""
        return self._as_page(
            self.get("/dashboard/pods", params={"groupName": group_name, "page": page, "size": size}),
            page, size,
        )

    def dashboard_card(self) -> dict:
        """GET /dashboard/task-retry-job → 首页大盘统计卡片。"""
        return self.get("/dashboard/task-retry-job") or {}

    def page_jobs(
        self,
        *,
        group_name: str | None = None,
        job_name: str | None = None,
        job_status: int | None = None,
        executor_info: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        """GET /job/page/list → 任务分页列表。"""
        return self._as_page(
            self.get(
                "/job/page/list",
                params={
                    "groupName": group_name,
                    "jobName": job_name,
                    "jobStatus": job_status,
                    "executorInfo": executor_info,
                    "page": page,
                    "size": size,
                },
            ),
            page, size,
        )

    def get_job(self, job_id: int) -> dict:
        """GET /job/{id} → 任务详情。"""
        return self.get(f"/job/{job_id}")

    def create_job(self, payload: dict) -> int:
        """POST /job → 新增任务，返回新任务 id。"""
        data = self.post("/job", json=payload)
        return int(data)

    def update_job(self, payload: dict) -> bool:
        """PUT /job → 整体更新任务（需带 id 与全部字段）。"""
        return bool(self.put("/job", json=payload))

    def update_job_status(self, job_id: int, status: int) -> bool:
        """PUT /job/status → 启动(1)/暂停(0) 任务。"""
        return bool(self.put("/job/status", json={"id": job_id, "status": status}))

    def delete_jobs(self, job_ids: list[int]) -> bool:
        """DELETE /job/ids → 批量删除任务（body 传 id 集合）。"""
        return bool(self.delete("/job/ids", json=job_ids))

    def trigger_job(self, job_id: int, tmp_args: str | None = None) -> bool:
        """POST /job/trigger → 手动触发任务。"""
        return bool(self.post("/job/trigger", json={"jobId": job_id, "tmpArgsStr": tmp_args}))

    def page_executors(
        self,
        *,
        group_name: str | None = None,
        executor_info: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> dict:
        """GET /job/executor/page/list → 已注册执行器分页。"""
        return self._as_page(
            self.get(
                "/job/executor/page/list",
                params={"groupName": group_name, "executorInfo": executor_info, "page": page, "size": size},
            ),
            page, size,
        )

    def list_executor_names(self, group_name: str | None = None) -> list[str]:
        """GET /job/executor/list → 已注册执行器名称集合。"""
        return list(self.get("/job/executor/list", params={"groupName": group_name}) or [])

    def page_batches(
        self,
        *,
        job_id: int | None = None,
        group_name: str | None = None,
        task_batch_status: list[int] | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        """GET /job/batch/list → 执行批次分页列表。"""
        params: dict = {"jobId": job_id, "groupName": group_name, "page": page, "size": size}
        if task_batch_status:
            # httpx 会把 list 序列化为重复 key（taskBatchStatus=1&taskBatchStatus=2）
            params["taskBatchStatus"] = task_batch_status
        return self._as_page(self.get("/job/batch/list", params=params), page, size)

    def get_batch(self, task_batch_id: int) -> dict:
        """GET /job/batch/{id} → 批次详情。"""
        return self.get(f"/job/batch/{task_batch_id}")

    def stop_batch(self, task_batch_id: int) -> bool:
        """POST /job/batch/stop/{taskBatchId} → 停止执行中批次。"""
        return bool(self.post(f"/job/batch/stop/{task_batch_id}"))

    def retry_batch(self, task_batch_id: int) -> bool:
        """POST /job/batch/retry/{taskBatchId} → 重试批次（重跑失败子任务）。"""
        return bool(self.post(f"/job/batch/retry/{task_batch_id}"))

    def batch_task_tree(
        self,
        *,
        task_batch_id: int,
        job_id: int | None = None,
        parent_id: int | None = None,
        task_status: int | None = None,
    ) -> list[dict]:
        """GET /job/task/tree/list → 批次执行树（子任务明细）。"""
        return self.get(
            "/job/task/tree/list",
            params={
                "taskBatchId": task_batch_id,
                "jobId": job_id,
                "parentId": parent_id,
                "taskStatus": task_status,
            },
        ) or []

    def page_dead_letters(
        self,
        *,
        group_name: str | None = None,
        scene_name: str | None = None,
        biz_no: str | None = None,
        idempotent_id: str | None = None,
        unique_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        """GET /retry-dead-letter/list → 重试死信分页列表。"""
        return self._as_page(
            self.get(
                "/retry-dead-letter/list",
                params={
                    "groupName": group_name,
                    "sceneName": scene_name,
                    "bizNo": biz_no,
                    "idempotentId": idempotent_id,
                    "uniqueId": unique_id,
                    "page": page,
                    "size": size,
                },
            ),
            page, size,
        )

    def rollback_dead_letters(self, ids: list[int]) -> int:
        """POST /retry-dead-letter/batch/rollback → 死信回滚，返回回滚条数。"""
        data = self.post("/retry-dead-letter/batch/rollback", json={"ids": ids})
        return int(data or 0)

    def delete_dead_letters(self, ids: list[int]) -> bool:
        """DELETE /retry-dead-letter/batch → 批量删除死信。"""
        return bool(self.delete("/retry-dead-letter/batch", json={"ids": ids}))
