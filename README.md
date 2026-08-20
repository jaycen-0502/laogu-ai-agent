# Laogu AI Agent

老谷系统由 Ubuntu 协调服务器、React 管理后台、Windows Agent、Laogu Browser 和桌面外部控制台组成。

- 应用版本：`0.20.0`
- 部署/恢复工具版本：`v0.18.2`
- 生产地址示例：`https://api.jaycwl.org`

本项目使用 GitHub 私有仓库。服务器不能匿名下载代码，必须先配置一次 GitHub
只读 Deploy Key；密钥、数据库、Telegram Token 和生产配置永远不上传 GitHub。

## 项目组件

| 目录 | 组件 | 运行位置 |
|---|---|---|
| `server/`、`alembic/` | FastAPI 服务端和数据库迁移 | Ubuntu 服务器 |
| `web/` | React 管理后台 | Ubuntu 服务器构建，由 Nginx 提供 |
| `agent/` | Windows Agent 和任务执行层 | Windows |
| `browser/` | Laogu Browser（Wails + Go + React）源码 | Windows |
| `desktop/` | 桌面外部控制台源码 | Windows |
| `packaging/windows/` | 桌面控制台 PyInstaller 打包配置 | Windows 构建机 |

仓库只保存可审查、可复现构建的源码。以下内容不进入 Git：浏览器实例目录、Cookie、登录状态、
本地 `config.yaml`、数据库、日志、代理/Chrome 运行时、授权私钥、密码文件以及编译后的 EXE。
Windows 安装包和便携版应通过 GitHub Release 附件发布。

## 生产结构

```text
Internet
  -> Nginx :443
      -> React 管理后台
      -> FastAPI 127.0.0.1:8000
          -> PostgreSQL

Windows Agent
  -> HTTPS / WebSocket
      -> Ubuntu 协调服务器
```

Laogu Browser 及 Windows Agent 只在 Windows 运行，不安装到 Ubuntu 服务器。

## Windows 程序构建

### Laogu Browser

```powershell
cd browser
npm --prefix frontend ci
wails build -clean
```

输出位于 `browser\build\bin\Laogu-Browser.exe`，详细说明见
[Browser 捆绑构建说明](browser/BUNDLE_BUILD_ZH_CN.md)。程序首次运行会生成本地配置和运行数据，
这些文件不会被 Git 跟踪。

### 桌面外部控制台

```powershell
python -m pip install -r desktop\requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass `
  -File packaging\windows\build_portable.ps1 -Clean
```

输出位于 `dist\Laogu-Desktop\Laogu-Desktop.exe`。控制台会复用现有 Agent 能力，首次运行可导入
同一 Windows 用户下由 DPAPI 保护的 Agent 凭据；如检测到独立命令行 Agent，会询问是否停止它，
但不会关闭 Laogu Browser。

## 小白全新服务器一键部署

适用场景：全新的 Ubuntu 24.04 服务器，创建一个空白系统。

准备：

1. 云安全组开放 `22`、`80`、`443`，不要开放 `5432`、`8000`；
2. 域名 A 记录已经指向新服务器公网 IP；
3. 服务器有公网 IPv4；
4. 准备 Let's Encrypt 邮箱、工作区名称、管理员用户名和至少 12 位密码。

### 第一次配置私有仓库读取权限

在新服务器执行：

```bash
apt update && apt install -y git openssh-client
install -d -m 700 /root/.ssh
ssh-keygen -t ed25519 -f /root/.ssh/laogu-github-deploy -N '' \
  -C 'laogu-production-server'
cat /root/.ssh/laogu-github-deploy.pub
```

复制公钥到 GitHub：

```text
仓库 Settings → Deploy keys → Add deploy key
```

只读即可，不要勾选 `Allow write access`。然后执行：

```bash
cat > /root/.ssh/config <<'EOF'
Host github.com
    HostName github.com
    User git
    IdentityFile /root/.ssh/laogu-github-deploy
    IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config
ssh -T git@github.com
```

### 一键搭建命令

```bash
git clone git@github.com:jaycen-0502/laogu-ai-agent.git /opt/laogu-ai-agent \
&& cd /opt/laogu-ai-agent \
&& git checkout v0.18.2 \
&& sudo bash deploy/ubuntu/install.sh --domain api.jaycwl.org
```

脚本会自动安装 PostgreSQL、Nginx、Python、Node.js、Alembic、systemd、HTTPS，
创建初始管理员，并执行健康检查。它只适用于空白服务器，检测到已有生产配置或数据库会停止。

完整教程见：

- [Ubuntu 24.04 一键部署与灾备恢复](deploy/ubuntu/ONE_CLICK_DEPLOY_ZH_CN.md)

## 恢复现有生产数据的一键命令

适用场景：原服务器故障，要把 Telegram 里的最新加密备份恢复到全新备用服务器。

先从 Windows 上传以下文件到备用服务器的 `/root/restore/`：

```text
laogu-recovery-时间.tar.gz.age
laogu-recovery-时间.tar.gz.age.sha256（如果有）
laogu-backup-recovery.key
```

然后在备用服务器执行：

```bash
git clone git@github.com:jaycen-0502/laogu-ai-agent.git /opt/laogu-ai-agent \
&& cd /opt/laogu-ai-agent \
&& git checkout v0.18.2 \
&& sudo bash deploy/ubuntu/restore.sh
```

恢复向导会自动寻找 `/root/restore` 下的文件，并逐步询问域名、证书邮箱和私钥路径。
它会显示文件大小、校验信息和目标域名，必须输入 `RESTORE` 才开始恢复。

恢复完成后会自动检查：

- PostgreSQL、Nginx、后端服务状态；
- 本机和公网健康接口；
- API 版本和数据库迁移版本；
- Nginx 配置和 HTTPS。

确认登录和数据正常后，重新绑定 Telegram 备份：

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/install-backup.sh
sudo systemctl start laogu-backup.service
sudo journalctl -u laogu-backup.service -n 80 --no-pager
```

最后删除备用服务器上的临时私钥和恢复包，Windows 中的私钥原件继续离线保存。

## 灾备

- 每日使用 age 公钥加密 PostgreSQL、精简源码、应用数据和生产配置；
- 加密恢复包上传 Telegram；
- 本机自动备份保留 7 天；
- 每周检查磁盘、服务、数据库和备份状态；
- 私钥不提交 GitHub，也不包含在恢复包中。

恢复包中的 `database.dump` 恢复用户、工作区、Agent、任务、许可证和审计数据；
`server.env` 恢复数据库连接、JWT 和 AI 凭证加密密钥；Telegram Bot Token 不在包内，
恢复后必须重新输入。HTTPS 证书不搬迁，由 Certbot 在新服务器重新申请。

## 安全

本仓库不保存 `.env`、生产配置、数据库、日志、Agent 凭证、Telegram Token、
age 私钥或 TLS 私钥。生产仓库应保持 Private，并使用只读 Deploy Key 部署。

## 部署文件索引

| 文件 | 用途 |
|---|---|
| `deploy/ubuntu/install.sh` | 全新服务器安装 |
| `deploy/ubuntu/restore.sh` | 交互式恢复生产数据 |
| `deploy/ubuntu/verify.sh` | 部署完成验收 |
| `deploy/ubuntu/install-backup.sh` | Telegram 加密备份和每周检查 |
| `deploy/ubuntu/ONE_CLICK_DEPLOY_ZH_CN.md` | 完整中文小白教程 |
