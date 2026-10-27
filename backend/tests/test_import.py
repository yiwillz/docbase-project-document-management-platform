"""第 14A 步：存量导入——扫描、去重、识别、版本归并、疑似新设备、预演报告导出导回、执行、暂停继续、
原文件被改、整批撤销；原目录全程不被改动。

虚构项目 IM-1：
M1
  1.1 整个种类 Spec
  1.2 按设备 Datasheet（关键词 DS）：72 Mixer（Vendor A）、73 Dryer
  1.3 按模块 Layout：A、B
虚构存量目录（临时目录里造）：
  Vendor A/72 Mixer/Mixer DS V1.pdf … V3.pdf、Mixer DS V4 20260301.pdf、copy of DS V4.pdf（和 V4 相同）
  Vendor A/Dryer DS.pdf（和平台里已有的文件相同）
  Spec/Spec rev 2.pdf
  Layout/Layout A plan.dwg
  Tool 205/notes.pdf（识别不出，疑似新设备 205）
  ~$lock.docx、Thumbs.db（跳过）
"""

import hashlib
import io
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app import worker
from app.models import Deliverable, File, ImportItem, Job, JobStatus, Version
from app.services import importer

USER = {"X-User": "alice"}
TREE = {
    "Vendor A/72 Mixer/Mixer DS V1.pdf": b"ds v1",
    "Vendor A/72 Mixer/Mixer DS V2.pdf": b"ds v2",
    "Vendor A/72 Mixer/Mixer DS V3.pdf": b"ds v3",
    "Vendor A/72 Mixer/Mixer DS V4 20260301.pdf": b"ds v4",
    "Vendor A/72 Mixer/copy of DS V4.pdf": b"ds v4",
    "Vendor A/Dryer DS.pdf": b"already here",
    "Spec/Spec rev 2.pdf": b"spec",
    "Layout/Layout A plan.dwg": b"layout a",
    "Tool 205/notes.pdf": b"notes",
    "Spec/~$lock.docx": b"lock",
    "Thumbs.db": b"thumbs",
}


def url(project, path):
    return f"/api/projects/{project['id']}/{path}"


def call(api_client, method, project, path, body=None, status=200, **kw):
    resp = api_client.request(method, url(project, path), json=body, headers=USER, **kw)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp


def fingerprint(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() + str(p.stat().st_mtime)
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def source(tmp_path, monkeypatch):
    from app.config import get_settings

    root = tmp_path / "share" / "old project"
    for rel, data in TREE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    monkeypatch.setattr(get_settings(), "import_roots", str(tmp_path / "share"))
    return root


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "IM-1", "name": "Import demo"}, headers=USER).json()


@pytest.fixture
def d(api_client, project, session):
    m1 = call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)
    docs = call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m1["id"], "rows": [{"name": "Spec"}, {"name": "Datasheet", "keywords": ["DS"]},
                                           {"name": "Layout"}]}, 201)
    eq = {}
    for e in [{"tag": "72", "name": "Mixer", "vendor": "Vendor A"}, {"tag": "73", "name": "Dryer"}]:
        eq[call(api_client, "POST", project, "equipment", e, 201)["id"]] = e["tag"]
    call(api_client, "POST", project, "document-types/bulk-equipment", {
        "document_type_ids": [docs[1]["id"]], "all_equipment": True, "mode": "add"})
    call(api_client, "PUT", project, f"document-types/{docs[2]['id']}/modules", {"module_names": ["A", "B"]})
    modules = {m["id"]: m["name"] for m in call(api_client, "GET", project, "modules")}
    out = {}
    for row in session.scalars(select(Deliverable).where(Deliverable.project_id == project["id"],
                                                         Deliverable.obsolete.is_(False))):
        out[eq.get(row.equipment_id) or modules.get(row.module_id) or "spec"] = row.id
    # 平台里已有一份和 "Dryer DS.pdf" 相同的文件
    resp = api_client.post(url(project, "files/preview"), headers=USER, data={"deliverable_ids": str(out["73"])},
                           files={"file": ("dryer.pdf", b"already here")})
    call(api_client, "POST", project, f"files/uploads/{resp.json()['token']}/confirm", {})
    return out


def run(session, kind):
    assert worker.run_once(session, "test-worker", [kind])


def scanned(api_client, project, session, source):
    batch = call(api_client, "POST", project, "imports", {"source_dir": str(source)}, 201)
    assert batch["status"] == "scanning" and batch["job"]["status"] == "queued"
    run(session, importer.SCAN)
    batch = call(api_client, "GET", project, f"imports/{batch['id']}")
    assert batch["status"] == "ready", batch
    return batch


def items_by_path(api_client, project, batch):
    rows = call(api_client, "GET", project, f"imports/{batch['id']}/items?limit=1000")["items"]
    return {r["path"]: r for r in rows}


# ---------------------------------------------------------------- 目录检查


def test_only_allowed_roots(api_client, project, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "import_roots", "")
    resp = call(api_client, "POST", project, "imports", {"source_dir": str(tmp_path)}, 422)
    assert resp["detail"]["code"] == "import.no_roots"
    monkeypatch.setattr(get_settings(), "import_roots", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    resp = call(api_client, "POST", project, "imports", {"source_dir": str(tmp_path / "other")}, 422)
    assert resp["detail"]["code"] == "import.not_allowed"
    resp = call(api_client, "POST", project, "imports", {"source_dir": str(tmp_path / "allowed" / "missing")}, 422)
    assert resp["detail"]["code"] == "import.not_found"
    assert call(api_client, "GET", project, "imports/roots") == [str((tmp_path / "allowed").resolve())]


# ---------------------------------------------------------------- 扫描和分析


def test_scan_and_analyze(api_client, project, d, session, source):
    before = fingerprint(source)
    batch = scanned(api_client, project, session, source)
    rows = items_by_path(api_client, project, batch)
    assert "Thumbs.db" not in rows and "Spec/~$lock.docx" not in rows  # 临时文件跳过
    assert len(rows) == 9
    v = {n: rows[f"Vendor A/72 Mixer/Mixer DS {n}"] for n in ("V1.pdf", "V2.pdf", "V3.pdf", "V4 20260301.pdf")}
    assert [r["version_rank"] for r in v.values()] == [1, 2, 3, 4]
    assert [r["state"] for r in v.values()] == ["old_version"] * 3 + ["new"]
    assert v["V4 20260301.pdf"]["planned_name"] == "1.2.72_Datasheet-Mixer-20260301-V4.pdf"
    assert v["V1.pdf"]["planned_name"].endswith("-V1.pdf") and v["V1.pdf"]["deliverable_ids"] == [d["72"]]
    dup = rows["Vendor A/72 Mixer/copy of DS V4.pdf"]
    assert dup["state"] == "duplicate" and dup["action"] == "skip" and dup["duplicate_of"] == v["V4 20260301.pdf"]["id"]
    assert rows["Vendor A/Dryer DS.pdf"]["state"] == "existing" and rows["Vendor A/Dryer DS.pdf"]["action"] == "skip"
    assert rows["Spec/Spec rev 2.pdf"]["deliverable_ids"] == [d["spec"]]
    assert rows["Layout/Layout A plan.dwg"]["deliverable_ids"] == [d["A"]]
    notes = rows["Tool 205/notes.pdf"]
    assert notes["deliverable_ids"] == [] and notes["suspected_equipment"] == "205"
    assert notes["planned_name"] == "notes.pdf"  # 待归类保持原名
    counts = batch["counts"]
    assert counts["total"] == 9 and counts["state_duplicate"] == 1 and counts["state_existing"] == 1
    assert counts["unclassified"] == 1 and counts["suspected"] >= 1
    assert fingerprint(source) == before  # 原目录没动


def test_scan_resumes_after_interruption(api_client, project, d, session, source):
    batch = call(api_client, "POST", project, "imports", {"source_dir": str(source)}, 201)
    b = importer.get_batch(session, project["id"], batch["id"])
    # 模拟扫到一半：先手工记下一个文件，再让 worker 扫，已扫过的不重复
    session.add(ImportItem(batch_id=b.id, rel_path="Spec/Spec rev 2.pdf", size=4, sha256=hashlib.sha256(b"spec").hexdigest()))
    session.commit()
    run(session, importer.SCAN)
    paths = session.scalars(select(ImportItem.rel_path).where(ImportItem.batch_id == b.id)).all()
    assert len(paths) == len(set(paths)) == 9


# ---------------------------------------------------------------- 报告


def test_report_round_trip(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    resp = call(api_client, "GET", project, f"imports/{batch['id']}/report.xlsx")
    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb["items"]
    head = [c.value for c in ws[1]]
    assert head[0] == "序号" and head[12].startswith("处理")
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 2).value == "Tool 205/notes.pdf")
    ws.cell(row, 14).value = "1.1"  # 待归类 → 1.1 Spec
    ws.cell(row, 18).value = "Site notes"
    dryer = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 2).value == "Vendor A/72 Mixer/Mixer DS V1.pdf")
    ws.cell(dryer, 13).value = "跳过"
    out = io.BytesIO()
    wb.save(out)
    p = call(api_client, "POST", project, f"imports/{batch['id']}/report/preview",
             files={"file": ("report.xlsx", out.getvalue())})
    assert p["ok"] and len(p["changes"]) == 2
    notes = next(c for c in p["changes"] if c["path"] == "Tool 205/notes.pdf")
    assert notes["changes"]["deliverable_ids"] == [[], [d["spec"]]] and notes["changes"]["title"] == [None, "Site notes"]
    r = call(api_client, "POST", project, f"imports/{batch['id']}/report/confirm", {"token": p["token"]})
    assert r["updated"] == 2 and r["batch"]["counts"].get("unclassified", 0) == 0
    rows = items_by_path(api_client, project, batch)
    assert rows["Tool 205/notes.pdf"]["planned_name"].startswith("1.1_Spec-Site notes-")
    # 跳过 V1 后，V2 成了组里第 1 份
    assert rows["Vendor A/72 Mixer/Mixer DS V2.pdf"]["version_rank"] == 1
    call(api_client, "POST", project, f"imports/{batch['id']}/report/confirm", {"token": p["token"]}, 409)


def test_report_errors(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    data = call(api_client, "GET", project, f"imports/{batch['id']}/report.xlsx").content
    wb = load_workbook(io.BytesIO(data))
    ws = wb["items"]
    ws.cell(2, 14).value = "9.9"
    ws.cell(3, 13).value = "maybe"
    out = io.BytesIO()
    wb.save(out)
    p = call(api_client, "POST", project, f"imports/{batch['id']}/report/preview", files={"file": ("r.xlsx", out.getvalue())})
    assert not p["ok"] and len(p["errors"]) == 2
    call(api_client, "POST", project, f"imports/{batch['id']}/report/confirm", {"token": p["token"]}, 409)
    other = scanned(api_client, project, session, source)
    resp = call(api_client, "POST", project, f"imports/{other['id']}/report/preview", files={"file": ("r.xlsx", data)}, status=422)
    assert resp["detail"]["code"] == "import_report.wrong_batch"


# ---------------------------------------------------------------- 执行、撤销


def execute(api_client, project, session, batch):
    b = call(api_client, "POST", project, f"imports/{batch['id']}/execute")
    assert b["status"] == "executing"
    run(session, importer.EXECUTE)
    return call(api_client, "GET", project, f"imports/{batch['id']}")


def test_execute_imports_versions_and_keeps_source(api_client, project, d, session, source):
    before = fingerprint(source)
    batch = scanned(api_client, project, session, source)
    b = execute(api_client, project, session, batch)
    assert b["status"] == "done" and b["counts"]["result_done"] == 7 and b["counts"].get("result_failed", 0) == 0
    files72 = call(api_client, "GET", project, f"deliverables/{d['72']}/files")
    assert len(files72) == 1
    versions = files72[0]["versions"]
    assert [x["version_no"] for x in versions] == [4, 3, 2, 1]
    assert [x["recycled_at"] is not None for x in versions] == [False, False, False, True]  # 只留最新 3 版
    assert versions[0]["filename"] == "1.2.72_Datasheet-Mixer-20260301-V4.pdf"
    assert versions[0]["uploaded_by"].startswith(f"导入 #{batch['id']}")
    assert call(api_client, "GET", project, "files/unclassified")[0]["versions"][0]["filename"] == "notes.pdf"
    assert fingerprint(source) == before  # 原目录没动
    call(api_client, "POST", project, f"imports/{batch['id']}/execute", status=409)  # 不能再执行一次


def test_changed_source_file_fails_that_item_only(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    (source / "Spec/Spec rev 2.pdf").write_bytes(b"changed after scan")
    b = execute(api_client, project, session, batch)
    assert b["counts"]["result_failed"] == 1 and b["counts"]["result_done"] == 6
    failed = call(api_client, "GET", project, f"imports/{batch['id']}/items?result=failed")["items"]
    assert failed[0]["path"] == "Spec/Spec rev 2.pdf" and "改过" in failed[0]["error"]


def test_pause_and_resume_execute(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    call(api_client, "POST", project, f"imports/{batch['id']}/execute")
    b = call(api_client, "POST", project, f"imports/{batch['id']}/pause")
    assert b["job"]["status"] == "paused"
    assert not worker.run_once(session, "w", [importer.EXECUTE])  # 暂停的不做
    call(api_client, "POST", project, f"imports/{batch['id']}/pause", status=409)
    b = call(api_client, "POST", project, f"imports/{batch['id']}/resume")
    assert b["job"]["status"] == "queued"
    run(session, importer.EXECUTE)
    assert call(api_client, "GET", project, f"imports/{batch['id']}")["status"] == "done"


def test_execute_resumes_from_checkpoint(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    call(api_client, "POST", project, f"imports/{batch['id']}/execute")
    b = importer.get_batch(session, project["id"], batch["id"])
    job = session.get(Job, b.job_id)
    # 模拟 worker 做了一半崩了：一项已标完成，任务卡在 running、租约过期
    first = session.scalars(select(ImportItem).where(ImportItem.batch_id == b.id, ImportItem.action == "import")
                            .order_by(ImportItem.group_key, ImportItem.version_rank)).first()
    first.result = "skipped"
    job.status, job.lease_owner = JobStatus.RUNNING, "crashed"
    from datetime import UTC, datetime, timedelta

    job.lease_until = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()
    run(session, importer.EXECUTE)
    session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED and job.progress == job.total == 7
    assert call(api_client, "GET", project, f"imports/{batch['id']}")["counts"]["result_done"] == 6


def test_undo_batch(api_client, project, d, session, source):
    batch = scanned(api_client, project, session, source)
    execute(api_client, project, session, batch)
    # Spec 的文件交给了客户；Layout A 的文件导入后又被人升了版：这两个不撤
    call(api_client, "POST", project, "transmittals", {"deliverable_ids": [d["spec"]]}, 201)
    resp = api_client.post(url(project, "files/preview"), headers=USER, data={"deliverable_ids": str(d["A"])},
                           files={"file": ("Layout A plan v2.dwg", b"layout a v2")})
    call(api_client, "POST", project, f"files/uploads/{resp.json()['token']}/confirm", {})
    b = call(api_client, "POST", project, f"imports/{batch['id']}/undo")
    assert b["status"] == "undoing"
    run(session, importer.UNDO)
    b = call(api_client, "GET", project, f"imports/{batch['id']}")
    assert b["status"] == "undone" and b["counts"]["result_kept"] == 2 and b["counts"]["result_undone"] == 5
    kept = call(api_client, "GET", project, f"imports/{batch['id']}/items?result=kept")["items"]
    assert {k["path"] for k in kept} == {"Spec/Spec rev 2.pdf", "Layout/Layout A plan.dwg"}
    assert call(api_client, "GET", project, f"deliverables/{d['72']}/files") == []  # 72 的文件整个删了
    assert call(api_client, "GET", project, "files/unclassified") == []
    assert len(call(api_client, "GET", project, f"deliverables/{d['spec']}/files")) == 1
    # 72 原来就有的文件（Dryer 那份）不受影响
    assert len(call(api_client, "GET", project, f"deliverables/{d['73']}/files")) == 1
    pid = project["id"]
    assert session.scalar(select(File).where(File.project_id == pid, File.title.like("Mixer%"))) is None
    call(api_client, "POST", project, f"imports/{batch['id']}/undo", status=409)


def test_undo_restores_versions_pushed_into_recycle(api_client, project, d, session, source, tmp_path):
    # 平台里 72 已有 3 版；导入一个新版本后最早那版进回收区，撤销后放回来
    for n in range(1, 4):
        resp = api_client.post(url(project, "files/preview"), headers=USER, data={"deliverable_ids": str(d["72"])},
                               files={"file": (f"ds V{n}.pdf", f"old {n}".encode())})
        call(api_client, "POST", project, f"files/uploads/{resp.json()['token']}/confirm", {})
    one = tmp_path / "share" / "one"
    (one / "72 Mixer").mkdir(parents=True)
    (one / "72 Mixer" / "ds V4.pdf").write_bytes(b"final")
    batch = scanned(api_client, project, session, one)
    execute(api_client, project, session, batch)
    versions = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"]
    assert [v["recycled_at"] is not None for v in versions] == [False, False, False, True]
    call(api_client, "POST", project, f"imports/{batch['id']}/undo")
    run(session, importer.UNDO)
    versions = call(api_client, "GET", project, f"deliverables/{d['72']}/files")[0]["versions"]
    assert [v["version_no"] for v in versions] == [3, 2, 1] and all(v["recycled_at"] is None for v in versions)
    assert session.scalar(select(Version).where(Version.original_name == "ds V4.pdf")) is None


def test_several_documents_per_equipment(api_client, project, d, session, source, tmp_path):
    """一台设备下有好几份不同的文件（图、流程图、同一张图的 pdf 和 dwg），编号后面的分隔符有 _ . - 空格几种写法：
    各自是独立的文件，不当成版本；名字里带标题区分。（虚构的文件名，仿照真实文件夹的命名方式）"""
    folder = tmp_path / "share" / "pids"
    folder.mkdir(parents=True)
    names = {
        "1.2.72_Wiring diagram_2026.3.02_V2.png": b"flow",
        "1.2.72.Preliminary drawing-2026.3.5.pdf": b"draw pdf",
        "1.2.72.Preliminary drawing-2026.3.5.dwg": b"draw dwg",
        "1.2.72-Wiring-2026.4.24-V1.dwg": b"wiring",
        "1.2.72&73 Shared layout 2026.3.3_V1.dwg": b"shared",
    }
    for name, data in names.items():
        (folder / name).write_bytes(data)
    batch = scanned(api_client, project, session, folder)
    rows = items_by_path(api_client, project, batch)
    for name in names:
        assert rows[name]["state"] == "new" and rows[name]["version_rank"] == 1, name
        assert rows[name]["level"] == "high", name
    assert rows["1.2.72&73 Shared layout 2026.3.3_V1.dwg"]["deliverable_ids"] == sorted([d["72"], d["73"]])
    assert rows["1.2.72_Wiring diagram_2026.3.02_V2.png"]["planned_name"] == \
        "1.2.72_Datasheet-Mixer-Wiring diagram-20260302-V1.png"
    assert rows["1.2.72-Wiring-2026.4.24-V1.dwg"]["planned_name"] == "1.2.72_Datasheet-Mixer-Wiring-20260424-V1.dwg"
    execute(api_client, project, session, batch)
    files72 = call(api_client, "GET", project, f"deliverables/{d['72']}/files")
    assert len(files72) == 5  # 4 份只挂 72 的 + 1 份 72&73 共用的
    assert all(len(f["versions"]) == 1 for f in files72)

