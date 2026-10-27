# docbase 镜像：后端和 worker 共用（启动命令不同）。在仓库根目录构建：
#   docker build -f deploy/linux/Dockerfile -t docbase:版本 --build-arg VERSION=版本 .
# 一般不用手动构建：deploy/linux/docker-install.sh 或 docker compose build 会做。

# ---------------------------------------------------------------- 1. 打包前端
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- 2. 运行环境
FROM python:3.12-slim-bookworm
ARG VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    DOCBASE_FRONTEND_DIR=/opt/docbase/frontend/dist \
    DOCBASE_DATA_DIR=/var/lib/docbase/data \
    DOCBASE_BACKUP_DIR=/var/backups/docbase \
    DOCBASE_LOG_DIR=/var/log/docbase

# PostgreSQL 17 客户端（备份用的 pg_dump / pg_restore 要和数据库同一个大版本，Debian 自带的是 15，从官方源装）；
# 中文字体（MDL 导出中文 PDF 用）；时区数据
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata fonts-wqy-microhei \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Python 依赖：版本按 backend/requirements.lock（开发机测试过的版本）
WORKDIR /opt/docbase/backend
COPY backend/pyproject.toml backend/requirements.lock ./
RUN python -c "import tomllib; print('\n'.join(d.split('#')[0].strip() for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt -c requirements.lock \
 && rm /tmp/requirements.txt

# 程序（不含测试）和前端
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/scripts ./scripts
COPY --from=frontend /src/frontend/dist /opt/docbase/frontend/dist
RUN echo "$VERSION" > /opt/docbase/VERSION

# 不用 root 运行；文件、备份、日志目录挂载宿主机目录（宿主机上要给 uid 10001 写权限，安装脚本会做）
RUN groupadd --gid 10001 docbase \
 && useradd --uid 10001 --gid docbase --no-create-home --shell /usr/sbin/nologin docbase \
 && install -d -o docbase -g docbase /var/lib/docbase/data /var/backups/docbase /var/log/docbase
USER docbase

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)" || exit 1

# 默认是后端；worker 用 command: ["python", "scripts/worker.py"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
