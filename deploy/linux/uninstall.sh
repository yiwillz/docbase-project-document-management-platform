#!/usr/bin/env bash
# docbase 卸载（systemd 安装方式）：停止并删除两个服务。
#   sudo ./uninstall.sh                  # 只删服务；程序、配置、日志、数据库、文件、备份都留着
#   sudo ./uninstall.sh --remove-program # 另外删掉 /opt/docbase（程序和虚拟环境）
# 数据库、文件存储、备份、/etc/docbase 一律不删；Caddy 配置恢复成安装前的（如果有备份）。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh
require_root

step "停止并删除服务"
systemctl disable --now docbase-api docbase-worker 2>/dev/null || true
rm -f /etc/systemd/system/docbase-api.service /etc/systemd/system/docbase-worker.service
systemctl daemon-reload
ok "已删除 docbase-api、docbase-worker"
if [ -f /etc/caddy/Caddyfile.before-docbase ]; then
  mv /etc/caddy/Caddyfile.before-docbase /etc/caddy/Caddyfile
  systemctl reload caddy 2>/dev/null || true
  ok "Caddy 配置已恢复成安装前的"
fi
if [ "${1:-}" = --remove-program ]; then
  rm -rf /opt/docbase
  ok "已删除 /opt/docbase"
fi
say "卸载完成。数据库、文件存储、备份、/etc/docbase 没有动。"
