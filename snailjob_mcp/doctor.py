"""``python -m snailjob_mcp.doctor [env]`` — 新环境接入自检。

依次验证：配置加载 → 控制台登录 → 命名空间解析 → 组发现与组 token 获取 →
任务列表 → 在线机器，逐项打印 OK/FAIL 与失败原因。
"""

from __future__ import annotations

import argparse
import logging
import sys

from .errors import SnailJobError
from .registry import registry


def _print(name: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def run_doctor(env: str = "", config_path: str | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from .config import load_config

    print(f"snailjob-mcp doctor（环境: {env or '<default_env>'}）")
    try:
        cfg = load_config(config_path)
    except SnailJobError as exc:
        _print("加载配置", False, str(exc))
        return 1
    if not _print(
        "加载配置",
        True,
        f"环境: {cfg.env_names()}，default_env={cfg.default_env}，"
        f"目标 base_url={cfg.get_env(env or None).base_url}",
    ):
        return 1

    ctx = registry.context(env or None)

    # 1. 登录（顺带展示登录响应里携带的命名空间）
    try:
        data = ctx.console().login()
        visible = [ns.get("name") for ns in (data or {}).get("namespaceIds") or []]
        _print("控制台登录", True, f"账号={ctx.config.username}，可见命名空间={visible}")
    except SnailJobError as exc:
        _print("控制台登录", False, str(exc))
        return 1

    # 2. 命名空间解析
    try:
        ns_id = ctx.namespace_id()
        _print("命名空间解析", True, f"{ctx.config.namespace!r} -> uniqueId={ns_id}")
    except SnailJobError as exc:
        _print("命名空间解析", False, str(exc))
        return 1

    # 3. 组发现 + 组 token
    try:
        groups = ctx.group_names()
        if not _print("组配置发现", True, f"可用组: {groups}"):
            return 1
    except SnailJobError as exc:
        _print("组配置发现", False, str(exc))
        return 1
    try:
        client = ctx.openapi()
        token = client._client.headers.get("token", "")
        masked = (token[:4] + "..." + token[-4:]) if len(token) > 8 else "(短token)"
        _print("组 token 获取", True, f"token={masked}（请与控制台组管理页面对比确认是明文）")
    except SnailJobError as exc:
        _print("组 token 获取", False, str(exc))
        return 1

    # 4. 任务列表
    try:
        result = ctx.console().page_jobs(page=1, size=5)
        rows = (result or {}).get("data") or []
        total = (result or {}).get("total", "?")
        _print("任务列表", True, f"total={total}，首条任务={[r.get('jobName') for r in rows[:3]]}")
    except Exception as exc:  # noqa: BLE001 — 自检要报告任何失败原因，而不是崩溃
        _print("任务列表", False, f"{type(exc).__name__}: {exc}")

    # 5. 在线机器
    try:
        pods = ctx.console().list_pods(page=1, size=50) or {}
        rows = pods.get("data") or []
        _print(
            "在线机器",
            True,
            f"total={pods.get('total', '?')}，"
            f"示例={[[r.get('hostIp'), r.get('hostPort')] for r in rows[:3]]}",
        )
    except Exception as exc:  # noqa: BLE001
        _print("在线机器", False, f"{type(exc).__name__}: {exc}")

    print("doctor 自检完成。全部 OK 后即可在 MCP 客户端里注册使用。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="snailjob-mcp 环境自检")
    parser.add_argument("env", nargs="?", default="", help="环境名（config.yml 的 environments 键），缺省用 default_env")
    parser.add_argument("--config", default=None, help="config.yml 路径（缺省走默认查找顺序）")
    args = parser.parse_args()
    try:
        return run_doctor(args.env, args.config)
    finally:
        registry.reset()


if __name__ == "__main__":
    raise SystemExit(main())
