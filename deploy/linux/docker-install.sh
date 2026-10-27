#!/usr/bin/env bash
# docbase 的 docker 安装（任何装了 Docker Engine 和 compose 插件的 Linux）。在安装包目录里运行：
#   sudo ./docker-install.sh --data-dir /srv/docbase/data
# 参数：
#   --data-dir     文件存储目录（必填）
#   --backup-dir   备份目录，默认 /srv/docbase/backup
#   --log-dir      日志目录，默认 /srv/docbase/logs
#   --address      访问地址（IP 或名字，多个用逗号分隔），默认本机第一个 IP
#   --tz           时区（备份时间按它算），默认取系统时区
#   --admin-user / --admin-password   第一个系统管理员（不给就交互输入）
# 有 docbase-image.tar 时先 docker load（离线）；没有就从源码构建（要能上网）。重复运行是安全的。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh

DATA_DIR="" BACKUP_DIR=/srv/docbase/backup LOG_DIR=/srv/docbase/logs ADDRESS="" TZ_NAME="" ADMIN_USER="" ADMIN_PASSWORD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --data-dir) DATA_DIR=$2; shift 2 ;;
    --backup-dir) BACKUP_DIR=$2; shift 2 ;;
    --log-dir) LOG_DIR=$2; shift 2 ;;
    --address) ADDRESS=$2; shift 2 ;;
    --tz) TZ_NAME=$2; shift 2 ;;
    --admin-user) ADMIN_USER=$2; shift 2 ;;
    --admin-password) ADMIN_PASSWORD=$2; shift 2 ;;
    *) die "不认识的参数：$1（看脚本开头的说明）" ;;
  esac
done
[ -n "$DATA_DIR" ] || die "请用 --data-dir 指定文件存储目录"
VERSION=$(cat VERSION 2>/dev/null || echo latest)
say "docbase $VERSION（docker）安装"

step "1/6 检查"
command -v docker >/dev/null || die "没有 docker，请先安装 Docker Engine（https://docs.docker.com/engine/install/）"
docker compose version >/dev/null 2>&1 || die "没有 docker compose 插件（apt install docker-compose-plugin）"
docker info >/dev/null 2>&1 || die "连不上 docker（用 sudo 运行，或把当前用户加进 docker 组）"
ok "$(docker --version)；$(docker compose version)"

step "2/6 镜像"
if [ -f docbase-image.tar ]; then
  docker load -i docbase-image.tar
  ok "已从 docbase-image.tar 载入"
elif docker image inspect "docbase:$VERSION" >/dev/null 2>&1; then
  ok "已有镜像 docbase:$VERSION"
elif [ -f ../../backend/pyproject.toml ]; then
  DOCBASE_VERSION=$VERSION DB_PASSWORD=x SITE_ADDRESS=x DATA_DIR=/tmp BACKUP_DIR=/tmp LOG_DIR=/tmp docker compose build api
  ok "已从源码构建 docbase:$VERSION"
else
  die "没有镜像：安装包里没有 docbase-image.tar，也不在源码目录里"
fi

step "3/6 设置（.env）"
if [ -f .env ]; then
  ok "沿用已有的 .env（重新安装 / 修复）"
  sed -i "s/^DOCBASE_VERSION=.*/DOCBASE_VERSION=$VERSION/" .env
else
  [ -n "$ADDRESS" ] || ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}')
  [ -n "$ADDRESS" ] || ADDRESS=localhost
  sites=$(echo "$ADDRESS" | tr ',' '\n' | sed 's/^ *//; s/ *$//; /^$/d; s#^#https://#' | paste -sd, - | sed 's/,/, /g')
  [ -n "$TZ_NAME" ] || TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)
  umask 077
  cat > .env <<EOF
# docker-install.sh 生成；说明见 docker.env.example 和 docs/install-linux.md
DOCBASE_VERSION=$VERSION
DB_PASSWORD=$(random_secret)
SITE_ADDRESS="$sites, https://localhost"
HTTPS_PORT=443
HTTP_PORT=80
DATA_DIR=$DATA_DIR
BACKUP_DIR=$BACKUP_DIR
LOG_DIR=$LOG_DIR
BACKUP_HOUR=1
BACKUP_DAYS=30
TZ=$TZ_NAME
EOF
  umask 022
  ok "已写 .env（数据库密码随机生成，只有 root 能读）"
fi
set -a; . ./.env; set +a
for d in "$DATA_DIR" "$BACKUP_DIR" "$LOG_DIR"; do
  mkdir -p "$d"
  chown -R 10001:10001 "$d"   # 镜像里的 docbase 用户（uid 10001）要能写
done
ok "目录：文件 $DATA_DIR、备份 $BACKUP_DIR、日志 $LOG_DIR"

step "4/6 启动"
docker compose up -d
wait_until 120 "docker compose exec -T api python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)\"" \
  || { docker compose logs --tail 50 api; die "后端没有正常启动（上面是后端日志）"; }
ok "后端正常"

step "5/6 第一个系统管理员"
if [ -n "$ADMIN_USER" ]; then
  docker compose exec -T api python scripts/create_user.py --admin --if-none --username "$ADMIN_USER" --password "$ADMIN_PASSWORD"
else
  docker compose exec api python scripts/create_user.py --admin --if-none
fi

step "6/6 HTTPS 和证书"
wait_until 60 "curl -skf -o /dev/null https://localhost:${HTTPS_PORT:-443}/api/health" || warn "HTTPS 还没准备好，稍后看 docker compose logs proxy"
if wait_until 30 "docker compose exec -T proxy test -f /data/caddy/pki/authorities/local/root.crt"; then
  docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./docbase-root.crt >/dev/null
  ok "根证书：$(pwd)/docbase-root.crt（发给每台访问的电脑安装一次）"
else
  warn "没找到 Caddy 的根证书，稍后再导出（见 docs/install-linux.md）"
fi

say ""
say "安装完成"
echo "  访问地址：$SITE_ADDRESS"
echo "  看状态：  docker compose ps；日志：docker compose logs -f api"
echo "  备份：    每天 ${BACKUP_HOUR:-1} 点自动备份到 $BACKUP_DIR，保留 ${BACKUP_DAYS:-30} 天"
