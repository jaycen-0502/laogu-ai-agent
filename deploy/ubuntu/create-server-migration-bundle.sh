#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "请使用 sudo 运行此脚本" >&2
    exit 1
fi

APP="/opt/laogu-ai-agent"
ENV_FILE="/etc/laogu/server.env"
SERVICE_FILE="/etc/systemd/system/laogu-server.service"
NGINX_FILE="/etc/nginx/sites-available/laogu-server"
STAMP="$(date +%F-%H%M%S)"
ROOT="/var/backups/laogu/server-migration-${STAMP}"
PAYLOAD="${ROOT}/payload"
ARCHIVE="${ROOT}/laogu-server-migration-${STAMP}.tar.gz"

if [[ ! -d "${APP}" || ! -f "${ENV_FILE}" ]]; then
    echo "找不到生产目录或 server.env：${APP} / ${ENV_FILE}" >&2
    exit 1
fi

mkdir -p "${PAYLOAD}/etc/laogu/license" "${PAYLOAD}/etc/systemd/system" "${PAYLOAD}/etc/nginx/sites-available"
chmod 700 "${ROOT}" "${PAYLOAD}"

# Read trusted production settings only to locate the signing key files.
set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

KEY_FILE="${LAOGU_LICENSE_ISSUER_PRIVATE_KEY_FILE:-/etc/laogu/license/Laogu-License-Issuer.pem}"
PASSWORD_FILE="${LAOGU_LICENSE_ISSUER_KEY_PASSWORD_FILE:-/etc/laogu/license/Laogu-License-Password.txt}"
for required in "${KEY_FILE}" "${PASSWORD_FILE}"; do
    if [[ ! -f "${required}" ]]; then
        echo "找不到授权文件：${required}" >&2
        exit 1
    fi
done

echo "===== 导出数据库 ====="
sudo -u postgres pg_dump -Fc -d laogu > "${PAYLOAD}/laogu.dump"

echo "===== 复制生产配置 ====="
install -m 600 "${ENV_FILE}" "${PAYLOAD}/etc/laogu/server.env"
install -m 600 "${KEY_FILE}" "${PAYLOAD}/etc/laogu/license/Laogu-License-Issuer.pem"
install -m 600 "${PASSWORD_FILE}" "${PAYLOAD}/etc/laogu/license/Laogu-License-Password.txt"
if [[ -f "${SERVICE_FILE}" ]]; then
    install -m 644 "${SERVICE_FILE}" "${PAYLOAD}/etc/systemd/system/laogu-server.service"
fi
if [[ -f "${NGINX_FILE}" ]]; then
    install -m 644 "${NGINX_FILE}" "${PAYLOAD}/etc/nginx/sites-available/laogu-server"
fi

echo "===== 记录版本和恢复说明 ====="
git -C "${APP}" rev-parse HEAD > "${PAYLOAD}/source-commit.txt" 2>/dev/null || echo "unknown" > "${PAYLOAD}/source-commit.txt"
sudo -u postgres psql -At -d laogu -c "SELECT version_num FROM alembic_version LIMIT 1;" > "${PAYLOAD}/database-alembic-version.txt" || true
cat > "${PAYLOAD}/MIGRATION_MANIFEST.txt" <<EOF
Laogu server migration bundle
Created: $(date --iso-8601=seconds)
Source host: $(hostname)
Database: laogu (custom pg_dump format)

Included:
- laogu.dump
- etc/laogu/server.env
- etc/laogu/license/Laogu-License-Issuer.pem
- etc/laogu/license/Laogu-License-Password.txt
- etc/systemd/system/laogu-server.service (if present)
- etc/nginx/sites-available/laogu-server (if present)
- source-commit.txt
- database-alembic-version.txt

This archive contains production secrets. Do not commit, email, or upload it to GitHub/cloud storage.
Restore only on the replacement server with restricted permissions.
HTTPS private keys under /etc/letsencrypt are intentionally not included; re-issue the certificate on the new server.
EOF

echo "===== 生成校验清单 ====="
(cd "${PAYLOAD}" && find . -type f -not -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)

echo "===== 打包 ====="
tar -czf "${ARCHIVE}" -C "${PAYLOAD}" .
chmod 600 "${ARCHIVE}"
sha256sum "${ARCHIVE}" | tee "${ARCHIVE}.sha256"
chmod 600 "${ARCHIVE}.sha256"

echo "MIGRATION_BUNDLE=${ARCHIVE}"
echo "BUNDLE_SHA256_FILE=${ARCHIVE}.sha256"
echo "警告：迁移包包含数据库、生产配置、授权私钥和私钥密码，只能加密保存或通过加密传输。"
