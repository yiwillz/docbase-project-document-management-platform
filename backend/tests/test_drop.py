"""第 12A 步：分块上传（断点续传）、智能投递（识别 → 预览 → 确认）、待归类与归类、
客户退回文件记回复、闭环检查"批准后又传了新版本"。

虚构项目 DR-1：
M1
  1.1 整个种类 Spec
  1.2 按设备 Datasheet（关键词 DS）：72 Mixer（Vendor A）、73 Mixer（Vendor A）
"""

import io
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Deliverable, ReplySource, TransmittalItem
from app.services import storage as storage_mod
from app.services.errors import Invalid

USER = {"X-User": "alice"}


def url(project, path):
    return f"/api/projects/{project['id']}/{path}"


def call(api_client, method, project, path, body=None, status=200, **kw):
    resp = api_client.request(method, url(project, path), json=body, headers=USER, **kw)
    assert resp.status_code == status, resp.text
    return resp.json()


def drop(api_client, project, name, content, status=200, **form):
    resp = api_client.post(url(project, "drop/preview"), headers=USER, data=form,
                           files={"file": (name, content, "application/octet-stream")})
    assert resp.status_code == status, resp.text
    return resp.json()


def confirm(api_client, project, token, body=None, status=200):
    return call(api_client, "POST", project, f"files/uploads/{token}/confirm", body or {}, status)


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "DR-1", "name": "Drop demo"},
                           headers=USER).json()


@pytest.fixture
def d(api_client, project, session):
    m1 = call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)
    docs = call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m1["id"], "rows": [{"name": "Spec"}, {"name": "Datasheet", "keywords": ["DS"]}]}, 201)
    eq = {}
    for tag in ("72", "73"):
        eq[call(api_client, "POST", project, "equipment",
                {"tag": tag, "name": "Mixer", "vendor": "Vendor A"}, 201)["id"]] = tag
    call(api_client, "POST", project, "document-types/bulk-equipment", {
        "document_type_ids": [docs[1]["id"]], "all_equipment": True, "mode": "add"})
    out = {"milestone": m1["id"]}
    for row in session.scalars(select(Deliverable).where(Deliverable.project_id == project["id"],
                                                         Deliverable.obsolete.is_(False))):
        out[eq.get(row.equipment_id, "spec")] = row.id
    return out


# ---------------------------------------------------------------- 分块上传


def test_chunked_upload_resume_and_preview(api_client, project, d, monkeypatch):
    data = b"0123456789" * 3
    up = call(api_client, "POST", project, "uploads", {"filename": "big DS 72.pdf", "size": len(data)})
    uid = up["upload_id"]
    r = call(api_client, "PUT", project, f"uploads/{uid}?offset=0", content=data[:12])
    assert r["received"] == 12
    # 断了：重传同一块（位置不对）被拒，告诉已收到多少
    resp = call(api_client, "PUT", project, f"uploads/{uid}?offset=0", content=data[:12], status=422)
    assert resp["detail"]["code"] == "storage.bad_offset" and resp["detail"]["params"]["received"] == 12
    assert call(api_client, "GET", project, f"uploads/{uid}")["received"] == 12
    # 没传完不能预览
    resp = api_client.post(url(project, "files/preview"), headers=USER,
                           data={"deliverable_ids": str(d["72"]), "upload_id": uid})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "upload.incomplete"
    call(api_client, "PUT", project, f"uploads/{uid}?offset=12", content=data[12:])
    resp = api_client.post(url(project, "files/preview"), headers=USER,
                           data={"deliverable_ids": str(d["72"]), "upload_id": uid})
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert p["size"] == len(data) and p["filename"].startswith("1.2.72_Datasheet-Mixer-")
    stored = confirm(api_client, project, p["token"])
    resp = api_client.get(url(project, f"versions/{stored['version_id']}/download"))
    assert resp.content == data
    # 用过的上传编号不能再传
    call(api_client, "PUT", project, f"uploads/{uid}?offset=30", content=b"x", status=409)


def test_chunked_upload_limits(api_client, project, d):
    call(api_client, "POST", project, "uploads", {"filename": "x.pdf", "size": 3 * 1024**3}, status=422)
    up = call(api_client, "POST", project, "uploads", {"filename": "x.pdf", "size": 4})
    resp = call(api_client, "PUT", project, f"uploads/{up['upload_id']}?offset=0", content=b"too long",
                status=422)
    assert resp["detail"]["code"] == "storage.too_large"
    assert call(api_client, "GET", project, f"uploads/{up['upload_id']}")["received"] == 0


def test_storage_append_rejects_missing(tmp_path):
    s = storage_mod.LocalDiskStorage(tmp_path)
    sid = s.begin()

    assert s.append(sid, 0, io.BytesIO(b"ab")) == 2
    assert s.append(sid, 2, io.BytesIO(b"cd")) == 4
    assert s.finish(sid) == storage_mod.hash_file(s.staging / sid)
    s.discard(sid)
    with pytest.raises(Invalid) as e:
        s.append(sid, 4, io.BytesIO(b"e"))
    assert e.value.code == "storage.staging_missing"


# ---------------------------------------------------------------- 智能投递


def test_drop_recognizes_and_previews(api_client, project, d):
    p = drop(api_client, project, "Mixer DS 72 20260226.pdf", b"ds72")
    assert p["deliverable_ids"] == [d["72"]] and not p["unclassified"]
    assert p["filename"] == "1.2.72_Datasheet-Mixer-20260226-V1.pdf"
    rec = p["recognition"]
    assert rec["level"] == "high" and rec["candidates"][0]["deliverable_ids"] == [d["72"]]
    stored = confirm(api_client, project, p["token"])
    assert stored["mode"] == "new" and not stored["unclassified"]


def test_drop_unrecognized_goes_to_unclassified_with_original_name(api_client, project, d):
    p = drop(api_client, project, "meeting notes.pdf", b"notes")
    assert p["unclassified"] and p["deliverable_ids"] == [] and p["filename"] == "meeting notes.pdf"
    stored = confirm(api_client, project, p["token"])
    assert stored["unclassified"]
    files = call(api_client, "GET", project, "files/unclassified")
    assert len(files) == 1 and files[0]["versions"][0]["filename"] == "meeting notes.pdf"
    projects = api_client.get("/api/projects").json()
    assert next(x for x in projects if x["id"] == project["id"])["unclassified_count"] == 1


def test_drop_change_target_before_confirm(api_client, project, d):
    p = drop(api_client, project, "Mixer DS 72.pdf", b"x")
    p = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan", {"deliverable_ids": [d["spec"]]})
    assert p["filename"].startswith("1.1_Spec-") and p["recognition"]["candidates"]  # 识别结果还在
    p = call(api_client, "POST", project, f"files/uploads/{p['token']}/plan", {"deliverable_ids": []})
    assert p["unclassified"] and p["filename"] == "Mixer DS 72.pdf"
    stored = confirm(api_client, project, p["token"], {"deliverable_ids": [d["73"]]})
    assert stored["filename"].startswith("1.2.73_Datasheet-Mixer-")


def test_classify_unclassified_file(api_client, project, d):
    p = drop(api_client, project, "scan0001.pdf", b"scan")
    fid = confirm(api_client, project, p["token"])["file_id"]
    r = call(api_client, "POST", project, f"files/{fid}/classify", {"deliverable_ids": [d["72"]], "reason": "checked"})
    assert r["filename"].startswith("1.2.72_Datasheet-Mixer-")
    assert call(api_client, "GET", project, "files/unclassified") == []
    assert call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["id"] == fid
    # 已归类的不能再归类
    call(api_client, "POST", project, f"files/{fid}/classify", {"deliverable_ids": [d["73"]]}, 409)
    # 目标已有同样的文件（同标题、同格式）：请改用上传为新版本
    p = drop(api_client, project, "scan0001.pdf", b"scan again")
    fid2 = confirm(api_client, project, p["token"])["file_id"]
    resp = call(api_client, "POST", project, f"files/{fid2}/classify", {"deliverable_ids": [d["72"]]}, 409)
    assert resp["detail"]["code"] == "file.target_has_file"
    # 标题不同就是同一台设备下的另一份文件：可以归类，名字里带标题
    p = drop(api_client, project, "scan0002.pdf", b"scan 2")
    fid3 = confirm(api_client, project, p["token"])["file_id"]
    r = call(api_client, "POST", project, f"files/{fid3}/classify", {"deliverable_ids": [d["72"]]})
    assert r["filename"].startswith("1.2.72_Datasheet-Mixer-scan0002-")


# ---------------------------------------------------------------- 客户退回的带章文件


def test_client_return_records_reply(api_client, project, d, session):
    p = drop(api_client, project, "Mixer DS 72.pdf", b"ours")
    v1 = confirm(api_client, project, p["token"])
    t = call(api_client, "POST", project, "transmittals", {"deliverable_ids": [d["72"], d["73"]]}, 201)
    p = drop(api_client, project, f"{t['number']} Mixer DS 72 Code 1 20261020.pdf", b"stamped")
    rec = p["recognition"]
    assert rec["client_reply"]["code"] == "1" and rec["client_reply"]["transmittal_id"] == t["id"]
    # 客户来源：保持原名，另存一个文件，不是供应商文件的新版本
    assert p["source"] == "client" and p["mode"] == "new" and p["filename"] == p["original_name"]
    stored = confirm(api_client, project, p["token"], {"record_reply": True})
    assert stored["reply"]["updated"] == 1 and stored["file_id"] != v1["file_id"]
    item = session.scalar(select(TransmittalItem).where(TransmittalItem.transmittal_id == t["id"],
                                                        TransmittalItem.deliverable_id == d["72"]))
    assert item.review_code == "1" and item.reply_source is ReplySource.FILE
    assert item.reply_date == date(2026, 10, 20)
    items = {i["id"]: i for i in call(api_client, "GET", project, "deliverables")["items"]}
    assert items[d["72"]]["status"] == "approved" and items[d["73"]]["status"] == "submitted"


def test_record_reply_needs_recognized_reply(api_client, project, d):
    p = drop(api_client, project, "Mixer DS 72.pdf", b"x")
    resp = confirm(api_client, project, p["token"], {"record_reply": True}, status=422)
    assert resp["detail"]["code"] == "file.no_client_reply"


# ---------------------------------------------------------------- 闭环：批准后又传了新版本


def test_closeout_flags_new_version_after_approval(api_client, project, d):
    for key in ("spec",):
        p = drop(api_client, project, "Spec Area A.pdf", b"spec v1")
        confirm(api_client, project, p["token"], {"deliverable_ids": [d[key]]})
    t = call(api_client, "POST", project, "transmittals", {"deliverable_ids": [d["spec"]]}, 201)
    call(api_client, "POST", project, f"transmittals/{t['id']}/reply", {"code": "1", "reply_date": "2026-10-20"})
    issues = lambda: next(i for i in call(api_client, "GET", project, f"milestones/{d['milestone']}/closeout")["items"]  # noqa: E731
                          if i["id"] == d["spec"])["issues"]
    assert "new_version_after_approval" not in issues()
    # 批准后客户退回的带章文件不算
    p = drop(api_client, project, "Spec stamped.pdf", b"stamped spec")
    confirm(api_client, project, p["token"], {"deliverable_ids": [d["spec"]], "source": "client"})
    assert "new_version_after_approval" not in issues()
    # 我方又传了新版本：挡关闭
    p = drop(api_client, project, "Spec Area A V2.pdf", b"spec v2")
    confirm(api_client, project, p["token"], {"deliverable_ids": [d["spec"]]})
    result = call(api_client, "GET", project, f"milestones/{d['milestone']}/closeout")
    assert "new_version_after_approval" in issues() and result["counts"]["new_version_after_approval"] == 1
