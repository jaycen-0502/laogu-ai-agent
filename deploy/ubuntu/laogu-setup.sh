#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

# Chinese first-run menu for a clean install, disaster recovery, upgrade, and
# verification. It deliberately delegates the actual work to the existing
# guarded scripts instead of duplicating database or TLS logic here.
APP="${APP:-/opt/laogu-ai-agent}"
REPO="${GITHUB_REPOSITORY:-jaycen-0502/laogu-ai-agent}"
REF="${GITHUB_REF:-main}"
RESTORE_DIR="${RESTORE_DIR:-/root/restore}"
TMP=""

usage() {
  cat <<'EOF'
老谷 Web 后台一键部署向导

用法：
  sudo bash laogu-setup.sh [--repo OWNER/REPO] [--ref TAG_OR_BRANCH]

菜单：
  1 全新安装   2 从加密备份恢复   3 GitHub 在线升级
  4 部署验收   5 Telegram 备份    6 查看说明   0 退出

说明：脚本只允许 Ubuntu 24.04；全新安装和恢复都会拒绝覆盖已有生产系统。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --app) APP="${2:-}"; shift 2 ;;
    --restore-dir) RESTORE_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done

if [[ -t 1 ]] && command -v tput >/dev/null 2>&1 && [[ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]]; then
  BOLD=$(tput bold); BLUE=$(tput setaf 4); GREEN=$(tput setaf 2)
  YELLOW=$(tput setaf 3); RED=$(tput setaf 1); RESET=$(tput sgr0)
else
  BOLD=""; BLUE=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

cleanup() {
  if [[ -n "${TMP:-}" && -d "$TMP" ]]; then
    rm -rf -- "$TMP"
  fi
}
trap cleanup EXIT

say() { printf '  %s\n' "$1"; }
ok() { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '  %s⚠ %s%s\n' "$YELLOW" "$1" "$RESET"; }
fail() { printf '  %s✗ %s%s\n' "$RED" "$1" "$RESET" >&2; return 1; }

clear_screen() {
  [[ -t 1 ]] || return 0
  command -v tput >/dev/null 2>&1 && tput clear || true
}

pause() {
  printf '  %s按 Enter 继续...%s ' "$YELLOW" "$RESET"
  read -r _ || true
}

confirm() {
  local answer=""
  printf '  %s%s [y/N]%s ' "$YELLOW" "$1" "$RESET"
  read -r answer || true
  [[ "$answer" =~ ^[Yy]$|^[Yy][Ee][Ss]$ ]]
}

require_root() {
  [[ "$(id -u)" -eq 0 ]] || fail "请使用 sudo 或 root 运行此脚本。"
}

require_ubuntu() {
  [[ -f /etc/os-release ]] || fail "无法识别操作系统。"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] || \
    fail "只支持 Ubuntu 24.04 LTS；当前为 ${PRETTY_NAME:-未知}。"
}

validate_repo_ref() {
  [[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail "GitHub 仓库格式错误：$REPO"
  [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$REF" != *..* && "$REF" != */ ]] || \
    fail "GitHub 版本或分支格式错误：$REF"
}

source_ready() {
  [[ -f "$APP/server/main.py" && -f "$APP/deploy/ubuntu/install.sh" &&
    -f "$APP/deploy/ubuntu/restore.sh" && -f "$APP/deploy/ubuntu/verify.sh" ]]
}

install_download_tools() {
  if command -v curl >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
    return 0
  fi
  say "首次运行需要安装下载工具 curl、tar。"
  apt-get update
  apt-get install -y ca-certificates curl tar
}

download_source() {
  validate_repo_ref
  install_download_tools
  TMP="$(mktemp -d /tmp/laogu-setup.XXXXXX)"
  chmod 700 "$TMP"
  local archive="$TMP/source.tar.gz" extract="$TMP/source" top="" token="" config="$TMP/curl.conf"
  mkdir -m 700 "$extract"

  # A public repository needs no credential. A private repository can use a
  # short-lived token entered only for this process; it is never written down.
  if ! curl -fsSL --retry 3 --proto '=https' --tlsv1.2 \
      "https://api.github.com/repos/$REPO/tarball/$REF" -o "$archive"; then
    printf '  GitHub 仓库可能是私有仓库。请输入临时读取 Token（没有则 Ctrl+C）：'
    read -r -s token
    printf '\n'
    [[ -n "$token" && "$token" != *$'\n'* && "$token" != *$'\r'* ]] || fail "没有提供可用 Token。"
    printf 'header = "Authorization: Bearer %s"\n' "$token" > "$config"
    chmod 600 "$config"
    curl -fsSL --retry 3 --proto '=https' --tlsv1.2 --config "$config" \
      "https://api.github.com/repos/$REPO/tarball/$REF" -o "$archive" || \
      fail "无法下载 GitHub 仓库，请检查仓库、版本或只读权限。"
    unset token
  fi
  tar -tzf "$archive" >/dev/null || fail "GitHub 下载包损坏。"
  top="$(tar -tzf "$archive" | sed -n '1s#/.*##p')"
  [[ "$top" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "GitHub 压缩包目录名异常。"
  tar -xzf "$archive" -C "$extract" --no-same-owner
  [[ -f "$extract/$top/server/main.py" ]] || fail "下载版本缺少 server/main.py。"

  if [[ -e "$APP" ]] && [[ -n "$(find "$APP" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    fail "$APP 已存在但不是完整源码目录；为防止覆盖，脚本已停止。"
  fi
  install -d -m 750 "$APP"
  cp -a "$extract/$top/." "$APP/"
  chmod 750 "$APP/deploy/ubuntu"/*.sh 2>/dev/null || true
  ok "已下载并准备 $REPO@$REF。"
  rm -rf -- "$TMP"
  TMP=""
}

ensure_source() {
  if source_ready; then
    ok "已找到源码：$APP"
    return 0
  fi
  say "当前服务器没有完整源码，准备从 GitHub 下载 $REPO@$REF。"
  if [[ -e "$APP" && -n "$(find "$APP" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    fail "$APP 非空但不完整；请改用干净服务器或手动清理后再运行。"
  fi
  download_source
}

ask_domain_email() {
  local domain="" email=""
  read -r -p "  生产域名（例如 api.jaycwl.org）：" domain
  read -r -p "  Let's Encrypt 证书邮箱：" email
  [[ "$domain" =~ ^https?:// ]] && domain="${domain#*://}"
  domain="${domain%%/*}"
  [[ "$domain" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]] || fail "域名格式不正确。"
  [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || fail "邮箱格式不正确。"
  DOMAIN="$domain"
  EMAIL="$email"
}

run_fresh_install() {
  clear_screen
  printf '%s%s  全新安装%s\n\n' "$BOLD" "$BLUE" "$RESET"
  ensure_source
  ask_domain_email
  say "这会在空白 Ubuntu 服务器安装 PostgreSQL、Nginx、Python、Node.js、HTTPS 和后台。"
  say "随后原有 install.sh 还会询问工作区、管理员用户名和管理员密码。"
  confirm "确认开始全新安装？" || { warn "已取消，没有修改系统。"; pause; return; }
  (cd "$APP" && bash deploy/ubuntu/install.sh --domain "$DOMAIN" --email "$EMAIL")
  ok "全新安装完成。建议立即选择菜单 5 配置 Telegram 加密备份。"
  pause
}

latest_file() {
  local pattern="$1"
  find "$RESTORE_DIR" -maxdepth 1 -type f -name "$pattern" -printf '%T@ %p\n' 2>/dev/null |
    sort -nr | sed -n '1s/^[^ ]* //p'
}

run_restore() {
  clear_screen
  printf '%s%s  从加密备份恢复%s\n\n' "$BOLD" "$BLUE" "$RESET"
  ensure_source
  install -d -m 700 "$RESTORE_DIR"
  local package checksum key
  package="$(latest_file 'laogu-recovery-*.tar.gz.age')"
  checksum=""
  key="$RESTORE_DIR/laogu-backup-recovery.key"
  if [[ -n "$package" ]]; then
    say "自动找到最新恢复包：$package"
  else
    read -r -p "  输入 .age 恢复包完整路径：" package
  fi
  package="$(readlink -f "$package")"
  [[ -f "$package" ]] || fail "找不到加密恢复包：$package"
  if [[ -f "$package.sha256" ]]; then
    checksum="$package.sha256"
  else
    read -r -p "  外部 SHA256 文件路径（没有可直接回车）：" checksum
  fi
  [[ -z "$checksum" ]] || checksum="$(readlink -f "$checksum")"
  [[ -z "$checksum" || -f "$checksum" ]] || fail "找不到 SHA256 文件：$checksum"
  if [[ ! -f "$key" ]]; then
    read -r -p "  输入 age 私钥完整路径：" key
  fi
  key="$(readlink -f "$key")"
  [[ -f "$key" ]] || fail "找不到 age 私钥：$key"
  ask_domain_email
  say "恢复只允许在全新服务器执行，会创建数据库并配置 HTTPS。"
  say "脚本会再次显示摘要，必须输入大写 RESTORE 才会真正开始。"
  confirm "确认进入恢复向导？" || { warn "已取消，没有修改系统。"; pause; return; }
  local args=(--domain "$DOMAIN" --email "$EMAIL" --package "$package" --key "$key")
  [[ -n "$checksum" ]] && args+=(--checksum "$checksum")
  (cd "$APP" && bash deploy/ubuntu/restore.sh "${args[@]}")
  ok "恢复流程完成。请登录后台并验证用户、工作区、Agent 和授权。"
  pause
}

run_upgrade() {
  clear_screen
  printf '%s%s  从 GitHub 在线升级%s\n\n' "$BOLD" "$BLUE" "$RESET"
  ensure_source
  say "升级脚本会先备份数据库和当前源码，并检查数据库迁移兼容性。"
  say "升级前请确认没有正在执行的批量任务。"
  confirm "确认开始在线升级？" || { warn "已取消，没有修改系统。"; pause; return; }
  local token=""
  printf '  GitHub 私有仓库读取 Token（公开仓库直接按 Enter）：'
  read -r -s token || true
  printf '\n'
  if [[ -x /usr/local/sbin/laogu-upgrade-from-github ]]; then
    env GITHUB_REPOSITORY="$REPO" GITHUB_REF="$REF" GITHUB_TOKEN="$token" \
      /usr/local/sbin/laogu-upgrade-from-github
  else
    (cd "$APP" && env GITHUB_REPOSITORY="$REPO" GITHUB_REF="$REF" GITHUB_TOKEN="$token" \
      bash deploy/ubuntu/upgrade-from-github.sh)
  fi
  unset token
  ok "在线升级完成。"
  pause
}

run_verify() {
  clear_screen
  printf '%s%s  部署验收%s\n\n' "$BOLD" "$BLUE" "$RESET"
  ensure_source
  local domain=""
  read -r -p "  生产域名：" domain
  domain="${domain#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"
  [[ "$domain" =~ ^([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$ ]] || fail "域名格式不正确。"
  (cd "$APP" && bash deploy/ubuntu/verify.sh "$domain")
  pause
}

run_backup_setup() {
  clear_screen
  printf '%s%s  配置 Telegram 加密备份%s\n\n' "$BOLD" "$BLUE" "$RESET"
  ensure_source
  say "备份包会使用 age 加密后发送，Bot Token 不会写入 GitHub，也不会放入备份包。"
  say "安装脚本会隐藏输入 Bot Token，并要求绑定私人管理员聊天。"
  confirm "确认配置或重新绑定 Telegram 自动备份？" || { warn "已取消，没有修改系统。"; pause; return; }
  (cd "$APP" && bash deploy/ubuntu/install-backup.sh)
  systemctl start laogu-backup.service
  journalctl -u laogu-backup.service -n 80 --no-pager
  pause
}

help_screen() {
  clear_screen
  printf '%s%s  老谷后台服务器一键向导%s\n\n' "$BOLD" "$BLUE" "$RESET"
  say "1 全新安装：只适用于空白 Ubuntu 24.04 服务器。"
  say "2 备份恢复：使用 Telegram 下载的 .tar.gz.age 和 age 私钥。"
  say "3 在线升级：从 GitHub 拉取版本，自动备份、迁移数据库并验收。"
  say "4 部署验收：检查服务、健康接口、数据库迁移和 HTTPS。"
  say "5 Telegram 备份：首次配置或重新绑定加密自动备份。"
  say "0 退出。"
  printf '\n'
  say "源码目录：$APP"
  say "GitHub：$REPO@$REF"
  say "恢复目录：$RESTORE_DIR"
  printf '\n'
  warn "不要把 age 私钥、Bot Token、server.env 或授权签发私钥提交到 GitHub。"
  pause
}

menu() {
  while true; do
    clear_screen
    printf '%s%s  老谷 Web 后台一键部署向导%s\n' "$BOLD" "$BLUE" "$RESET"
    printf '  GitHub：%s@%s\n\n' "$REPO" "$REF"
    printf '  %s1%s  全新安装\n' "$GREEN" "$RESET"
    printf '  %s2%s  从加密备份恢复\n' "$GREEN" "$RESET"
    printf '  %s3%s  GitHub 在线升级\n' "$GREEN" "$RESET"
    printf '  %s4%s  部署验收和健康检查\n' "$GREEN" "$RESET"
    printf '  %s5%s  配置 Telegram 自动备份\n' "$GREEN" "$RESET"
    printf '  %s6%s  查看说明和安全注意事项\n' "$GREEN" "$RESET"
    printf '  %s0%s  退出\n\n' "$GREEN" "$RESET"
    local choice=""
    read -r -p "  请选择菜单：" choice
    case "$choice" in
      1) run_fresh_install ;;
      2) run_restore ;;
      3) run_upgrade ;;
      4) run_verify ;;
      5) run_backup_setup ;;
      6) help_screen ;;
      0) return 0 ;;
      *) warn "请输入 0 到 6。"; pause ;;
    esac
  done
}

require_root
require_ubuntu
menu
