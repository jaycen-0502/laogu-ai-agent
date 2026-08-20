# Laogu 10E 部署说明：生产安全加固与故障演练

版本：Server `0.15.0`，Windows Agent 保持 `0.9.0`，数据库迁移头不变（`0011_credential_capabilities`）。本阶段没有 Alembic 迁移，也不读取、保存或返回 Cookie Value、Agent Token、JWT、密码或 AI API Key。

## 一、上传前检查

在 Windows PowerShell 中执行：

```powershell
$Package = "C:\Users\Administrator\Desktop\stage10e-update.tar.gz"
Test-Path $Package
Get-FileHash $Package -Algorithm SHA256
tar -tzf $Package
```

清单应只包含 `server/`、`tests/` 和 `deploy/ubuntu/STAGE10E_DEPLOY_ZH_CN.md` 下的本阶段文件。将压缩包上传到服务器 `/tmp/stage10e-update.tar.gz`，不要覆盖生产目录中的 `.env`、数据库或图片目录。

## 二、服务器备份

```bash
cd /opt/laogu-ai-agent
set -euo pipefail

BACKUP="/var/backups/laogu/stage10e-$(date +%F-%H%M%S)"
sudo mkdir -p "$BACKUP"
sudo -u postgres pg_dump -Fc -d laogu > "$BACKUP/laogu-before-stage10e.dump"
sudo tar -czf "$BACKUP/source-before-stage10e.tar.gz" \
  --exclude='web/node_modules' --exclude='.venv' /opt/laogu-ai-agent
sudo cp /etc/laogu/server.env "$BACKUP/server-before-stage10e.env"
sudo cp /etc/systemd/system/laogu-server.service "$BACKUP/laogu-server-before-stage10e.service"
sudo cp -a /etc/systemd/system/laogu-server.service.d "$BACKUP/" 2>/dev/null || true
sudo cp /etc/nginx/sites-available/laogu-server "$BACKUP/nginx-before-stage10e"
sudo ls -lh "$BACKUP"
echo "BACKUP=$BACKUP"
```

`tar: Removing leading '/' from member names` 是正常提示。若 `server.env` 实际位于其他路径，只需调整该条 `cp`，不要把文件内容贴到聊天中。

## 三、校验和安全解压检查

```bash
sha256sum /tmp/stage10e-update.tar.gz
tar -tzf /tmp/stage10e-update.tar.gz

REVIEW="$(mktemp -d /tmp/stage10e-review.XXXXXX)"
tar -xzf /tmp/stage10e-update.tar.gz -C "$REVIEW"
find "$REVIEW" -type f -print0 | xargs -0 file
find "$REVIEW" -type f \( -name '*.so' -o -name '*.exe' -o -name '*.dll' \) -print
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -m compileall -q \
  "$REVIEW/server" "$REVIEW/tests"
echo "安全检查通过：$REVIEW"
```

检查结果中不应出现危险绝对路径、软链接、可执行文件或 `*.env`。确认无误后继续。

## 四、安装 10E 服务端文件

```bash
cd /opt/laogu-ai-agent
sudo systemctl stop laogu-server
sudo tar --no-same-owner -xzf /tmp/stage10e-update.tar.gz -C /opt/laogu-ai-agent
sudo chown -R laogu:laogu /opt/laogu-ai-agent/server

sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -m compileall -q server
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -c \
  'import server.main; print("SERVER_IMPORT_OK", server.main.app.version)'
```

预期输出 `SERVER_IMPORT_OK 0.15.0`。本阶段没有迁移，勿执行 `alembic downgrade`；如需读取数据库版本，必须先加载生产环境：

```bash
sudo -u laogu bash -c '
set -a; . /etc/laogu/server.env; set +a
cd /opt/laogu-ai-agent
.venv/bin/alembic current
'
```

## 五、启动与基础检查

```bash
sudo systemctl start laogu-server
sleep 2
sudo systemctl is-active laogu-server
curl -fsS http://127.0.0.1:8000/api/health; echo
curl -fsS http://127.0.0.1:8000/api/health/ready; echo
curl -fsS http://127.0.0.1:8000/openapi.json | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["info"]["version"]); print("/api/admin/security/diagnostics" in d["paths"])'
```

应看到服务 `active`、两个健康检查均为 `{"ok":true}`、版本 `0.15.0`，并确认诊断路由存在。最近错误检查：

```bash
sudo journalctl -u laogu-server --since "10 minutes ago" --no-pager | \
  grep -Ei 'error|exception|traceback|failed' || echo '最近10分钟没有服务错误'
```

## 六、安全诊断接口验收

该接口只接受管理员 JWT，返回脱敏配置状态、数据库可达性、迁移头、Agent 在线计数、WebSocket 路径和 HTTP Pull fallback 状态。接口不会返回任何 Secret Value。

```bash
read -r -p "管理员用户名: " API_USER
read -r -s -p "管理员密码: " API_PASSWORD; echo
LOGIN_RESPONSE=$(curl -fsS -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$API_USER\",\"password\":\"$API_PASSWORD\"}")
unset API_PASSWORD
ACCESS_TOKEN=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<< "$LOGIN_RESPONSE")
curl -fsS http://127.0.0.1:8000/api/admin/security/diagnostics \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool
unset ACCESS_TOKEN LOGIN_RESPONSE
```

正常生产服务器的 `ok` 应为 `true`；若为 `false`，只根据 `failures` 和布尔状态修复配置，不打印环境文件。普通 MEMBER 请求应返回 `403`。

## 七、故障演练（不改生产数据）

1. **Agent 离线观测**：关闭 Windows Agent 约 2 分钟，再调用诊断接口，`agents.offline` 应增加；恢复 Agent 后心跳恢复为 `ONLINE`。
2. **Command 租约恢复**：创建一个测试 Command 后不 ACK，使用 `GET /api/commands/metrics` 查看 `stale_delivered`；超过 60 秒后重新 Pull/WebSocket 连接，应再次投递。不要使用真实业务任务。
3. **WebSocket fallback**：临时停止 Agent 的 WebSocket 客户端，HTTP Pull 仍应能够领取并完成测试 Command；恢复连接后继续使用 WebSocket。不要修改 Browser/CDP。
4. **备份完整性**：

```bash
sudo pg_restore --list "$BACKUP/laogu-before-stage10e.dump" | head -20
sudo tar -tzf "$BACKUP/source-before-stage10e.tar.gz" | head -20
```

5. **越权检查**：MEMBER 访问诊断接口必须 `403`；诊断响应、Command 结果和 Task 结果中不得出现 Bearer Token、Cookie Value、Password 或 API Key 原文。

## 八、完成备份

```bash
FINAL_BACKUP="/var/backups/laogu/stage10e-final-$(date +%F-%H%M%S)"
sudo mkdir -p "$FINAL_BACKUP"
sudo -u postgres pg_dump -Fc -d laogu > "$FINAL_BACKUP/laogu-after-stage10e.dump"
sudo tar -czf "$FINAL_BACKUP/source-after-stage10e.tar.gz" \
  --exclude='web/node_modules' --exclude='.venv' /opt/laogu-ai-agent
sudo cp /etc/laogu/server.env "$FINAL_BACKUP/server-after-stage10e.env"
sudo cp /etc/systemd/system/laogu-server.service "$FINAL_BACKUP/laogu-server-after-stage10e.service"
sudo cp -a /etc/systemd/system/laogu-server.service.d "$FINAL_BACKUP/" 2>/dev/null || true
sudo cp /etc/nginx/sites-available/laogu-server "$FINAL_BACKUP/nginx-after-stage10e"
sudo cp /tmp/stage10e-update.tar.gz "$FINAL_BACKUP/"
sudo ls -lh "$FINAL_BACKUP"
echo "FINAL_BACKUP=$FINAL_BACKUP"
```

回滚时停止服务，从 `source-before-stage10e.tar.gz` 恢复代码并恢复 Nginx/systemd 配置；本阶段无数据库迁移，因此不需要回滚数据库版本。
