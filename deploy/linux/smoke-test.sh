#!/usr/bin/env bash
# 装好后的冒烟测试：健康检查、页面、登录、建一个测试项目再归档、退出。
#   ./smoke-test.sh https://localhost 管理员用户名 密码
# 只建一个名为 SMOKE-时间 的项目并立即归档，不碰其他数据。证书是自签的，所以 curl 用 -k。
set -euo pipefail
cd "$(dirname "$0")"
. ./common.sh
BASE=${1:?用法：./smoke-test.sh https://地址 用户名 密码}
USER_NAME=${2:?缺用户名}
PASSWORD=${3:?缺密码}
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT
c() { curl -sk --fail-with-body -b "$JAR" -c "$JAR" "$@"; }

step "健康检查"
h=$(c "$BASE/api/health")
echo "   $h"
echo "$h" | grep -q '"ok":true' || die "健康检查不通过"
echo "$h" | grep -q '"storage":true' || die "文件存储不可用"

step "页面"
c "$BASE/projects" | grep -q '<div id="app"' || die "页面没有正常返回"
ok "页面正常"

step "没登录被拒绝"
code=$(curl -sk -o /dev/null -w '%{http_code}' "$BASE/api/projects")
[ "$code" = 401 ] || die "没登录访问项目列表应该返回 401，实际 $code"
ok "401"

step "登录、建项目、归档、退出"
c -H 'Content-Type: application/json' -d "{\"username\":\"$USER_NAME\",\"password\":\"$PASSWORD\"}" "$BASE/api/auth/login" >/dev/null
ok "登录成功"
code="SMOKE-$(date +%Y%m%d%H%M%S)"
pid=$(c -H 'Content-Type: application/json' -H 'X-Docbase: 1' -d "{\"code\":\"$code\",\"name\":\"Smoke test\"}" "$BASE/api/projects" \
  | grep -o '"id":[0-9]*' | head -1 | cut -d: -f2)
ok "建了项目 $code（id $pid）"
c "$BASE/api/projects" | grep -q "$code" || die "项目列表里没有刚建的项目"
c -X POST -H 'X-Docbase: 1' "$BASE/api/projects/$pid/archive" >/dev/null
ok "已归档"
c -X POST -H 'X-Docbase: 1' "$BASE/api/auth/logout" >/dev/null
ok "已退出"
say "冒烟测试通过"
