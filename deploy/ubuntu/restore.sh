#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

DOMAIN=""
EMAIL=""
PACKAGE=""
KEY=""
CHECKSUM=""
APP=/opt/laogu-ai-agent
WORK=""

usage() {
  cat <<'EOF'
老谷系统灾备恢复程序（用于全新 Ubuntu 24.04 服务器）

用法：
  sudo bash restore.sh \
    --domain api.example.com \
    --email you@example.com \
    --package /root/restore/laogu-recovery-时间.tar.gz.age \
    --key /root/restore/laogu-backup-recovery.key \
    [--checksum /root/restore/laogu-recovery-时间.tar.gz.age.sha256]

恢复完成后，请删除服务器上的临时私钥和下载的恢复包。
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --package) PACKAGE="${2:-}"; shift 2 ;;
    --key) KEY="${2:-}"; shift 2 ;;
    --checksum) CHECKSUM="${2:-}"; shift 2 ;;
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

DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"
if ! [[ "$DOMAIN" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]]; then
  echo "域名格式错误" >&2; usage; exit 1
fi
if ! [[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "邮箱格式错误" >&2; usage; exit 1
fi
PACKAGE="$(readlink -f "$PACKAGE")"
KEY="$(readlink -f "$KEY")"
test -f "$PACKAGE" || { echo "找不到加密恢复包" >&2; exit 1; }
test -f "$KEY" || { echo "找不到 age 私钥" >&2; exit 1; }

if systemctl is-active --quiet laogu-server 2>/dev/null || [ -f /etc/laogu/server.env ]; then
  echo "检测到现有老谷系统，本脚本拒绝覆盖。请只在全新服务器运行。" >&2
  exit 1
fi

cleanup() {
  rc=$?
  trap - EXIT
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    rm -rf -- "$WORK"
  fi
  exit "$rc"
}
trap cleanup EXIT

echo "=== 1/12 安装恢复环境 ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip postgresql postgresql-contrib \
  nginx certbot python3-certbot-nginx git ufw curl openssl ca-certificates \
  nodejs npm age rsync
timedatectl set-timezone Asia/Shanghai
systemctl enable --now postgresql nginx

echo "=== 2/12 校验并解密恢复包 ==="
if [ -n "$CHECKSUM" ]; then
  CHECKSUM="$(readlink -f "$CHECKSUM")"
  test -f "$CHECKSUM" || { echo "找不到 SHA256 文件" >&2; exit 1; }
  EXPECTED="$(awk 'NR==1 {print $1}' "$CHECKSUM")"
  ACTUAL="$(sha256sum "$PACKAGE" | awk '{print $1}')"
  [ "$EXPECTED" = "$ACTUAL" ] || { echo "外部 SHA256 校验失败" >&2; exit 1; }
fi

WORK="$(mktemp -d /root/laogu-restore.XXXXXX)"
chmod 700 "$WORK"
age --decrypt --identity "$KEY" --output "$WORK/recovery.tar.gz" "$PACKAGE"
gzip -t "$WORK/recovery.tar.gz"

python3 - "$WORK/recovery.tar.gz" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"恢复包包含不安全路径：{member.name}")
PY
mkdir "$WORK/extracted"
tar -xzf "$WORK/recovery.tar.gz" -C "$WORK/extracted"

for required in database.dump application-source.tar.gz server.env SHA256SUMS; do
  test -f "$WORK/extracted/$required" || { echo "恢复包缺少：$required" >&2; exit 1; }
done
(cd "$WORK/extracted" && sha256sum --check SHA256SUMS)
pg_restore --list "$WORK/extracted/database.dump" >/dev/null

echo "=== 3/12 恢复程序和数据文件 ==="
ARCHIVES=(application-source.tar.gz)
if [ -s "$WORK/extracted/application-data.tar.gz" ]; then
  ARCHIVES+=(application-data.tar.gz)
else
  echo "提示：这是旧格式备份，不包含 application-data.tar.gz；将创建空的数据目录。"
fi
for archive_name in "${ARCHIVES[@]}"; do
  python3 - "$WORK/extracted/$archive_name" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"源码包包含不安全路径：{member.name}")
PY
done

if ! id laogu >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$APP" --shell /usr/sbin/nologin laogu
fi
install -d -o laogu -g laogu -m 750 "$APP"
tar -xzf "$WORK/extracted/application-source.tar.gz" -C "$APP"
if [ -s "$WORK/extracted/application-data.tar.gz" ]; then
  tar -xzf "$WORK/extracted/application-data.tar.gz" -C "$APP"
fi
install -d -o laogu -g laogu -m 750 "$APP/logs" "$APP/data/ai-images"
chown -R laogu:laogu "$APP"
chmod -R o-w "$APP"

echo "=== 4/12 恢复生产配置 ==="
install -d -o root -g laogu -m 750 /etc/laogu
install -o root -g laogu -m 640 "$WORK/extracted/server.env" /etc/laogu/server.env
if [ -f "$WORK/extracted/backup-age-recipient.txt" ]; then
  install -o root -g root -m 600 "$WORK/extracted/backup-age-recipient.txt" /etc/laogu/backup-age-recipient.txt
fi

echo "=== 5/12 创建数据库并恢复 PostgreSQL ==="
DB_PASSWORD="$(python3 - /etc/laogu/server.env <<'PY'
import sys
from urllib.parse import unquote, urlsplit

value = ""
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("LAOGU_SERVER_DATABASE_URL="):
        value = line.split("=", 1)[1].strip()
        break
value = value.replace("postgresql+psycopg://", "postgresql://", 1)
value = value.replace("postgresql+psycopg2://", "postgresql://", 1)
password = urlsplit(value).password
if not password:
    raise SystemExit("server.env 中没有数据库密码")
print(unquote(password))
PY
)"

if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='laogu'" | grep -q 1; then
  echo "数据库 laogu 已存在，拒绝覆盖" >&2
  exit 1
fi
if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='laogu'" | grep -q 1; then
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v db_password="$DB_PASSWORD" <<'SQL'
ALTER ROLE laogu LOGIN PASSWORD :'db_password';
SQL
else
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v db_password="$DB_PASSWORD" <<'SQL'
CREATE ROLE laogu LOGIN PASSWORD :'db_password';
SQL
fi
unset DB_PASSWORD
runuser -u postgres -- createdb --owner=laogu laogu
runuser -u postgres -- pg_restore --exit-on-error --no-owner --no-acl \
  --role=laogu --dbname=laogu "$WORK/extracted/database.dump"

echo "=== 6/12 安装程序依赖 ==="
runuser -u laogu -- python3 -m venv "$APP/.venv"
runuser -u laogu -- "$APP/.venv/bin/python" -m pip install --upgrade pip
runuser -u laogu -- "$APP/.venv/bin/pip" install -r "$APP/server/requirements.txt"
runuser -u laogu -- bash -c "set -a; . /etc/laogu/server.env; set +a; cd '$APP'; .venv/bin/alembic upgrade head"

echo "=== 7/12 构建前端 ==="
runuser -u laogu -- bash -c "cd '$APP/web'; npm ci; npm run build"
test -s "$APP/web/dist/index.html"

echo "=== 8/12 安装后端服务 ==="
install -o root -g root -m 644 "$APP/deploy/ubuntu/laogu-server.service" /etc/systemd/system/laogu-server.service
systemctl daemon-reload
systemctl enable --now laogu-server
for attempt in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/api/health/ready >/dev/null && break
  if [ "$attempt" -eq 30 ]; then
    journalctl -u laogu-server -n 80 --no-pager
    exit 1
  fi
  sleep 1
done

echo "=== 9/12 配置 Nginx ==="
cat > /etc/nginx/sites-available/laogu-server <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    root /opt/laogu-ai-agent/web/dist;
    index index.html;
    client_max_body_size 1m;

    location = /api/auth/bootstrap { deny all; return 403; }
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

echo "=== 10/12 配置防火墙 ==="
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "=== 11/12 申请 HTTPS ==="
certbot --nginx --non-interactive --agree-tos --redirect \
  --email "$EMAIL" -d "$DOMAIN"

echo "=== 12/12 最终验收 ==="
bash "$APP/deploy/ubuntu/verify.sh" "$DOMAIN"

echo
echo "=== 灾备恢复成功 ==="
echo "请确认网站后执行以下清理命令："
echo "  rm -f -- '$KEY' '$PACKAGE'${CHECKSUM:+ '$CHECKSUM'}"
echo "然后重新运行 install-backup.sh，输入 Telegram Bot Token 和 Chat ID。"
