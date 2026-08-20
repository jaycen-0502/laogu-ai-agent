# 更换服务器迁移说明

本文说明更换 Ubuntu 服务器时，如何保持 Browser、桌面控制台、Windows Agent 和远程授权继续使用。

## 程序与地址对应关系

| 程序 | 配置文件 | 配置项 | 说明 |
|---|---|---|---|
| Laogu Browser | Browser 安装目录 `config.yaml` | `license.server_url` | 远程授权服务器地址；环境变量 `LAOGU_LICENSE_SERVER_URL` 优先级更高 |
| Laogu Desktop Control Center | `dist\Laogu-Desktop\config\laogu.env` | `LAOGU_SERVER_URL` | Ubuntu 协调服务器地址；控制台本地连接 Browser 使用 `LAOGU_BASE_URL=http://127.0.0.1:19876` |
| Windows Agent | 与桌面控制台相同的 `config\laogu.env` | `LAOGU_SERVER_URL` | 不要把 Agent Token 写入配置文件，Token 由 Windows DPAPI 加密保存 |
| Ubuntu 服务端 | `/etc/laogu/server.env` | `LAOGU_LICENSE_ISSUER_PUBLIC_KEY` 等 | 服务端数据库和授权签发配置 |

### 只更换 IP、域名不变

只需要把域名 DNS A/AAAA 记录指向新服务器，并在新服务器重新配置 HTTPS。Windows 程序中的地址无需修改，
也无需重新编译 Browser 或桌面控制台。

### 域名也更换

1. 在桌面控制台的 `config\laogu.env` 修改 `LAOGU_SERVER_URL=https://新域名`。
2. 在 Browser 的 `config.yaml` 修改 `license.server_url: "https://新域名"`。
3. 重启桌面控制台、Windows Agent 和 Browser。
4. 新服务器申请匹配新域名的 HTTPS 证书。

不要直接把 HTTPS 地址改成裸 IP；证书通常不包含 IP，会导致 TLS 校验失败。临时测试也应使用有效证书或内部 DNS。

## 必须保留的服务端资料

以下文件不在 GitHub 仓库中，位于生产 Ubuntu 服务器：

| 内容 | 当前路径 | 丢失后的影响 |
|---|---|---|
| PostgreSQL 数据库 | 通过 `pg_dump -Fc -d laogu` 导出 | 用户、Agent、授权、设备和审计记录丢失 |
| 生产环境变量 | `/etc/laogu/server.env` | 数据库连接、JWT、授权公钥等配置丢失 |
| 授权签发私钥 | `/etc/laogu/license/Laogu-License-Issuer.pem` | 不能继续签发与现有公钥匹配的激活码 |
| 私钥密码 | `/etc/laogu/license/Laogu-License-Password.txt` | 服务端无法读取签发私钥 |
| systemd 服务 | `/etc/systemd/system/laogu-server.service` | 服务启动参数和运行用户丢失 |
| Nginx 配置 | `/etc/nginx/sites-available/laogu-server` | 域名反向代理和安全头配置丢失 |
| HTTPS 证书 | 通常在 `/etc/letsencrypt/` | 可在新服务器重新申请，不建议放入普通迁移包 |

授权私钥、公钥和数据库必须来自同一套生产环境。只复制数据库而不复制授权私钥，或只复制私钥而不复制数据库，
都可能导致旧授权状态与新服务端不一致。

## 在旧服务器生成迁移包

仓库提供收集脚本：

```text
/opt/laogu-ai-agent/deploy/ubuntu/create-server-migration-bundle.sh
```

在旧服务器执行：

```bash
sudo bash /opt/laogu-ai-agent/deploy/ubuntu/create-server-migration-bundle.sh
```

脚本会在以下目录生成权限为 `700` 的迁移目录，并生成权限为 `600` 的压缩包：

```text
/var/backups/laogu/server-migration-YYYY-MM-DD-HHMMSS/
└── laogu-server-migration-YYYY-MM-DD-HHMMSS.tar.gz
```

迁移包包含数据库导出、`server.env`、systemd/Nginx 配置、授权私钥、私钥密码、源代码提交号和校验清单。
它是高敏感文件，不能上传 GitHub、网盘或发给其他人；应通过加密磁盘或加密传输保存。

## 新服务器恢复原则

1. 先从 GitHub 私有仓库部署相同或兼容版本的源码。
2. 将迁移包只上传到新服务器的受限目录，例如 `/root/restore/`。
3. 恢复数据库、`/etc/laogu/server.env` 和 `/etc/laogu/license/` 文件。
4. 如果域名变更，更新 Nginx 并重新申请 HTTPS 证书。
5. 执行 Alembic 迁移、启动服务和 `/api/health/ready` 检查。
6. 确认授权状态、Agent 心跳和 Browser `/api/license/check` 正常后，再切换 DNS。

如果没有迁移旧数据库，Windows Agent 需要重新注册；如果没有迁移授权私钥，必须使用新密钥重新签发激活码。

## Windows 端迁移后检查

```powershell
Get-Content .\config\laogu.env
Test-NetConnection 新域名 -Port 443
```

确认 `LAOGU_SERVER_URL` 使用 `https://`，并且 Browser 的 `config.yaml` 中 `license.server_url` 指向同一个地址。
