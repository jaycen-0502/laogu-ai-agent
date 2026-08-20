# Stage 12：网页远程授权管理

本阶段增加管理员网页的“远程授权”页面，提供授权列表、有效期、设备数量、在线检查记录和撤销操作。

安全边界：网页不会显示完整 LGACT1 激活码、安装公钥或签名私钥；设备 ID 和 IP 只显示脱敏结果。现有授权终端离线激活流程保持不变。

## 安装前备份

```bash
cd /opt/laogu-ai-agent
set -euo pipefail
BACKUP="/var/backups/laogu/stage12-before-$(date +%F-%H%M%S)"
sudo mkdir -p "$BACKUP"
sudo -u postgres pg_dump -Fc -d laogu > "$BACKUP/laogu-before-stage12.dump"
sudo tar -czf "$BACKUP/source-before-stage12.tar.gz" --exclude='web/node_modules' --exclude='.venv' /opt/laogu-ai-agent
sudo cp /etc/laogu/server.env "$BACKUP/server-before-stage12.env"
sudo cp /etc/nginx/sites-available/laogu-server "$BACKUP/nginx-before-stage12"
echo "BACKUP=$BACKUP"
```

## 安装更新

```bash
cd /opt/laogu-ai-agent
sha256sum /tmp/stage12-license-admin-*.tar.gz
sudo systemctl stop laogu-server
sudo tar -xzf /tmp/stage12-license-admin-*.tar.gz -C /opt/laogu-ai-agent --strip-components=1
sudo chown -R laogu:laogu /opt/laogu-ai-agent/server /opt/laogu-ai-agent/web
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent && .venv/bin/python -m pytest tests/test_remote_license.py -q'
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent/web && npm ci && npm run build'
sudo systemctl start laogu-server
sudo systemctl is-active laogu-server
sudo systemctl is-active nginx
curl -fsS http://127.0.0.1:8000/api/health; echo
curl -fsS http://127.0.0.1:8000/api/health/ready; echo
```

## 验证网页

使用管理员账号登录 `https://你的域名/`，左侧应出现“远程授权”。打开后可查看授权、设备和检查记录；点击“撤销”会要求确认并记录原因。

```bash
curl -fsS https://你的域名/assets/licenses-*.js -I | sed -n '1,10p'
sudo journalctl -u laogu-server --since "5 minutes ago" --no-pager | grep -Ei "error|exception|traceback|failed" || echo "最近5分钟没有服务错误"
```

本阶段无数据库迁移。续期和签发仍使用现有授权终端，下一阶段再接入“网页申请—授权终端签名—浏览器自动激活”。
