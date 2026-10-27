"""文件存储接口（PRD 第 11 节）：第一版只实现本地磁盘。

本地磁盘按 SHA-256 内容寻址：data_dir/blobs/ab/cd/<sha256>，同内容只存一份。
写入分两步：先收进暂存区（边写边算哈希），确认后再移到正式位置（同一块盘上的原子移动）。
外接盘拔掉 / 不可写时报 StorageUnavailable，拒绝写入，不会在别处偷偷建目录。

文件先落盘、数据库后提交：数据库失败只会留下没人引用的文件（以后由 worker 清理），
不会出现"有记录没文件"。
"""

import hashlib
import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import get_settings
from app.services.errors import Invalid, ServiceError

CHUNK = 1024 * 1024
STAGING_TTL = 24 * 3600  # 暂存区文件保留 24 小时（预览后没确认的）


class StorageUnavailable(ServiceError):
    """存储不可用（外接盘断开、目录不可写），HTTP 503。"""


class Storage(Protocol):
    def check(self) -> None:
        """可用性检查；不可用时抛 StorageUnavailable。"""

    def stage(self, stream: BinaryIO, *, max_bytes: int | None = None) -> tuple[str, str, int]:
        """收进暂存区，返回 (暂存编号, sha256, 字节数)。"""

    def staged_exists(self, staging_id: str) -> bool: ...

    def begin(self) -> str:
        """开始一次分块上传，返回暂存编号。"""

    def append(self, staging_id: str, offset: int, stream: BinaryIO, *,
               max_bytes: int | None = None) -> int:
        """从 offset 处接着写一块（offset 必须等于已收到的字节数），返回已收到的字节数。"""

    def staged_size(self, staging_id: str) -> int: ...

    def finish(self, staging_id: str) -> str:
        """分块传完：算出 sha256。"""

    def discard(self, staging_id: str) -> None: ...

    def commit(self, staging_id: str, sha256: str) -> None:
        """暂存文件校验哈希后存为正式文件；同内容已存在时直接丢弃暂存文件。"""

    def exists(self, sha256: str) -> bool: ...

    def open(self, sha256: str) -> BinaryIO: ...

    def path(self, sha256: str) -> Path | None:
        """本地文件路径（本地磁盘才有，用于直接发送文件）；其他实现返回 None。"""

    def delete(self, sha256: str) -> None: ...


def unlink(path: Path) -> None:
    """删除文件；只读的（镜像用硬链接时会和镜像里的文件一起被设成只读）先去掉只读。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except PermissionError:
        import stat

        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink(missing_ok=True)


def _check_sha(sha256: str) -> str:
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise Invalid("storage.bad_hash", f"哈希格式不对：{sha256!r}")
    return sha256


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


class LocalDiskStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.blobs = self.root / "blobs"
        self.staging = self.root / "staging"

    # ------------------------------------------------------------ 可用性
    def check(self) -> None:
        try:
            for d in (self.blobs, self.staging):
                d.mkdir(parents=True, exist_ok=True)
            probe = self.staging / f".probe-{uuid.uuid4().hex}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            raise StorageUnavailable("storage.unavailable",
                                     f"文件存储目录不可用（{self.root}）：{exc}",
                                     path=str(self.root)) from exc

    # ------------------------------------------------------------ 暂存
    def _staged(self, staging_id: str) -> Path:
        if not staging_id or not all(c in "0123456789abcdef" for c in staging_id):
            raise Invalid("storage.bad_staging_id", f"暂存编号不对：{staging_id!r}")
        return self.staging / staging_id

    def stage(self, stream: BinaryIO, *, max_bytes: int | None = None) -> tuple[str, str, int]:
        self.check()
        self.cleanup_staging()
        staging_id = uuid.uuid4().hex
        target = self._staged(staging_id)
        h, size = hashlib.sha256(), 0
        try:
            with target.open("wb") as out:
                while chunk := stream.read(CHUNK):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise Invalid("storage.too_large", f"文件超过 {max_bytes} 字节",
                                      max_bytes=max_bytes)
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return staging_id, h.hexdigest(), size

    def staged_exists(self, staging_id: str) -> bool:
        return self._staged(staging_id).is_file()

    # 分块上传（断点续传）：同一个暂存文件一块一块往后追加，断了按已收到的字节数接着传
    def begin(self) -> str:
        self.check()
        self.cleanup_staging()
        staging_id = uuid.uuid4().hex
        self._staged(staging_id).touch()
        return staging_id

    def append(self, staging_id: str, offset: int, stream: BinaryIO, *,
               max_bytes: int | None = None) -> int:
        self.check()
        path = self._staged(staging_id)
        if not path.is_file():
            raise Invalid("storage.staging_missing", "上传已失效（可能已超过 24 小时），请重新上传")
        size = path.stat().st_size
        if offset != size:
            raise Invalid("storage.bad_offset", f"应从第 {size} 字节接着传，收到的是 {offset}",
                          received=size)
        with path.open("ab") as out:
            while chunk := stream.read(CHUNK):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    out.truncate(offset)  # 这一块作废，已收到的保留
                    raise Invalid("storage.too_large", f"文件超过 {max_bytes} 字节",
                                  max_bytes=max_bytes)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        return size

    def staged_size(self, staging_id: str) -> int:
        path = self._staged(staging_id)
        if not path.is_file():
            raise Invalid("storage.staging_missing", "上传已失效（可能已超过 24 小时），请重新上传")
        return path.stat().st_size

    def finish(self, staging_id: str) -> str:
        path = self._staged(staging_id)
        if not path.is_file():
            raise Invalid("storage.staging_missing", "上传已失效（可能已超过 24 小时），请重新上传")
        return hash_file(path)

    def discard(self, staging_id: str) -> None:
        self._staged(staging_id).unlink(missing_ok=True)

    def cleanup_staging(self, max_age: float = STAGING_TTL) -> int:
        """删掉超过 24 小时的暂存文件（预览了但没确认的），返回删了几个。"""
        removed, cutoff = 0, time.time() - max_age
        for p in self.staging.glob("*"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    # ------------------------------------------------------------ 正式文件
    def _blob(self, sha256: str) -> Path:
        _check_sha(sha256)
        return self.blobs / sha256[:2] / sha256[2:4] / sha256

    def commit(self, staging_id: str, sha256: str) -> None:
        self.check()
        src, dst = self._staged(staging_id), self._blob(sha256)
        if not src.is_file():
            raise Invalid("storage.staging_missing", "暂存文件不存在（可能已超过 24 小时），请重新上传")
        if hash_file(src) != sha256:  # 校验后再移动
            src.unlink(missing_ok=True)
            raise Invalid("storage.hash_mismatch", "暂存文件内容和预览时不一致，请重新上传")
        if dst.is_file():
            src.unlink()
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    def put_bytes(self, data: bytes) -> str:
        """直接存一段内容（测试和小文件用），返回 sha256。"""
        import io

        staging_id, sha, _ = self.stage(io.BytesIO(data))
        self.commit(staging_id, sha)
        return sha

    def exists(self, sha256: str) -> bool:
        return self._blob(sha256).is_file()

    def open(self, sha256: str) -> BinaryIO:
        try:
            return self._blob(sha256).open("rb")
        except FileNotFoundError as exc:
            self.check()  # 盘不在时报"不可用"，盘在而文件没了报"找不到"
            raise StorageUnavailable("storage.blob_missing", f"文件实体不存在：{sha256}",
                                     sha256=sha256) from exc

    def path(self, sha256: str) -> Path | None:
        return self._blob(sha256)

    def delete(self, sha256: str) -> None:
        unlink(self._blob(sha256))


@lru_cache
def _local(root: str) -> LocalDiskStorage:
    return LocalDiskStorage(Path(root))


def get_storage() -> Storage:
    """当前配置的存储（第一版只有本地磁盘，目录取 DOCBASE_DATA_DIR）。"""
    return _local(str(get_settings().data_dir))
