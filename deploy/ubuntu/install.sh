#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP=/opt/laogu-ai-agent
DOMAIN=""
EMAIL=""
WORKSPACE_NAME=""
ADMIN_USERNAME=""

usage() {
  cat <<'EOF'
老谷系统 Ubuntu 24.04 全新安装程序

用法：
  sudo bash deploy/ubuntu/install.sh --domain api.example.com

可选参数：
  --email EMAIL              Let's Encrypt 邮箱
  --workspace NAME           初始工作区名称
  --admin USER               初始管理员用户名
  -h, --help                 显示帮助

本脚本只用于全新安装。已有数据请使用 restore.sh。
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --workspace) WORKSPACE_NAME="${2:-}"; shift 2 ;;
    --admin) ADMIN_USERNAME="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 1 ;;
  esac
done

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "请使用 root 运行本脚本" >&2
  exit 1
fi

if [ ! -f /etc/os-release ]; then
  echo "无法识别操作系统" >&2
  exit 1
fi
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
  echo "仅支持 Ubuntu 24.04 LTS；当前为 ${PRETTY_NAME:-未知}" >&2
  exit 1
fi

if [ "$(readlink -f "$(pwd)")" != "$APP" ]; then
  echo "请先把仓库克隆到 $APP，再进入该目录运行安装脚本" >&2
  exit 1
fi
for required in server/main.py server/requirements.txt web/package.json alembic.ini deploy/ubuntu/laogu-server.service; do
  test -f "$APP/$required" || { echo "缺少项目文件：$required" >&2; exit 1; }
done

if [ -z "$DOMAIN" ]; then
  read -r -p "请输入系统域名（例如 api.jaycwl.org）：" DOMAIN
fi
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"
if ! [[ "$DOMAIN" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]]; then
  echo "域名格式错误：$DOMAIN" >&2
  exit 1
fi

if [ -z "$EMAIL" ]; then
  read -r -p "请输入 Let's Encrypt 联系邮箱：" EMAIL
fi
if ! [[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "邮箱格式错误" >&2
  exit 1
fi

if [ -z "$WORKSPACE_NAME" ]; then
  read -r -p "请输入初始工作区名称：" WORKSPACE_NAME
fi
if [ -z "$ADMIN_USERNAME" ]; then
  read -r -p "请输入初始管理员用户名：" ADMIN_USERNAME
fi
if [ -z "$WORKSPACE_NAME" ] || [ -z "$ADMIN_USERNAME" ]; then
  echo "工作区和管理员用户名不能为空" >&2
  exit 1
fi

read -r -s -p "请输入管理员密码（至少 12 位，输入不会显示）：" ADMIN_PASSWORD
echo
read -r -s -p "请再次输入管理员密码：" ADMIN_PASSWORD_CONFIRM
echo
if [ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_CONFIRM" ]; then
  echo "两次密码不一致" >&2
  exit 1
fi
if [ "${#ADMIN_PASSWORD}" -lt 12 ]; then
  echo "管理员密码至少需要 12 位" >&2
  exit 1
fi
unset ADMIN_PASSWORD_CONFIRM

if systemctl is-active --quiet laogu-server 2>/dev/null || [ -f /etc/laogu/server.env ]; then
  echo "检测到现有老谷系统配置。本脚本不会覆盖已有系统。" >&2
  echo "灾备恢复请使用 deploy/ubuntu/restore.sh。" >&2
  exit 1
fi

echo "=== 1/12 检查域名解析 ==="
if ! getent ahostsv4 "$DOMAIN" >/dev/null; then
  echo "域名尚未解析：$DOMAIN" >&2
  echo "请先把域名 A 记录指向本服务器公网 IP。" >&2
  exit 1
fi

echo "=== 2/12 安装系统组件 ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip postgresql postgresql-contrib \
  nginx certbot python3-certbot-nginx git ufw curl openssl ca-certificates \
  nodejs npm age rsync

timedatectl set-timezone Asia/Shanghai
systemctl enable --now postgresql nginx

echo "=== 3/12 配置防火墙 ==="
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "=== 4/12 创建运行用户和目录 ==="
if ! id laogu >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$APP" --shell /usr/sbin/nologin laogu
fi
install -d -o laogu -g laogu -m 750 "$APP/data/ai-images" "$APP/logs"
chown -R laogu:laogu "$APP"
chmod -R o-w "$APP"

echo "=== 5/12 创建 PostgreSQL 数据库 ==="
if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='laogu'" | grep -q 1; then
  echo "数据库角色 laogu 已存在，拒绝覆盖。请确认这是全新服务器。" >&2
  exit 1
fi
if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='laogu'" | grep -q 1; then
  echo "数据库 laogu 已存在，拒绝覆盖。请确认这是全新服务器。" >&2
  exit 1
fi

DB_PASSWORD="$(openssl rand -hex 24)"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v db_password="$DB_PASSWORD" <<'SQL'
CREATE ROLE laogu LOGIN PASSWORD :'db_password';
CREATE DATABASE laogu OWNER laogu;
SQL

echo "=== 6/12 创建生产配置 ==="
JWT_SECRET="$(openssl rand -hex 48)"
AI_CREDENTIAL_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
install -d -o root -g laogu -m 750 /etc/laogu
cat > /etc/laogu/server.env <<EOF
LAOGU_SERVER_ENVIRONMENT=production
LAOGU_SERVER_DATABASE_URL=postgresql+psycopg://laogu:${DB_PASSWORD}@127.0.0.1/laogu
LAOGU_SERVER_JWT_SECRET=${JWT_SECRET}
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
LAOGU_AI_CREDENTIAL_KEY=${AI_CREDENTIAL_KEY}
LAOGU_AI_IMAGE_STORAGE_PATH=/opt/laogu-ai-agent/data/ai-images
EOF
chown root:laogu /etc/laogu/server.env
chmod 640 /etc/laogu/server.env
unset DB_PASSWORD JWT_SECRET AI_CREDENTIAL_KEY

echo "=== 7/12 安装后端依赖并迁移数据库 ==="
runuser -u laogu -- python3 -m venv "$APP/.venv"
runuser -u laogu -- "$APP/.venv/bin/python" -m pip install --upgrade pip
runuser -u laogu -- "$APP/.venv/bin/pip" install -r "$APP/server/requirements.txt"
runuser -u laogu -- bash -c "set -a; . /etc/laogu/server.env; set +a; cd '$APP'; .venv/bin/alembic upgrade head"

echo "=== 8/12 构建前端 ==="
runuser -u laogu -- bash -c "cd '$APP/web'; npm ci; npm run build"
test -s "$APP/web/dist/index.html"

echo "=== 9/12 安装后端服务 ==="
install -o root -g root -m 644 "$APP/deploy/ubuntu/laogu-server.service" /etc/systemd/system/laogu-server.service
systemctl daemon-reload
systemctl enable --now laogu-server

for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health/ready >/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    journalctl -u laogu-server -n 80 --no-pager
    echo "后端服务未通过就绪检查" >&2
    exit 1
  fi
  sleep 1
done

echo "=== 10/12 配置 Nginx ==="
cat > /etc/nginx/sites-available/laogu-server <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    root /opt/laogu-ai-agent/web/dist;
    index index.html;
    client_max_body_size 1m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 10s;
        proxy_read_timeout 180s;
    }
    location /assets/ { try_files \$uri =404; expires 1y; add_header Cache-Control "public, immutable"; }
    location / { try_files \$uri \$uri/ /index.html; add_header Cache-Control "no-store"; }
    location ~ /\.(?!well-known/) { deny all; }
}
EOF
ln -sfn /etc/nginx/sites-available/laogu-server /etc/nginx/sites-enabled/laogu-server
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "=== 11/12 申请 HTTPS 证书 ==="
certbot --nginx --non-interactive --agree-tos --redirect \
  --email "$EMAIL" -d "$DOMAIN"
PUBLIC_URL="https://$DOMAIN"

echo "=== 12/12 创建初始管理员并关闭初始化入口 ==="
BOOTSTRAP_FILE="$(mktemp)"
BOOTSTRAP_RESPONSE="$(mktemp)"
cleanup() {
  rm -f -- "${BOOTSTRAP_FILE:-}" "${BOOTSTRAP_RESPONSE:-}"
}
trap cleanup EXIT
export WORKSPACE_NAME ADMIN_USERNAME ADMIN_PASSWORD BOOTSTRAP_FILE
python3 - <<'PY'
import json
import os

with open(os.environ["BOOTSTRAP_FILE"], "w", encoding="utf-8") as stream:
    json.dump(
        {
            "workspace_name": os.environ["WORKSPACE_NAME"],
            "username": os.environ["ADMIN_USERNAME"],
            "password": os.environ["ADMIN_PASSWORD"],
        },
        stream,
        ensure_ascii=False,
    )
PY
unset ADMIN_PASSWORD WORKSPACE_NAME ADMIN_USERNAME

HTTP_CODE="$(curl -sS -o "$BOOTSTRAP_RESPONSE" -w '%{http_code}' \
  -X POST "$PUBLIC_URL/api/auth/bootstrap" \
  -H 'Content-Type: application/json' \
  --data-binary "@$BOOTSTRAP_FILE")"
if [ "$HTTP_CODE" != "200" ]; then
  echo "初始化管理员失败，HTTP $HTTP_CODE" >&2
  cat "$BOOTSTRAP_RESPONSE" >&2
  exit 1
fi
rm -f -- "$BOOTSTRAP_FILE" "$BOOTSTRAP_RESPONSE"
BOOTSTRAP_FILE=""
BOOTSTRAP_RESPONSE=""

python3 - "$DOMAIN" /etc/nginx/sites-available/laogu-server <<'PY'
import pathlib
import sys

domain = sys.argv[1]
path = pathlib.Path(sys.argv[2])
text = path.read_text(encoding="utf-8")
needle = f"server_name {domain};"
positions = [i for i in range(len(text)) if text.startswith(needle, i)]
target = positions[-1]
insert_at = text.find("\n", target) + 1
block = "\n    location = /api/auth/bootstrap {\n        deny all;\n        return 403;\n    }\n"
if "location = /api/auth/bootstrap" not in text:
    text = text[:insert_at] + block + text[insert_at:]
path.write_text(text, encoding="utf-8")
PY
nginx -t
systemctl reload nginx

echo
echo "=== 全新安装成功 ==="
echo "系统地址：$PUBLIC_URL"
echo "API 健康：$PUBLIC_URL/api/health"
echo "管理员已创建，bootstrap 入口已关闭。"
echo
echo "下一步安装加密备份："
echo "  sudo bash $APP/deploy/ubuntu/install-backup.sh"
echo
bash "$APP/deploy/ubuntu/verify.sh" "$DOMAIN"
