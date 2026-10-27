import pytest
from sqlalchemy import select

from app.models import (
    ChangeLog,
    Deliverable,
    Equipment,
    EquipmentType,
    File,
    FileDeliverable,
    FileSource,
    ModuleEquipmentType,
)

USER = {"X-User": "alice"}


@pytest.fixture
def project(api_client):
    resp = api_client.post("/api/projects", json={"code": "S-001", "name": "Structure"},
                           headers=USER)
    assert resp.status_code == 201
    return resp.json()


def _url(project, path):
    return f"/api/projects/{project['id']}/{path}"


def _call(api_client, method, project, path, body=None, status=200):
    resp = api_client.request(method, _url(project, path), json=body, headers=USER)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.content else None


def _error(api_client, method, project, path, body=None):
    resp = api_client.request(method, _url(project, path), json=body, headers=USER)
    assert resp.status_code >= 400, resp.text
    return resp.json()["detail"]["code"]


def _milestone(api_client, project, **body):
    return _call(api_client, "POST", project, "milestones", body, 201)


def _docs(api_client, project, milestone, *names, position=None):
    body = {"milestone_id": milestone["id"], "rows": [{"name": n} for n in names]}
    if position is not None:
        body["position"] = position
    return _call(api_client, "POST", project, "document-types/bulk", body, 201)


def _numbers(api_client, project):
    """[(里程碑代码, [种类编号…])]，含停用的。"""
    tree = api_client.get(_url(project, "structure?include_inactive=true")).json()
    return [(m["code"], [d["submittal_no"] for d in m["document_types"]])
            for m in tree["milestones"]]


def _attach_file(session, project, document_type_id):
    """给文件种类挂一个文件：用结构修改时自动生成的应交项（第 5 步）。"""
    deliverable = session.scalars(select(Deliverable).where(
        Deliverable.document_type_id == document_type_id)).first()
    file = File(project_id=project["id"], source=FileSource.VENDOR)
    session.add(file)
    session.flush()
    session.add(FileDeliverable(file_id=file.id, deliverable_id=deliverable.id))
    session.flush()


# ---------------------------------------------------------------- 里程碑编号


def test_milestones_numbered_automatically(api_client, project):
    ms = [_milestone(api_client, project, name=f"Phase {i}") for i in range(3)]
    assert [(m["code"], m["number"]) for m in ms] == [("M1", "1"), ("M2", "2"), ("M3", "3")]


def test_insert_milestone_shifts_later_ones_and_their_document_types(api_client, project):
    m1 = _milestone(api_client, project)
    m2 = _milestone(api_client, project)
    _docs(api_client, project, m2, "A", "B")
    new = _milestone(api_client, project, name="Inserted", position=2)
    assert new["code"] == "M2"
    assert _numbers(api_client, project) == [("M1", []), ("M2", []), ("M3", ["3.1", "3.2"])]
    assert m1["code"] == "M1"


def test_delete_milestone_closes_gap(api_client, project):
    _milestone(api_client, project)
    m2 = _milestone(api_client, project)
    m3 = _milestone(api_client, project)
    _docs(api_client, project, m3, "A")
    _call(api_client, "DELETE", project, f"milestones/{m2['id']}", status=204)
    assert _numbers(api_client, project) == [("M1", []), ("M2", ["2.1"])]


def test_delete_milestone_requires_empty(api_client, project):
    m = _milestone(api_client, project)
    _docs(api_client, project, m, "A")
    assert _error(api_client, "DELETE", project, f"milestones/{m['id']}") == "milestone.not_empty"


def test_reorder_milestones_renumbers(api_client, project):
    m1 = _milestone(api_client, project)
    m2 = _milestone(api_client, project)
    _docs(api_client, project, m1, "A")
    _docs(api_client, project, m2, "B")
    _call(api_client, "POST", project, "reorder",
          {"entity": "milestones", "ids": [m2["id"], m1["id"]]}, 204)
    assert _numbers(api_client, project) == [("M1", ["1.1"]), ("M2", ["2.1"])]
    tree = api_client.get(_url(project, "structure")).json()
    assert tree["milestones"][0]["document_types"][0]["name"] == "B"


def test_milestone_deactivate_keeps_number_and_needs_inactive_children(api_client, project):
    m = _milestone(api_client, project)
    [doc] = _docs(api_client, project, m, "A")
    assert _error(api_client, "POST", project,
                  f"milestones/{m['id']}/deactivate") == "milestone.has_document_types"
    _call(api_client, "POST", project, f"document-types/{doc['id']}/deactivate")
    stopped = _call(api_client, "POST", project, f"milestones/{m['id']}/deactivate")
    assert (stopped["active"], stopped["code"]) == (False, "M1")
    assert _milestone(api_client, project)["code"] == "M2"  # 停用的仍占着 M1
    assert _error(api_client, "POST", project,
                  f"document-types/{doc['id']}/activate") == "milestone.inactive"


# ---------------------------------------------------------------- 文件种类编号


def test_document_types_numbered_automatically_after_nine_is_ten(api_client, project):
    _milestone(api_client, project)
    _milestone(api_client, project)
    m3 = _milestone(api_client, project)
    next_url = _url(project, f"document-types/next-number?milestone_id={m3['id']}")
    assert api_client.get(next_url).json()["submittal_no"] == "3.1"
    docs = _docs(api_client, project, m3, *[f"D{i}" for i in range(1, 11)])
    assert [d["submittal_no"] for d in docs][-2:] == ["3.9", "3.10"]
    assert api_client.get(next_url).json()["submittal_no"] == "3.11"


def test_insert_document_types_at_position(api_client, project):
    m = _milestone(api_client, project)
    _docs(api_client, project, m, "A", "B", "C")
    inserted = _docs(api_client, project, m, "X", "Y", position=2)
    assert [d["submittal_no"] for d in inserted] == ["1.2", "1.3"]
    tree = api_client.get(_url(project, "structure")).json()
    names = [(d["submittal_no"], d["name"]) for d in tree["milestones"][0]["document_types"]]
    assert names == [("1.1", "A"), ("1.2", "X"), ("1.3", "Y"), ("1.4", "B"), ("1.5", "C")]


def test_delete_document_type_closes_gap(api_client, project):
    m = _milestone(api_client, project)
    a, b, c = _docs(api_client, project, m, "A", "B", "C")
    _call(api_client, "DELETE", project, f"document-types/{b['id']}", status=204)
    assert _numbers(api_client, project) == [("M1", ["1.1", "1.2"])]


def test_deactivated_document_type_keeps_its_number(api_client, project):
    m = _milestone(api_client, project)
    a, b = _docs(api_client, project, m, "A", "B")
    _call(api_client, "POST", project, f"document-types/{a['id']}/deactivate")
    [c] = _docs(api_client, project, m, "C")
    assert c["submittal_no"] == "1.3"


def test_reorder_document_types_renumbers(api_client, project):
    m = _milestone(api_client, project)
    a, b, c = _docs(api_client, project, m, "A", "B", "C")
    _call(api_client, "POST", project, "reorder",
          {"entity": "document_types", "milestone_id": m["id"], "ids": [c["id"], a["id"], b["id"]]},
          204)
    tree = api_client.get(_url(project, "structure")).json()
    names = [(d["submittal_no"], d["name"]) for d in tree["milestones"][0]["document_types"]]
    assert names == [("1.1", "C"), ("1.2", "A"), ("1.3", "B")]
    # 不给全部 → 拒绝
    assert _error(api_client, "POST", project, "reorder", {
        "entity": "document_types", "milestone_id": m["id"], "ids": [a["id"]],
    }) == "structure.reorder_incomplete"


def test_move_document_type_to_other_milestone(api_client, project):
    m1 = _milestone(api_client, project)
    m2 = _milestone(api_client, project)
    a, b, c = _docs(api_client, project, m1, "A", "B", "C")
    _docs(api_client, project, m2, "X")
    moved = _call(api_client, "PATCH", project, f"document-types/{a['id']}",
                  {"milestone_id": m2["id"]})
    assert moved["submittal_no"] == "2.2"
    assert _numbers(api_client, project) == [("M1", ["1.1", "1.2"]), ("M2", ["2.1", "2.2"])]


# ---------------------------------------------------------------- 有文件后编号锁定


def test_numbers_locked_once_files_exist(api_client, project, session):
    m = _milestone(api_client, project)
    a, b, c = _docs(api_client, project, m, "A", "B", "C")
    _attach_file(session, project, c["id"])

    # 插到前面会让 1.3 变动 → 拒绝
    assert _error(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m["id"], "rows": [{"name": "X"}], "position": 1,
    }) == "structure.numbers_locked"
    # 排在最后不影响别人 → 可以
    assert _docs(api_client, project, m, "D")[0]["submittal_no"] == "1.4"
    # 删掉 1.2：1.3 有文件，不往前补，留下空号
    _call(api_client, "DELETE", project, f"document-types/{b['id']}", status=204)
    assert _numbers(api_client, project) == [("M1", ["1.1", "1.3", "1.4"])]
    # 挂了文件的种类不能删、不能移，只能停用
    assert _error(api_client, "DELETE", project,
                  f"document-types/{c['id']}") == "document_type.has_files"
    m2 = _milestone(api_client, project)
    assert _error(api_client, "PATCH", project, f"document-types/{c['id']}",
                  {"milestone_id": m2["id"]}) == "document_type.number_locked"
    # 其他字段仍然能改
    assert _call(api_client, "PATCH", project, f"document-types/{c['id']}",
                 {"name": "Renamed"})["name"] == "Renamed"


def test_milestone_insert_locked_when_shifted_types_have_files(api_client, project, session):
    _milestone(api_client, project)
    m2 = _milestone(api_client, project)
    [doc] = _docs(api_client, project, m2, "A")
    _attach_file(session, project, doc["id"])
    assert _error(api_client, "POST", project, "milestones",
                  {"position": 1}) == "structure.numbers_locked"


# ---------------------------------------------------------------- 批量新建


def test_bulk_create_all_or_nothing(api_client, project):
    m = _milestone(api_client, project)
    resp = api_client.post(_url(project, "document-types/bulk"), json={
        "milestone_id": m["id"], "rows": [{"name": "A"}, {"name": "   "}, {"name": "C"}],
    }, headers=USER)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["params"]["row"] == 2 and detail["message"].startswith("第 2 行")
    assert _numbers(api_client, project) == [("M1", [])]


# ---------------------------------------------------------------- 模块分区


def test_modules_created_inline_and_shared(api_client, project):
    m = _milestone(api_client, project)
    a, b = _docs(api_client, project, m, "A", "B")
    first = _call(api_client, "PUT", project, f"document-types/{a['id']}/modules",
                  {"module_names": ["Automation", "Alpha"]})
    assert first["per_module"] is True
    _call(api_client, "PUT", project, f"document-types/{b['id']}/modules",
          {"module_names": ["Alpha", " Thermal "]})
    modules = [x["name"] for x in api_client.get(_url(project, "modules")).json()]
    assert modules == ["Automation", "Alpha", "Thermal"]  # Alpha 只有一个


def test_set_modules_toggles_per_module_and_keeps_history(api_client, project):
    m = _milestone(api_client, project)
    [doc] = _docs(api_client, project, m, "A")
    _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules",
          {"module_names": ["Automation", "Alpha"]})
    cleared = _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules", {})
    assert cleared["per_module"] is False
    tree = api_client.get(_url(project, "structure?include_inactive=true")).json()
    parts = tree["milestones"][0]["document_types"][0]["partitions"]
    assert {(p["module_name"], p["active"]) for p in parts} == {
        ("Automation", False), ("Alpha", False)
    }


def test_bulk_modules_add_remove_replace(api_client, project):
    m = _milestone(api_client, project)
    ids = [d["id"] for d in _docs(api_client, project, m, "D1", "D2")]

    def active_modules():
        tree = api_client.get(_url(project, "structure")).json()
        return [[p["module_name"] for p in d["partitions"]]
                for d in tree["milestones"][0]["document_types"]]

    _call(api_client, "POST", project, "document-types/bulk-modules",
          {"document_type_ids": ids, "module_names": ["A", "B"], "mode": "add"})
    assert active_modules() == [["A", "B"], ["A", "B"]]
    _call(api_client, "POST", project, "document-types/bulk-modules",
          {"document_type_ids": ids, "module_names": ["A"], "mode": "remove"})
    assert active_modules() == [["B"], ["B"]]
    _call(api_client, "POST", project, "document-types/bulk-modules",
          {"document_type_ids": ids[:1], "module_names": ["C"], "mode": "replace"})
    assert active_modules() == [["C"], ["B"]]


# ---------------------------------------------------------------- 对应设备


@pytest.fixture
def equipment(project, session, api_client):
    """两个设备种类、四台设备（其中一台停用），模块 Alpha 对应种类A种类。"""
    alpha_type = EquipmentType(project_id=project["id"], name="Alpha bench")
    beta_type = EquipmentType(project_id=project["id"], name="Beta")
    session.add_all([alpha_type, beta_type])
    session.flush()
    items = [
        Equipment(project_id=project["id"], equipment_type_id=alpha_type.id, tag="71", name="W1",
                  sort_order=1),
        Equipment(project_id=project["id"], equipment_type_id=alpha_type.id, tag="72", name="W2",
                  sort_order=2),
        Equipment(project_id=project["id"], equipment_type_id=beta_type.id, tag="80", name="D1",
                  sort_order=3),
        Equipment(project_id=project["id"], equipment_type_id=beta_type.id, tag="81", name="Old",
                  sort_order=4, active=False),
    ]
    session.add_all(items)
    session.flush()
    alpha = _call(api_client, "POST", project, "modules", {"name": "Alpha"}, 201)
    session.add(ModuleEquipmentType(module_id=alpha["id"], equipment_type_id=alpha_type.id))
    session.flush()
    return {"alpha_type": alpha_type.id, "beta_type": beta_type.id, "alpha_module": alpha["id"],
            "ids": [e.id for e in items]}


def _equipment_tags(api_client, project):
    tree = api_client.get(_url(project, "structure")).json()
    return [[e["tag"] for e in d["equipment"]] for d in tree["milestones"][0]["document_types"]]


def test_bulk_equipment_selectors(api_client, project, equipment):
    m = _milestone(api_client, project)
    a, b = _docs(api_client, project, m, "A", "B")

    result = _call(api_client, "POST", project, "document-types/bulk-equipment",
                   {"document_type_ids": [a["id"]], "all_equipment": True, "mode": "add"})
    assert result[0]["per_equipment"] is True  # 关联设备后自动标为按设备
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [b["id"]], "module_ids": [equipment["alpha_module"]],
           "mode": "replace"})
    assert _equipment_tags(api_client, project) == [["71", "72", "80"], ["71", "72"]]  # 停用的不选

    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [a["id"], b["id"]], "equipment_type_ids": [equipment["alpha_type"]],
           "mode": "remove"})
    assert _equipment_tags(api_client, project) == [["80"], []]
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [b["id"]], "equipment_ids": [equipment["ids"][2]], "mode": "add"})
    assert _equipment_tags(api_client, project) == [["80"], ["80"]]

    assert _error(api_client, "POST", project, "document-types/bulk-equipment", {
        "document_type_ids": [a["id"]], "equipment_ids": [equipment["ids"][3]], "mode": "add",
    }) == "equipment.inactive"


def test_turning_off_per_equipment_drops_links(api_client, project, equipment):
    m = _milestone(api_client, project)
    [doc] = _docs(api_client, project, m, "A")
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "all_equipment": True, "mode": "add"})
    _call(api_client, "PATCH", project, f"document-types/{doc['id']}", {"per_equipment": False})
    assert _equipment_tags(api_client, project) == [[]]


# ---------------------------------------------------------------- 复制、只读、留痕


def test_copy_structure_copies_definitions_only(api_client, project, equipment):
    m = _milestone(api_client, project, due_date="2026-06-07")
    [doc] = _call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m["id"],
        "rows": [{"name": "Spec", "mdl_status_text": "Approved", "content": "Specs"}],
    }, 201)
    _call(api_client, "PUT", project, f"document-types/{doc['id']}/modules",
          {"module_names": ["Alpha"]})
    _call(api_client, "POST", project, "document-types/bulk-equipment",
          {"document_type_ids": [doc["id"]], "all_equipment": True, "mode": "add"})

    target = api_client.post("/api/projects", json={"code": "S-002", "name": "Copy"},
                             headers=USER).json()
    # 目标项目已有同名设备种类（如先导入了设备清单）：沿用，不重复建
    _call(api_client, "POST", target, "equipment-types", {"name": "Beta"}, 201)
    result = _call(api_client, "POST", target, "copy-structure",
                   {"source_project_id": project["id"]})
    assert result == {"milestones": 1, "modules": 1, "document_types": 1, "partitions": 1,
                      "equipment_types": 1, "module_equipment_types": 1}
    types = {t["name"]: t["id"] for t in _call(api_client, "GET", target, "equipment-types")}
    assert set(types) == {"Alpha bench", "Beta"}
    [module] = _call(api_client, "GET", target, "modules")
    assert [t["name"] for t in _call(api_client, "GET", target, f"modules/{module['id']}/equipment-types")]         == ["Alpha bench"]  # 模块对应的设备种类也复制了
    assert _call(api_client, "GET", target, "equipment") == []  # 设备不复制

    tree = api_client.get(_url(target, "structure")).json()
    ms = tree["milestones"][0]
    copied = ms["document_types"][0]
    assert (ms["code"], ms["due_date"]) == ("M1", None)
    assert (copied["submittal_no"], copied["name"], copied["content"]) == ("1.1", "Spec", "Specs")
    assert copied["mdl_status_text"] is None  # 进度不复制
    assert copied["equipment"] == []  # 设备属于各自项目
    assert copied["partitions"][0]["module_name"] == "Alpha"
    assert _error(api_client, "POST", target, "copy-structure",
                  {"source_project_id": project["id"]}) == "structure.target_not_empty"


def test_archived_project_structure_read_only(api_client, project):
    m = _milestone(api_client, project)
    api_client.post(f"/api/projects/{project['id']}/archive", headers=USER)
    assert _error(api_client, "POST", project, "document-types",
                  {"milestone_id": m["id"], "name": "X"}) == "project.archived"
    assert api_client.get(_url(project, "structure")).status_code == 200


def test_writes_recorded_in_change_log(api_client, project, session):
    m = _milestone(api_client, project)
    a, b = _docs(api_client, project, m, "Spec", "Other")
    _call(api_client, "PATCH", project, f"document-types/{b['id']}",
          {"name": "Other v2", "reason": "客户改名"})
    _call(api_client, "DELETE", project, f"document-types/{a['id']}", status=204)

    def logs(doc_id):
        return session.scalars(select(ChangeLog).where(
            ChangeLog.entity_type == "document_types", ChangeLog.entity_id == str(doc_id),
        ).order_by(ChangeLog.id)).all()

    deleted = logs(a["id"])
    assert [x.action.value for x in deleted] == ["create", "delete"]
    assert deleted[1].changes["name"] == ["Spec", None]  # 删除前的完整内容留在记录里
    renamed = logs(b["id"])
    assert [x.action.value for x in renamed] == ["create", "update", "update"]
    assert renamed[1].changes == {"name": ["Other", "Other v2"]}
    assert renamed[2].changes["submittal_no"] == ["1.2", "1.1"]  # 删除后往前补


def test_other_project_ids_rejected(api_client, project):
    m = _milestone(api_client, project)
    other = api_client.post("/api/projects", json={"code": "S-OTHER", "name": "Other"},
                            headers=USER).json()
    assert _error(api_client, "POST", other, "document-types",
                  {"milestone_id": m["id"], "name": "X"}) == "milestone.not_found"
