#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 交互式灾备恢复向导。只在全新的 Ubuntu 24.04 服务器运行。
APP=/opt/laogu-ai-agent
RESTORE_DIR=/root/restore
DOMAIN=""
EMAIL=""
PACKAGE=""
KEY=""
CHECKSUM=""
WORK=""

usage() {
  cat <<'EOF'
老谷系统灾备恢复向导

推荐用法（交互式）：
  sudo bash deploy/ubuntu/restore.sh

也支持完整参数：
  sudo bash restore.sh --domain api.example.com --email you@example.com \
    --package /root/restore/laogu-recovery-时间.tar.gz.age \
    --key /root/restore/laogu-backup-recovery.key \
    [--checksum /root/restore/laogu-recovery-时间.tar.gz.age.sha256]

说明：本脚本只恢复到全新服务器，检测到已有老谷服务或配置时会停止。
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
  echo "请使用 root 运行：sudo bash deploy/ubuntu/restore.sh" >&2
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

normalize_domain() {
  local value="$1"
  value="${value#http://}"
  value="${value#https://}"
  value="${value%%/*}"
  printf '%s' "$value"
}

valid_domain() { [[ "$1" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]]; }
valid_email() { [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; }

choose_latest() {
  local pattern="$1"
  find "$RESTORE_DIR" -maxdepth 1 -type f -name "$pattern" -printf '%T@ %p\n' 2>/dev/null |
    sort -nr | head -n 1 | cut -d' ' -f2-
}

if [ -z "$DOMAIN" ]; then
  read -r -p "1/8 输入生产域名（例如 api.jaycwl.org）：" DOMAIN
fi
DOMAIN="$(normalize_domain "$DOMAIN")"
valid_domain "$DOMAIN" || { echo "域名格式错误：$DOMAIN" >&2; exit 1; }

if [ -z "$EMAIL" ]; then
  read -r -p "2/8 输入 Let's Encrypt 证书邮箱：" EMAIL
fi
valid_email "$EMAIL" || { echo "邮箱格式错误：$EMAIL" >&2; exit 1; }

install -d -m 700 "$RESTORE_DIR"
if [ -z "$PACKAGE" ]; then
  PACKAGE="$(choose_latest 'laogu-recovery-*.tar.gz.age')"
  if [ -n "$PACKAGE" ]; then
    echo "自动找到最新恢复包：$PACKAGE"
  else
    read -r -p "3/8 输入加密恢复包完整路径：" PACKAGE
  fi
fi
PACKAGE="$(readlink -f "$PACKAGE")"
test -f "$PACKAGE" || { echo "找不到恢复包：$PACKAGE" >&2; exit 1; }

if [ -z "$CHECKSUM" ]; then
  candidate="${PACKAGE}.sha256"
  if [ -f "$candidate" ]; then
    CHECKSUM="$candidate"
    echo "自动找到 SHA256 文件：$CHECKSUM"
  else
    CHECKSUM="$(choose_latest "$(basename "$PACKAGE").sha256")"
  fi
fi
if [ -n "$CHECKSUM" ]; then
  CHECKSUM="$(readlink -f "$CHECKSUM")"
  test -f "$CHECKSUM" || { echo "找不到 SHA256 文件：$CHECKSUM" >&2; exit 1; }
else
  echo "提示：未提供外部 SHA256 文件，后续仍会执行恢复包内部校验。"
fi

if [ -z "$KEY" ]; then
  if [ -f "$RESTORE_DIR/laogu-backup-recovery.key" ]; then
    KEY="$RESTORE_DIR/laogu-backup-recovery.key"
    echo "自动找到 age 私钥：$KEY"
  else
    read -r -p "4/8 输入 age 私钥完整路径：" KEY
  fi
fi
KEY="$(readlink -f "$KEY")"
test -f "$KEY" || { echo "找不到 age 私钥：$KEY" >&2; exit 1; }

if systemctl is-active --quiet laogu-server 2>/dev/null || [ -f /etc/laogu/server.env ]; then
  echo "检测到现有老谷服务或 /etc/laogu/server.env。" >&2
  echo "为防止覆盖正在运行的系统，恢复已停止。请使用全新服务器。" >&2
  exit 1
fi
if [ -e "$APP" ] && [ "$(find "$APP" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  # 允许 APP 仅包含刚从 GitHub 克隆的部署脚本；生产服务、配置或数据库
  # 一旦存在仍然拒绝，避免把恢复包覆盖到正在运行的系统。
  if [ -f /etc/laogu/server.env ] || systemctl list-unit-files laogu-server.service >/dev/null 2>&1 && systemctl is-enabled laogu-server.service >/dev/null 2>&1; then
    echo "检测到已有老谷服务或配置，拒绝覆盖。" >&2
    exit 1
  fi
  if [ ! -f "$APP/deploy/ubuntu/restore.sh" ]; then
    echo "检测到非空 $APP，且不是本项目 GitHub 工作目录。" >&2
    echo "请使用全新服务器，或清空该目录后重新克隆项目。" >&2
    exit 1
  fi
  echo "检测到 GitHub 部署目录，将用恢复包中的正式源码覆盖。"
fi

echo
echo "=== 5/8 恢复前确认 ==="
echo "目标域名：$DOMAIN"
echo "恢复包：$PACKAGE"
echo "恢复包大小：$(du -h "$PACKAGE" | awk '{print $1}')"
echo "SHA256：${CHECKSUM:-未提供（将执行内部校验）}"
echo "私钥：$KEY"
echo
echo "这会在本机安装 PostgreSQL/Nginx/Python/Node.js，恢复数据库并配置 HTTPS。"
echo "不会覆盖已存在的老谷服务、生产配置或数据库。"
read -r -p '确认继续请输入 RESTORE，否则退出：' CONFIRM
[ "$CONFIRM" = "RESTORE" ] || { echo "已取消，没有修改系统。"; exit 0; }

cleanup() {
  rc=$?
  trap - EXIT
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    rm -rf -- "$WORK"
  fi
  exit "$rc"
}
trap cleanup EXIT

echo "=== 6/8 安装恢复环境 ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip postgresql postgresql-contrib \
  nginx certbot python3-certbot-nginx git ufw curl openssl ca-certificates \
  nodejs npm age rsync
timedatectl set-timezone Asia/Shanghai
systemctl enable --now postgresql nginx

echo "=== 7/8 校验、解密和恢复 ==="
if [ -n "$CHECKSUM" ]; then
  EXPECTED="$(awk 'NF {print $1; exit}' "$CHECKSUM")"
  ACTUAL="$(sha256sum "$PACKAGE" | awk '{print $1}')"
  [[ "$EXPECTED" =~ ^[0-9a-fA-F]{64}$ ]] || { echo "SHA256 文件格式错误" >&2; exit 1; }
  [ "$EXPECTED" = "$ACTUAL" ] || { echo "外部 SHA256 校验失败" >&2; exit 1; }
  echo "外部 SHA256：通过"
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

install -d -o root -g laogu -m 750 /etc/laogu
install -o root -g laogu -m 640 "$WORK/extracted/server.env" /etc/laogu/server.env
if [ -f "$WORK/extracted/backup-age-recipient.txt" ]; then
  install -o root -g root -m 600 "$WORK/extracted/backup-age-recipient.txt" /etc/laogu/backup-age-recipient.txt
fi

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

runuser -u laogu -- python3 -m venv "$APP/.venv"
runuser -u laogu -- "$APP/.venv/bin/python" -m pip install --upgrade pip
runuser -u laogu -- "$APP/.venv/bin/pip" install -r "$APP/server/requirements.txt"
runuser -u laogu -- bash -c "set -a; . /etc/laogu/server.env; set +a; cd '$APP'; .venv/bin/alembic upgrade head"
runuser -u laogu -- bash -c "cd '$APP/web'; npm ci; npm run build"
test -s "$APP/web/dist/index.html"

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
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
certbot --nginx --non-interactive --agree-tos --redirect --email "$EMAIL" -d "$DOMAIN"

echo
echo "=== 8/8 恢复验收 ==="
bash "$APP/deploy/ubuntu/verify.sh" "$DOMAIN"
echo
echo "数据库、用户、工作区、配置和程序已恢复。"
echo "请打开 https://$DOMAIN 登录确认数据，再执行："
echo "  sudo bash $APP/deploy/ubuntu/install-backup.sh"
echo "重新绑定 Telegram 备份。"
echo
echo "确认无误后删除临时私钥和恢复包："
echo "  rm -f -- '$KEY' '$PACKAGE'${CHECKSUM:+ '$CHECKSUM'}"
