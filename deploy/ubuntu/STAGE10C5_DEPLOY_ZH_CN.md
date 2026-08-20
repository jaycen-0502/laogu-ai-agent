# Stage 10C-5 部署说明

版本：Server `0.13.0`，Windows Agent `0.8.4`。数据库仍为迁移头 `0010_commands`，本阶段不执行 Alembic 迁移。

新增 Command 可靠性和可观测性：WebSocket/HTTP Pull 对 `DELIVERED` 命令使用 60 秒租约，超时后允许安全重投；终端状态继续幂等；新增 `GET /api/commands/metrics` 查看状态计数和过期租约数量；Agent status 增加通道、重连次数和最近通道变化时间。

部署前备份数据库、源码、systemd、Nginx 和环境配置。校验 SHA256 后停止服务、解压、编译导入检查并重启；不要执行数据库迁移。

验收：服务版本 `0.13.0`，Agent `client_version` 为 `0.8.4` 且 `ONLINE`；`/api/commands/metrics` 可访问；制造一个未 ACK 的命令，等待租约后再次 Pull 或 WebSocket 连接应能重新投递；重复 ACK/结果不会改变终端结果。
