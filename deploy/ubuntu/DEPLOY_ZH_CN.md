# Laogu Server Ubuntu 24.04 完整部署教程

这份教程面向第一次部署服务器的用户。请按顺序执行，不要跳步骤。

## 1. 部署后是什么结构

```text
Ubuntu Server
  Nginx :443 HTTPS
      -> FastAPI 127.0.0.1:8000
          -> PostgreSQL

Windows
  AgentService
      -> HTTPS访问Ubuntu
      -> 本机Laogu Browser 127.0.0.1:19876
```

Ubuntu绝对不会访问Windows的 `127.0.0.1:19876`，也不安装Laogu Browser。

## 2. 需要提前准备

建议配置：

- Ubuntu 24.04 LTS
- 2核CPU、4GB内存、40GB硬盘
- 一个公网IPv4地址
- 一个域名，例如 `api.example.com`
- 域名A记录已经指向服务器公网IP
- Windows端项目目录仍为 `C:\Users\Administrator\Desktop\laogu-ai-agent`

先在自己电脑测试域名解析：

```powershell
nslookup api.example.com
```

返回的IP必须是Ubuntu服务器公网IP。DNS刚修改时可能需要等待几分钟到数小时。

## 3. 登录Ubuntu服务器

Windows PowerShell执行：

```powershell
ssh root@你的服务器IP
```

如果服务商给的是普通用户：

```powershell
ssh ubuntu@你的服务器IP
```

后续命令前面保留 `sudo`。如果你本身就是root，也可以保留，不影响理解。

## 4. 更新系统和设置时区

```bash
sudo apt update
sudo apt upgrade -y
sudo timedatectl set-timezone Asia/Shanghai
timedatectl
```

## 5. 安装基础软件

```bash
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib nginx certbot python3-certbot-nginx git ufw curl openssl
```

检查版本：

```bash
python3 --version
psql --version
nginx -v
```

## 6. 配置防火墙

必须先允许SSH，否则可能把自己锁在服务器外面。

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

正常应当允许：

```text
22/tcp
80/tcp
443/tcp
```

不要开放PostgreSQL的5432端口，也不要开放FastAPI的8000端口到公网。

## 7. 创建服务器运行用户和项目目录

```bash
sudo adduser --system --group --home /opt/laogu-ai-agent --no-create-home laogu
sudo mkdir -p /opt/laogu-ai-agent
sudo chown -R laogu:laogu /opt/laogu-ai-agent
```

## 8. 把项目上传到Ubuntu

最简单的方法是使用WinSCP：

1. WinSCP连接服务器。
2. 把 `C:\Users\Administrator\Desktop\laogu-ai-agent` 中的项目代码上传到 `/opt/laogu-ai-agent`。
3. 不需要上传Laogu Browser安装目录。
4. 不要上传Windows的 `.venv`、`__pycache__`、临时测试数据库和Agent凭证。

上传完成后在Ubuntu执行：

```bash
sudo chown -R laogu:laogu /opt/laogu-ai-agent
ls -la /opt/laogu-ai-agent
```

应该能看到 `server`、`agent`、`desktop`、`deploy` 等目录。

## 9. 创建Python虚拟环境

```bash
cd /opt/laogu-ai-agent
sudo -u laogu python3 -m venv .venv
sudo -u laogu .venv/bin/python -m pip install --upgrade pip
sudo -u laogu .venv/bin/pip install -r server/requirements.txt
```

检查FastAPI是否安装成功：

```bash
sudo -u laogu .venv/bin/python -c "import fastapi, sqlalchemy; print('依赖安装成功')"
```

## 10. 创建PostgreSQL数据库

先生成数据库密码。下面命令只输出安全的十六进制字符，放进数据库URL时不容易出错：

```bash
openssl rand -hex 24
```

记住输出，例如 `abc123...`，下面用 `你的数据库密码` 表示。

进入PostgreSQL：

```bash
sudo -u postgres psql
```

在 `postgres=#` 后逐行执行：

```sql
CREATE USER laogu WITH PASSWORD '你的数据库密码';
CREATE DATABASE laogu OWNER laogu;
GRANT ALL PRIVILEGES ON DATABASE laogu TO laogu;
\q
```

测试数据库连接：

```bash
PGPASSWORD='你的数据库密码' psql -h 127.0.0.1 -U laogu -d laogu -c 'SELECT 1;'
```

看到一行数字 `1` 就表示成功。

## 11. 创建服务器环境变量

生成JWT密钥：

```bash
openssl rand -hex 32
```

复制输出，然后创建配置目录：

```bash
sudo mkdir -p /etc/laogu
sudo nano /etc/laogu/server.env
```

填入以下内容：

```ini
LAOGU_SERVER_ENVIRONMENT=production
LAOGU_SERVER_DATABASE_URL=postgresql+psycopg://laogu:你的数据库密码@127.0.0.1/laogu
LAOGU_SERVER_JWT_SECRET=刚才生成的64位十六进制JWT密钥
LAOGU_SERVER_JWT_EXPIRE_MINUTES=720
LAOGU_AGENT_OFFLINE_SECONDS=90
LAOGU_SERVER_DEBUG=false
LAOGU_SERVER_HTTPS_ENABLED=true
LAOGU_SERVER_MAX_REQUEST_BYTES=1048576
LAOGU_RATE_LIMIT_WINDOW_SECONDS=60
LAOGU_RATE_LIMIT_AUTH=10
LAOGU_RATE_LIMIT_REGISTER=10
LAOGU_RATE_LIMIT_HEARTBEAT=120
LAOGU_RATE_LIMIT_TASKS=60
LAOGU_AGENT_TOKEN_TTL_DAYS=365
```

保存nano：按 `Ctrl+O`、回车，再按 `Ctrl+X`。

限制配置文件权限：

```bash
sudo chown root:laogu /etc/laogu/server.env
sudo chmod 640 /etc/laogu/server.env
sudo ls -l /etc/laogu/server.env
```

不要把数据库密码和JWT密钥发给客户，也不要提交到Git。

## 12. 执行数据库迁移

新数据库直接执行：

```bash
cd /opt/laogu-ai-agent
sudo -u laogu bash -c 'set -a; source /etc/laogu/server.env; set +a; .venv/bin/alembic upgrade head'
sudo -u laogu bash -c 'set -a; source /etc/laogu/server.env; set +a; .venv/bin/alembic current'
```

最后应显示 `0002_security (head)`。生产环境不再由FastAPI自动建表，以后每次更新代码后都先执行 `alembic upgrade head`。

如果是从第7阶段旧数据库升级，先备份：

```bash
sudo -u postgres pg_dump laogu > /root/laogu-before-stage8a.sql
```

只有旧表已经存在、但数据库中没有 `alembic_version` 表时，才执行：

```bash
cd /opt/laogu-ai-agent
sudo -u laogu bash -c 'set -a; source /etc/laogu/server.env; set +a; .venv/bin/alembic stamp 0001_stage7'
sudo -u laogu bash -c 'set -a; source /etc/laogu/server.env; set +a; .venv/bin/alembic upgrade head'
```

不要对空数据库执行 `stamp 0001_stage7`，空数据库应直接 `upgrade head`。

## 13. 手动启动一次FastAPI检查

```bash
cd /opt/laogu-ai-agent
sudo -u laogu bash -c 'set -a; source /etc/laogu/server.env; set +a; .venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8000'
```

保持这个窗口运行，再开一个SSH窗口测试：

```bash
curl http://127.0.0.1:8000/api/health
```

应该返回：

```json
{"ok":true}
```

回到第一个窗口按 `Ctrl+C` 停止手动服务。

如果启动失败，先看错误，不要继续配置Nginx。

同时检查数据库就绪状态：

```bash
curl http://127.0.0.1:8000/api/health/ready
```

也应返回 `{"ok":true}`。

## 14. 配置systemd自动运行

复制项目内的服务文件：

```bash
sudo cp /opt/laogu-ai-agent/deploy/ubuntu/laogu-server.service /etc/systemd/system/laogu-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now laogu-server
sudo systemctl status laogu-server --no-pager
```

状态应为：

```text
active (running)
```

查看实时日志：

```bash
sudo journalctl -u laogu-server -f
```

重新启动：

```bash
sudo systemctl restart laogu-server
```

## 15. 先配置HTTP版Nginx

证书还没签发时，不要直接复制最终HTTPS模板，否则证书文件不存在会导致 `nginx -t` 失败。

创建配置：

```bash
sudo nano /etc/nginx/sites-available/laogu-server
```

把 `api.example.com` 替换成你自己的域名：

```nginx
server {
    listen 80;
    server_name api.example.com;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 90s;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/laogu-server /etc/nginx/sites-enabled/laogu-server
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

测试HTTP：

```bash
curl http://api.example.com/api/health
```

必须先返回 `{"ok":true}`，再继续申请证书。

## 16. 申请HTTPS证书

```bash
sudo certbot --nginx -d api.example.com
```

按提示输入邮箱、同意条款，并选择把HTTP跳转到HTTPS。

测试：

```bash
curl https://api.example.com/api/health
```

应返回：

```json
{"ok":true}
```

测试自动续期：

```bash
sudo certbot renew --dry-run
```

## 17. 第一次初始化管理员和Workspace

这个接口只能在数据库没有用户时成功一次。

```bash
curl -X POST https://api.example.com/api/auth/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{
    "workspace_name": "我的工作室",
    "username": "admin",
    "password": "请改成至少8位的强密码"
  }'
```

返回内容包含：

```json
{
  "workspace_id": "...",
  "user_id": "...",
  "access_token": "..."
}
```

`access_token` 是用户JWT，用于第一次注册Windows Agent。不要发给客户，不要写进聊天记录。

以后登录获取新JWT：

```bash
curl -X POST https://api.example.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"你的管理员密码"}'
```

## 18. 初始化后关闭bootstrap公网入口

虽然程序在已有用户时会返回409，但生产环境建议由Nginx直接阻止再次访问。

编辑：

```bash
sudo nano /etc/nginx/sites-available/laogu-server
```

在HTTPS的 `server {}` 内增加：

```nginx
location = /api/auth/bootstrap {
    deny all;
    return 403;
}
```

然后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 19. Windows配置服务器地址

打开Windows PowerShell：

```powershell
cd C:\Users\Administrator\Desktop\laogu-ai-agent

[Environment]::SetEnvironmentVariable(
  "LAOGU_SERVER_URL",
  "https://api.example.com",
  "User"
)
```

关闭并重新打开PowerShell，让新环境变量生效。

第一次注册时，把刚才bootstrap或login返回的JWT临时放到当前窗口：

```powershell
$env:LAOGU_SERVER_ENROLLMENT_TOKEN = "你的JWT"
python -m agent.service_main --once
```

成功后检查：

```powershell
Get-Content .\agent_data\credentials.json
```

文件中应有 `agent_id` 和 `agent_token_protected`，绝对不能出现明文 `agent_token`。不要把文件内容发给别人。

立即删除当前窗口里的临时JWT：

```powershell
Remove-Item Env:LAOGU_SERVER_ENROLLMENT_TOKEN
```

Agent后续使用自己的Agent Token，不再需要用户JWT。

## 20. 检查Windows Agent凭证保护

当前版本会使用Windows DPAPI加密Agent Token，并自动为 `agent_data` 目录设置ACL。JSON中只能看到 `agent_token_protected`，不能出现明文 `agent_token`。

```powershell
cd C:\Users\Administrator\Desktop\laogu-ai-agent
Select-String -Path .\agent_data\credentials.json -Pattern 'agent_token_protected'
icacls .\agent_data\credentials.json
```

ACL应只包含当前Windows用户、SYSTEM和Administrators。不要手工编辑加密内容，也不要把 `agent_data` 上传到服务器、网盘或Git。

如果旧文件中仍有明文 `agent_token`，Agent会明确拒绝读取。请重新注册，不会静默降级为明文保存。

## 21. 启动Windows Agent

先确认Laogu Browser已经运行，然后执行：

```powershell
cd C:\Users\Administrator\Desktop\laogu-ai-agent
python -m agent.service_main
```

桌面控制中心仍然使用：

```powershell
python -m desktop
```

桌面顶部会显示：

```text
Server: ONLINE
Agent: ONLINE
Last Heartbeat: 时间
```

如果显示 `UNCONFIGURED`，通常是 `LAOGU_SERVER_URL` 没有生效，需要关闭并重新打开桌面程序。

## 22. 常用检查命令

Ubuntu检查服务器：

```bash
sudo systemctl status laogu-server --no-pager
sudo journalctl -u laogu-server -n 100 --no-pager
sudo systemctl status nginx --no-pager
sudo nginx -t
sudo ss -lntp | grep -E ':80|:443|:8000'
curl https://api.example.com/api/health
```

Windows检查：

```powershell
Invoke-RestMethod https://api.example.com/api/health
Get-Content .\logs\agent.log -Tail 100
Get-Content .\agent_data\credentials.json
```

## 22. 数据库备份

每天至少备份一次：

```bash
sudo mkdir -p /var/backups/laogu
sudo -u postgres pg_dump laogu | gzip | sudo tee /var/backups/laogu/laogu-$(date +%F-%H%M).sql.gz > /dev/null
sudo ls -lh /var/backups/laogu
```

备份文件还应定期下载到另一台机器，服务器硬盘损坏时才有恢复能力。

## 23. 更新服务器代码

更新前先备份数据库，再上传新代码：

```bash
sudo -u postgres pg_dump laogu > /tmp/laogu-before-update.sql
cd /opt/laogu-ai-agent
sudo -u laogu .venv/bin/pip install -r server/requirements.txt
sudo systemctl restart laogu-server
sudo systemctl status laogu-server --no-pager
curl https://api.example.com/api/health
```

当前版本没有Alembic数据库迁移，因此数据库结构发生变化时不能只覆盖代码。解决办法见下一节。

## 24. 已知问题及解决办法

### 24.1 没有Alembic数据库迁移

影响：第一次安装可以自动建表，但以后增加字段或修改表结构时，SQLAlchemy不会自动安全升级旧数据库。

当前做法：第7阶段首次部署可以正常使用。每次更新前必须备份数据库。

正式解决方案：

1. 在项目加入Alembic。
2. 把当前数据库结构生成第一份基线迁移。
3. 以后每次修改模型都生成新的迁移文件。
4. 发布前执行测试库升级和回滚测试。
5. 生产更新时先备份，再运行 `alembic upgrade head`。

不要在没有迁移文件时直接修改生产数据库表。

### 24.2 Agent Token本地保护

当前Agent Token已经使用Windows DPAPI加密，JSON只保存 `agent_token_protected`，并自动设置Windows ACL。不要复制或共享 `agent_data`。

### 24.3 管理员审计日志

当前已经通过 `audit_logs` 记录用户、Workspace、操作、资源、时间、来源IP和结果，并对密码、JWT、Agent Token及Authorization信息脱敏。

### 24.4 TestClient弃用警告

这是开发测试依赖的兼容提示，不影响Ubuntu生产服务器和Windows Agent运行。

解决方案：后续统一升级FastAPI、Starlette测试客户端，或迁移到官方推荐的新测试传输包。不要为了消除警告随意降级生产依赖。

### 24.5 Agent Token轮换和吊销

当前已经支持管理员或Workspace OWNER轮换Token和吊销Token。吊销后Windows Agent会进入 `REAUTH_REQUIRED`，不会关闭Laogu Browser或终止本地任务。

## 25. 常见错误

### `nginx -t` 提示证书文件不存在

原因：在Certbot签发证书前用了最终HTTPS模板。

处理：先使用第14节HTTP配置，再运行第15节Certbot。

### 浏览器访问502 Bad Gateway

检查：

```bash
sudo systemctl status laogu-server --no-pager
curl http://127.0.0.1:8000/api/health
sudo journalctl -u laogu-server -n 100 --no-pager
```

如果本机8000不通，问题在FastAPI或数据库，不是Nginx。

### systemd提示数据库连接失败

检查 `/etc/laogu/server.env` 中用户名、密码、数据库名和URL，重新运行第10节连接测试。

### Windows显示Server OFFLINE

依次检查：

1. Windows能否打开 `https://你的域名/api/health`。
2. `LAOGU_SERVER_URL` 是否包含正确的 `https://`。
3. 服务器证书是否有效。
4. Windows时间是否准确。
5. `agent_data/credentials.json` 是否存在。

### Agent注册要求Enrollment Token

第一次注册需要用户JWT。重新调用 `/api/auth/login` 获取JWT，临时设置 `LAOGU_SERVER_ENROLLMENT_TOKEN`，完成注册后立即删除该环境变量。

## 26. 部署完成检查表

- `https://域名/api/health` 返回 `{"ok":true}`
- PostgreSQL的5432没有开放到公网
- FastAPI的8000只监听127.0.0.1
- Nginx HTTPS证书有效
- bootstrap公网入口已关闭
- Windows已生成 `agent_data/credentials.json`
- 凭证文件已设置ACL
- Desktop显示Server和Agent为ONLINE
- Heartbeat持续更新
- Profile和X公开账号信息可以同步
- 服务器可以创建只读Task并收到Result
- 数据库备份命令已经测试

全部完成后，第8A生产安全部署才算验收完成。

## 27. 部署Web管理后台

Ubuntu安装Node.js和npm：

```bash
sudo apt update
sudo apt install -y nodejs npm
node --version
npm --version
```

Node.js建议18或更高版本。然后构建Web静态文件：

```bash
cd /opt/laogu-ai-agent/web
sudo -u laogu npm ci
sudo -u laogu npm run build
ls -la /opt/laogu-ai-agent/web/dist
```

不要在生产服务器运行 `npm run dev` 或Vite开发服务器。

复制第8B Nginx配置模板，并把域名替换成实际域名：

```bash
sudo cp /opt/laogu-ai-agent/deploy/ubuntu/nginx-laogu.conf /etc/nginx/sites-available/laogu-server
sudo sed -i 's/api.example.com/api.jaycwl.org/g' /etc/nginx/sites-available/laogu-server
sudo nginx -t
sudo systemctl reload nginx
```

最终路由结构：

```text
https://域名/api/*  -> FastAPI 127.0.0.1:8000
https://域名/*      -> /opt/laogu-ai-agent/web/dist
```

浏览器访问：

```text
https://api.jaycwl.org/
```

应显示Laogu Web登录页面。登录继续使用现有 `/api/auth/login`，没有第二套认证系统。
