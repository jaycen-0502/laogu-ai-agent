# Laogu 0.17.0：多用户邀请与账号安全升级

本版本为现有管理系统补齐第一阶段用户自助闭环：一次性邀请注册、工作区角色限制、个人资料与修改密码。修改或重置密码会使旧登录令牌立即失效；升级后旧版登录令牌也会失效一次，用户重新登录即可。现有管理员创建用户接口继续保留，浏览器客户端与 Windows Agent 协议不变。

## 版本基线

- Server：`0.17.0`
- Web：`0.17.0`
- Windows Agent：保持 `0.9.0`
- PostgreSQL migration：`0012_user_invitations`
- 新公开页面：`/invite/:token`
- 新公开 API：邀请查询和接受（均受限流保护）

## 上线前备份

必须先备份数据库和当前程序目录。请把下面的时间戳替换为实际值，并确认备份文件存在且大小正常。

```bash
sudo -u postgres pg_dump -Fc laogu > /var/backups/laogu/laogu-before-0.17.0.dump
sudo cp -a /opt/laogu-ai-agent /var/backups/laogu/app-before-0.17.0
```

## 更新步骤

把新源码同步到 `/opt/laogu-ai-agent` 后执行：

```bash
cd /opt/laogu-ai-agent
sudo -u laogu .venv/bin/pip install -r server/requirements.txt
sudo -u laogu bash -c '
set -a; . /etc/laogu/server.env; set +a
.venv/bin/alembic upgrade head
'
sudo -u laogu bash -c 'cd web && npm ci && npm run build'
sudo systemctl restart laogu-server
sudo nginx -t
sudo systemctl reload nginx
```

## 验收

```bash
curl -fsS http://127.0.0.1:8000/api/health
sudo -u laogu bash -c '
set -a; . /etc/laogu/server.env; set +a
cd /opt/laogu-ai-agent
.venv/bin/alembic current
'
```

预期迁移头为 `0012_user_invitations`。然后在网页端完成以下检查：

1. ADMIN 选择工作区并生成 OWNER 或 MEMBER 邀请；
2. OWNER 只能为自己的工作区生成 MEMBER 邀请；
3. 未登录用户打开邀请链接，设置用户名和密码后自动进入工作台；
4. 已接受、已撤销或已过期的邀请不能再次使用；
5. 普通成员无法进入用户与邀请管理；
6. 任意登录用户可在“账号与安全”修改自己的密码；
7. 不同工作区无法查看或撤销对方邀请。

## 回滚原则

应用回滚前优先恢复整库备份。若数据库尚未产生新邀请数据，可在确认影响后执行一次 Alembic 降级：

```bash
sudo -u laogu bash -c '
set -a; . /etc/laogu/server.env; set +a
cd /opt/laogu-ai-agent
.venv/bin/alembic downgrade 0011_credential_capabilities
'
```

不要在没有数据库备份的情况下执行降级。
