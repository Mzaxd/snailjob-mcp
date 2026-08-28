"""环境级 client 注册表：自动发现命名空间 id、组名、组 token，并做进程内缓存。

用户在 config.yml 里只需配置 base_url / namespace(名称) / 账号 / 密码：

- 命名空间 id：``GET /namespace/all`` 按 name 匹配取 uniqueId（header 用的
  SNAIL-JOB-NAMESPACE-ID 就是 uniqueId 字符串）。
- 组名：配置里给了 group_name 就用配置；否则从组配置列表里按 namespaceId
  过滤自动发现（多个组时全部返回给调用方自行确认）。
- 组 token：从组配置列表的 ``token`` 字段直接取明文（OpenAPI 认证用）。
"""

from __future__ import annotations

import logging
import threading

from .config import AppConfig, EnvironmentConfig, load_config
from .console_client import ConsoleClient
from .errors import ApiError, ConfigError
from .openapi_client import OpenApiClient

logger = logging.getLogger(__name__)


class EnvContext:
    """单个环境的运行时上下文：client 单例 + 自动发现缓存。"""

    def __init__(self, cfg: AppConfig, env_cfg: EnvironmentConfig) -> None:
        self._cfg = cfg
        self._env_cfg = env_cfg
        self._console: ConsoleClient | None = None
        # RLock：namespace_id() 持锁期间会回调 console() 再次加锁
        self._lock = threading.RLock()
        self._namespace_id: str | None = None
        self._group_configs: list[dict] | None = None
        self._openapi_clients: dict[str, OpenApiClient] = {}

    @property
    def config(self) -> EnvironmentConfig:
        return self._env_cfg

    # ---------------------------------------------------------------- console
    def console(self) -> ConsoleClient:
        with self._lock:
            if self._console is None:
                self._console = ConsoleClient(self._env_cfg)
                self._console._namespace_resolver = self.namespace_id
            return self._console

    # -------------------------------------------------------------- 自动发现
    def namespace_id(self) -> str:
        """命名空间名称 → uniqueId（带缓存）。

        从登录响应的 ``namespaceIds``（完整命名空间对象列表）里按名匹配；
        不能走 ``GET /namespace/all`` 兜底——该接口同样要求携带已解析的
        SNAIL-JOB-NAMESPACE-ID header，未解析时服务端返回 5001，鸡生蛋。
        """
        if self._namespace_id:
            return self._namespace_id
        with self._lock:
            if self._namespace_id:
                return self._namespace_id
            console = self.console()
            unique_id: str | None = None

            login_data = console.login_data or console.login()
            namespaces = (login_data or {}).get("namespaceIds") or []
            for ns in namespaces:
                if str(ns.get("name")) == self._env_cfg.namespace and ns.get("uniqueId"):
                    unique_id = str(ns["uniqueId"])
                    break

            if unique_id is None:
                available = [str(ns.get("name")) for ns in namespaces]
                raise ConfigError(
                    f"环境 {self._env_cfg.name} 配置的命名空间 {self._env_cfg.namespace!r} 不存在",
                    hint=(
                        f"账号可见命名空间: {available}；"
                        "请把 config.yml 的 namespace 字段改成其中之一（填名称，不是 id）"
                    ),
                )

            console.set_namespace_header(unique_id)
            self._namespace_id = unique_id
            logger.info("namespace %r -> uniqueId=%s", self._env_cfg.namespace, unique_id)
            return self._namespace_id

    def _load_group_configs(self) -> list[dict]:
        """本命名空间下的组配置列表（带缓存）。"""
        if self._group_configs is not None:
            return self._group_configs
        with self._lock:
            if self._group_configs is not None:
                return self._group_configs
            all_groups = self.console().list_group_configs(namespace_id=self.namespace_id())
            ns_id = self.namespace_id()
            own = [g for g in all_groups if str(g.get("namespaceId")) == ns_id]
            if not own:
                own = all_groups
                if not own:
                    raise ConfigError(
                        f"环境 {self._env_cfg.name} 未发现任何组配置",
                        hint="请在控制台确认该命名空间下已创建组，或检查账号权限",
                    )
            self._group_configs = own
            return self._group_configs

    def group_names(self) -> list[str]:
        """本命名空间下可用的组名列表。"""
        return [str(g.get("groupName")) for g in self._load_group_configs()]

    def resolve_group_name(self, group_name: str | None) -> str:
        """确定本次调用使用的组名：显式入参 > 配置 > 自动发现（唯一组）。"""
        if group_name:
            return group_name
        if self._env_cfg.group_name:
            return self._env_cfg.group_name
        names = self.group_names()
        if len(names) == 1:
            return names[0]
        raise ConfigError(
            f"环境 {self._env_cfg.name} 存在多个组 {names}，无法自动确定组名",
            hint="请在 config.yml 里指定 group_name，或在工具调用时显式传入 group_name 参数",
        )

    # ---------------------------------------------------------------- openapi
    def openapi(self, group_name: str | None = None) -> OpenApiClient:
        """获取（或创建缓存的）OpenAPI client，三 header 自动装配。"""
        name = self.resolve_group_name(group_name)
        with self._lock:
            client = self._openapi_clients.get(name)
            if client is not None:
                return client
        group = next((g for g in self._load_group_configs() if str(g.get("groupName")) == name), None)
        if group is None:
            raise ConfigError(
                f"环境 {self._env_cfg.name} 下不存在组 {name!r}",
                hint=f"可用组: {self.group_names()}",
            )
        token = group.get("token")
        if not token:
            raise ApiError(
                f"组 {name!r} 的配置响应中没有 token 字段或为空",
                hint="请人工核对控制台组管理页面显示的 token；若为密文需改从组详情接口获取明文",
            )
        client = OpenApiClient(
            self._env_cfg,
            namespace_id=self.namespace_id(),
            group_name=name,
            token=str(token),
        )
        with self._lock:
            self._openapi_clients[name] = client
        logger.info("openapi client ready (env=%s group=%s token_len=%d)", self._env_cfg.name, name, len(str(token)))
        return client

    def close(self) -> None:
        with self._lock:
            if self._console is not None:
                self._console.close()
                self._console = None
            for c in self._openapi_clients.values():
                c.close()
            self._openapi_clients.clear()
            self._namespace_id = None
            self._group_configs = None


class Registry:
    """环境名 → EnvContext 的全局注册表（进程级单例）。"""

    def __init__(self) -> None:
        self._config: AppConfig | None = None
        self._contexts: dict[str, EnvContext] = {}
        self._lock = threading.RLock()

    def reset(self, config: AppConfig | None = None) -> None:
        """清空缓存（主要用于测试与配置热加载）。"""
        with self._lock:
            for ctx in self._contexts.values():
                ctx.close()
            self._contexts.clear()
            self._config = config

    def config(self) -> AppConfig:
        with self._lock:
            if self._config is None:
                self._config = load_config()
            return self._config

    def context(self, env_name: str | None) -> EnvContext:
        cfg = self.config()
        env_cfg = cfg.get_env(env_name)
        with self._lock:
            ctx = self._contexts.get(env_cfg.name)
            if ctx is None:
                ctx = EnvContext(cfg, env_cfg)
                self._contexts[env_cfg.name] = ctx
            return ctx


registry = Registry()
