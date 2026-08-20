# Ubuntu 24.04 部署说明

新服务器请优先阅读 [一键部署与灾备恢复教程](./ONE_CLICK_DEPLOY_ZH_CN.md)。

- `install.sh`：全新服务器安装；拒绝覆盖已有系统
- `restore.sh`：从 age 加密恢复包重建故障服务器
- `verify.sh`：生产环境验收
- `install-backup.sh`：安装 Telegram 加密备份和每周检查
- `laogu-server.service`：systemd 服务模板
- `nginx-laogu.conf`：HTTPS Nginx 最终配置模板

`DEPLOY_ZH_CN.md` 和 `STAGE*.md` 是历史分阶段部署记录，仅用于问题追溯。

Laogu Browser 只运行在 Windows，禁止安装到 Ubuntu 服务器。
