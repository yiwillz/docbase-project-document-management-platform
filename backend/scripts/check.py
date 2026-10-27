"""一条命令跑全部检查：代码检查（ruff）→ 真实名称扫描 → 全部测试 → 前端检查。每步结束时跑一次。

用法（在 backend 目录，激活虚拟环境后）：
    python scripts/check.py                  全部
    python scripts/check.py --quick          只跑代码检查、名称扫描、前端代码检查（几秒钟）
    python scripts/check.py --skip-frontend  不跑前端（远程 CI 里前端单独一个任务）

测试连测试库（DOCBASE_TEST_DATABASE_URL）；没配置时测试会被跳过，这里当作失败，避免"看起来全过"。
前端检查（frontend 目录）：ESLint + 界面文字检查、类型检查、Vitest 测试、打包、页面测试（Playwright，
自己启动连测试库的后端和前端，用电脑上装的 Chrome）。
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def run(title: str, cmd: list[str], cwd: Path = BACKEND) -> bool:
    print(f"\n=== {title} ===", flush=True)
    start = time.perf_counter()
    ok = subprocess.run(cmd, cwd=cwd).returncode == 0
    print(f"--- {title}：{'通过' if ok else '不通过'}（{time.perf_counter() - start:.1f} 秒）", flush=True)
    return ok


def frontend(quick: bool) -> dict[str, bool]:
    npm = shutil.which("npm")
    if npm is None or not (FRONTEND / "node_modules").exists():
        print("\n前端：没找到 npm 或还没装依赖（在 frontend 目录运行 npm install），算作不通过")
        return {"前端": False}
    steps = [("前端代码检查（ESLint + 界面文字）", "lint")]
    if not quick:
        steps += [("前端类型检查", "type-check"), ("前端测试", "test"), ("前端打包", "build"),
                  ("页面测试（Playwright）", "e2e")]
    return {title: run(title, [npm, "run", script], cwd=FRONTEND) for title, script in steps}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="全部检查")
    parser.add_argument("--quick", action="store_true", help="不跑测试")
    parser.add_argument("--skip-frontend", action="store_true", help="不跑前端检查")
    args = parser.parse_args()

    results = {
        "代码检查（ruff）": run("代码检查（ruff）", [sys.executable, "-m", "ruff", "check", "."]),
        "名称扫描": run("名称扫描", [sys.executable, "scripts/check_names.py"]),
    }
    if not args.quick:
        sys.path.insert(0, str(BACKEND))
        from app.config import get_settings

        if not get_settings().test_database_url:
            print("\n没有配置 DOCBASE_TEST_DATABASE_URL：测试会全部跳过，算作不通过")
            results["测试"] = False
        else:
            results["测试"] = run("测试", [sys.executable, "-m", "pytest", "-q"])
    if not args.skip_frontend and FRONTEND.exists():
        results.update(frontend(args.quick))

    print("\n=== 结果 ===")
    for name, ok in results.items():
        print(f"{'✅' if ok else '❌'} {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
