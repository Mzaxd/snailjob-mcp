# snailjob-mcp

管理自建 [SnailJob](https://snailjob.opensnail.com/) 分布式任务调度平台的 MCP Server。让 AI Agent 通过 MCP 工具完成任务全生命周期操作：查任务、建任务、改 cron/参数、手动触发、启停、看在线机器、查执行批次和失败明细、停止批次、死信回滚。

- 目标服务端版本：**SnailJob 1.8.1**（版本锁定；控制台接口属于前端内部接口，升级 SnailJob 需回归测试）
- 全部能力通过 HTTP 调 SnailJob 服务端，**不直连数据库、不改 SnailJob 源码**
- Python 3.11+，FastMCP（`mcp` 官方 SDK）+ httpx + PyYAML，stdio transport 单进程

## 认证体系（从 1.8.1 jar 反编译确认）

SnailJob 服务端有两套 HTTP 接口，本 MCP 内部封装两个 client：

| | 控制台接口（console） | OpenAPI 接口（openapi） |
|---|---|---|
| 认证 | 登录换 token（password 传 **MD5(明文)**，与前端一致），请求带 `SNAIL-JOB-AUTH` + `SNAIL-JOB-NAMESPACE-ID` | 每请求三 header：`namespace` + `group-name` + `token` |
| 能力 | 页面全部能力（任务/批次/执行树/在线机器/死信/大盘） | 官方任务管理契约（增删改查/触发/启停/批次详情） |
| token 有效期 | 过期自动重登一次并重试（只重试一次，防死循环） | 组 token 长期有效 |

> 登录协议：客户端发 `MD5(明文)`，服务端 `sha256(收到的值)` 与库中哈希比对，即
> `stored = sha256(md5(明文))`（前端 bundle + 服务端字节码 + 线上实测三方确认）。

**用户只需配置：控制台地址 + 命名空间名 + 账号 + 密码。** 其余全部自动发现：

- 命名空间 name → uniqueId：从登录响应的 `namespaceIds` 解析（配置名写错时
  报错并列出账号可见的命名空间；`GET /namespace/all` 需要已解析的 header，不能兜底）
- 组 token：从 `GET /group/list` 直接取明文（该部署中
  `/group/all/group-config/list` GET 返回 405，实测 `/group/list` 可用且字段齐全）
- 组名：配置里可不填，从组列表自动发现（唯一组直接用；多组会提示明确指定）

## 快速开始

```bash
# 1. 安装（Python 3.11+）
pip install -e .
# 或 pip install "mcp>=1.2" httpx pyyaml 后直接用源码目录运行

# 2. 准备配置
cp config.example.yml config.yml   # 填入 base_url / namespace / username / password

# 3. 自检（新环境接入必跑）
python -m snailjob_mcp.doctor          # 用 default_env
python -m snailjob_mcp.doctor prd      # 指定环境
python -m snailjob_mcp.doctor dev --config D:\path\to\config.yml

# 4. 启动 MCP Server（stdio，一般由 MCP 客户端拉起）
python -m snailjob_mcp
```

doctor 依次验证：配置加载 → 控制台登录 → 命名空间解析 → 组发现 → 组 token 获取 → 任务列表 → 在线机器，逐项打印 OK/FAIL 与失败原因。

## 配置说明（config.yml）

查找顺序：环境变量 `SNAILJOB_CONFIG`（绝对路径）→ 当前目录 `config.yml` → 本包根目录 `config.yml`。

```yaml
default_env: dev            # 工具不传 env 时用这个
environments:
  dev:
    base_url: http://<host>:<port>/snail-job   # 必须含 context-path
    namespace: 默认命名空间                      # 填名称，运行时解析为 id
    group_name: gas          # 可选；不填自动发现
    username: admin
    password: your-password  # 支持环境变量覆盖：SNAILJOB_DEV_PASSWORD，或写 env:VARNAME
    read_only: false
  prd:
    base_url: ...
    namespace: ...
    username: ...
    password: env:SNAILJOB_PRD_PASSWORD
    read_only: true          # 生产默认只读！写操作工具直接拒绝
```

密码优先级：`SNAILJOB_<环境名大写>_PASSWORD` 环境变量 > `env:VARNAME` 引用 > 明文。
`read_only: true` 的环境，create/update/delete/trigger/status/stop/retry 类工具一律返回拒绝信息，不可绕过。

## MCP 客户端注册

ZCode（`~/.zcode/mcp.json` 或项目 `.zcode/mcp.json`）：

```json
{
  "mcpServers": {
    "snailjob": {
      "command": "python",
      "args": ["-m", "snailjob_mcp"],
      "env": { "SNAILJOB_CONFIG": "D:\\Code\\AI\\mcps\\snailjob-mcp\\config.yml" }
    }
  }
}
```

Claude Code：

```bash
claude mcp add snailjob -- python -m snailjob_mcp
```

Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "snailjob": {
      "command": "python",
      "args": ["-m", "snailjob_mcp"]
    }
  }
}
```

> 若 `python` 不在 MCP 客户端的 PATH 里，换成解释器绝对路径（如 `D:\Dev\Python\Python312\python.exe`）。

## MCP 工具集（18 个）

每个工具第一个参数固定为 `env`（config.yml 里的环境名），缺省用 `default_env`。

**任务管理**

| 工具 | 说明 |
|---|---|
| `list_jobs(env, group_name?, keyword?, job_status?, page?)` | 任务分页列表（jobName 模糊、状态过滤） |
| `get_job(env, job_id)` | 任务详情；**删除/修改前必须先调用确认** |
| `create_job(env, executor_name, job_desc, cron, ...)` | 新增；先校验执行器已注册；默认创建为暂停态（`start_now=true` 才启动） |
| `update_job(env, job_id, cron?, job_params?, ...)` | 局部更新（内部先 get 再整体提交） |
| `trigger_job(env, job_id, job_params?)` | 手动触发一次（临时参数仅本次生效） |
| `set_job_status(env, job_id, "start"/"pause")` | 启动/暂停 |
| `delete_job(env, job_id)` | **危险**：删除任务，工具描述已注明先 get 确认 |

**运行观测**

| 工具 | 说明 |
|---|---|
| `list_pods(env, group_name?, page?)` | 在线机器（服务端+客户端）+ 各组在线客户端汇总 |
| `list_executors(env, keyword?)` | 已注册执行器列表（创建任务前校验用） |
| `query_batches(env, job_id?, status?, page?)` | 执行批次列表 |
| `get_batch_tree(env, task_batch_id)` | 批次执行树（分片/Map 子任务成败明细） |
| `dashboard(env)` | 大盘统计卡片 |
| `get_batch_detail(env, task_batch_id)` | 批次详情（走 OpenAPI 通道） |

**运维**

| 工具 | 说明 |
|---|---|
| `stop_batch(env, task_batch_id)` | 停止执行中批次 |
| `retry_batch(env, task_batch_id)` | 重试批次（重跑失败子任务） |
| `dead_letter_query(env, group_name?, scene_name?, biz_no?)` | 重试死信查询 |
| `dead_letter_rollback(env, ids)` | **危险**：死信回滚重放，先查询确认再回滚 |
| `dead_letter_delete(env, ids)` | **危险**：删除死信，不可恢复 |

出参里的数字枚举都会附 `*_name` 可读字段（如 `job_status_name: "enabled"`、`task_batch_status_name: "fail"`）。错误返回统一为 `{"error": ..., "hint": ...}`，hint 是下一步排查建议。

## 枚举取值速查（1.8.1 反编译确认）

| 字段 | 取值 |
|---|---|
| jobStatus | 0=暂停 paused，1=开启 enabled |
| triggerType | 2=固定间隔 fixed_interval，3=cron，5=指定时间点，99=工作流 |
| routeKey | 1=一致性哈希，2=随机，3=LRU，4=轮询，5=第一个，6=最后一个 |
| blockStrategy | 1=丢弃 discard，2=覆盖 overlay，3=并行 concurrency，4=恢复 recovery |
| taskType | 1=集群，2=广播，3=分片 |
| argsType | 1=文本，2=JSON |
| taskBatchStatus | 1=waiting，2=running，3=success，4=fail，5=stop，6=cancel |

## 实现要点

- httpx 连接复用；console client 内置「请求 → 401/登录过期 → 重登 → 重试一次」，登录并发加锁
- 命名空间解析、组配置、组 token 进程内缓存，首次发现后常驻
- 错误带上下文：HTTP 状态码 + 响应 msg + 下一步建议
- 日志走 stderr（stdio MCP 约定，stdout 只跑 MCP 协议）

## 端点契约来源

端点路径、请求字段、响应包装均以本地 Maven 仓库 1.8.1 jar 反编译为准
（`snail-job-server-web` / `snail-job-server-openapi` / `snail-job-common-model` / `snail-job-common-core` / `snail-job-client-job-core`）：

- 控制台：`POST /auth/login`、`GET /namespace/all`、`GET /group/list`、
  `GET /group/on-line/pods/{groupName}`、`GET /dashboard/pods`、`GET /dashboard/task-retry-job`、
  `GET|POST|PUT|DELETE /job[/...]`、`GET /job/executor/page/list`、`GET /job/batch/list`、
  `GET /job/task/tree/list`、`POST /job/batch/stop/{id}`、`GET /retry-dead-letter/list`、
  `POST /retry-dead-letter/batch/rollback`
- OpenAPI：`POST /api/job/add`、`PUT /api/job/update`、`PUT /api/job/update/status`、
  `POST /api/job/trigger`、`DELETE /api/job/delete`、`GET /api/job/detail/id?id=`、
  `GET /api/job-batch/detail/{id}`
- 响应包装：`{status, message, data}`，**成功 status=1、失败 status=0**（线上实测）
- 分页：线上 1.8.1 的 `*page/list` 端点返回**裸数组**而非 PageResult，console client
  已统一归一化为 `{"data": [...], "total": n, "page": p, "size": s}`

## 测试

```bash
pytest tests/ -v
```

冒烟测试用 httpx.MockTransport 模拟服务端，覆盖：配置解析、枚举归一化、登录、
Result 解包、token 失效自动重登（且只重试一次）、命名空间/组/token 自动发现、
read_only 拒绝写操作。对真实环境的验收链路（doctor 全绿 → list_jobs →
list_pods → trigger_job → query_batches 看到新批次 → read_only 拒绝写）按
「快速开始」逐步执行即可。
