#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN="${1:-}"
APP=/opt/laogu-ai-agent

if [ -z "$DOMAIN" ]; then
  read -r -p "请输入系统域名（例如 api.jaycwl.org）：" DOMAIN
fi
if ! [[ "$DOMAIN" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]]; then
  echo "域名格式错误：$DOMAIN" >&2
  exit 1
fi

echo "=== 服务状态 ==="
for service in laogu-server nginx postgresql; do
  printf '%s：' "$service"
  systemctl is-active "$service"
done

echo "=== 本机健康检查 ==="
curl -fsS http://127.0.0.1:8000/api/health
echo
curl -fsS http://127.0.0.1:8000/api/health/ready
echo

echo "=== 公网健康检查 ==="
curl -fsS "https://$DOMAIN/api/health"
echo

echo "=== API 版本 ==="
OPENAPI_FILE="$(mktemp)"
trap 'rm -f -- "$OPENAPI_FILE"' EXIT
curl -fsS http://127.0.0.1:8000/openapi.json -o "$OPENAPI_FILE"
python3 - "$OPENAPI_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
print("title:", data["info"]["title"])
print("version:", data["info"]["version"])
PY

echo "=== 数据库迁移 ==="
runuser -u laogu -- bash -c "set -a; . /etc/laogu/server.env; set +a; cd '$APP'; .venv/bin/alembic current"

echo "=== Nginx 配置 ==="
nginx -t

echo "=== 定时器 ==="
systemctl list-timers laogu-backup.timer laogu-weekly-check.timer --no-pager 2>/dev/null || true

echo "=== 验收通过 ==="
