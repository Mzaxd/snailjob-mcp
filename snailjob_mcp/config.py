"""config.yml 加载与环境配置解析。

配置查找顺序：
1. 环境变量 ``SNAILJOB_CONFIG`` 指定的路径
2. 当前工作目录下的 ``config.yml``
3. 本包根目录（项目根）下的 ``config.yml``

密码优先级：环境变量 ``SNAILJOB_<ENV大写>_PASSWORD`` > yaml 里的
``env:VARNAME`` 引用 > yaml 里的明文。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .errors import ConfigError

CONFIG_FILE_NAME = "config.yml"
_PASSWORD_ENV_RE = re.compile(r"[^A-Z0-9]")


@dataclass
class EnvironmentConfig:
    """单个环境的连接配置。"""

    name: str
    base_url: str
    namespace: str
    username: str
    password: str
    group_name: str | None = None
    read_only: bool = False
    timeout: float = 30.0
    # 内网自签证书（企业 https 域名）无法通过系统 CA 校验时设为 false
    verify_ssl: bool = True

    def password_env_var(self) -> str:
        """该环境对应的密码环境变量名，如 ``SNAILJOB_DEV_PASSWORD``。"""
        suffix = _PASSWORD_ENV_RE.sub("_", self.name.upper()).strip("_")
        return f"SNAILJOB_{suffix}_PASSWORD"


@dataclass
class AppConfig:
    """整个 config.yml 的解析结果。"""

    environments: dict[str, EnvironmentConfig] = field(default_factory=dict)
    default_env: str | None = None

    def env_names(self) -> list[str]:
        return sorted(self.environments)

    def get_env(self, name: str | None) -> EnvironmentConfig:
        """按名取环境配置；name 为空回退 default_env。

        Raises:
            ConfigError: 环境不存在时，附上可用环境列表。
        """
        if name is None or str(name).strip() == "":
            if self.default_env is None:
                if len(self.environments) == 1:
                    return next(iter(self.environments.values()))
                raise ConfigError(
                    "未配置 default_env，且工具未显式传入 env 参数",
                    hint=f"请在 config.yml 中设置 default_env，当前可用环境: {self.env_names()}",
                )
            name = self.default_env
        env = self.environments.get(str(name))
        if env is None:
            raise ConfigError(
                f"环境 {name!r} 不存在于配置中",
                hint=f"可用环境: {self.env_names()}，或检查 config.yml 拼写",
            )
        return env


def _resolve_password(env_cfg_name: str, raw: object) -> str:
    """解析密码：环境变量覆盖 > env:VARNAME 引用 > 明文。"""
    override = os.environ.get(f"SNAILJOB_{_PASSWORD_ENV_RE.sub('_', env_cfg_name.upper()).strip('_')}_PASSWORD")
    if override:
        return override
    if isinstance(raw, str) and raw.startswith("env:"):
        var = raw[4:].strip()
        value = os.environ.get(var)
        if not value:
            raise ConfigError(
                f"环境 {env_cfg_name} 的密码引用了环境变量 {var}，但该变量未设置",
                hint=f"请设置环境变量 {var} 或直接在 config.yml 中写入密码",
            )
        return value
    if raw is None:
        return ""
    return str(raw)


def _parse_environment(name: str, raw: dict) -> EnvironmentConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"环境 {name!r} 的配置必须是键值映射")
    missing = [
        k for k in ("base_url", "namespace", "username") if not raw.get(k)
    ]
    if missing:
        raise ConfigError(
            f"环境 {name!r} 缺少必填字段: {', '.join(missing)}",
            hint="必填字段为 base_url（含 context-path）、namespace（命名空间名称）、username",
        )
    base_url = str(raw["base_url"]).rstrip("/")
    if "://" not in base_url:
        raise ConfigError(
            f"环境 {name!r} 的 base_url 缺少协议前缀: {base_url}",
            hint="示例: http://host:8800/snail-job（必须包含 context-path）",
        )
    return EnvironmentConfig(
        name=name,
        base_url=base_url,
        namespace=str(raw["namespace"]),
        username=str(raw["username"]),
        password=_resolve_password(name, raw.get("password")),
        group_name=(str(raw["group_name"]) if raw.get("group_name") else None),
        read_only=bool(raw.get("read_only", False)),
        timeout=float(raw.get("timeout", 30.0)),
        verify_ssl=bool(raw.get("verify_ssl", True)),
    )


def _find_config_file(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    candidates = [
        Path.cwd() / CONFIG_FILE_NAME,
        Path(__file__).resolve().parent.parent / CONFIG_FILE_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_config(path: str | None = None) -> AppConfig:
    """加载并校验 config.yml，返回解析后的 :class:`AppConfig`。"""
    file = _find_config_file(path or os.environ.get("SNAILJOB_CONFIG"))
    if file is None:
        searched = [os.environ.get("SNAILJOB_CONFIG"), str(Path.cwd() / CONFIG_FILE_NAME)]
        raise ConfigError(
            "找不到 config.yml 配置文件",
            hint=(
                "请复制 config.example.yml 为 config.yml 并填写环境信息；"
                f"查找路径: {', '.join(s for s in searched if s)}，"
                "或用环境变量 SNAILJOB_CONFIG 指定绝对路径"
            ),
        )
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 {file} 不是合法的 YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {file} 顶层必须是键值映射")

    raw_envs = data.get("environments")
    if not isinstance(raw_envs, dict) or not raw_envs:
        raise ConfigError(
            f"配置文件 {file} 缺少 environments 段或为空",
            hint="至少需要定义一个环境，参考 config.example.yml",
        )

    cfg = AppConfig(default_env=data.get("default_env"))
    for name, raw in raw_envs.items():
        cfg.environments[str(name)] = _parse_environment(str(name), raw or {})

    if cfg.default_env is not None and cfg.default_env not in cfg.environments:
        raise ConfigError(
            f"default_env 指向的环境 {cfg.default_env!r} 未在 environments 中定义",
            hint=f"当前已定义的环境: {cfg.env_names()}",
        )
    return cfg
