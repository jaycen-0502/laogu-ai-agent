# Stage 13：网页在线签发授权

本阶段增加管理员网页在线生成 `LGACT1` 激活码。现有授权终端离线签发继续保留。

## 安全说明

- 只有 `ADMIN` 可以调用在线签发接口。
- 私钥不进入数据库、不进入 Web、不进入更新包。
- 服务器只保存激活码 SHA256 哈希和授权元数据。
- 激活码只在本次 HTTP 响应中返回；请立即复制到对应浏览器，不要写入聊天或日志。
- 如果未安装私钥，网页按钮保持禁用，离线授权终端照常工作。

## 安装前提

服务器必须配置与浏览器内置公钥匹配的加密 Ed25519 私钥和密码文件。请从授权终端管理目录单独上传：

```text
Laogu-License-Issuer.pem
Laogu-License-Password.txt
```

不要把它们放入 `/opt/laogu-ai-agent` 或 Web 目录。推荐目标：

```text
/etc/laogu/license/Laogu-License-Issuer.pem
/etc/laogu/license/Laogu-License-Password.txt
```

## 安装前备份

```bash
cd /opt/laogu-ai-agent
set -euo pipefail
BACKUP="/var/backups/laogu/stage13-before-$(date +%F-%H%M%S)"
sudo mkdir -p "$BACKUP"
sudo -u postgres pg_dump -Fc -d laogu > "$BACKUP/laogu-before-stage13.dump"
sudo tar -czf "$BACKUP/source-before-stage13.tar.gz" --exclude='web/node_modules' --exclude='.venv' /opt/laogu-ai-agent
sudo cp /etc/laogu/server.env "$BACKUP/server-before-stage13.env"
sudo cp /etc/nginx/sites-available/laogu-server "$BACKUP/nginx-before-stage13"
echo "BACKUP=$BACKUP"
```

## 安装更新包

```bash
cd /opt/laogu-ai-agent
set -euo pipefail
PKG="/tmp/stage13-online-license-*.tar.gz"
sudo systemctl stop laogu-server
sudo tar -xzf $PKG -C /opt/laogu-ai-agent --strip-components=1
sudo chown -R laogu:laogu /opt/laogu-ai-agent/server /opt/laogu-ai-agent/web
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent && .venv/bin/python -m py_compile server/config.py server/remote_license_api.py server/schemas.py'
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent && .venv/bin/python -m pytest tests/test_remote_license.py -q' || echo "生产环境未安装 pytest，可跳过"
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent/web && npm ci && npm run build'
```

## 安装私钥文件（单独执行）

```bash
sudo install -d -o laogu -g laogu -m 700 /etc/laogu/license
sudo install -o laogu -g laogu -m 600 /tmp/Laogu-License-Issuer.pem /etc/laogu/license/Laogu-License-Issuer.pem
sudo install -o laogu -g laogu -m 600 /tmp/Laogu-License-Password.txt /etc/laogu/license/Laogu-License-Password.txt
```

把下面三行加入 `/etc/laogu/server.env`（不要把密码写入这份配置）：

```text
LAOGU_LICENSE_ISSUER_PRIVATE_KEY_FILE=/etc/laogu/license/Laogu-License-Issuer.pem
LAOGU_LICENSE_ISSUER_KEY_PASSWORD_FILE=/etc/laogu/license/Laogu-License-Password.txt
LAOGU_RATE_LIMIT_LICENSE_ISSUE=5
```

启动并验证：

```bash
sudo systemctl start laogu-server
sudo systemctl is-active laogu-server
curl -fsS http://127.0.0.1:8000/api/health; echo
curl -fsS http://127.0.0.1:8000/openapi.json | python3 -c 'import json,sys; p=json.load(sys.stdin)["paths"]; print("ISSUE_ROUTE", "/api/license/issue" in p)'
```

登录管理后台，打开“远程授权”。配置成功时“在线生成激活码”按钮可用；未配置时会显示离线模式提示。

## 回滚

停止服务后，从 `stage13-before-*` 备份恢复源码/Web 和 `/etc/laogu/server.env`，再启动服务。不要删除现有数据库备份，也不要删除离线授权终端的私钥备份。
