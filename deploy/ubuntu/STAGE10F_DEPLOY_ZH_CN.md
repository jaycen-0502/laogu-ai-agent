# Laogu 10F 部署说明：运维可观测性与备份校验

版本：Server `0.16.0`，Windows Agent 保持 `0.9.0`，数据库迁移头保持 `0011_credential_capabilities`。本阶段无数据库迁移，不修改 Browser/CDP，不读取或保存凭据。

## 新增内容

- `GET /api/admin/ops/metrics`：仅 ADMIN 可访问，返回服务版本、运行时长、数据库可达性、Agent 在线计数、Command 状态和过期租约计数。
- `scripts/verify_backup.py`：只读检查最终备份文件、源码归档、空文件和危险归档成员。
- WebSocket 和 HTTP Pull fallback 状态继续同时保留。

## 安装与验收

先按 10E 方式备份并校验 `stage10f-update.tar.gz`，再停止服务、解压、执行 Python 语法/导入检查后启动。不要执行 Alembic migration。

```bash
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -m compileall -q server scripts
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -c \
  'import server.main; print(server.main.app.version)'
sudo systemctl restart laogu-server
curl -fsS http://127.0.0.1:8000/api/health; echo
```

管理员登录后调用：

```bash
curl -fsS http://127.0.0.1:8000/api/admin/ops/metrics \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool
```

预期版本为 `0.16.0`，`database.reachable` 为 `true`，并显示 `channels.websocket=true` 和 `channels.http_pull_fallback=true`。普通 MEMBER 必须得到 `403`。

## 备份校验

```bash
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python \
  scripts/verify_backup.py /var/backups/laogu/stage10e-final-YYYY-MM-DD-HHMMSS
```

成功输出 `BACKUP_OK ...`。该脚本只读，不会恢复、删除或修改备份。
