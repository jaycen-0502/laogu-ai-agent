#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# 小白一键升级：从私有 GitHub 下载指定标签，备份当前版本，迁移数据库，
# 构建前端并执行健康检查。不会覆盖 /etc/laogu/server.env。
APP="${APP:-/opt/laogu-ai-agent}"
REPO="${GITHUB_REPOSITORY:-jaycen-0502/laogu-ai-agent}"
REF="${GITHUB_REF:-}"
TMP="$(mktemp -d /tmp/laogu-github-upgrade.XXXXXX)"
BACKUP="/var/backups/laogu"
mkdir -p "$BACKUP"
trap 'rm -rf -- "$TMP"' EXIT

if [ "$(id -u)" -ne 0 ]; then echo "请使用 sudo 执行：sudo bash $0" >&2; exit 1; fi
command -v curl >/dev/null || { echo "缺少 curl" >&2; exit 1; }
command -v tar >/dev/null || { echo "缺少 tar" >&2; exit 1; }
test -f /etc/laogu/server.env || { echo "找不到 /etc/laogu/server.env，停止升级" >&2; exit 1; }

read -r -p "GitHub 仓库 [${REPO}]：" input_repo; REPO="${input_repo:-$REPO}"
# Public repositories work anonymously. For private repositories, set
# GITHUB_TOKEN in the environment; it is used only for this process.
AUTH_ARGS=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $GITHUB_TOKEN")
fi
if [ -z "$REF" ]; then
  latest_ref="$(curl -fsSL --retry 3 "${AUTH_ARGS[@]}" -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$REPO/tags?per_page=100" | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\(v[0-9][0-9.]*\)".*/\1/p' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)"
  [ -n "$latest_ref" ] || { echo "无法读取 GitHub 最新版本标签，停止升级" >&2; exit 1; }
  REF="$latest_ref"
  echo "当前 GitHub 最新版本：$REF"
else
  [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || { echo "GITHUB_REF 格式不安全" >&2; exit 1; }
  [[ "$REF" != *..* && "$REF" != */ ]] || { echo "GITHUB_REF 格式不安全" >&2; exit 1; }
  echo "指定升级版本：$REF"
fi

echo "1/8 下载 GitHub 版本：$REPO@$REF"
curl -fsSL --retry 3 "${AUTH_ARGS[@]}" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/tarball/$REF" -o "$TMP/source.tar.gz"
tar -tzf "$TMP/source.tar.gz" >/dev/null
ROOT_DIR="$(tar -tzf "$TMP/source.tar.gz" | awk -F/ 'NR==1{print $1}')"
tar -xzf "$TMP/source.tar.gz" -C "$TMP"
SRC="$TMP/$ROOT_DIR"
test -f "$SRC/server/main.py" && test -f "$SRC/alembic.ini" && test -f "$SRC/web/package.json" || { echo "GitHub 包结构不正确，停止升级" >&2; exit 1; }
# 将本次下载的升级脚本保存为后续标准入口，后续直接运行它即可获得最新默认版本提示。
if [ -f "$SRC/deploy/ubuntu/upgrade-from-github.sh" ]; then
  install -o root -g root -m 700 "$SRC/deploy/ubuntu/upgrade-from-github.sh" /usr/local/sbin/laogu-upgrade-from-github
fi

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
APP_VERSION="$(sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SRC/web/package.json" | head -n 1)"
test -n "$APP_VERSION" || { echo "无法读取应用版本，停止升级" >&2; exit 1; }
SERVER_VERSION="$(sed -n 's/^[[:space:]]*DEFAULT_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$SRC/common/release.py" | head -n 1)"
test "$SERVER_VERSION" = "$APP_VERSION" || { echo "前后端版本不一致（Web=$APP_VERSION，Server=$SERVER_VERSION），停止升级" >&2; exit 1; }
test -f "$SRC/alembic/versions/0014_user_ai_policies.py" || { echo "缺少 0014 迁移，停止升级" >&2; exit 1; }

# Never replace a production checkout with a branch that cannot understand
# the database revision already recorded in alembic_version.
CURRENT_REVISION="$(set -a; . /etc/laogu/server.env; set +a; cd "$APP"; .venv/bin/alembic current 2>/dev/null | sed -n 's/.* \([0-9][A-Za-z0-9_-]*\).*/\1/p' | tail -n 1 || true)"
if [ -n "$CURRENT_REVISION" ] && ! find "$SRC/alembic/versions" -maxdepth 1 -type f -name "*${CURRENT_REVISION}*.py" -print -quit | grep -q .; then
  echo "目标版本缺少当前数据库迁移：$CURRENT_REVISION，停止升级" >&2
  exit 1
fi

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
# 构建脚本使用严格 umask；仅将公开静态产物设置为 Nginx 可遍历、可读。
# server.env、源码及密钥权限不会因此放宽。
chown -R laogu:laogu "$APP/web/dist"
chmod 755 "$APP" "$APP/web" "$APP/web/dist"
find "$APP/web/dist" -type d -exec chmod 755 {} +
find "$APP/web/dist" -type f -exec chmod 644 {} +
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
echo "程序版本：$APP_VERSION"
echo "GitHub 标签：$REF"
echo "备份目录：$BACKUP"
echo "注意：GitHub Token 只在本次命令内存中使用，不会写入配置文件。"
