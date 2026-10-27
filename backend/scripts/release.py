"""打安装包（离线安装：带上打包好的前端页面和 Python 依赖）。

用法（在 backend 目录、开发机上，需要能上网下载依赖）：
  python scripts/release.py                    # Windows 包：release/docbase-<版本>-windows.zip
  python scripts/release.py --target linux     # Linux 包：release/docbase-<版本>-linux.tar.gz（systemd 安装 + docker 文件）
  python scripts/release.py --target linux --docker-image docbase-image.tar   # 把离线 docker 镜像一起放进去
  python scripts/release.py --skip-build       # 用现有的 frontend/dist
  python scripts/release.py --version 1.0.0
  python scripts/release.py --lock             # 只更新 backend/requirements.lock（升级了依赖、测试通过后运行并提交）

Windows 包里：install.ps1 / upgrade.ps1 / uninstall.ps1、common.ps1、templates/、bin/（WinSW、Caddy）、
app/backend（不含测试）、app/frontend/dist、wheelhouse/（Python 依赖）、requirements.txt（版本固定）、docs/、VERSION。
Linux 包里：install.sh 等（systemd）、docker-install.sh、docker-compose.yml、templates/、app/、wheelhouse/、docs/、VERSION。
Python 依赖的版本按 backend/requirements.lock（开发机上测试过的版本）。
"""

import argparse
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT = BACKEND_DIR.parent
DEPLOY = ROOT / "deploy" / "windows"
LINUX = ROOT / "deploy" / "linux"
BIN_CACHE = DEPLOY / "bin"          # 已被 .gitignore 忽略
OUT_DIR = ROOT / "release"          # 已被 .gitignore 忽略
LOCK = BACKEND_DIR / "requirements.lock"

# 第三方程序：固定版本；SHA-256 记在 bin-hashes.txt（第一次下载时记下，之后每次核对，防止下载到被篡改的文件）
WINSW = ("https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe", "WinSW-x64.exe")
CADDY = ("https://github.com/caddyserver/caddy/releases/download/v2.10.2/caddy_2.10.2_windows_amd64.zip",
         "caddy.exe")
HASHES_FILE = DEPLOY / "bin-hashes.txt"

BACKEND_FILES = ["app", "alembic", "alembic.ini", "scripts", "pyproject.toml"]
DOCS = {"windows": ["install-windows.md", "migrate-machine.md"],
        "linux": ["install-linux.md", "migrate-machine.md"]}
# 下载哪个平台的依赖（都是 Python 3.12）
PLATFORMS = {"windows": ["win_amd64"],
             "linux": ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "linux_x86_64"]}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def known_hashes() -> dict[str, str]:
    if not HASHES_FILE.is_file():
        return {}
    return dict(line.split()[::-1] for line in HASHES_FILE.read_text(encoding="utf-8").splitlines() if line.strip())


def fetch_bin(url: str, name: str) -> Path:
    """下载（有缓存就用缓存）并核对 SHA-256。"""
    BIN_CACHE.mkdir(parents=True, exist_ok=True)
    target = BIN_CACHE / name
    if not target.is_file():
        print(f"下载 {url}")
        data = urllib.request.urlopen(url, timeout=120).read()  # noqa: S310  固定的 https 地址
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                data = z.read(name)
        target.write_bytes(data)
    digest = sha256(target)
    hashes = known_hashes()
    if name in hashes and hashes[name] != digest:
        target.unlink()
        sys.exit(f"{name} 的 SHA-256 和记录的不一样，可能下载到了被篡改的文件，已删除：{digest}")
    if name not in hashes:
        with open(HASHES_FILE, "a", encoding="utf-8") as f:
            f.write(f"{digest}  {name}\n")
        print(f"  记下 {name} 的 SHA-256：{digest}（{HASHES_FILE.name}，请提交）")
    return target


def runtime_requirements() -> list[str]:
    deps = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    return [d.split("#")[0].strip() for d in deps]


def write_lock() -> None:
    """把开发机虚拟环境里的版本写进 requirements.lock：安装包和 docker 镜像都按它装（测试过的版本）。"""
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True,
                            text=True, check=True).stdout
    lines = sorted(line for line in freeze.splitlines() if line and not line.startswith(("-e", "#")))
    header = "# 依赖版本（python scripts/release.py --lock 生成；安装包和 docker 镜像按它装）\n"
    LOCK.write_text(header + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"已写 {LOCK}（{len(lines)} 个包）")


def download_wheels(wheelhouse: Path, target: str) -> list[str]:
    """按 requirements.lock 的版本下载目标平台的依赖；返回固定版本的 requirements。"""
    if not LOCK.is_file():
        sys.exit("没有 backend/requirements.lock，先运行 python scripts/release.py --lock")
    platforms = [arg for p in PLATFORMS[target] for arg in ("--platform", p)]
    cmd = [sys.executable, "-m", "pip", "download", "--only-binary=:all:", *platforms,
           "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "-d", str(wheelhouse),
           "-c", str(LOCK), "--disable-pip-version-check", "--quiet", *runtime_requirements()]
    subprocess.run(cmd, check=True)
    pins = []
    for whl in sorted(wheelhouse.glob("*.whl")):
        name, version = whl.name.split("-")[:2]
        pins.append(f"{name.replace('_', '-').lower()}=={version}")
    return pins


def ensure_bom(text: str) -> bytes:
    """PowerShell 5.1 在中文 Windows 上把没有 BOM 的脚本按 GBK 读，中文会乱码：脚本一律存成带 BOM 的 UTF-8。"""
    return b"\xef\xbb\xbf" + text.lstrip("\ufeff").encode("utf-8")


def lf(path: Path) -> bytes:
    """Linux 用的文件一律 LF 换行（在 Windows 上检出的可能是 CRLF，脚本会跑不了）。"""
    return path.read_bytes().replace(b"\r\n", b"\n")


def default_version() -> str:
    base = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        rev = "nogit"
    return f"{base}-{date.today():%Y%m%d}-{rev}"


def copy_app(stage: Path, dist: Path) -> None:
    (stage / "app" / "backend").mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.log")
    for item in BACKEND_FILES:
        src = BACKEND_DIR / item
        dst = stage / "app" / "backend" / item
        shutil.copytree(src, dst, ignore=ignore) if src.is_dir() else shutil.copy2(src, dst)
    shutil.copytree(dist, stage / "app" / "frontend" / "dist")


def stage_windows(stage: Path) -> None:
    for ps1 in DEPLOY.glob("*.ps1"):
        (stage / ps1.name).write_bytes(ensure_bom(ps1.read_text(encoding="utf-8-sig")))
    shutil.copytree(DEPLOY / "templates", stage / "templates")
    (stage / "bin").mkdir()
    for url, exe in (WINSW, CADDY):
        shutil.copy2(fetch_bin(url, exe), stage / "bin" / exe)
    # 文件名用英文：有些解压工具不认 zip 里的中文文件名
    readme = [
        "docbase 安装包",
        "",
        "新安装：先装好 Python 3.12 和 PostgreSQL 17，再用管理员 PowerShell 在本目录运行：",
        "  Set-ExecutionPolicy -Scope Process Bypass",
        r"  .\install.ps1 -DataDir E:\docbase-data",
        "",
        r"升级：管理员 PowerShell 在新版本的本目录运行 .\upgrade.ps1",
        "",
        r"详细说明见 docs\install-windows.md",
    ]
    (stage / "README.txt").write_text("\r\n".join(readme) + "\r\n", encoding="utf-8-sig")


def stage_linux(stage: Path, docker_image: Path | None) -> None:
    for f in LINUX.iterdir():
        if f.is_file():
            (stage / f.name).write_bytes(lf(f))
    for f in (LINUX / "templates").rglob("*"):
        if f.is_file():
            dst = stage / "templates" / f.relative_to(LINUX / "templates")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(lf(f))
    (stage / "nginx").mkdir()
    (stage / "nginx" / "docbase.conf").write_bytes(lf(ROOT / "deploy" / "nginx" / "docbase.conf"))
    if docker_image:
        shutil.copy2(docker_image, stage / "docbase-image.tar")
    readme = [
        "docbase 安装包（Linux）",
        "",
        "用 docker：                    sudo ./docker-install.sh --data-dir /srv/docbase/data",
        "不用 docker（Ubuntu 24.04）：  sudo ./install.sh --data-dir /srv/docbase/data",
        "",
        "详细说明见 docs/install-linux.md",
    ]
    (stage / "README.txt").write_text("\n".join(readme) + "\n", encoding="utf-8", newline="\n")


def build(version: str, skip_build: bool, target: str = "windows", docker_image: Path | None = None) -> Path:
    if not skip_build:
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        sys.exit("没有 frontend/dist，请先 npm run build（或去掉 --skip-build）")

    name = f"docbase-{version}-{target}"
    stage = OUT_DIR / name
    if stage.exists():
        shutil.rmtree(stage)
    copy_app(stage, dist)
    if target == "windows":
        stage_windows(stage)
    else:
        stage_linux(stage, docker_image)
    (stage / "docs").mkdir()
    for doc in DOCS[target]:
        if (ROOT / "docs" / doc).is_file():
            shutil.copy2(ROOT / "docs" / doc, stage / "docs" / doc)
    # 生成的文本文件一律 LF（Windows 上 write_text 默认写 CRLF，Linux 上 VERSION 带 \r 会出错）
    (stage / "VERSION").write_text(version + "\n", encoding="utf-8", newline="\n")

    print(f"下载 Python 依赖（{target}、Python 3.12）…")
    wheelhouse = stage / "wheelhouse"
    wheelhouse.mkdir()
    pins = download_wheels(wheelhouse, target)
    (stage / "requirements.txt").write_text("\n".join(pins) + "\n", encoding="utf-8", newline="\n")

    # 发布包里不能有私有数据
    for p in stage.rglob("*"):
        if "private" in p.parts or p.name == ".env":
            sys.exit(f"发布包里不该有：{p}")

    if target == "windows":
        out = OUT_DIR / f"{name}.zip"
        out.unlink(missing_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, Path(name) / p.relative_to(stage))
    else:
        out = OUT_DIR / f"{name}.tar.gz"
        out.unlink(missing_ok=True)

        def owner_and_mode(info: tarfile.TarInfo) -> tarfile.TarInfo:
            # 脚本要能执行（在 Windows 上打包时文件没有执行权限）
            info.mode = 0o755 if info.isdir() or info.name.endswith(".sh") else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            return info

        with tarfile.open(out, "w:gz") as t:
            t.add(stage, arcname=name, filter=owner_and_mode)
    size = out.stat().st_size / 1e6
    print(f"已生成 {out}（{size:.0f} MB，{len(pins)} 个 Python 依赖包）")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="打安装包")
    parser.add_argument("--target", choices=["windows", "linux"], default="windows")
    parser.add_argument("--version", default=None)
    parser.add_argument("--skip-build", action="store_true", help="不重新打包前端")
    parser.add_argument("--docker-image", type=Path, help="Linux 包里一起放的离线镜像（docker save 的 tar）")
    parser.add_argument("--lock", action="store_true", help="只更新 requirements.lock")
    args = parser.parse_args()
    if args.lock:
        write_lock()
        return
    version = args.version or default_version()
    if not re.fullmatch(r"[0-9A-Za-z.\-+]+", version):
        sys.exit("版本号只能用字母、数字和 . - +")
    build(version, args.skip_build, args.target, args.docker_image)


if __name__ == "__main__":
    main()
