"""FastAPI 入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import logs
from app.api import (
    auth,
    change_logs,
    closeout,
    deliverables,
    delivery,
    equipment,
    files,
    imports,
    mdl,
    mdl_versions,
    projects,
    review_codes,
    status_mappings,
    structure,
    structure_import,
    transmittals,
)
from app.api import (
    system as system_api,
)
from app.api.deps import check_access
from app.config import app_version, get_settings
from app.db import check_db
from app.migrate import run_migrations
from app.services import system
from app.services.errors import Conflict, Forbidden, Invalid, NotFound, ServiceError, Unauthorized
from app.services.storage import StorageUnavailable


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logs.setup("api")
    if get_settings().auto_migrate:
        await run_in_threadpool(run_migrations)
    yield


# 每个接口都先过权限检查（规则在 services/access.py）
app = FastAPI(title="docbase", version=app_version(), lifespan=lifespan, dependencies=[Depends(check_access)])

# 业务错误 → HTTP 状态码
_STATUS = {NotFound: 404, Conflict: 409, Invalid: 422, Unauthorized: 401, Forbidden: 403,
           StorageUnavailable: 503}


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS.get(type(exc), 400),
        content={"detail": {"code": exc.code, "message": exc.message, "params": exc.params}},
    )


app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(structure.router)
app.include_router(structure_import.router)
app.include_router(status_mappings.router)
app.include_router(mdl_versions.router)
app.include_router(deliverables.router)
app.include_router(equipment.router)
app.include_router(mdl.router)
app.include_router(change_logs.router)
app.include_router(change_logs.project_router)
app.include_router(review_codes.router)
app.include_router(transmittals.router)
app.include_router(closeout.router)
app.include_router(files.router)
app.include_router(imports.router)
app.include_router(delivery.router)
app.include_router(system_api.router)


@app.get("/api/health")
def health() -> dict:
    # database：当前连的库名，写数据前可据此确认没连错库
    database = make_url(get_settings().database_url).database
    storage = system.storage_status()["ok"]
    db = check_db()
    return {"ok": db and storage, "db": db, "storage": storage, "database": database, "version": app_version()}


# ---------------------------------------------------------------- 前端页面
# 不另开路由：其他地址都没匹配上（404）时，GET 请求且不是 /api 开头的，返回前端文件；
# 找不到文件的返回 index.html（前端路由），这样在浏览器里刷新 /projects/12/matrix 也不会 404。
# 接口的尾斜杠重定向、405 等行为不受影响。


def _frontend_file(path: str) -> Path | None:
    root = get_settings().frontend_dir
    if root is None or not (root / "index.html").is_file():
        return None
    target = (root / path).resolve()
    if path and target.is_file() and target.is_relative_to(root.resolve()):
        return target
    return root / "index.html"


@app.exception_handler(StarletteHTTPException)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    path = request.url.path.lstrip("/")
    if exc.status_code != 404 or request.method not in ("GET", "HEAD") or path == "api"             or path.startswith("api/"):
        return await http_exception_handler(request, exc)
    file = _frontend_file(path)
    if file is None:
        return JSONResponse(status_code=404, content={"detail": {
            "code": "frontend.not_built", "message": "前端还没打包：在 frontend 目录运行 npm run build"}})
    return FileResponse(file)
