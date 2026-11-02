"""验证本机 Windows 部署；仅创建一个冒烟项目并归档，可选备份恢复演练。"""

import argparse
import http.cookiejar
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", type=Path, default=Path("C:/docbase"))
    parser.add_argument("--restore-drill", action="store_true")
    args = parser.parse_args()
    root = args.install_dir.resolve()
    credentials = json.loads((root / "config/setup-credentials.json").read_text(encoding="utf-8"))
    context = ssl.create_default_context(cafile=str(root / "certs/docbase-root.crt"))
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), urllib.request.HTTPCookieProcessor(cookies)
    )

    def request(path: str, data=None, method="GET"):
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            "https://localhost" + path, data=body, method=method,
            headers={"Content-Type": "application/json", "X-Docbase": "1"},
        )
        with opener.open(req, timeout=30) as response:
            content = response.read()
            return json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content

    health = request("/api/health")
    if any(health.get(key) is not True for key in ("ok", "db", "storage")):
        sys.exit(f"FAIL: health {health}")
    print("PASS: HTTPS certificate, database, storage", flush=True)
    if b'id="app"' not in request("/projects"):
        sys.exit("FAIL: frontend")
    print("PASS: frontend", flush=True)
    try:
        request("/api/projects")
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
    else:
        sys.exit("FAIL: anonymous request should return 401")
    print("PASS: anonymous request rejected", flush=True)
    request("/api/auth/login", {"username": credentials["admin_user"], "password": credentials["admin_password"]}, "POST")
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    project = request("/api/projects", {"code": f"SMOKE-{stamp}", "name": "Deployment verification"}, "POST")
    try:
        projects = request("/api/projects")
        if not any(p["id"] == project["id"] for p in projects):
            sys.exit("FAIL: created project missing")
    finally:
        request(f"/api/projects/{project['id']}/archive", {}, "POST")
    request("/api/auth/logout", {}, "POST")
    print("PASS: login, create/read/archive project, logout", flush=True)
    if args.restore_drill:
        backend = root / "app/backend"
        env = {**os.environ, "DOCBASE_ENV_FILE": str(root / "config/docbase.env"), "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, "scripts/backup.py"], cwd=backend, env=env, check=True)
        # 目录和库名均为全新的演练目标，不覆盖运行库。
        drill_dir = root / "restore-drills" / stamp
        subprocess.run(
            [sys.executable, "scripts/restore.py", "--to", f"docbase_drill_{stamp}", "--data-dir", str(drill_dir)],
            cwd=backend, env=env, check=True,
        )
        print(f"PASS: backup/restore drill; retained database docbase_drill_{stamp}, files {drill_dir}", flush=True)
    print("DEPLOYMENT VERIFIED", flush=True)


if __name__ == "__main__":
    main()
