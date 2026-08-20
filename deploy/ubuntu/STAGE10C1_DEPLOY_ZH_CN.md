# Stage 10C-1：Command Channel 基础协议部署说明

## 定位

10C-1 是 10C 的第一小阶段，先建立可审计、可幂等、可回退的 Command 基础协议。它把实时控制指令和业务 Task 分离，并提供 HTTP Pull fallback；现有 `/api/tasks/pull` 继续保留。

本阶段暂不启用 WebSocket、ProfileWorker 长驻调度、RuntimeConfig 热更新和 Credential Snapshot。Command 被 Agent 拉取后，仍需后续 10C 阶段接入具体 ProfileWorker 执行器。

## 更新内容

- 服务版本：`0.12.0`
- 数据库迁移：`0009_ai_task_proposals -> 0010_commands`
- 新增表：`commands`
- 新增后端模块：`server/command_api.py`
- Agent 客户端新增 HTTP fallback 方法：拉取 Command、ACK、提交结果

## Command 状态机

```text
PENDING -> DELIVERED -> ACKNOWLEDGED -> RUNNING -> SUCCESS
                                             -> FAILED
                                             -> CANCELLED
```

- Pull 会把 `PENDING` 变为 `DELIVERED`，并增加 `attempts`。
- 60 秒未继续处理的 `DELIVERED` Command 可重新投递。
- 相同 Agent 的相同 `idempotency_key` 不会重复创建 Command。
- 终态结果重复提交只返回幂等响应，不覆盖第一次结果。

## API

管理端：

- `POST /api/commands`
- `GET /api/commands`
- `GET /api/commands/{command_id}`
- `POST /api/commands/{command_id}/cancel`

Agent HTTP fallback：

- `POST /api/agent/commands/pull`
- `POST /api/agent/commands/{command_id}/ack`
- `POST /api/agent/commands/{command_id}/result`

允许的 Command 类型：

`START_PROFILE`、`STOP_PROFILE`、`START_TASK`、`STOP_TASK`、`UPDATE_PARAMS`、`UPDATE_KEYWORDS`、`REFRESH_PROFILE`。

## 安全边界

- 只有 `ADMIN` 或 Workspace `OWNER` 可以创建、取消 Command；`MEMBER` 只能查看。
- Agent、Profile、Task 必须属于同一 Workspace 和同一 Agent。
- Command 类型使用白名单，不支持任意命令、任意 Python 或系统 Shell。
- 现有 Agent Token 认证继续使用；Command 不记录 Cookie、Credential 或 Token 内容。
- 现有 Task Pull、Script 校验和 Browser 控制路径不被替换。

## 部署顺序

1. 备份 PostgreSQL、源码、Web、配置、服务文件和图片。
2. 校验更新包 SHA256，并执行压缩包危险路径检查。
3. 解压更新包并设置后端权限。
4. 执行 `.venv/bin/alembic upgrade head`，预期版本为 `0010_commands`。
5. 重启 `laogu-server` 并检查健康接口。

## 验收

```bash
systemctl is-active laogu-server
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/ready
curl -fsS http://127.0.0.1:8000/openapi.json
sudo -u postgres psql -d laogu -c "SELECT version_num FROM alembic_version;"
sudo -u postgres psql -d laogu -c "SELECT to_regclass('public.commands');"
```

预期服务版本为 `0.12.0`，数据库版本为 `0010_commands`，并存在 `commands` 表。创建一个 `REFRESH_PROFILE` Command 后，重复使用同一幂等键创建不得新增记录；Agent Pull、ACK、终态结果回传应分别得到 `DELIVERED`、`RUNNING` 和 `SUCCESS/FAILED/CANCELLED`。

## 回滚

停止服务后恢复部署前源码、配置和 Web 备份。数据库回滚执行：

```bash
.venv/bin/alembic downgrade 0009_ai_task_proposals
```

确认 `commands` 表已移除后再启动服务。由于本阶段不修改已有 Task 数据，回滚不会删除 Task 或 Activity 记录。

## 后续 10C 阶段

后续阶段再接入 ProfileWorker、Command Dispatcher、START/STOP 实际执行、RuntimeConfig 版本、WebSocket Agent Control Channel、HTTP fallback 自动消费和 Cookie Capability Probe。Credential Snapshot 只有在 Probe 证明 Browser 真正支持安全读取/导入后才实现。
