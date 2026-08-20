# Laogu 10H：第一版发布收口清单

10H 不新增业务能力，不修改 Server、Agent、Web 或数据库。本阶段修复通用备份校验工具，使其可验证任意 `stage*-final-*` 最终备份，并在权限不足时返回清晰错误而不是 Python traceback。

## 当前发布基线

- Server：`0.16.0`
- Web：`0.16.0`
- Windows Agent：`0.9.0`
- PostgreSQL migration：`0011_credential_capabilities`
- WebSocket Command Channel：启用
- HTTP Pull fallback：保留
- Credential Snapshot：未实现（能力探测结果不支持）
- Browser/CDP：未修改或替换

## 安装

更新包仅包含 `scripts/verify_backup.py`、对应测试和本清单。校验 SHA256 后解压即可，不需要停止或重启 Server，不执行数据库迁移，不重建 Web。

```bash
sudo tar --no-same-owner -xzf /tmp/stage10h-update.tar.gz -C /opt/laogu-ai-agent
sudo chown laogu:laogu /opt/laogu-ai-agent/scripts/verify_backup.py
sudo -u laogu /opt/laogu-ai-agent/.venv/bin/python -m compileall -q /opt/laogu-ai-agent/scripts
```

## 最终备份验证

备份目录由 root 管理，因此使用 `sudo`，不要使用 `sudo -u laogu`：

```bash
sudo /opt/laogu-ai-agent/.venv/bin/python \
  /opt/laogu-ai-agent/scripts/verify_backup.py \
  /var/backups/laogu/stage10g-final-2026-08-19-155144
```

预期输出 `BACKUP_OK ...`。

## 第一版发布验收

```bash
sudo systemctl is-active laogu-server nginx
curl -fsS http://127.0.0.1:8000/api/health; echo
curl -fsS http://127.0.0.1:8000/api/health/ready; echo
sudo -u laogu bash -c '
set -a; . /etc/laogu/server.env; set +a
cd /opt/laogu-ai-agent
.venv/bin/alembic current
'
sudo journalctl -u laogu-server --since "15 minutes ago" --no-pager | \
  grep -Ei "error|exception|traceback|failed" || echo "最近15分钟没有服务错误"
```

管理员页面应显示数据库正常、Agent 在线、过期租约为 0、WebSocket 正常、HTTP Pull fallback 启用。完成后创建 `stage10h-final-*` 备份并再次用通用校验器验证。
