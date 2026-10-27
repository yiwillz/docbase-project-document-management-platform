"""第 11 步：本地磁盘存储（按 SHA-256 存，同内容只存一份；先暂存、校验后再移动；盘不可用拒绝写入）。"""

import hashlib
import io
import os
import time

import pytest

from app.services.errors import Invalid
from app.services.storage import LocalDiskStorage, StorageUnavailable


@pytest.fixture
def storage(tmp_path):
    return LocalDiskStorage(tmp_path / "store")


def test_stage_and_commit_by_hash(storage):
    data = b"hello docbase"
    staging_id, sha, size = storage.stage(io.BytesIO(data))
    assert sha == hashlib.sha256(data).hexdigest() and size == len(data)
    assert storage.staged_exists(staging_id) and not storage.exists(sha)
    storage.commit(staging_id, sha)
    assert storage.exists(sha) and not storage.staged_exists(staging_id)
    assert storage.path(sha) == storage.root / "blobs" / sha[:2] / sha[2:4] / sha
    with storage.open(sha) as f:
        assert f.read() == data


def test_same_content_stored_once(storage):
    sha1 = storage.put_bytes(b"same")
    staging_id, sha2, _ = storage.stage(io.BytesIO(b"same"))
    storage.commit(staging_id, sha2)
    assert sha1 == sha2
    assert len(list((storage.root / "blobs").rglob("*"))) == 3  # 两层目录 + 一个文件
    assert not storage.staged_exists(staging_id)


def test_commit_refuses_when_content_changed(storage):
    staging_id, sha, _ = storage.stage(io.BytesIO(b"original"))
    (storage.staging / staging_id).write_bytes(b"tampered")
    with pytest.raises(Invalid) as e:
        storage.commit(staging_id, sha)
    assert e.value.code == "storage.hash_mismatch"
    assert not storage.exists(sha) and not storage.staged_exists(staging_id)


def test_too_large_is_rejected_and_not_left_behind(storage):
    with pytest.raises(Invalid) as e:
        storage.stage(io.BytesIO(b"x" * 100), max_bytes=10)
    assert e.value.code == "storage.too_large"
    assert list(storage.staging.iterdir()) == []


def test_unavailable_root_refuses_writes(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the folder should be")  # 模拟外接盘不在：目录建不出来
    storage = LocalDiskStorage(blocker / "store")
    with pytest.raises(StorageUnavailable) as e:
        storage.stage(io.BytesIO(b"data"))
    assert e.value.code == "storage.unavailable"


def test_bad_hash_rejected(storage):
    with pytest.raises(Invalid):
        storage.exists("../../etc/passwd")
    with pytest.raises(Invalid):
        storage.staged_exists("../x")


def test_old_staging_files_cleaned(storage):
    old_id, _, _ = storage.stage(io.BytesIO(b"old"))
    past = time.time() - 2 * 24 * 3600
    os.utime(storage.staging / old_id, (past, past))
    new_id, _, _ = storage.stage(io.BytesIO(b"new"))  # 每次暂存时顺手清理
    assert not storage.staged_exists(old_id) and storage.staged_exists(new_id)


def test_delete(storage):
    sha = storage.put_bytes(b"bye")
    storage.delete(sha)
    assert not storage.exists(sha)
    storage.delete(sha)  # 已不存在也不报错
