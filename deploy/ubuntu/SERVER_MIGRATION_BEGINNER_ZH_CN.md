# 老谷系统更换服务器：小白一键迁移教程

适用版本：`v0.21.16` 及当前配套的 `deploy/ubuntu/restore.sh`。

本文按“旧服务器仍能登录、域名继续使用 `api.jaycwl.org`、新服务器为全新 Ubuntu 24.04 LTS”编写。
完成后，Windows 控制中心、Windows Agent 和 Laogu Browser 仍连接原域名，通常不需要重新打包、重新注册或修改配置。

## 先看结论

迁移实际分为四件事：

1. 在旧服务器生成最后一份加密灾备包。
2. 把灾备包、解密私钥和授权签发密钥安全传到新服务器。
3. 把 `api.jaycwl.org` 的 DNS 指向新服务器。
4. 在新服务器运行一次 `restore.sh`，然后检查网站、Agent 和 Browser。

正式切换时建议预留 30-60 分钟维护时间。旧服务器不要立即删除，至少保留 3-7 天作为回退。

## 一、你需要提前准备什么

### 1. 需要知道的信息

请先记在自己的本地记事本中，不要发到聊天或 GitHub：

| 名称 | 示例 |
|---|---|
| 旧服务器 IP | `1.2.3.4` |
| 新服务器 IP | `5.6.7.8` |
| 生产域名 | `api.jaycwl.org` |
| HTTPS 证书邮箱 | 你自己的有效邮箱 |
| 新服务器登录用户 | 通常为 `root` |

### 2. 新服务器要求

- 全新的 Ubuntu 24.04 LTS。
- 建议至少 2 核 CPU、4 GB 内存、80 GB SSD。
- 云厂商安全组开放 TCP `22`、`80`、`443`。
- 不要向公网开放 PostgreSQL `5432` 或后端端口 `8000`。
- 新服务器中不能已经安装另一套老谷系统；恢复脚本会拒绝覆盖现有系统。

### 3. Windows 建立临时迁移目录

在 Windows PowerShell 执行：

```powershell
New-Item -ItemType Directory -Force C:\Laogu-Migration
```

这个目录会暂时保存高敏感材料。迁移完成后应把长期保留的恢复私钥移到加密 U 盘或离线密码库，并删除临时副本。

## 二、迁移材料说明

必须准备：

| 文件 | 用途 |
|---|---|
| `laogu-recovery-时间.tar.gz.age` | 加密的数据库、源码、应用数据和服务器环境配置 |
| `laogu-recovery-时间.tar.gz.age.sha256` | 检查传输过程中是否损坏 |
| `laogu-backup-recovery.key` | 解密灾备包的 age 私钥 |

如果系统启用了在线签发或续期授权，还必须带上：

| 文件 | 默认旧服务器路径 |
|---|---|
| 授权签发私钥 | `/etc/laogu/license/Laogu-License-Issuer.pem` |
| 私钥密码文件 | `/etc/laogu/license/Laogu-License-Password.txt` |

重要：当前 `laogu-backup.service` 生成的灾备包不会自动包含上述两个授权签发文件。因此，完整迁移时必须单独安全复制它们。缺少它们可能不影响普通页面登录，但会导致在线签发或续期授权不可用。

以下文件不能上传 GitHub、普通网盘、聊天工具或交给其他人：

- `laogu-backup-recovery.key`
- `Laogu-License-Issuer.pem`
- `Laogu-License-Password.txt`
- `server.env`、数据库备份、Telegram Bot Token

## 三、迁移前先检查旧服务器

### 第 1 步：登录旧服务器

在 Windows PowerShell 执行，把 `旧服务器IP` 换成真实 IP：

```powershell
ssh root@旧服务器IP
```

### 第 2 步：确认服务正常

在旧服务器执行：

```bash
sudo systemctl is-active laogu-server postgresql nginx
curl -fsS http://127.0.0.1:8000/api/health/ready
```

预期看到三个 `active`，健康检查没有报错。如果此时旧服务器本身就不正常，应先查明问题，不要直接迁移故障状态。

### 第 3 步：确认恢复私钥还在手中

`laogu-backup-recovery.key` 通常应离线保存在管理员电脑或加密 U 盘，不应长期放在服务器上。

如果只有私钥、不确定它是否与服务器备份公钥配对，可以在安装了 `age` 的 Linux 环境执行：

```bash
age-keygen -y laogu-backup-recovery.key
sudo cat /etc/laogu/backup-age-recipient.txt
```

两条命令显示的 `age1...` 公钥必须相同。

### 第 4 步：确认授权签发文件

在旧服务器执行：

```bash
sudo test -r /etc/laogu/license/Laogu-License-Issuer.pem && echo "签发私钥存在"
sudo test -r /etc/laogu/license/Laogu-License-Password.txt && echo "密码文件存在"
sudo grep -E '^LAOGU_LICENSE_ISSUER_(PRIVATE_KEY_FILE|KEY_PASSWORD_FILE)=' /etc/laogu/server.env
```

如果 `server.env` 显示的是其他路径，后续复制和安装时要使用实际路径。本文其余命令按默认路径编写。

## 四、降低 DNS 切换影响

提前在 Cloudflare 中打开 `api.jaycwl.org` 的 DNS 记录，把 TTL 调低到 `300` 秒或“自动”。

此时先不要切换 IP，旧服务器继续提供服务。确认以下事项：

- 记下当前旧服务器 IP，方便回退。
- 如果存在 `AAAA` IPv6 记录，而新服务器没有配置 IPv6，应在切换时删除或更新它。
- 正式恢复期间建议临时把 Cloudflare 代理改为“仅 DNS（灰色云）”，减少 HTTPS 证书验证干扰。

## 五、生成最后一份一致的灾备包

这一步开始后进入短暂停机维护。这样可以避免“备份完成以后旧服务器又产生新数据”，造成新旧数据库不一致。

### 第 1 步：停止旧服务器应用写入

在旧服务器执行：

```bash
sudo systemctl stop laogu-server
sudo systemctl is-active postgresql
```

PostgreSQL 应继续显示 `active`。此时网页和 Agent 暂时不能正常连接，这是预期现象。

### 第 2 步：生成最新备份

```bash
sudo systemctl start laogu-backup.service
sudo journalctl -u laogu-backup.service -n 100 --no-pager
```

日志最后应出现：

```text
BACKUP_OK /var/backups/laogu-auto/laogu-recovery-时间.tar.gz.age
```

查看最新文件名：

```bash
sudo ls -lht /var/backups/laogu-auto/
```

必须确认同一时间戳的 `.age` 和 `.age.sha256` 两个文件都存在。

如果备份失败，先执行下面的命令恢复旧服务，然后排查备份问题：

```bash
sudo systemctl start laogu-server
```

## 六、把迁移材料下载到 Windows

先在旧服务器记下刚生成的准确文件名，然后退出 SSH：

```bash
exit
```

在 Windows PowerShell 执行。把示例时间和旧服务器 IP 换成真实值：

```powershell
scp root@旧服务器IP:/var/backups/laogu-auto/laogu-recovery-20260828-120000.tar.gz.age C:\Laogu-Migration\
scp root@旧服务器IP:/var/backups/laogu-auto/laogu-recovery-20260828-120000.tar.gz.age.sha256 C:\Laogu-Migration\
scp root@旧服务器IP:/etc/laogu/license/Laogu-License-Issuer.pem C:\Laogu-Migration\
scp root@旧服务器IP:/etc/laogu/license/Laogu-License-Password.txt C:\Laogu-Migration\
```

再把你离线保存的 `laogu-backup-recovery.key` 复制到：

```text
C:\Laogu-Migration\laogu-backup-recovery.key
```

检查目录：

```powershell
Get-ChildItem C:\Laogu-Migration
```

应至少看到 5 个文件：灾备包、SHA256、age 私钥和两个授权签发文件。

## 七、把材料上传到新服务器

在 Windows PowerShell 执行：

```powershell
ssh root@新服务器IP "install -d -m 700 /root/restore"
scp C:\Laogu-Migration\* root@新服务器IP:/root/restore/
```

登录新服务器检查：

```powershell
ssh root@新服务器IP
```

```bash
ls -lah /root/restore
chmod 600 /root/restore/*
```

## 八、在新服务器准备授权签发密钥

如果旧系统使用在线授权签发，请在运行恢复脚本之前执行本节。这样恢复脚本第一次启动后端时就能正确读取密钥。

```bash
getent group laogu >/dev/null || groupadd --system laogu
id laogu >/dev/null 2>&1 || useradd --system --gid laogu --home-dir /opt/laogu-ai-agent --shell /usr/sbin/nologin laogu
install -d -o root -g laogu -m 750 /etc/laogu/license
install -o root -g laogu -m 640 /root/restore/Laogu-License-Issuer.pem /etc/laogu/license/Laogu-License-Issuer.pem
install -o root -g laogu -m 640 /root/restore/Laogu-License-Password.txt /etc/laogu/license/Laogu-License-Password.txt
```

检查服务用户能否读取：

```bash
sudo -u laogu test -r /etc/laogu/license/Laogu-License-Issuer.pem && echo "签发私钥权限正确"
sudo -u laogu test -r /etc/laogu/license/Laogu-License-Password.txt && echo "密码文件权限正确"
```

两行都应显示“权限正确”。如果旧服务器使用自定义路径，应按 `server.env` 中的真实路径安装，而不是机械照抄默认路径。

## 九、获取正式恢复脚本

仓库当前为公开仓库时，直接执行：

```bash
apt-get update
apt-get install -y git ca-certificates
git clone https://github.com/jaycen-0502/laogu-ai-agent.git /opt/laogu-ai-agent
cd /opt/laogu-ai-agent
git checkout v0.21.16
```

看到 `HEAD is now at ...` 属于正常现象。

如果以后仓库改为私有仓库，需要先给新服务器配置只读 Deploy Key，再使用 SSH 地址克隆；不要把有写权限的个人 GitHub Token 写进命令历史。

## 十、切换 Cloudflare DNS

现在把 Cloudflare 中 `api.jaycwl.org` 的 A 记录从旧 IP 改成新服务器 IP。

暂时使用：

- 代理状态：仅 DNS（灰色云）。
- A 记录：新服务器公网 IPv4。
- AAAA 记录：只有新服务器确实配置 IPv6 时才保留。

在 Windows PowerShell 检查：

```powershell
Resolve-DnsName api.jaycwl.org
```

结果应显示新服务器 IP。不同网络的 DNS 缓存刷新速度不同，必要时等待几分钟。

## 十一、执行一键恢复

仍在新服务器 SSH 窗口中执行。把邮箱和灾备包文件名替换成真实值：

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/restore.sh \
  --domain api.jaycwl.org \
  --email your-email@example.com \
  --package /root/restore/laogu-recovery-20260828-120000.tar.gz.age \
  --checksum /root/restore/laogu-recovery-20260828-120000.tar.gz.age.sha256 \
  --key /root/restore/laogu-backup-recovery.key
```

脚本显示恢复摘要后，会要求输入：

```text
RESTORE
```

必须大写输入。脚本随后自动完成：

- 校验和解密灾备包。
- 恢复源码、`data/` 和 `/etc/laogu/server.env`。
- 安装并恢复 PostgreSQL 数据库。
- 安装 Python、Node.js、Nginx 等依赖。
- 执行 Alembic 数据库升级和 Web 前端构建。
- 安装并启动 `laogu-server`。
- 配置防火墙、Nginx 和 HTTPS 证书。
- 执行最终健康检查。

运行期间不要关闭 SSH 窗口，不要重复运行脚本。根据服务器性能和网络速度，通常需要数分钟到二十多分钟。

## 十二、恢复后的验收

### 1. 新服务器基础验收

```bash
sudo systemctl is-active laogu-server nginx postgresql
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/ready
curl -fsS https://api.jaycwl.org/api/health
curl -fsS https://api.jaycwl.org/api/health/ready
```

三个服务应显示 `active`，四次健康检查均不应报错。

也可以执行项目自带验收：

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/verify.sh api.jaycwl.org
```

### 2. 网页功能验收

使用浏览器打开：

```text
https://api.jaycwl.org
```

逐项确认：

- 原管理员账号可以登录。
- 原用户、工作区、邀请记录仍存在。
- Agent、Profile、账号和任务配置仍存在。
- 用户停用、授权撤销和设备绑定状态正确。
- AI Provider 配置仍能读取和解密。
- 自动化脚本列表和默认引擎能够读取。

### 3. Windows 控制中心与 Agent 验收

如果域名仍是 `api.jaycwl.org`，Windows 端通常不需要修改。

在 Windows PowerShell 执行：

```powershell
Test-NetConnection api.jaycwl.org -Port 443
```

确认 `TcpTestSucceeded : True`，然后启动控制中心检查：

- 原 Agent ID 和 Token 能继续心跳。
- 控制中心可以读取服务器状态。
- Browser 已登录账号状态能正常读取。
- 启动、停止和紧急停止功能正常。
- 日志、今日点赞、关注、评论等统计能够更新。

### 4. Browser 授权验收

确认 Browser 的 `config.yaml` 仍包含：

```yaml
license:
  server_url: "https://api.jaycwl.org"
```

测试一次授权检查、续期或管理员签发功能。如果在线签发失败，优先检查 `/etc/laogu/license/` 两个文件及其权限。

## 十三、重新安装自动备份

数据库和应用已经恢复，但 Telegram Bot Token 不在灾备包内。需要重新绑定 Telegram：

```bash
cd /opt/laogu-ai-agent
sudo bash deploy/ubuntu/install-backup.sh
sudo systemctl start laogu-backup.service
sudo journalctl -u laogu-backup.service -n 100 --no-pager
```

安装时准备：

- age 公钥，以 `age1` 开头。
- Telegram Bot Token。
- Telegram 管理员私人用户 ID。

看到 `BACKUP_OK`，并且 Telegram 收到新服务器发来的加密备份，才算迁移全部完成。

## 十四、重新开启 Cloudflare 代理

所有 HTTPS 检查通过后，可以把 Cloudflare 代理从灰色云改回橙色云。

建议 Cloudflare SSL/TLS 模式使用：

```text
Full (strict)
```

不要使用 `Flexible`。开启代理后再次检查：

```powershell
Test-NetConnection api.jaycwl.org -Port 443
```

并在浏览器中重新打开后台网页。

## 十五、成功后如何清理

确认系统稳定后，在新服务器删除上传的临时恢复材料：

```bash
rm -f -- /root/restore/laogu-backup-recovery.key
rm -f -- /root/restore/laogu-recovery-*.tar.gz.age
rm -f -- /root/restore/laogu-recovery-*.tar.gz.age.sha256
rm -f -- /root/restore/Laogu-License-Issuer.pem
rm -f -- /root/restore/Laogu-License-Password.txt
```

注意：这里删除的是新服务器 `/root/restore/` 中的临时副本，不是 `/etc/laogu/license/` 中正在使用的授权文件。

Windows 的 `C:\Laogu-Migration` 也不能长期明文保留：

- 把 `laogu-backup-recovery.key` 和授权签发密钥转移到加密 U 盘或可靠的离线密码库。
- 确认离线副本可读取后，删除 `C:\Laogu-Migration` 临时目录。
- 不要清空回收站之前就删除唯一的恢复私钥。

旧服务器至少保留 3-7 天。确认新服务器连续运行稳定、自动备份成功后，再取消旧服务器。

## 十六、迁移失败时怎么回退

如果新服务器无法验收：

1. 停止新服务器应用，防止继续写入新数据库。
2. 把 Cloudflare A 记录改回旧服务器 IP。
3. 在旧服务器重新启动应用。

新服务器执行：

```bash
sudo systemctl stop laogu-server
```

旧服务器执行：

```bash
sudo systemctl start laogu-server
sudo systemctl is-active laogu-server
```

注意：新旧服务器切换后产生的数据不会自动合并。任何时刻只能选择一台服务器作为正式写入端，不能让两台服务器同时长期运行并接收任务。

## 十七、常见问题

### 1. `restore.sh` 提示检测到已有老谷服务

恢复脚本只允许在全新服务器运行。这是防止误覆盖数据库的保护机制。不要删除生产文件后强行重跑，最安全的处理是重装一台干净的 Ubuntu 24.04 服务器再恢复。

### 2. HTTPS 证书申请失败

先检查：

```powershell
Resolve-DnsName api.jaycwl.org
Test-NetConnection api.jaycwl.org -Port 80
```

确认域名已经指向新 IP、安全组开放 80/443、Cloudflare 暂时为灰色云、AAAA 没有指向旧 IPv6。

如果恢复脚本已经恢复数据库，只是最后的证书步骤失败，不要再次运行完整恢复脚本。在新服务器修正 DNS 后执行：

```bash
sudo certbot --nginx -d api.jaycwl.org
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/verify.sh api.jaycwl.org
```

### 3. 后端启动失败

```bash
sudo systemctl status laogu-server --no-pager
sudo journalctl -u laogu-server -n 120 --no-pager
```

重点查看数据库连接、`server.env`、授权签发文件路径和权限错误。不要把包含密码或私钥的完整日志公开发送。

### 4. Windows Agent 不在线

先检查域名和 443 端口，再重启控制中心。域名未改变并且数据库、`server.env` 完整恢复时，通常不需要重新生成 Agent Token。

### 5. 域名也要更换怎么办

除了按本文恢复新服务器，还需要修改 Windows 控制中心 `config\laogu.env`：

```env
LAOGU_SERVER_URL=https://新域名
```

并修改 Browser 的 `config.yaml`：

```yaml
license:
  server_url: "https://新域名"
```

然后重启控制中心、Agent 和 Browser。不要直接使用裸 IP 代替 HTTPS 域名，因为证书通常不匹配。

### 6. 哪个旧迁移脚本不能直接用

`create-server-migration-bundle.sh` 生成的目录结构与当前 `restore.sh` 要求的格式不同，不能把它生成的压缩包直接作为 `restore.sh --package` 输入。

本教程只使用下面这一条配套链路：

```text
laogu-backup.service
  -> laogu-recovery-*.tar.gz.age
  -> deploy/ubuntu/restore.sh
```

## 十八、最终验收清单

以下项目全部打勾后，才算迁移完成：

- [ ] DNS 已指向新服务器。
- [ ] HTTPS 证书有效。
- [ ] `laogu-server`、`nginx`、`postgresql` 都是 `active`。
- [ ] `/api/health` 和 `/api/health/ready` 正常。
- [ ] 原管理员、用户和工作区数据存在。
- [ ] 原 Agent 使用原 Token 可以继续心跳。
- [ ] Browser 授权检查和在线签发正常。
- [ ] 自动化脚本、任务配置和账号映射存在。
- [ ] 控制中心启动、停止、日志和统计正常。
- [ ] Telegram 新备份测试出现 `BACKUP_OK`。
- [ ] Cloudflare 已恢复预期代理状态和 `Full (strict)`。
- [ ] 敏感临时文件已安全清理并保留离线恢复副本。
- [ ] 旧服务器暂未删除，具备短期回退条件。

