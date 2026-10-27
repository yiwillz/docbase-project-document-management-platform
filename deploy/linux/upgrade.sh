#!/usr/bin/env bash
# docbase 升级（systemd 安装方式）。在**新版本**安装包目录里运行：
#   sudo ./upgrade.sh
# 做的事：备份 → 停服务 → 换程序（旧的留在 /opt/docbase/app.previous）→ 装依赖 → 升级数据库结构 → 启动 → 检查。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh
PKG=$(pwd)
INSTALL=/opt/docbase
ENV_FILE=/etc/docbase/docbase.env
SKIP_BACKUP=0
[ "${1:-}" = --skip-backup ] && SKIP_BACKUP=1
require_root
[ -f $ENV_FILE ] || die "没有找到已安装的 docbase（$ENV_FILE）；新安装请用 install.sh"
VERSION=$(cat VERSION)
OLD=$(cat $INSTALL/app/VERSION 2>/dev/null || echo unknown)
say "docbase 升级：$OLD → $VERSION"
run_as_docbase() { (cd $INSTALL/app/backend && sudo -u docbase env DOCBASE_ENV_FILE="$ENV_FILE" PYTHONIOENCODING=utf-8 $INSTALL/venv/bin/python "$@"); }

step "1/5 升级前备份"
if [ $SKIP_BACKUP = 1 ]; then warn "跳过了备份"; else run_as_docbase scripts/backup.py; fi

step "2/5 停服务、换程序"
systemctl stop docbase-api docbase-worker
rm -rf $INSTALL/app.previous
cp -a $INSTALL/app $INSTALL/app.previous
rollback() {
  printf '\n\033[33m回退方法：\n  sudo systemctl stop docbase-api docbase-worker\n  sudo rm -rf %s/app && sudo mv %s/app.previous %s/app\n  如果数据库结构已经升级：用 scripts/restore.py 恢复刚才的备份（见 docs/install-linux.md）\n  sudo systemctl start docbase-api docbase-worker\033[0m\n' $INSTALL $INSTALL $INSTALL
}
trap rollback ERR
rm -rf $INSTALL/app.new
cp -a "$PKG/app" $INSTALL/app.new
cp VERSION $INSTALL/app.new/VERSION
rm -rf $INSTALL/app && mv $INSTALL/app.new $INSTALL/app
ok "旧版本留在 $INSTALL/app.previous"

step "3/5 Python 依赖、数据库结构"
$INSTALL/venv/bin/pip install --quiet --disable-pip-version-check --no-index --find-links "$PKG/wheelhouse" -r "$PKG/requirements.txt"
run_as_docbase scripts/migrate.py

step "4/5 服务配置（新版本可能改了）、启动"
for unit in docbase-api docbase-worker; do
  render "templates/$unit.service" "/etc/systemd/system/$unit.service" "BACKEND_DIR=$INSTALL/app/backend" \
    "ENV_FILE=$ENV_FILE" "PYTHON=$INSTALL/venv/bin/python"
done
systemctl daemon-reload
systemctl start docbase-api docbase-worker

step "5/5 检查"
wait_until 120 "curl -sf http://127.0.0.1:8000/api/health" || { journalctl -u docbase-api -n 40 --no-pager; false; }
trap - ERR
ok "后端正常：$(curl -s http://127.0.0.1:8000/api/health)"
say "升级完成：$OLD → $VERSION"
