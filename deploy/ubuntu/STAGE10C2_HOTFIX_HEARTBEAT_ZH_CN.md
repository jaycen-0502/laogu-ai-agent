# 10C-2 心跳版本上报热修复

## 原因

Agent 的 `client_version` 原本只在首次注册时写入 Server。已有 Agent Token 会跳过注册，后续 Heartbeat 也没有携带版本，因此 Server 仍显示旧注册版本 `0.7.0`，即使 Windows Agent 源码已经更新到 `0.8.0`。

## 修复

- Agent Heartbeat 增加 `client_version`。
- Server Heartbeat 接收并更新 `agents.client_version`。
- 应用版本更新为 `0.12.1`。
- 无数据库迁移。

## 部署

1. 备份 Server 源码、配置和服务文件。
2. 安装 `server/main.py`、`server/schemas.py` 和 `agent/agent_service.py`。
3. 重启 `laogu-server`。
4. Windows Agent 使用热修复后的 `agent/agent_service.py` 重新启动。
5. 等待一次心跳后查询 `agents.client_version`。

预期从 `0.7.0` 更新为 `0.8.0`，无需重新注册 Agent，也无需迁移数据库。
