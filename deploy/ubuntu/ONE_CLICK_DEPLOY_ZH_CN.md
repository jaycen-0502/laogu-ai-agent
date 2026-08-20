# 老谷系统 0.18.0：全新服务器一键部署与灾备恢复

本文面向第一次管理 Linux 服务器的用户。生产域名默认以
`api.jaycwl.org` 举例。

## 一、部署前准备

- 一台全新的 Ubuntu 24.04 LTS 服务器；建议 2 vCPU、4 GB 内存、80 GB SSD。
- 云安全组开放 22、80、443；不要开放 5432 和 8000。
- GitHub 私有仓库的读取权限。
- 一个域名，A 记录已经指向新服务器公网 IP。
- 一个用于申请 HTTPS 证书的邮箱。
- 首次安装时准备工作区名称、管理员用户名和至少 12 位密码。

灾备恢复还需要 Telegram 下载的 `*.tar.gz.age`、对应的 `*.sha256`（如果有），
以及与备份公钥配对的 `laogu-backup-recovery.key` 私钥。

## 二、全新安装

### 1. 登录服务器

在 Windows PowerShell 执行：

```powershell
ssh root@服务器公网IP
```

### 2. 克隆私有仓库

推荐给服务器配置 GitHub 只读 Deploy Key。完成后执行：

```bash
apt update
apt install -y git
git clone git@github.com:jaycen-0502/laogu-ai-agent.git /opt/laogu-ai-agent
cd /opt/laogu-ai-agent
git checkout v0.18.0
```

### 3. 执行一键安装

```bash
cd /opt/laogu-ai-agent
sudo bash deploy/ubuntu/install.sh --domain api.jaycwl.org
```

根据提示输入 Let's Encrypt 邮箱、初始工作区名称、管理员用户名和至少 12 位管理员密码。
脚本会自动完成系统软件、UFW、PostgreSQL、Python、Alembic、React 前端、systemd、
Nginx、HTTPS、初始管理员和最终验收。

脚本拒绝覆盖已有数据库或 `/etc/laogu/server.env`，不能用于升级。

### 4. 安装加密自动备份

准备 age 公钥、Telegram Bot Token 和 Chat ID：

```bash
cd /opt/laogu-ai-agent
sudo bash deploy/ubuntu/install-backup.sh
sudo systemctl start laogu-backup.service
sudo journalctl -u laogu-backup.service -n 80 --no-pager
```

看到 `BACKUP_OK` 且 Telegram 收到文件，才算备份通过。

## 三、日常验收

```bash
cd /opt/laogu-ai-agent
sudo bash deploy/ubuntu/verify.sh api.jaycwl.org
```

应看到后端、Nginx、PostgreSQL 为 `active`，健康检查返回 `{"ok":true}`，API 版本为
`0.18.0`，迁移为 `0013_remote_licenses (head)`，且 Nginx 检查通过。

## 四、服务器故障后的灾备恢复

灾备恢复只能在全新的 Ubuntu 24.04 服务器运行，脚本发现已有系统会拒绝覆盖。

### 1. 上传恢复材料

Windows PowerShell：

```powershell
ssh root@新服务器IP "install -d -m 700 /root/restore"
scp C:\Users\Administrator\Desktop\laogu-backup-recovery.key root@新服务器IP:/root/restore/
scp C:\下载目录\laogu-recovery-时间.tar.gz.age root@新服务器IP:/root/restore/
scp C:\下载目录\laogu-recovery-时间.tar.gz.age.sha256 root@新服务器IP:/root/restore/
```

把 `restore.sh` 放到新服务器后，先把 DNS A 记录指向新服务器公网 IP。

### 2. 执行恢复

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/restore.sh \
  --domain api.jaycwl.org \
  --email 你的证书邮箱 \
  --package /root/restore/laogu-recovery-时间.tar.gz.age \
  --checksum /root/restore/laogu-recovery-时间.tar.gz.age.sha256 \
  --key /root/restore/laogu-backup-recovery.key
```

脚本完成外部校验、age 解密、内部校验、源码和配置恢复、PostgreSQL `pg_restore`、
前后端依赖、systemd、Nginx、HTTPS 和验收。旧格式备份如果没有
`application-data.tar.gz`，脚本会兼容恢复。

### 3. 恢复后清理

确认网站正常后删除临时私钥、加密包和校验文件，然后重新执行 `install-backup.sh`，
因为 Telegram Bot Token 不在恢复包里：

```bash
rm -f -- /root/restore/laogu-backup-recovery.key \
  /root/restore/laogu-recovery-时间.tar.gz.age \
  /root/restore/laogu-recovery-时间.tar.gz.age.sha256
```

## 五、GitHub 安全规则

仓库必须保持 Private。以下内容永远不能提交：`server.env`、`backup.env`、age 私钥、
TLS 私钥、数据库密码、JWT 密钥、Telegram Token、`agent_data/`、`logs/`、`data/`、
`*.db`、`.env`、`node_modules/`、`.venv/`、构建目录和历史升级压缩包。

代码使用正式标签部署，例如 `v0.18.0`；生产服务器不要直接跟随未验收的开发分支。
