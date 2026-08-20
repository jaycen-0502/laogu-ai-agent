# Stage 10C-2：ProfileWorker 与 Command 执行层部署说明

## 定位

10C-2 在 10C-1 Command HTTP fallback 的基础上，为 Windows Agent 增加每个 Profile 独立的 ProfileWorker。每个 Worker 使用独立锁和状态，不会因为一个 Profile 的异常停止整个 Agent。

本阶段仍使用 HTTP Pull；不修改 Laogu Browser，不重写 CDP，不启用 WebSocket，也不实现 Cookie/Credential。

## 更新内容

- Windows Agent 客户端版本：`0.8.0`
- 新增模块：`agent/profile_worker.py`
- Agent Service 接入 Command Pull、ACK 和结果回传
- 支持的实际 Command：
  - `START_PROFILE`
  - `STOP_PROFILE`
  - `REFRESH_PROFILE`
- 其他 Command 类型会被安全拒绝并回传 `FAILED`，不会执行任意代码或系统命令。

## ProfileWorker 状态

```text
STOPPED -> STARTING -> IDLE
IDLE -> RUNNING（由后续任务执行层使用）
IDLE -> STOPPING -> STOPPED
任意状态 -> ERROR / OFFLINE
```

- 同一 Profile 的重复 START/STOP 请求具有幂等行为。
- 不同 Profile 使用不同 Worker 实例和锁。
- `START_PROFILE`、`STOP_PROFILE` 通过现有 `BrowserManager` 调用已验证的 Laogu API。
- `REFRESH_PROFILE` 只读取 Profile runtime 状态，不返回 Cookie 或 Credential。

## Agent 处理流程

```text
HTTP Pull Command
    -> ACK(RUNNING)
    -> ProfileWorker.dispatch
    -> SUCCESS 或 FAILED 回传
```

如果 Agent 或 Server 暂时断线，Command 不会被标记为成功；Server 的投递租约过期后可以重新投递。Worker 的生命周期操作是幂等的，重复投递不会重复启动或停止已处于目标状态的 Profile。

## 部署范围

本阶段主要更新 Windows Agent。Server 不新增数据库表，不需要执行 Alembic migration；Server 必须已经完成 10C-1 的 `0010_commands` 迁移和 API 部署。

1. 备份 Windows Agent 配置、DPAPI 凭据文件、Agent 状态 SQLite 和日志。
2. 校验更新包 SHA256。
3. 将 Agent 文件更新到现有 Agent 项目目录。
4. 保留现有 `agent_credentials_file` 和 Server URL 配置。
5. 以原方式启动 `python -m agent.service_main`。

## 验收

- Agent 启动后心跳保持 `ONLINE`。
- Server Command Pull 能收到 `START_PROFILE`、`STOP_PROFILE`、`REFRESH_PROFILE`。
- 每条 Command 依次出现 `DELIVERED`、`RUNNING`、`SUCCESS` 或 `FAILED`。
- 重复 START/STOP 不会创建第二个 Worker，也不会重复调用目标状态的 Browser API。
- 一个 Profile Worker 失败时，其他 Profile 仍可继续心跳和处理 Task。
- Agent 日志、Server Audit 和 Web 页面都不包含 Cookie Value、Agent Token 或 Credential 内容。

## 回滚

停止 Agent，恢复 Agent 更新前的源码和配置，再按原方式启动。由于 10C-2 无数据库迁移，Server 数据库不需要回滚。

## 后续阶段

后续再实现 START_TASK/STOP_TASK 与 TaskManager 的安全协同、RuntimeConfig 版本和关键词热更新、WebSocket Agent Control Channel，以及 Cookie Capability Probe。Credential Snapshot 只有在 Probe 证明 Browser 支持安全读取/导入后才设计。
