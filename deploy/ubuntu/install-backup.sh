#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "请使用 root 运行：sudo bash deploy/ubuntu/install-backup.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$SCRIPT_DIR/ops"
SYSTEMD_DIR="$SCRIPT_DIR/systemd"

for file in laogu-telegram laogu-backup laogu-weekly-check; do
  test -f "$OPS_DIR/$file" || { echo "缺少文件：$OPS_DIR/$file" >&2; exit 1; }
done

RECIPIENT="${LAOGU_BACKUP_AGE_RECIPIENT:-}"
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"

if [ -z "$RECIPIENT" ]; then
  read -r -p "请输入 age 公钥（age1 开头）：" RECIPIENT
fi
RECIPIENT="$(printf '%s' "$RECIPIENT" | tr -d '[:space:]')"
case "$RECIPIENT" in
  age1*) ;;
  *) echo "age 公钥格式错误" >&2; exit 1 ;;
esac

if [ -z "$BOT_TOKEN" ]; then
  read -r -s -p "请输入 Telegram Bot Token（输入不会显示）：" BOT_TOKEN
  echo
fi
if ! [[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  echo "Telegram Bot Token 格式错误" >&2
  exit 1
fi

if [ -z "$CHAT_ID" ]; then
  read -r -p "请输入 Telegram Chat ID：" CHAT_ID
fi
if ! [[ "$CHAT_ID" =~ ^-?[0-9]+$ ]]; then
  echo "Telegram Chat ID 格式错误" >&2
  exit 1
fi

install -d -o root -g root -m 700 /etc/laogu /var/backups/laogu-auto /var/backups/laogu
install -o root -g root -m 700 "$OPS_DIR/laogu-telegram" /usr/local/sbin/laogu-telegram
install -o root -g root -m 700 "$OPS_DIR/laogu-backup" /usr/local/sbin/laogu-backup
install -o root -g root -m 700 "$OPS_DIR/laogu-weekly-check" /usr/local/sbin/laogu-weekly-check

printf '%s\n' "$RECIPIENT" > /etc/laogu/backup-age-recipient.txt
chmod 600 /etc/laogu/backup-age-recipient.txt

{
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$BOT_TOKEN"
  printf 'TELEGRAM_CHAT_ID=%s\n' "$CHAT_ID"
} > /etc/laogu/backup.env
chmod 600 /etc/laogu/backup.env
unset BOT_TOKEN TELEGRAM_BOT_TOKEN

install -o root -g root -m 644 "$SYSTEMD_DIR/laogu-backup.service" /etc/systemd/system/laogu-backup.service
install -o root -g root -m 644 "$SYSTEMD_DIR/laogu-backup.timer" /etc/systemd/system/laogu-backup.timer
install -o root -g root -m 644 "$SYSTEMD_DIR/laogu-weekly-check.service" /etc/systemd/system/laogu-weekly-check.service
install -o root -g root -m 644 "$SYSTEMD_DIR/laogu-weekly-check.timer" /etc/systemd/system/laogu-weekly-check.timer

systemctl daemon-reload
systemctl enable --now laogu-backup.timer laogu-weekly-check.timer

/usr/local/sbin/laogu-telegram message \
  "✅ 老谷系统备份机器人绑定成功
服务器：$(hostname)
自动备份和每周检查已启用"

echo
echo "=== 自动备份安装完成 ==="
systemctl list-timers laogu-backup.timer laogu-weekly-check.timer --no-pager
echo
echo "需要立刻测试备份时执行："
echo "  systemctl start laogu-backup.service"
echo "  journalctl -u laogu-backup.service -n 80 --no-pager"
