#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 小白一键升级：从私有 GitHub 下载指定标签，备份当前版本，迁移数据库，
# 构建前端并执行健康检查。不会覆盖 /etc/laogu/server.env。
APP="${APP:-/opt/laogu-ai-agent}"
REPO="${GITHUB_REPOSITORY:-jaycen-0502/laogu-ai-agent}"
REF="${GITHUB_REF:-main}"
TMP="$(mktemp -d /tmp/laogu-github-upgrade.XXXXXX)"
BACKUP="/var/backups/laogu"
mkdir -p "$BACKUP"
trap 'rm -rf -- "$TMP"' EXIT

if [ "$(id -u)" -ne 0 ]; then echo "请使用 sudo 执行：sudo bash $0" >&2; exit 1; fi
command -v curl >/dev/null || { echo "缺少 curl" >&2; exit 1; }
command -v tar >/dev/null || { echo "缺少 tar" >&2; exit 1; }
test -f /etc/laogu/server.env || { echo "找不到 /etc/laogu/server.env，停止升级" >&2; exit 1; }

read -r -p "GitHub 仓库 [${REPO}]：" input_repo; REPO="${input_repo:-$REPO}"
read -r -p "升级版本/标签 [${REF}]（建议填写 v0.20.0）：" input_ref; REF="${input_ref:-$REF}"
if [ -z "${GITHUB_TOKEN:-}" ]; then
  read -r -s -p "私有仓库 GitHub Token（输入时不显示）：" GITHUB_TOKEN; echo
fi
test -n "$GITHUB_TOKEN" || { echo "没有 GitHub Token，停止升级" >&2; exit 1; }

echo "1/8 下载 GitHub 版本：$REPO@$REF"
curl -fsSL --retry 3 -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/tarball/$REF" -o "$TMP/source.tar.gz"
tar -tzf "$TMP/source.tar.gz" >/dev/null
ROOT_DIR="$(tar -tzf "$TMP/source.tar.gz" | awk -F/ 'NR==1{print $1}')"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
SRC="$TMP/$ROOT_DIR"
test -f "$SRC/server/main.py" && test -f "$SRC/alembic.ini" && test -f "$SRC/web/package.json" || { echo "GitHub 包结构不正确，停止升级" >&2; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
echo "2/8 备份 PostgreSQL 与当前程序"
set -a; . /etc/laogu/server.env; set +a
if command -v pg_dump >/dev/null && [ -n "${LAOGU_SERVER_DATABASE_URL:-}" ]; then
  # SQLAlchemy 使用 postgresql+psycopg(2)://，pg_dump 只接受
  # postgresql://。只转换驱动前缀，不打印包含密码的连接地址。
  DB_URL="${LAOGU_SERVER_DATABASE_URL/postgresql+psycopg2/postgresql}"
  DB_URL="${DB_URL/postgresql+psycopg/postgresql}"
  pg_dump --format=custom --no-owner --no-acl \
    --file="$BACKUP/database-before-$STAMP.dump" "$DB_URL"
  pg_restore --list "$BACKUP/database-before-$STAMP.dump" >/dev/null
fi
tar -czf "$BACKUP/application-before-$STAMP.tar.gz" --exclude='server/*.db' --exclude='*.log' -C "$APP" agent alembic common server web alembic.ini

echo "3/8 检查版本与迁移"
grep -q 'version="0.20.0"' "$SRC/server/main.py" || { echo "版本不是 0.20.0，停止升级" >&2; exit 1; }
test -f "$SRC/alembic/versions/0014_user_ai_policies.py" || { echo "缺少 0014 迁移，停止升级" >&2; exit 1; }

echo "4/8 停止服务并同步代码"
systemctl stop laogu-server
for dir in agent alembic common server web; do rsync -a --delete --exclude='*.db' --exclude='*.log' "$SRC/$dir/" "$APP/$dir/"; done
cp -f "$SRC/alembic.ini" "$APP/alembic.ini"
chown -R laogu:laogu "$APP/agent" "$APP/alembic" "$APP/common" "$APP/server" "$APP/web" "$APP/alembic.ini"

echo "5/8 更新后端依赖"
runuser -u laogu -- bash -c "cd '$APP' && .venv/bin/pip install -r server/requirements.txt"
echo "6/8 执行数据库迁移"
runuser -u laogu -- bash -c "set -a; . /etc/laogu/server.env; set +a; cd '$APP'; .venv/bin/alembic upgrade head"
echo "7/8 构建前端并启动"
runuser -u laogu -- bash -c "cd '$APP/web' && npm ci --no-audit --no-fund && npm run build"
systemctl start laogu-server
systemctl is-active --quiet laogu-server
nginx -t
systemctl reload nginx

echo "8/8 健康检查"
READY=0
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "后端启动超时，最近日志如下：" >&2
  journalctl -u laogu-server -n 40 --no-pager >&2 || true
  exit 1
fi
curl -fsS http://127.0.0.1:8000/api/health
echo
curl -fsS http://127.0.0.1:8000/api/health/ready
echo
echo "=== 升级成功 ==="
echo "版本：0.20.0"
echo "备份目录：$BACKUP"
echo "注意：GitHub Token 只在本次命令内存中使用，不会写入配置文件。"
