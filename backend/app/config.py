"""配置：只从环境变量 / 配置文件读取，前缀 DOCBASE_。

配置文件默认是 backend/.env；正式安装时放在代码目录外（升级换代码不丢），由环境变量 DOCBASE_ENV_FILE 指过去
（Windows 服务、systemd、docker 都这样设）。
"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录；.env 按此定位，与启动时的当前目录无关
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOCBASE_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库连接，如 postgresql+psycopg://postgres:密码@localhost:5432/docbase
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/docbase"
    # 测试库连接（只给 pytest 用）
    test_database_url: str | None = None
    # 文件存储根目录
    data_dir: Path = BACKEND_DIR / "data"
    # 测试用文件存储目录（scripts/test_server.py 用它，不碰正式文件）
    test_data_dir: Path = BACKEND_DIR / "data_test"
    # 共享盘只读镜像目录；为空表示不做镜像
    mirror_dir: Path | None = None
    # 镜像方式：link 硬链接（同一块盘不占空间；共享必须由 IT 设成只读，否则有人改了镜像里的文件，
    # 平台里存的那一版也跟着变）/ copy 复制（多占一倍空间，最安全）。硬链接做不了时自动改复制
    mirror_mode: str = "link"
    # 镜像里文件夹名用的语言：zh / en
    mirror_lang: str = "zh"
    # 交付包做好后保留几天（之后由每天的例行清理删除）
    package_days: int = 7
    # 供应商 / 我方文件保留的版本数
    keep_versions: int = 3
    # 回收区保留天数
    recycle_days: int = 90
    # 存量导入允许读取的目录（多个用 ; 分隔），只能导入这些目录下的文件；空着表示不允许导入
    import_roots: str = ""
    # 后台 worker：每天几点做例行清理（回收区、没人引用的文件、过期暂存）
    maintenance_hour: int = 2
    # 登录：多久没操作自动退出（小时）、密码最短几位
    session_hours: int = 12
    password_min_length: int = 8
    # 只给自动测试用（连测试库时才生效，连正式库时拒绝）：
    # auth_dev_header —— 认请求头 X-User 当作已登录的系统管理员；
    # auth_dev_anonymous —— 没登录的请求也当作系统管理员 dev（pytest 用）
    auth_dev_header: bool = False
    auth_dev_anonymous: bool = False
    # 启动时自动执行数据库迁移
    auto_migrate: bool = True
    # 前端打包结果（npm run build 的 dist 目录），由后端直接提供页面；不存在时只提供接口
    frontend_dir: Path | None = BACKEND_DIR.parent / "frontend" / "dist"

    # 日志目录：为空只打印到控制台（开发时）；设了就按天写文件（api-日期.log、worker-日期.log）
    log_dir: Path | None = None
    # 日志保留天数、日志目录总大小上限（MB），超了从最旧的删
    log_days: int = 30
    log_max_mb: int = 500

    # 备份目录：为空表示不自动备份（页面提醒系统管理员）；worker 每天 backup_hour 点备份一次
    backup_dir: Path | None = None
    backup_days: int = 30
    backup_hour: int = 1
    # PostgreSQL 命令（pg_dump、pg_restore、psql）所在目录；为空时自动找（PATH、Windows 默认安装目录）
    pg_bin: Path | None = None
    # 存储 / 备份状态多久检查一次（秒）
    status_check_seconds: int = 60


def import_roots(settings: "Settings | None" = None) -> list[Path]:
    """允许导入的目录（已规范化）。"""
    raw = (settings or get_settings()).import_roots
    return [Path(p.strip()).expanduser().resolve() for p in raw.split(";") if p.strip()]


def env_file() -> Path:
    """配置文件位置：DOCBASE_ENV_FILE，没设就是 backend/.env。"""
    return Path(os.environ.get("DOCBASE_ENV_FILE") or BACKEND_DIR / ".env")


def app_version() -> str:
    """版本号：发布包里有 VERSION 文件（scripts/release.py 写）；开发时是 dev。"""
    for f in (BACKEND_DIR / "VERSION", BACKEND_DIR.parent / "VERSION"):
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    return "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_file())
