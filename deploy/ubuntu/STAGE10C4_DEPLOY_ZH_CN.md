# Stage 10C-4 部署说明

版本：Server 0.12.3，Windows Agent 0.8.3。

本阶段启用 Agent WebSocket 命令客户端：连接服务端 `/api/agent/commands/ws`，使用 Agent Token 鉴权，完成命令推送、ACK、结果回传；连接失败、超时或服务端不可用时，自动回退到原有 HTTP Pull。

本阶段无数据库迁移，数据库保持 `0010_commands`。

服务器安装前必须备份数据库、源码、systemd 配置和环境配置。校验 SHA256 后停止服务、解压更新包、执行 Python 编译和导入检查，再重启服务。不要执行新的 Alembic 迁移。

Windows Agent 安装前停止旧的 `python -m agent.service_main`，备份项目目录，解压更新包，在 Agent 虚拟环境中安装 `agent/requirements-windows.txt`，再运行 `python -m agent.service_main`。

验收：服务版本 `0.12.3`，Agent `client_version` 为 `0.8.3` 且状态 `ONLINE`；WebSocket 应保持常驻，创建命令后应能看到推送完成，断开 WebSocket 后 HTTP Pull 仍可完成命令。
