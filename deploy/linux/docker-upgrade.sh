#!/usr/bin/env bash
# docbase 的 docker 升级。在**新版本**安装包目录里运行，并指向原来的安装目录（有 .env 的那个）：
#   sudo ./docker-upgrade.sh --from /opt/docbase-docker
# 做的事：备份 → 载入新镜像 → 改 .env 里的版本 → 重新创建容器（后端启动时自动升级数据库结构）→ 检查。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh
PKG=$(pwd)

FROM=""
SKIP_BACKUP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM=$2; shift 2 ;;
    --skip-backup) SKIP_BACKUP=1; shift ;;
    *) die "不认识的参数：$1" ;;
  esac
done
[ -n "$FROM" ] && [ -f "$FROM/.env" ] || die "请用 --from 指定原来的安装目录（里面要有 .env）"
VERSION=$(cat VERSION)
OLD=$(grep '^DOCBASE_VERSION=' "$FROM/.env" | cut -d= -f2)
say "docbase 升级（docker）：$OLD → $VERSION"

step "1/4 升级前备份"
if [ $SKIP_BACKUP = 1 ]; then warn "跳过了备份"; else
  (cd "$FROM" && docker compose exec -T worker python scripts/backup.py)
fi

step "2/4 载入新镜像、更新 compose 文件"
[ -f docbase-image.tar ] && docker load -i docbase-image.tar
docker image inspect "docbase:$VERSION" >/dev/null 2>&1 || die "没有镜像 docbase:$VERSION"
if [ "$PKG" != "$(cd "$FROM" && pwd)" ]; then
  cp docker-compose.yml Caddyfile.docker docker.env.example common.sh docker-install.sh docker-upgrade.sh VERSION "$FROM/"
fi
sed -i "s/^DOCBASE_VERSION=.*/DOCBASE_VERSION=$VERSION/" "$FROM/.env"
ok "版本改为 $VERSION（回退：把 .env 里的 DOCBASE_VERSION 改回 $OLD 再 docker compose up -d，必要时用备份恢复数据库）"

step "3/4 重新创建容器"
cd "$FROM"
docker compose up -d
wait_until 180 "docker compose exec -T api python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)\"" \
  || { docker compose logs --tail 50 api; die "新版本后端没有正常启动（上面是日志）"; }

step "4/4 检查"
ok "$(docker compose exec -T api python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health'))['version'])")"
say "升级完成：$OLD → $VERSION"
