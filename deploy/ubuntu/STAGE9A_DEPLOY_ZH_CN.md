# 第9A阶段：AI Provider中心生产部署（中文版）

本阶段只部署 AI Provider 配置中心，不包含 AI 聊天、账号分析、回复生成或 AI 任务调用。

## 1. 上传前检查

把 `stage9a-update.tar.gz` 上传到服务器 `/tmp/stage9a-update.tar.gz`，然后检查压缩包SHA256和路径内容。不要在未核对SHA256前解压。

## 2. 生成独立加密密钥

在本地项目的 Python 环境执行一次：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把输出暂时保存到密码管理器。它不是管理员密码，也不是JWT；丢失后无法解密历史Provider密钥。不要把它发到聊天窗口、工单或日志。

## 3. 服务器备份

```bash
cd /opt/laogu-ai-agent
STAMP=$(date +%F-%H%M%S)
BACKUP="/var/backups/laogu/stage9a-$STAMP"
sudo mkdir -p "$BACKUP/agent" "$BACKUP/web"
sudo chmod 700 "$BACKUP"
sudo -u postgres pg_dump -Fc -d laogu > "$BACKUP/laogu-before-stage9a.dump"
sudo tar -czf "$BACKUP/source-before-stage9a.tar.gz" \
  --exclude='web/node_modules' --exclude='web/dist' --exclude='.venv' \
  --exclude='.npm' --exclude='.npm-cache' --exclude='logs' --exclude='agent_data' \
  server agent common alembic desktop scripts tests web
sudo cp -a /etc/laogu/server.env "$BACKUP/server.env-before-stage9a"
sudo chmod 600 "$BACKUP/server.env-before-stage9a"
echo "BACKUP=$BACKUP"
```

## 4. 覆盖代码并安装依赖

```bash
cd /opt/laogu-ai-agent
sudo tar -xzf /tmp/stage9a-update.tar.gz --no-same-owner -C /opt/laogu-ai-agent
sudo chown root:laogu server/*.py common/*.py alembic/versions/*.py
sudo chown laogu:laogu web/package.json web/package-lock.json web/src/App.tsx web/src/types.ts web/src/styles.css web/src/pages/ai_providers.tsx
sudo -u laogu bash -c 'cd /opt/laogu-ai-agent && .venv/bin/pip install -r server/requirements.txt'
```

如果服务器没有 Node.js/npm，先安装Node.js 20，再运行：

```bash
sudo chown -R laogu:laogu /opt/laogu-ai-agent/web
sudo mkdir -p /opt/laogu-ai-agent/.npm
sudo chown -R laogu:laogu /opt/laogu-ai-agent/.npm
sudo -u laogu env npm_config_cache=/opt/laogu-ai-agent/.npm \
  bash -c 'cd /opt/laogu-ai-agent/web && npm ci --no-audit --no-fund && npm run build'
```

## 5. 写入加密密钥

编辑服务器环境文件：

```bash
sudo nano /etc/laogu/server.env
```

追加下面一行，把等号右侧替换成第2步生成的值：

```text
LAOGU_AI_CREDENTIAL_KEY=这里填写Fernet密钥
```

保存后执行：

```bash
sudo chmod 600 /etc/laogu/server.env
sudo chown root:laogu /etc/laogu/server.env
```

不要用 `echo` 把真实密钥写入Shell历史，也不要执行 `grep` 打印这一行。

## 6. 执行0004数据库迁移

```bash
cd /opt/laogu-ai-agent
sudo systemctl stop laogu-server
sudo -u laogu bash -c '
  set -a; source /etc/laogu/server.env; set +a
  cd /opt/laogu-ai-agent
  .venv/bin/alembic heads
  .venv/bin/alembic upgrade head
  .venv/bin/alembic current -v
'
```

必须看到：

```text
0004_ai_providers (head)
```

并确认数据库有 `ai_providers` 表：

```bash
sudo -u postgres psql -d laogu -c \
  "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename='ai_providers';"
```

## 7. 启动和健康检查

```bash
sudo systemctl start laogu-server
sudo systemctl status laogu-server --no-pager
curl -fsS http://127.0.0.1:8000/api/health
echo
curl -fsS https://你的域名/api/health
echo
```

如果服务无法启动，先查看：

```bash
sudo journalctl -u laogu-server -n 80 --no-pager
```

常见原因是没有设置 `LAOGU_AI_CREDENTIAL_KEY`，或者密钥格式不是Fernet生成的44字符Base64值。

## 8. Web验收

登录管理后台，打开左侧“AI 服务商”：

1. 新增一个Provider，输入API Key；页面只显示掩码。
2. 保存后刷新，确认完整API Key不会出现。
3. 点击“测试连接”，只检查 `/models`并显示模型数量。
4. 设置一个启用的工作区默认Provider。
5. 用MEMBER账号确认只能查看，不能新增、编辑、测试或删除。

不要在生产环境使用真实API Key做反复测试；连接测试会访问对应Provider的模型接口。

## 9. 回滚

若迁移或启动失败，不要删除数据库。保留备份目录，先停止服务并恢复源码；只有确认需要数据库回滚时，才执行：

```bash
sudo -u laogu bash -c '
  set -a; source /etc/laogu/server.env; set +a
  cd /opt/laogu-ai-agent
  .venv/bin/alembic downgrade 0003_script_center
'
```

数据库恢复使用备份文件：

```bash
sudo -u postgres pg_restore --clean --if-exists --no-owner \
  -d laogu "$BACKUP/laogu-before-stage9a.dump"
```
