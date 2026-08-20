# Stage 10C-3 部署说明

本阶段将服务器版本更新为 0.12.2，Windows Agent 更新为 0.8.1。

功能包括 RuntimeConfig、白名单 START_TASK/STOP_TASK、UPDATE_PARAMS/UPDATE_KEYWORDS，以及 `/api/agent/commands/ws` 推送通道。HTTP `/api/agent/commands/pull` 保留为断线 fallback。

本阶段没有数据库迁移；必须先完成数据库和源码备份，校验 SHA256 后再替换文件。

服务器安装后执行 `systemctl restart laogu-server`，确认 `/api/health` 和 `/api/health/ready` 均返回 `{"ok":true}`。

Windows Agent 安装后运行 `python -m agent.service_main`，确认数据库 `agents.client_version` 为 `0.8.1` 且状态为 `ONLINE`。

WebSocket 使用 Agent Token 鉴权，路径为 `/api/agent/commands/ws?token=<agent-token>`。连接失败时 Agent 继续使用 HTTP Pull，不得删除 fallback。
