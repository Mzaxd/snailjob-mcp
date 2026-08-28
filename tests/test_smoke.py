"""冒烟测试：不依赖真实 SnailJob 服务端，用 httpx.MockTransport 走通核心链路。

覆盖：
- 配置加载与环境解析（default_env、密码环境变量覆盖、read_only）
- 枚举归一化（名称 → 数字值，非法值报错）
- console client：登录、Result 解包、token 失效自动重登重试
- registry：命名空间 name→uniqueId、组自动发现、组 token 获取
- 工具层：list_jobs 输出结构、read_only 环境写操作被拒绝

运行：pytest tests/ -v
"""

from __future__ import annotations

import httpx
import pytest

from snailjob_mcp.config import AppConfig, EnvironmentConfig, load_config
from snailjob_mcp.console_client import ConsoleClient
from snailjob_mcp.enums import resolve, ROUTE_KEY, ROUTE_KEY_BY_NAME
from snailjob_mcp.errors import ConfigError, ReadOnlyError, SnailJobError
from snailjob_mcp.registry import EnvContext, registry as _registry
from snailjob_mcp.tools.common import ensure_writable, get_context, normalize_page


# ---------------------------------------------------------------- 配置加载
def test_load_example_config(tmp_path, monkeypatch):
    import pathlib

    example = pathlib.Path(__file__).resolve().parent.parent / "config.example.yml"
    target = tmp_path / "config.yml"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("SNAILJOB_CONFIG", str(target))
    monkeypatch.setenv("SNAILJOB_PRD_PASSWORD", "prd-pwd-from-env")

    cfg = load_config()
    assert cfg.default_env == "dev"
    dev = cfg.get_env("dev")
    assert dev.base_url.endswith("/snail-job")
    assert dev.read_only is False
    prd = cfg.get_env("prd")
    assert prd.read_only is True
    assert prd.password == "prd-pwd-from-env"  # 环境变量覆盖 env: 引用


def test_env_not_found_lists_available():
    cfg = AppConfig(default_env="dev", environments={"dev": _fake_env("dev")})
    with pytest.raises(ConfigError) as exc:
        cfg.get_env("nope")
    assert "dev" in str(exc.value)


def _fake_env(name: str = "dev", read_only: bool = False) -> EnvironmentConfig:
    return EnvironmentConfig(
        name=name,
        base_url="http://127.0.0.1:8800/snail-job",
        namespace="默认命名空间",
        username="admin",
        password="x",
        read_only=read_only,
    )


# ---------------------------------------------------------------- 枚举归一化
def test_resolve_enum_by_name_and_number():
    assert resolve(ROUTE_KEY, ROUTE_KEY_BY_NAME, "round_robin", "route_key") == 4
    assert resolve(ROUTE_KEY, ROUTE_KEY_BY_NAME, 1, "route_key") == 1
    with pytest.raises(ValueError):
        resolve(ROUTE_KEY, ROUTE_KEY_BY_NAME, "bogus", "route_key")


# ---------------------------------------------------------------- console client
def _mock_server(handler) -> ConsoleClient:
    transport = httpx.MockTransport(handler)
    return ConsoleClient(_fake_env(), transport=transport)


def test_login_and_page_jobs():
    import hashlib
    import json as _json

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("SNAIL-JOB-AUTH")
        if request.url.path.endswith("/auth/login"):
            seen["login_body"] = _json.loads(request.content)
            return httpx.Response(200, json={"status": 1, "message": "success",
                                             "data": {"token": "tok-1", "namespaceIds": [
                                                 {"id": 1, "name": "默认命名空间", "uniqueId": "ns-uid-1"}]}})
        if request.url.path.endswith("/job/page/list"):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"status": 1, "message": "success", "data": {
                "page": 1, "size": 20, "total": 1,
                "data": [{"id": 9, "jobName": "demo", "jobStatus": 1}]}})
        return httpx.Response(404, json={"status": 404, "message": "not found", "data": None})

    client = _mock_server(handler)
    client.login()
    data = client.page_jobs(job_name="demo", page=1, size=20)
    assert data["total"] == 1
    assert seen["auth"] == "tok-1"
    assert seen["params"]["jobName"] == "demo"
    assert "jobStatus" not in seen["params"]  # None 参数已被过滤
    # 登录密码必须是 MD5 摘要（与前端一致），不能发明文
    assert seen["login_body"]["password"] == hashlib.md5(b"x").hexdigest()


def test_401_triggers_relogin_once():
    calls = {"login": 0, "jobs": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"status": 1, "message": "ok", "data": {"token": f"tok-{calls['login']}"}})
        if request.url.path.endswith("/job/page/list"):
            calls["jobs"] += 1
            # 无论 token 是什么都返回业务层 401：验证只重登重试一次，不死循环
            return httpx.Response(200, json={"status": 401, "message": "Login expired, please log in again", "data": None})
        return httpx.Response(404)

    client = _mock_server(handler)
    client.login()
    with pytest.raises(SnailJobError):
        client.page_jobs()
    assert calls == {"login": 2, "jobs": 2}


def test_business_error_unwrapped_with_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"status": 1, "message": "ok", "data": {"token": "tok-1"}})
        return httpx.Response(200, json={"status": 500, "message": "Namespace x does not exist", "data": None})

    client = _mock_server(handler)
    client.login()
    with pytest.raises(SnailJobError) as exc:
        client.list_namespaces()
    assert exc.value.hint  # 带下一步建议


# ---------------------------------------------------------------- registry 自动发现
def test_env_context_discovery(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"status": 1, "message": "ok", "data": {
                "token": "tok-1",
                "namespaceIds": [{"id": 1, "name": "默认命名空间", "uniqueId": "ns-uid-1"}]}})
        if path.endswith("/group/list"):
            assert request.headers["SNAIL-JOB-NAMESPACE-ID"] == "ns-uid-1"
            return httpx.Response(200, json={"status": 1, "message": "ok", "data": [
                {"id": 1, "groupName": "gas", "namespaceId": "ns-uid-1", "token": "group-token-plain"}]})
        return httpx.Response(404, json={"status": 404, "message": "?", "data": None})

    ctx = EnvContext(AppConfig(environments={}), _fake_env())
    ctx._console = ConsoleClient(_fake_env(), transport=httpx.MockTransport(handler))

    assert ctx.namespace_id() == "ns-uid-1"
    assert ctx.group_names() == ["gas"]
    assert ctx.resolve_group_name(None) == "gas"  # 唯一组自动发现
    api = ctx.openapi()
    assert api._client.headers["token"] == "group-token-plain"
    assert api._client.headers["group-name"] == "gas"


# ---------------------------------------------------------------- 工具层
def test_read_only_env_rejects_writes(monkeypatch):
    class FakeCtx:
        config = _fake_env("prd", read_only=True)

    monkeypatch.setattr("snailjob_mcp.tools.common.registry", type("R", (), {"context": staticmethod(lambda e: FakeCtx())})())
    with pytest.raises(ReadOnlyError):
        ensure_writable(FakeCtx.config, "手动触发")


def test_normalize_page_shapes():
    assert normalize_page({"page": 1, "size": 20, "total": 3, "data": [1, 2, 3]})["total"] == 3
    assert normalize_page([1, 2]) == {"rows": [1, 2]}


def test_get_context_uses_default_env(tmp_path, monkeypatch):
    import pathlib

    example = pathlib.Path(__file__).resolve().parent.parent / "config.example.yml"
    target = tmp_path / "config.yml"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("SNAILJOB_CONFIG", str(target))
    monkeypatch.setenv("SNAILJOB_PRD_PASSWORD", "x")  # example 里 prd 密码引用了该变量

    from snailjob_mcp import registry as reg

    _registry.reset()
    ctx = get_context("")
    assert ctx.config.name == "dev"
    _registry.reset()
