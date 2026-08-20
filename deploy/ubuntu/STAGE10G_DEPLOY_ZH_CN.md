# Laogu 10G 部署说明：Web 运维监控

版本：Server 保持 `0.16.0`，Web `0.16.0`，Windows Agent 保持 `0.9.0`，数据库迁移保持 `0011_credential_capabilities`。

本阶段只更新 Web 管理后台：新增仅 ADMIN 可见的“运维监控”页面，展示服务版本、运行时长、数据库可达性、Agent 在线状态、Command 状态、过期租约、WebSocket 和 HTTP Pull fallback。页面不显示密码、Token、Cookie 或 API Key。

## 安装

先备份 `/opt/laogu-ai-agent`、数据库和当前 `web/dist`，校验更新包 SHA256 后解压到项目目录。然后：

```bash
sudo chown -R laogu:laogu /opt/laogu-ai-agent/web
sudo -u laogu bash -c '
cd /opt/laogu-ai-agent/web
npm ci --no-audit --no-fund
npm run build
'
```

本阶段不需要停止或重启 Server，不执行 Alembic migration。Nginx 直接从 `web/dist` 提供新页面。

## 验收

```bash
curl -fsSI https://api.jaycwl.org/ | sed -n '1,8p'
find /opt/laogu-ai-agent/web/dist/assets -maxdepth 1 -type f -name 'ops_metrics-*.js' -printf '%f %s bytes\n'
sudo systemctl is-active laogu-server nginx
curl -fsS http://127.0.0.1:8000/api/health; echo
```

管理员登录后台后，左侧出现“运维监控”，打开后应显示数据库正常、Agent `1/1`、过期租约 `0`、WebSocket 正常和 HTTP Pull fallback 启用。OWNER/MEMBER 不显示入口且接口仍返回 `403`。
