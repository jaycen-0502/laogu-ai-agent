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
| `deploy/ubuntu/SERVER_MIGRATION_ZH_CN.md` | 更换服务器、域名和授权资料迁移说明 | Ubuntu / Windows 运维 |
| `deploy/ubuntu/create-server-migration-bundle.sh` | 在旧服务器生成受限迁移包 | Ubuntu 服务器 |

仓库只保存可审查、可复现构建的源码。以下内容不进入 Git：浏览器实例目录、Cookie、登录状态、
本地 `config.yaml`、数据库、日志、代理/Chrome 运行时、授权私钥、密码文件以及编译后的 EXE。
Windows 安装包和便携版应通过 GitHub Release 附件发布。

## Windows 程序位置速查

克隆仓库后，假设项目目录为 `C:\laogu-ai-agent`：

| 要找的内容 | 仓库源码路径 | 构建后运行文件 |
|---|---|---|
| Laogu Browser | `C:\laogu-ai-agent\browser\` | `C:\laogu-ai-agent\browser\build\bin\Laogu-Browser.exe` |
| 桌面外部控制台 | `C:\laogu-ai-agent\desktop\` | `C:\laogu-ai-agent\dist\Laogu-Desktop\Laogu-Desktop.exe` |
| 控制台配置模板 | `C:\laogu-ai-agent\packaging\windows\laogu.env.example` | `dist\Laogu-Desktop\config\laogu.env.example` |
| 构建脚本 | `C:\laogu-ai-agent\packaging\windows\build_portable.ps1` | — |

注意：`browser\build\bin\` 和 `dist\` 是本机生成目录，默认被 Git 忽略；如果刚克隆完仓库找不到
`.exe`，先按照下面的构建命令生成。发布给其他电脑时，从 GitHub Release 下载便携包，而不是在源码目录
里寻找未提交的 EXE。

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

## 更换服务器和域名

如果只更换 IP、域名不变，只需修改 DNS 并在新服务器配置 HTTPS，Windows 程序无需重新编译。
如果域名也变更：

- Browser 修改安装目录 `config.yaml` 的 `license.server_url`；
- 桌面控制台修改 `dist\Laogu-Desktop\config\laogu.env` 的 `LAOGU_SERVER_URL`；
- Windows Agent 使用桌面控制台同一个 `LAOGU_SERVER_URL`，DPAPI 加密凭据不要手工改成明文。

旧服务器必须保留数据库、`/etc/laogu/server.env`、授权签发私钥/密码、systemd 和 Nginx 配置。
在旧服务器执行 `sudo bash /opt/laogu-ai-agent/deploy/ubuntu/create-server-migration-bundle.sh`，
会生成 `/var/backups/laogu/server-migration-时间/` 下的受限迁移包。该包包含生产密钥，禁止提交 GitHub。
完整路径和恢复顺序见 [更换服务器迁移说明](deploy/ubuntu/SERVER_MIGRATION_ZH_CN.md)。

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

确认登录和数据正常后，重新绑定 Telegram 备份（必须绑定私人管理员用户 ID）：

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/install-backup.sh
sudo systemctl start laogu-backup.service
sudo journalctl -u laogu-backup.service -n 80 --no-pager
```

最后删除备用服务器上的临时私钥和恢复包，Windows 中的私钥原件继续离线保存。

## 灾备

- 每日使用 age 公钥加密 PostgreSQL、精简源码、应用数据和生产配置；
- 加密恢复包上传 Telegram；
- Telegram 目标必须是配置管理员用户 ID 对应的私人聊天，拒绝群组/频道；
- 本机自动备份只保留最近两份，第三份及更旧自动删除；
- Telegram 聊天记录不会由服务器自动删除；
- 每周检查磁盘、服务、数据库和备份状态；
- 私钥不提交 GitHub，也不包含在恢复包中。

### 查找 age 备份公钥

安装自动备份后，服务器会把当前加密接收者公钥保存为：

```bash
sudo cat /etc/laogu/backup-age-recipient.txt
```

正常输出是一行以 `age1` 开头的字符串。这一行就是可以交给
`install-backup.sh` 使用的公钥，不是恢复私钥。

如果该文件不存在，可以搜索常见位置：

```bash
sudo find /etc/laogu /root/restore /var/backups/laogu \
  -type f -name "backup-age-recipient.txt" -print 2>/dev/null
```

如果只有恢复私钥（例如 `/root/restore/laogu-backup-recovery.key`），可以从私钥推导公钥：

```bash
sudo age-keygen -y /root/restore/laogu-backup-recovery.key
```

命令输出的 `age1...` 行就是公钥。公钥可以写回服务器的
`/etc/laogu/backup-age-recipient.txt`；`age` 私钥必须离线保存，不能发送给助手、提交 GitHub
或放进 Telegram 备份。仓库只包含查找和备份脚本，不保存任何真实密钥。

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
