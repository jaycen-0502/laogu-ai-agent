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
# GitHub 一键升级

公开仓库默认匿名下载，不需要 GitHub Token；如果仓库改回私有，再通过环境变量临时传入只读 Token。

生产服务器已连接 SSH 后，执行：

```bash
cd /opt/laogu-ai-agent
curl -fsSL -H "Authorization: Bearer 你的GitHubToken" \
  https://raw.githubusercontent.com/jaycen-0502/laogu-ai-agent/main/deploy/ubuntu/upgrade-from-github.sh \
  -o /tmp/laogu-upgrade.sh
sudo bash /tmp/laogu-upgrade.sh
```

脚本会交互询问仓库和版本，建议填写 `v0.21.6`。GitHub Token 需要私有仓库只读权限；输入时不会显示，也不会保存。升级前会备份 PostgreSQL、应用代码，自动执行 `alembic upgrade head`，构建前端并检查 `/api/health` 与 `/api/health/ready`。

全新安装或灾备恢复完成后，系统会安装稳定的一键入口：

```bash
sudo /usr/local/sbin/laogu-upgrade-from-github
```

该入口每次都会从指定 GitHub 分支重新下载最新升级脚本，因此不会使用服务器上遗留的旧版本校验逻辑。临时升级修复分支时可执行：

```bash
sudo env GITHUB_REF=codex/license-issuer-availability-fix \
  /usr/local/sbin/laogu-upgrade-from-github
```

入口会提示输入私有仓库 Token；Token 只在当前进程内使用，不写入服务器配置。

升级后，管理员进入“用户与邀请”页面，点击用户行的“AI权限”，即可分配聊天、话术、分析、任务、生图以及对应模型。普通用户看不到 Provider 地址、模型名、Token 和延迟。
