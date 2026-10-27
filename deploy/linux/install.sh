#!/usr/bin/env bash
# docbase 安装（不用 docker，Ubuntu 24.04 LTS）。在解压后的安装包目录里运行：
#   sudo ./install.sh --data-dir /srv/docbase/data
# 参数：
#   --data-dir       文件存储目录（必填）
#   --backup-dir     备份目录，默认 /srv/docbase/backup
#   --address        访问地址（IP 或名字，多个用逗号分隔），默认本机第一个 IP
#   --proxy          caddy（默认，自签证书）/ nginx（只给配置示例，证书自己配）/ none
#   --database-url   用已有的数据库（不在本机自动建库）
#   --admin-user / --admin-password   第一个系统管理员（不给就交互输入）
# 会用 apt 安装 python3.12-venv、postgresql、caddy（服务器要能连 apt 源或内网镜像）；Python 依赖从安装包离线装。
# 重复运行是安全的：已有的配置、数据库、管理员都沿用。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh
PKG=$(pwd)

DATA_DIR="" BACKUP_DIR=/srv/docbase/backup ADDRESS="" PROXY=caddy DATABASE_URL="" ADMIN_USER="" ADMIN_PASSWORD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --data-dir) DATA_DIR=$2; shift 2 ;;
    --backup-dir) BACKUP_DIR=$2; shift 2 ;;
    --address) ADDRESS=$2; shift 2 ;;
    --proxy) PROXY=$2; shift 2 ;;
    --database-url) DATABASE_URL=$2; shift 2 ;;
    --admin-user) ADMIN_USER=$2; shift 2 ;;
    --admin-password) ADMIN_PASSWORD=$2; shift 2 ;;
    *) die "不认识的参数：$1（看脚本开头的说明）" ;;
  esac
done
[ -n "$DATA_DIR" ] || die "请用 --data-dir 指定文件存储目录"
case "$PROXY" in caddy|nginx|none) ;; *) die "--proxy 只能是 caddy、nginx 或 none" ;; esac
require_root
VERSION=$(cat VERSION)
INSTALL=/opt/docbase
ETC=/etc/docbase
ENV_FILE=$ETC/docbase.env
LOG_DIR=/var/log/docbase
say "docbase $VERSION 安装（systemd）"

step "1/8 检查系统、安装系统软件"
. /etc/os-release
if [ "${ID:-}" != ubuntu ] || [ "${VERSION_ID:-}" != 24.04 ]; then
  warn "这个脚本按 Ubuntu 24.04 写的，当前是 ${PRETTY_NAME:-未知}；其他系统建议用 docker-install.sh"
fi
pkgs="python3.12 python3.12-venv curl"
[ -z "$DATABASE_URL" ] && pkgs="$pkgs postgresql"
[ "$PROXY" = caddy ] && pkgs="$pkgs caddy"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# shellcheck disable=SC2086
apt-get install -y -qq $pkgs >/dev/null
ok "已安装：$pkgs"

step "2/8 用户和目录"
id docbase >/dev/null 2>&1 || useradd --system --home-dir $INSTALL --shell /usr/sbin/nologin docbase
mkdir -p $INSTALL $ETC "$LOG_DIR" "$DATA_DIR" "$BACKUP_DIR"
chown docbase:docbase "$LOG_DIR" "$DATA_DIR" "$BACKUP_DIR"
ok "程序 $INSTALL、配置 $ETC、日志 $LOG_DIR、文件 $DATA_DIR、备份 $BACKUP_DIR"
if [ "$(df --output=source "$DATA_DIR" | tail -1)" = "$(df --output=source "$BACKUP_DIR" | tail -1)" ]; then
  warn "文件存储和备份在同一块盘上：盘坏了两份一起丢。建议备份放到另一块盘或 NAS"
fi

step "3/8 程序和 Python 依赖（离线）"
systemctl stop docbase-api docbase-worker 2>/dev/null || true
rm -rf $INSTALL/app.new
cp -a "$PKG/app" $INSTALL/app.new
cp VERSION $INSTALL/app.new/VERSION
rm -rf $INSTALL/app && mv $INSTALL/app.new $INSTALL/app
[ -x $INSTALL/venv/bin/python ] || python3.12 -m venv $INSTALL/venv
$INSTALL/venv/bin/pip install --quiet --disable-pip-version-check --no-index --find-links "$PKG/wheelhouse" -r "$PKG/requirements.txt"
chown -R root:root $INSTALL/app $INSTALL/venv
ok "完成"

step "4/8 数据库"
if [ -f "$ENV_FILE" ]; then
  ok "已有配置 $ENV_FILE，沿用其中的数据库"
elif [ -n "$DATABASE_URL" ]; then
  ok "用指定的数据库"
else
  systemctl enable --now postgresql >/dev/null
  pw=$(random_secret)
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='docbase'" | grep -q 1; then
    sudo -u postgres psql -qc "ALTER ROLE docbase LOGIN CREATEDB PASSWORD '$pw'"
  else
    # CREATEDB：恢复演练时要能建新库（scripts/restore.py --to 新库名）
    sudo -u postgres psql -qc "CREATE ROLE docbase LOGIN CREATEDB PASSWORD '$pw'"
  fi
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='docbase'" | grep -q 1; then
    warn "数据库 docbase 已经存在，沿用（不会清空）"
  else
    sudo -u postgres createdb -O docbase -E UTF8 -T template0 docbase
  fi
  DATABASE_URL="postgresql+psycopg://docbase:$pw@localhost:5432/docbase"
  ok "数据库 docbase、账号 docbase（随机密码，写在配置文件里）"
fi

step "5/8 配置文件"
if [ ! -f "$ENV_FILE" ]; then
  render templates/docbase.env.template "$ENV_FILE" "DATABASE_URL=$DATABASE_URL" "DATA_DIR=$DATA_DIR" \
    "BACKUP_DIR=$BACKUP_DIR" "LOG_DIR=$LOG_DIR" "FRONTEND_DIR=$INSTALL/app/frontend/dist"
  chown root:docbase "$ENV_FILE"
  chmod 640 "$ENV_FILE"   # 里面有数据库密码
  ok "已写 $ENV_FILE"
else
  ok "沿用已有的配置文件"
fi

run_as_docbase() { (cd $INSTALL/app/backend && sudo -u docbase env DOCBASE_ENV_FILE="$ENV_FILE" PYTHONIOENCODING=utf-8 $INSTALL/venv/bin/python "$@"); }

step "6/8 数据库结构、第一个系统管理员"
run_as_docbase scripts/migrate.py
if [ -n "$ADMIN_USER" ]; then
  run_as_docbase scripts/create_user.py --admin --if-none --username "$ADMIN_USER" --password "$ADMIN_PASSWORD"
else
  run_as_docbase scripts/create_user.py --admin --if-none
fi

step "7/8 服务"
for unit in docbase-api docbase-worker; do
  render "templates/$unit.service" "/etc/systemd/system/$unit.service" "BACKEND_DIR=$INSTALL/app/backend" \
    "ENV_FILE=$ENV_FILE" "PYTHON=$INSTALL/venv/bin/python"
done
systemctl daemon-reload
systemctl enable --now docbase-api docbase-worker >/dev/null
wait_until 90 "curl -sf http://127.0.0.1:8000/api/health" || { journalctl -u docbase-api -n 40 --no-pager; die "后端没有正常启动（上面是日志）"; }
ok "docbase-api、docbase-worker 已启动，开机自动启动"

step "8/8 HTTPS"
[ -n "$ADDRESS" ] || ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}')
sites=$( (echo "$ADDRESS" | tr ',' '\n'; echo localhost) | sed 's/^ *//; s/ *$//; /^$/d; s#^#https://#' | sort -u | paste -sd, - | sed 's/,/, /g')
case "$PROXY" in
  caddy)
    [ -f /etc/caddy/Caddyfile ] && [ ! -f /etc/caddy/Caddyfile.before-docbase ] && cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-docbase
    render templates/Caddyfile.template /etc/caddy/Caddyfile "SITES=$sites"
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || die "Caddyfile 检查没通过"
    systemctl enable caddy >/dev/null && systemctl restart caddy
    if wait_until 60 "curl -skf -o /dev/null https://localhost/api/health"; then ok "HTTPS 正常：$sites"
    else warn "HTTPS 还没准备好，看 journalctl -u caddy"; fi
    root=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
    if wait_until 30 "test -f $root"; then
      cp $root $ETC/docbase-root.crt
      ok "根证书：$ETC/docbase-root.crt（发给每台访问的电脑安装一次）"
    fi
    ;;
  nginx) warn "请按 nginx/docbase.conf 配置 Nginx 和证书（见 docs/install-linux.md）" ;;
  none) warn "没有配置 HTTPS：后端只在本机 127.0.0.1:8000，外面访问不到" ;;
esac
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ok "防火墙已放行 80、443"
fi

say ""
say "安装完成"
echo "  访问地址：$sites"
echo "  配置：    $ENV_FILE；日志：$LOG_DIR（以及 journalctl -u docbase-api）"
echo "  备份：    每天 1 点自动备份到 $BACKUP_DIR，保留 30 天"
