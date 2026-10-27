"""真实名称扫描：仓库里不得出现真实客户、公司、人员名称（PRD 第 11 节）。

名单不写在这里（写了就泄露了）：
- 本机：backend/tests/fixtures/private/forbidden_names.txt（私有文件夹，不提交）
- 远程 CI：环境变量 DOCBASE_FORBIDDEN_NAMES（仓库的保密设置里配，逗号或换行分隔）
都没有时给出提醒、不算失败。

用法（在 backend 目录）：
    python scripts/check_names.py            扫描仓库里会被提交的全部文件（已跟踪 + 未忽略的新文件）
    python scripts/check_names.py --staged   只扫描这次要提交的文件（提交前检查用）

按完整单词匹配、不区分大小写（名单里的 "AB1" 会命中变量名 ab1，不会命中 AB10、XAB1）。
输出只写文件和行号、名单里的第几个，不打印名字本身（CI 日志可能公开）。
另外检查私有文件夹里的文件有没有被加进提交。
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
NAMES_FILE = BACKEND / "tests" / "fixtures" / "private" / "forbidden_names.txt"
PRIVATE_PREFIX = "backend/tests/fixtures/private/"
MAX_BYTES = 5 * 1024 * 1024


def load_names() -> list[str]:
    raw = os.environ.get("DOCBASE_FORBIDDEN_NAMES")
    if raw:
        items = re.split(r"[,\n]", raw)
    elif NAMES_FILE.exists():
        items = NAMES_FILE.read_text(encoding="utf-8").splitlines()
    else:
        return []
    return [i.strip() for i in items if i.strip() and not i.strip().startswith("#")]


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def files(staged: bool) -> list[str]:
    if staged:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    else:
        out = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return [f for f in out.decode("utf-8").split("\0") if f]


def content(path: str, staged: bool) -> bytes | None:
    try:
        data = git("show", f":{path}") if staged else (ROOT / path).read_bytes()
    except (subprocess.CalledProcessError, OSError):
        return None
    if len(data) > MAX_BYTES or b"\0" in data[:8000]:
        return None  # 太大或二进制（Excel、PDF 等）跳过
    return data


def scan(staged: bool) -> int:
    paths = files(staged)
    problems = [f"{p}：私有文件夹里的文件不能提交" for p in paths if p.startswith(PRIVATE_PREFIX)]
    names = load_names()
    if not names:
        print("提醒：没有找到禁用名称清单（私有文件夹的 forbidden_names.txt 或 DOCBASE_FORBIDDEN_NAMES），"
              "跳过名称扫描")
    patterns = [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", re.IGNORECASE)
                for n in names]
    for path in paths:
        if path.startswith(PRIVATE_PREFIX):
            continue
        data = content(path, staged)
        if data is None:
            continue
        text = data.decode("utf-8", errors="ignore")
        for i, pattern in enumerate(patterns, start=1):
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{path}:{line}：命中禁用名称清单第 {i} 个")
    for p in problems:
        print(p)
    scope = "这次要提交的" if staged else "仓库里的"
    if problems:
        print(f"名称扫描不通过：{len(problems)} 处（扫描了{scope} {len(paths)} 个文件）")
        return 1
    print(f"名称扫描通过（扫描了{scope} {len(paths)} 个文件，名单 {len(names)} 个）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="真实名称扫描")
    parser.add_argument("--staged", action="store_true", help="只扫描这次要提交的文件")
    sys.exit(scan(parser.parse_args().staged))
