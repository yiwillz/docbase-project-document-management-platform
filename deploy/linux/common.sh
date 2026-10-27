# shellcheck shell=bash
# docbase 的 Linux 安装脚本共用的函数（install.sh、upgrade.sh、uninstall.sh、docker-*.sh 用 . ./common.sh 引入）

say()  { printf '\033[36m%s\033[0m\n' "$*"; }
step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32m%s\033[0m\n' "$*"; }
warn() { printf '   \033[33m注意：%s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m失败：%s\033[0m\n' "$*" >&2; exit 1; }

require_root() { [ "$(id -u)" = 0 ] || die "请用 sudo 运行"; }

# 随机密码（字母数字，放进数据库连接串不用转义）
# 不用 tr </dev/urandom | head：开了 pipefail 时 tr 收到 SIGPIPE，整条命令算失败
random_secret() { head -c 16 /dev/urandom | od -An -tx1 | tr -d ' 
'; }

# 反复执行命令直到成功，最多等 N 秒
wait_until() {
  local seconds=$1; shift
  local end=$((SECONDS + seconds))
  while [ $SECONDS -lt $end ]; do
    if bash -c "$*" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

# 按模板生成文件：{{名字}} 换成值；用法 render 模板 目标 名字=值 ...
render() {
  python3 - "$@" <<'PY'
import re, sys
src, dst, *pairs = sys.argv[1:]
text = open(src, encoding="utf-8").read()
for pair in pairs:
    key, value = pair.split("=", 1)
    text = text.replace("{{%s}}" % key, value)
left = re.search(r"\{\{[A-Z_]+\}\}", text)
if left:
    sys.exit(f"模板 {src} 里还有没填的占位符：{left.group(0)}")
open(dst, "w", encoding="utf-8").write(text)
PY
}
