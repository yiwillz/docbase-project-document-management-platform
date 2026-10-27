"""第 11 步：存文件（方式一）——上传预览 → 确认、升版、去重、版本保留、回收区清理、下载、命名模板。

虚构项目 FL-1：
M1
  1.1 整个种类 Spec
  1.2 按设备 Datasheet：72 Mixer（供应商 Vendor A）、73 Mixer、Beta part（没编号，3000 个）
  1.3 按模块 A、B Layout
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import ChangeLog, Deliverable, Event, Version
from app.services import files as svc
from app.services.history import Actor
from app.services.storage import LocalDiskStorage, get_storage

USER = {"X-User": "alice"}


def _url(project, path=""):
    return f"/api/projects/{project['id']}/{path}"


def call(api_client, method, project, path, body=None, status=200, **kwargs):
    resp = api_client.request(method, _url(project, path), json=body, headers=USER, **kwargs)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp


def upload(api_client, project, ids, name, content, *, modified=None, status=200):
    data = {"deliverable_ids": ",".join(str(i) for i in ids)}
    if modified:
        data["modified"] = modified
    resp = api_client.post(_url(project, "files/preview"), headers=USER, data=data,
                           files={"file": (name, content, "application/octet-stream")})
    assert resp.status_code == status, resp.text
    return resp.json()


def store(api_client, project, ids, name, content, overrides=None, **kw):
    p = upload(api_client, project, ids, name, content, **kw)
    return p, call(api_client, "POST", project, f"files/uploads/{p['token']}/confirm", overrides or {})


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "FL-1", "name": "File demo"},
                           headers=USER).json()


@pytest.fixture
def d(api_client, project, session):
    """应交项 id：spec、72、73、part、A、B。"""
    m1 = call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)
    docs = call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m1["id"], "rows": [{"name": "Spec"}, {"name": "Datasheet"}, {"name": "Layout"}]}, 201)
    eq = {}
    for e in [{"tag": "72", "name": "Mixer", "vendor": "Vendor A"}, {"tag": "73", "name": "Mixer"},
              {"name": "Beta part", "quantity": 3000}]:
        eq[e.get("tag") or "part"] = call(api_client, "POST", project, "equipment", e, 201)["id"]
    call(api_client, "POST", project, "document-types/bulk-equipment", {
        "document_type_ids": [docs[1]["id"]], "all_equipment": True, "mode": "add"})
    call(api_client, "PUT", project, f"document-types/{docs[2]['id']}/modules", {"module_names": ["A", "B"]})
    modules = {m["id"]: m["name"] for m in call(api_client, "GET", project, "modules")}
    by_eq = {v: k for k, v in eq.items()}
    out = {"equipment": eq}
    for row in session.scalars(select(Deliverable).where(Deliverable.project_id == project["id"],
                                                         Deliverable.obsolete.is_(False))):
        if row.document_type_id == docs[0]["id"]:
            out["spec"] = row.id
        elif row.equipment_id:
            out[by_eq[row.equipment_id]] = row.id
        else:
            out[modules[row.module_id]] = row.id
    return out


# ---------------------------------------------------------------- 上传与命名


def test_per_equipment_upload_preview_and_confirm(api_client, project, d, session):
    p = upload(api_client, project, [d["72"]], "mixer datasheet 2026-02-26 Rev B.pdf", b"v1")
    assert p["ok"] and p["mode"] == "new" and p["version_no"] == 1
    assert p["filename"] == "1.2.72_Datasheet-Mixer-20260226-V1.pdf"
    assert p["source"] == "vendor"  # 设备有供应商
    assert (p["file_date"], p["date_source"]) == ("2026-02-26", "filename")
    assert p["original_version"] == "Rev B"
    # 预览不入库
    assert session.scalar(select(Version).limit(1)) is None

    r = call(api_client, "POST", project, f"files/uploads/{p['token']}/confirm", {})
    assert r["mode"] == "new" and r["version_no"] == 1 and r["recycled"] == []
    files = call(api_client, "GET", project, f"deliverables/{d['72']}/files")
    assert len(files) == 1 and files[0]["source"] == "vendor"
    v = files[0]["versions"][0]
    assert v["filename"] == "1.2.72_Datasheet-Mixer-20260226-V1.pdf" and v["size"] == 2
    assert v["uploaded_by"] == "alice"
    # 应交矩阵显示有文件
    item = next(i for i in call(api_client, "GET", project, "deliverables")["items"] if i["id"] == d["72"])
    assert (item["file_count"], item["current_version"]) == (1, 1)
    assert item["status"] == "not_submitted"  # 上传不改状态
    # 事件和变更记录
    event = session.scalar(select(Event).where(Event.event_type == "version.created"))
    assert event.payload["deliverable_ids"] == [d["72"]] and event.project_id == project["id"]
    logs = {log.entity_type for log in session.scalars(select(ChangeLog).where(
        ChangeLog.project_id == project["id"], ChangeLog.entity_type.in_(["files", "versions"])))}
    assert logs == {"files", "versions"}
    # 同一个 token 不能再存一次
    call(api_client, "POST", project, f"files/uploads/{p['token']}/confirm", {}, 409)


def test_reupload_bumps_version(api_client, project, d):
    store(api_client, project, [d["72"]], "datasheet.pdf", b"v1", modified="2026-03-01")
    p, r = store(api_client, project, [d["72"]], "datasheet V2.pdf", b"v2", modified="2026-03-05")
    assert p["mode"] == "version" and r["version_no"] == 2
    assert p["filename"] == "1.2.72_Datasheet-Mixer-20260305-V2.pdf"
    assert p["date_source"] == "modified"
    files = call(api_client, "GET", project, f"deliverables/{d['72']}/files")
    assert [v["version_no"] for v in files[0]["versions"]] == [2, 1]


def test_duplicate_content_blocked_with_location(api_client, project, d):
    store(api_client, project, [d["72"]], "datasheet.pdf", b"same bytes")
    p = upload(api_client, project, [d["spec"]], "copy.pdf", b"same bytes")
    assert not p["ok"] and p["duplicate"]["deliverable_ids"] == [d["72"]]
    assert p["duplicate"]["filename"].startswith("1.2.72_Datasheet-Mixer-")
    resp = call(api_client, "POST", project, f"files/uploads/{p['token']}/confirm", {}, 409)
    assert resp["detail"]["code"] == "file.duplicate"


def test_general_files_distinguished_by_title(api_client, project, d):
    _, a1 = store(api_client, project, [d["spec"]], "Area A V1.pdf", b"a1")
    _, b1 = store(api_client, project, [d["spec"]], "Area B.pdf", b"b1")
    p, a2 = store(api_client, project, [d["spec"]], "area_a_v2.pdf", b"a2")
    assert a1["file_id"] != b1["file_id"] and a2["file_id"] == a1["file_id"]
    assert p["mode"] == "version" and p["title"] == "Area A"  # 升版沿用原标题
    assert a2["filename"].startswith("1.1_Spec-Area A-") and a2["filename"].endswith("-V2.pdf")
    # 可以指定作为新文件
    p = upload(api_client, project, [d["spec"]], "Area A final.pdf", b"a3")
    p = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan", {"title": "Area A", "file_id": "new"})
    assert p["mode"] == "new" and p["version_no"] == 1
    assert len(call(api_client, "GET", project, f"deliverables/{d['spec']}/files")) == 2


def test_module_deliverable_uses_title_not_module(api_client, project, d):
    p = upload(api_client, project, [d["A"]], "GA alpha bench.dwg", b"ga")
    assert p["filename"].startswith("1.3_Layout-GA alpha bench-") and "-A-" not in p["filename"]


def test_multiple_equipment_one_file(api_client, project, d, session):
    p, r = store(api_client, project, [d["73"], d["72"]], "datasheet 20260226.pdf", b"both")
    assert p["filename"] == "1.2.72&73_Datasheet-Mixer-20260226-V1.pdf"
    for key in ("72", "73"):
        files = call(api_client, "GET", project, f"deliverables/{d[key]}/files")
        assert files[0]["id"] == r["file_id"] and files[0]["deliverable_ids"] == sorted([d["72"], d["73"]])
    # 再传到同样两台 → 升版；只传 72 → 新文件
    assert upload(api_client, project, [d["72"], d["73"]], "datasheet.pdf", b"both2")["mode"] == "version"
    assert upload(api_client, project, [d["72"]], "datasheet.pdf", b"only72")["mode"] == "new"
    # 同一台设备下标题不同、或格式不同，是另一份文件（用户 2026-10-25 确认）
    assert upload(api_client, project, [d["72"], d["73"]], "wiring diagram.pdf", b"other")["mode"] == "new"
    assert upload(api_client, project, [d["72"], d["73"]], "datasheet.dwg", b"cad")["mode"] == "new"
    # 不按设备的不能一个文件挂几项
    resp = upload(api_client, project, [d["A"], d["B"]], "x.pdf", b"ab", status=422)
    assert resp["detail"]["code"] == "file.multi_not_equipment"


def test_untagged_equipment_name(api_client, project, d):
    p = upload(api_client, project, [d["part"]], "part 20260226.pdf", b"c")
    assert p["filename"] == "1.2_Datasheet-Beta part-20260226-V1.pdf"
    assert p["source"] == "ours"  # 没有供应商


def test_client_file_keeps_original_name_and_overrides(api_client, project, d):
    p = upload(api_client, project, [d["spec"]], "Client Spec rev 2.pdf", b"client")
    p = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan",
             {"source": "client", "file_date": "2026-01-15"})
    assert p["filename"] == "Client Spec rev 2.pdf"
    assert (p["file_date"], p["date_source"]) == ("2026-01-15", "manual")
    call(api_client, "POST", project, f"files/uploads/{p['token']}/confirm", {"source": "client"})
    # 同标题再传：自动升版客户文件；明确选了别的来源就是另一个文件（来源不同的文件分开存）
    p = upload(api_client, project, [d["spec"]], "Client Spec rev 3.pdf", b"client 3")
    assert p["mode"] == "version" and p["source"] == "client"
    p = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan", {"source": "ours"})
    assert p["mode"] == "new" and p["source"] == "ours"
    # 指定升版某个文件时不能改来源
    resp = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan",
                {"source": "ours", "file_id": p["candidates"][0]["file_id"]}, 422)
    assert resp["detail"]["code"] == "file.source_fixed"


def test_upload_date_used_when_nothing_else(api_client, project, d):
    p = upload(api_client, project, [d["spec"]], "notes.pdf", b"n")
    assert p["date_source"] == "upload"


def test_obsolete_or_other_project_target_rejected(api_client, project, d):
    other = api_client.post("/api/projects", json={"code": "FL-2", "name": "Other"}, headers=USER).json()
    upload(api_client, other, [d["spec"]], "x.pdf", b"x", status=404)
    upload(api_client, project, [], "x.pdf", b"x", status=422)


def test_archived_project_refuses_upload(api_client, project, d):
    call(api_client, "POST", project, "archive", {})
    resp = api_client.post(_url(project, "files/preview"), headers=USER,
                           data={"deliverable_ids": str(d["spec"])}, files={"file": ("x.pdf", b"x")})
    assert resp.status_code == 409


# ---------------------------------------------------------------- 版本保留与回收区


def test_vendor_keeps_latest_three(api_client, project, d, session):
    for n in range(1, 5):
        _, r = store(api_client, project, [d["72"]], f"ds V{n}.pdf", f"v{n}".encode())
    assert r["version_no"] == 4 and r["recycled"] == [1]
    versions = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"]
    assert [(v["version_no"], v["recycled_at"] is not None) for v in versions] == [
        (4, False), (3, False), (2, False), (1, True)]
    # 回收区的版本清理前仍可下载
    resp = call(api_client, "GET", project, f"versions/{versions[-1]['id']}/download")
    assert resp.content == b"v1"


def test_client_files_all_kept(api_client, project, d):
    for n in range(1, 6):
        _, r = store(api_client, project, [d["spec"]], f"Client doc rev {n}.pdf", f"c{n}".encode(),
                     overrides={"source": "client"} if n == 1 else None)
        assert r["recycled"] == []
    versions = call(api_client, "GET", project, f"deliverables/{d['spec']}/files")[0]["versions"]
    assert len(versions) == 5 and all(v["recycled_at"] is None for v in versions)


def test_version_submitted_in_transmittal_is_protected(api_client, project, d, session):
    store(api_client, project, [d["72"]], "ds.pdf", b"v1")
    t = call(api_client, "POST", project, "transmittals", {"deliverable_ids": [d["72"]]}, 201)
    v1 = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"][0]
    assert t["items"][0]["version_id"] == v1["id"]
    for n in range(2, 6):
        _, r = store(api_client, project, [d["72"]], "ds.pdf", f"v{n}".encode())
    versions = {v["version_no"]: v for v in
                call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"]}
    assert versions[1]["protected"] and versions[1]["recycled_at"] is None
    assert versions[2]["recycled_at"] is not None
    # 传送单作废后不再保护，下次上传时进回收区
    call(api_client, "POST", project, f"transmittals/{t['id']}/void", {"reason": "mistake"})
    _, r = store(api_client, project, [d["72"]], "ds.pdf", b"v6")
    assert 1 in r["recycled"]


def test_purge_after_recycle_days(api_client, project, d, session):
    for n in range(1, 5):
        store(api_client, project, [d["72"]], "ds.pdf", f"v{n}".encode())
    v1 = session.scalar(select(Version).where(Version.version_no == 1))
    sha = v1.blob_sha256
    storage = get_storage()
    assert storage.exists(sha)
    actor = Actor(name="system")
    # 没满 90 天不清
    assert svc.purge_recycled(session, actor)["versions"] == 0
    v1.recycled_at = datetime.now(UTC) - timedelta(days=91)
    result = svc.purge_recycled(session, actor)
    assert result == {"versions": 1, "blobs": [sha]}
    assert svc.delete_unused_blobs(session, storage, result["blobs"]) == 1
    assert not storage.exists(sha) and v1.purged_at is not None
    resp = call(api_client, "GET", project, f"versions/{v1.id}/download", status=409)
    assert resp["detail"]["code"] == "version.purged"
    # 记录保留
    versions = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"]
    assert versions[-1]["purged_at"] is not None


def test_purge_keeps_blob_still_used_elsewhere(session, tmp_path):
    storage = LocalDiskStorage(tmp_path)
    sha = storage.put_bytes(b"shared")
    assert svc.delete_unused_blobs(session, storage, [sha]) == 1  # 没有版本用它 → 删
    assert not storage.exists(sha)


# ---------------------------------------------------------------- 下载与改名


def test_download_name_follows_renamed_equipment(api_client, project, d):
    store(api_client, project, [d["72"]], "ds 20260226.pdf", b"content")
    vid = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"][0]["id"]
    resp = call(api_client, "GET", project, f"versions/{vid}/download")
    assert resp.content == b"content"
    assert "1.2.72_Datasheet-Mixer-20260226-V1.pdf" in resp.headers["content-disposition"]
    call(api_client, "PATCH", project, f"equipment/{d['equipment']['72']}", {"name": "Mixer A"})
    resp = call(api_client, "GET", project, f"versions/{vid}/download")
    assert "1.2.72_Datasheet-Mixer%20A-20260226-V1.pdf" in resp.headers["content-disposition"]
    files = call(api_client, "GET", project, f"deliverables/{d['72']}/files")
    v = files[0]["versions"][0]
    assert v["filename"].startswith("1.2.72_Datasheet-Mixer A") and v["stored_name"].startswith(
        "1.2.72_Datasheet-Mixer")


def test_storage_unavailable_returns_503(api_client, project, d, monkeypatch, tmp_path):
    blocker = tmp_path / "unplugged"
    blocker.write_text("not a folder")
    monkeypatch.setattr("app.api.files.get_storage", lambda: LocalDiskStorage(blocker / "data"))
    resp = upload(api_client, project, [d["spec"]], "x.pdf", b"x", status=503)
    assert resp["detail"]["code"] == "storage.unavailable"


# ---------------------------------------------------------------- 命名模板


def test_naming_templates_api(api_client, project, d):
    cfg = call(api_client, "GET", project, "naming")
    assert cfg["templates"] == {} and cfg["samples"]["equipment"].startswith("3.5.72_Cut sheet")
    samples = call(api_client, "POST", project, "naming/preview",
                   {"templates": {"general": "{no} {title} V{version}"}})
    assert samples["general"] == "3.12 Cleanroom 1F V1.dwg"
    resp = call(api_client, "POST", project, "naming/preview", {"templates": {"general": "{oops}"}}, 422)
    assert resp["detail"]["code"] == "naming.unknown_field"
    # 保存到项目后，下一次上传按新模板命名
    call(api_client, "PATCH", project, "", {"naming_templates": {"general": "{no}-{title}-V{version}"}})
    assert upload(api_client, project, [d["spec"]], "Area A.pdf", b"a")["filename"] == "1.1-Area A-V1.pdf"
    call(api_client, "PATCH", project, "", {"naming_templates": {"general": "{no}"}}, 422)
