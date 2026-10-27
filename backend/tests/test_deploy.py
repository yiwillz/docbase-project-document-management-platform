"""第 17 步：部署文件的静态检查（真正的安装在 CI 的 Linux 机器上跑，Windows 安装见 docs/install-windows.md）。"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / "deploy" / "linux"
WINDOWS = ROOT / "deploy" / "windows"
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def test_linux_files_use_lf():
    # CRLF 的 shell 脚本在 Linux 上跑不了（.gitattributes 也规定了 LF）
    bad = [p.relative_to(ROOT) for p in LINUX.rglob("*") if p.is_file() and b"\r\n" in p.read_bytes()]
    assert not bad


@pytest.mark.parametrize(("template", "keys"), [
    (LINUX / "templates" / "docbase.env.template", {"DATABASE_URL", "DATA_DIR", "BACKUP_DIR", "LOG_DIR", "FRONTEND_DIR"}),
    (LINUX / "templates" / "docbase-api.service", {"BACKEND_DIR", "ENV_FILE", "PYTHON"}),
    (LINUX / "templates" / "docbase-worker.service", {"BACKEND_DIR", "ENV_FILE", "PYTHON"}),
    (LINUX / "templates" / "Caddyfile.template", {"SITES"}),
    (WINDOWS / "templates" / "docbase.env.template",
     {"DATABASE_URL", "DATA_DIR", "BACKUP_DIR", "LOG_DIR", "FRONTEND_DIR", "PG_BIN"}),
    (WINDOWS / "templates" / "docbase-api.xml", {"PYTHON", "API_PORT", "BACKEND_DIR", "ENV_FILE", "DEPEND", "LOG_DIR"}),
    (WINDOWS / "templates" / "docbase-worker.xml", {"PYTHON", "BACKEND_DIR", "ENV_FILE", "DEPEND", "LOG_DIR"}),
    (WINDOWS / "templates" / "docbase-proxy.xml", {"PROXY_DIR", "LOG_DIR"}),
    (WINDOWS / "templates" / "Caddyfile.template", {"SITES", "API_PORT", "LOG_DIR"}),
])
def test_templates_only_use_known_placeholders(template, keys):
    """安装脚本只填这些占位符；模板里多了别的，安装时会停在"还有没填的占位符"。"""
    assert set(PLACEHOLDER.findall(template.read_text(encoding="utf-8"))) <= keys


def test_install_scripts_fill_every_placeholder():
    # Linux：render 调用里给的键要覆盖模板里的占位符
    sh = (LINUX / "install.sh").read_text(encoding="utf-8") + (LINUX / "upgrade.sh").read_text(encoding="utf-8")
    for key in ("DATABASE_URL", "DATA_DIR", "BACKUP_DIR", "LOG_DIR", "FRONTEND_DIR", "BACKEND_DIR", "ENV_FILE",
                "PYTHON", "SITES"):
        assert f'"{key}=' in sh, key
    # Windows：install.ps1 的哈希表里有每个键
    ps = (WINDOWS / "install.ps1").read_text(encoding="utf-8-sig")
    for key in ("DATABASE_URL", "DATA_DIR", "BACKUP_DIR", "LOG_DIR", "FRONTEND_DIR", "PG_BIN", "PYTHON",
                "BACKEND_DIR", "ENV_FILE", "API_PORT", "DEPEND", "PROXY_DIR", "SITES"):
        assert re.search(rf"\b{key} = ", ps), key


def test_shell_scripts_are_executable_in_git():
    """Windows 上提交的文件默认没有执行权限，CI 里直接 ./docker-install.sh 会报 command not found。"""
    import shutil
    import subprocess

    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("不在 git 仓库里")
    out = subprocess.run(["git", "ls-files", "-s", "--", "deploy/linux/*.sh"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out, "没找到 deploy/linux 下的 .sh"
    bad = [line.split("	")[-1] for line in out if not line.startswith("100755")]
    assert not bad, f"这些脚本没有执行权限，运行 git update-index --chmod=+x：{bad}"


def test_dockerfile_copies_existing_paths():
    text = (LINUX / "Dockerfile").read_text(encoding="utf-8")
    for src in re.findall(r"^COPY (?!--from)(.+?) \S+$", text, re.M):
        for part in src.split():
            assert (ROOT / part.rstrip("/")).exists(), part


def test_lock_file_covers_runtime_dependencies():
    """docker 镜像和安装包按 requirements.lock 装：每个运行依赖都要有固定版本。"""
    backend = ROOT / "backend"
    deps = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    locked = {line.split("==")[0].lower().replace("_", "-")
              for line in (backend / "requirements.lock").read_text(encoding="utf-8").splitlines()
              if "==" in line}
    for dep in deps:
        name = re.split(r"[\[<>=~ #]", dep.strip())[0].lower().replace("_", "-")
        assert name in locked, f"{name} 不在 requirements.lock 里（运行 python scripts/release.py --lock）"


def test_compose_file_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    data = yaml.safe_load((LINUX / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(data["services"]) == {"db", "api", "worker", "proxy"}
    assert data["services"]["worker"]["command"] == ["python", "scripts/worker.py"]
