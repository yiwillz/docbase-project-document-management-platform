from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    Deliverable,
    DeliverableStatus,
    DocumentTypeModule,
    Equipment,
    EquipmentType,
    Event,
    File,
    FileDeliverable,
    FileSource,
    ModuleEquipmentType,
)

USER = {"X-User": "alice"}


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "DL-1", "name": "deliverables"},
                           headers=USER).json()


def _url(project, path=""):
    return f"/api/projects/{project['id']}/{path}"


def _call(api_client, method, project, path, body=None, status=200):
    resp = api_client.request(method, _url(project, path), json=body, headers=USER)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.content else None


@pytest.fixture
def m1(api_client, project):
    return _call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)


def _doc(api_client, project, milestone, **fields):
    [doc] = _call(api_client, "POST", project, "document-types/bulk",
                  {"milestone_id": milestone["id"], "rows": [{"name": "Doc", **fields}]}, 201)
    return doc


def _rows(session, project, document_type_id=None):
    stmt = select(Deliverable).where(Deliverable.project_id == project["id"])
    if document_type_id is not None:
        stmt = stmt.where(Deliverable.document_type_id == document_type_id)
    session.expire_all()
    return list(session.scalars(stmt.order_by(Deliverable.id)))


def _active(rows):
    return [r for r in rows if not r.obsolete]


@pytest.fixture
def equipment(project, session):
    """种类A 2 台、种类B 1 台、停用 1 台；Alpha 模块对应种类A种类。"""
    alpha = EquipmentType(project_id=project["id"], name="Alpha")
    beta = EquipmentType(project_id=project["id"], name="Beta")
    session.add_all([alpha, beta])
    session.flush()
    items = [Equipment(project_id=project["id"], equipment_type_id=alpha.id, tag="71", name="W1"),
             Equipment(project_id=project["id"], equipment_type_id=alpha.id, tag="72", name="W2"),
             Equipment(project_id=project["id"], equipment_type_id=beta.id, tag="80", name="D1"),
             Equipment(project_id=project["id"], equipment_type_id=beta.id, tag="81", name="Old",
                       active=False)]
    session.add_all(items)
    session.commit()
    return {"alpha_type": alpha.id, "ids": [e.id for e in items], "items": items}


# ---------------------------------------------------------------- 三种应交项


def test_whole_type_gets_one(api_client, project, m1, session):
    doc = _doc(api_client, project, m1)
    rows = _rows(session, project, doc["id"])
    assert len(rows) == 1 and (rows[0].equipment_id, rows[0].module_id) == (None, None)
    s = _call(api_client, "GET", project, "deliverables/summary")
    assert (s["active"], s["by_kind"]) == (1, {"whole": 1})


def test_per_module_one_per_partition(api_client, project, m1, session):
    doc = _doc(api_client, project, m1)
    _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules",
          {"module_names": ["Automation", "Alpha", "Thermal"]})
    rows = _rows(session, project, doc["id"])
    assert len(_active(rows)) == 3 and all(r.module_id for r in _active(rows))
    assert sum(r.obsolete for r in rows) == 1  # 原来"整个种类"的那个作废，不删除


def test_per_equipment_one_per_linked_active_equipment(api_client, project, m1, session,
                                                       equipment):
    doc = _doc(api_client, project, m1)
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "all_equipment": True, "mode": "add"})
    active = _active(_rows(session, project, doc["id"]))
    assert sorted(r.equipment_id for r in active) == sorted(equipment["ids"][:3])  # 停用的不算


def test_per_equipment_takes_precedence_over_modules(api_client, project, m1, session,
                                                    equipment):
    doc = _doc(api_client, project, m1)
    _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules",
          {"module_names": ["Alpha"]})
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "equipment_ids": equipment["ids"][:2],
           "mode": "add"})
    active = _active(_rows(session, project, doc["id"]))
    assert len(active) == 2 and all(r.equipment_id and r.module_id is None for r in active)


# ---------------------------------------------------------------- 反复执行、作废与恢复


def test_regenerate_is_idempotent(api_client, project, m1):
    _doc(api_client, project, m1)
    result = _call(api_client, "POST", project, "deliverables/regenerate")
    assert (result["created"], result["obsoleted"], result["restored"]) == (0, 0, 0)
    assert result["active"] == 1


def test_deactivate_and_restore_keep_same_rows(api_client, project, m1, session):
    doc = _doc(api_client, project, m1)
    [before] = _rows(session, project, doc["id"])
    _call(api_client, "POST", project, f"document-types/{doc['id']}/deactivate")
    assert _rows(session, project, doc["id"])[0].obsolete is True
    _call(api_client, "POST", project, f"document-types/{doc['id']}/activate")
    [after] = _rows(session, project, doc["id"])
    assert (after.id, after.obsolete) == (before.id, False)  # 恢复的是原来那一行


def test_equipment_deactivated_or_unlinked(api_client, project, m1, session, equipment):
    doc = _doc(api_client, project, m1)
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "all_equipment": True, "mode": "add"})
    equipment["items"][0].active = False  # 设备停用（第 7 步会有接口）
    session.commit()
    result = _call(api_client, "POST", project, "deliverables/regenerate")
    assert result["obsoleted"] == 1
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "equipment_ids": [equipment["ids"][2]],
           "mode": "remove"})
    assert len(_active(_rows(session, project, doc["id"]))) == 1


def test_deleted_type_rows_removed_but_file_rows_never(api_client, project, m1, session):
    doc = _doc(api_client, project, m1)
    _call(api_client, "DELETE", project, f"document-types/{doc['id']}", status=204)
    assert _rows(session, project, doc["id"]) == []  # 没挂文件的种类删除时，应交项一起删

    kept = _doc(api_client, project, m1, name="With file")
    [deliverable] = _rows(session, project, kept["id"])
    file = File(project_id=project["id"], source=FileSource.VENDOR)
    session.add(file)
    session.flush()
    session.add(FileDeliverable(file_id=file.id, deliverable_id=deliverable.id))
    session.commit()
    _call(api_client, "PUT", project, f"document-types/{kept['id']}/modules",
          {"module_names": ["A"]})
    rows = _rows(session, project, kept["id"])
    assert deliverable.id in [r.id for r in rows if r.obsolete]  # 挂了文件的只作废


# ---------------------------------------------------------------- 初始状态


def test_initial_status_only_when_definite(api_client, project, m1, session):
    approved = _doc(api_client, project, m1, name="A", mdl_status_text="Approved",
                    mdl_submitted_date="2026-03-05")
    unknown = _doc(api_client, project, m1, name="B", mdl_status_text="Something else")
    mixed = _doc(api_client, project, m1, name="C", mdl_status_text="Pending resubmit")
    [a] = _rows(session, project, approved["id"])
    assert (a.status.value, a.responsible.value) == ("approved", "none")
    assert a.status_basis.startswith("来自 MDL 状态文字") and a.status_date is None
    assert a.submitted_date == date(2026, 3, 5)
    for doc in (unknown, mixed):  # 没命中 / 结论冲突 → 不采用
        [row] = _rows(session, project, doc["id"])
        assert (row.status.value, row.status_basis) == ("not_submitted", None)


def test_equipment_status_from_its_module_partition(api_client, project, m1, session,
                                                    equipment):
    doc = _doc(api_client, project, m1, mdl_status_text="Pending resubmit")
    _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules",
          {"module_names": ["Alpha"]})
    tree = _call(api_client, "GET", project, "structure")
    alpha = next(m for m in tree["modules"] if m["name"] == "Alpha")
    # Alpha 模块对应种类A设备种类（第 7 步会有接口），Alpha 分区的状态文字为 Approved
    session.add(ModuleEquipmentType(module_id=alpha["id"], equipment_type_id=equipment["alpha_type"]))
    part = session.scalars(select(DocumentTypeModule).where(
        DocumentTypeModule.document_type_id == doc["id"])).one()
    part.mdl_status_text = "Approved"
    session.commit()
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "all_equipment": True, "mode": "add"})
    status = {r.equipment_id: r.status.value for r in _active(_rows(session, project, doc["id"]))}
    w1, w2, d1 = equipment["ids"][:3]
    assert status == {w1: "approved", w2: "approved",  # 种类A设备用 Alpha 分区的文字
                      d1: "not_submitted"}             # 种类B设备用种类那一行的（冲突，不采用）


def test_existing_status_not_overwritten(api_client, project, m1, session):
    doc = _doc(api_client, project, m1, mdl_status_text="Approved")
    [row] = _rows(session, project, doc["id"])
    row.status = DeliverableStatus.REVISE_RESUBMIT  # 有人手动改过
    session.commit()
    _call(api_client, "PATCH", project, f"document-types/{doc['id']}",
          {"mdl_status_text": "Approved"})
    _call(api_client, "POST", project, "deliverables/regenerate")
    assert _rows(session, project, doc["id"])[0].status.value == "revise_resubmit"


# ---------------------------------------------------------------- 留痕、导入、只读


def test_one_summary_log_and_event_per_recalculation(api_client, project, m1, session):
    _call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m1["id"], "rows": [{"name": f"D{i}"} for i in range(5)]}, 201)
    logs = api_client.get("/api/change-logs", params={
        "project_id": project["id"], "entity_type": "deliverables"}).json()
    assert logs["total"] == 1
    assert logs["items"][0]["changes"]["created"] == [None, 5]
    events = session.scalars(select(Event.event_type).where(
        Event.project_id == project["id"], Event.event_type == "deliverables.changed")).all()
    assert len(events) == 1


def test_template_import_generates_deliverables(api_client):
    from tests.test_structure_template import DOCS, MILESTONES, PARTS, _fill

    other = api_client.post("/api/projects", json={"code": "DL-2", "name": "tpl"},
                            headers=USER).json()
    preview = api_client.post(f"/api/projects/{other['id']}/structure-import/preview",
                              files={"file": ("s.xlsx", _fill(MILESTONES, DOCS, PARTS))},
                              headers=USER).json()
    api_client.post(f"/api/projects/{other['id']}/structure-import/confirm",
                    json={"token": preview["token"]}, headers=USER)
    s = api_client.get(f"/api/projects/{other['id']}/deliverables/summary").json()
    # 1.1 整个种类；2.1 按设备但还没勾选设备 → 0；2.2 整个种类；2.3 按设备 → 0
    assert s["by_kind"] == {"whole": 2}


def test_archived_project_refuses_regenerate(api_client, project, m1):
    api_client.post(f"/api/projects/{project['id']}/archive", headers=USER)
    resp = api_client.post(_url(project, "deliverables/regenerate"), headers=USER)
    assert resp.status_code == 409
