import pytest

from app.services.errors import Conflict, Invalid
from app.services.history import Actor
from app.services.mdl_versions import freeze_version

USER = {"X-User": "alice"}
ACTOR = Actor(name="alice")


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "MV-1", "name": "versions"},
                           headers=USER).json()


def _url(project, path=""):
    return f"/api/projects/{project['id']}/{path}"


def _call(api_client, method, project, path, body=None, status=200):
    resp = api_client.request(method, _url(project, path), json=body, headers=USER)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.content else None


def _docs(api_client, project, milestone, *rows, position=None):
    body = {"milestone_id": milestone["id"],
            "rows": [r if isinstance(r, dict) else {"name": r} for r in rows]}
    if position is not None:
        body["position"] = position
    return _call(api_client, "POST", project, "document-types/bulk", body, 201)


def _issue(session, project, description=None):
    """模拟第 6 步把 MDL 发给客户：记一个版本。"""
    version = freeze_version(session, project["id"], ACTOR, description=description)
    session.commit()
    return version


@pytest.fixture
def contract(api_client, project, session):
    """合同签订时的结构：M1 下 3 个合同规定的种类，第一次发给客户为 Rev.0。"""
    m1 = _call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)
    a, b, c = _docs(api_client, project, m1,
                    {"name": "Spec", "required_date": "2026-01-15", "basis": "Contract App. D"},
                    "Layout", "P&ID")
    rev0 = _issue(session, project, "合同签订")
    return {"m1": m1, "a": a, "b": b, "c": c, "rev0": rev0}


# ---------------------------------------------------------------- 要求类别与客户可见


def test_default_category_before_and_after_first_issue(api_client, project, contract):
    assert contract["a"]["origin"] == "contract"
    assert contract["a"]["in_client_mdl"] is True
    [added] = _docs(api_client, project, contract["m1"], "Added later")
    assert added["origin"] == "client"  # 发过 MDL 之后新建的默认是客户要求


def test_dates_are_real_dates_not_filled_automatically(api_client, project, contract):
    a = contract["a"]
    assert (a["required_date"], a["basis"]) == ("2026-01-15", "Contract App. D")
    assert contract["b"]["required_date"] is None  # 不知道就空着，不拿录入日期冒充
    changed = _call(api_client, "PATCH", project, f"document-types/{a['id']}",
                    {"changed_date": "2026-05-02", "change_ref": "CL-020"})
    assert (changed["changed_date"], changed["change_ref"]) == ("2026-05-02", "CL-020")


def test_internal_never_in_client_mdl(api_client, project, contract):
    [internal] = _docs(api_client, project, contract["m1"],
                       {"name": "Our checklist", "origin": "internal"})
    assert internal["in_client_mdl"] is False
    resp = api_client.patch(_url(project, f"document-types/{internal['id']}"),
                            json={"in_client_mdl": True}, headers=USER)
    assert resp.json()["detail"]["code"] == "document_type.internal_in_mdl"
    resp = api_client.post(_url(project, "document-types/bulk"), json={
        "milestone_id": contract["m1"]["id"],
        "rows": [{"name": "X", "origin": "internal", "in_client_mdl": True}],
    }, headers=USER)
    assert resp.json()["detail"]["params"]["row"] == 1
    # 改成合同规定 / 客户要求后，默认重新列入
    shown = _call(api_client, "PATCH", project, f"document-types/{internal['id']}",
                  {"origin": "client"})
    assert shown["in_client_mdl"] is True
    # 合同规定的改成内部要求后，自动不列入
    hidden = _call(api_client, "PATCH", project, f"document-types/{contract['c']['id']}",
                   {"origin": "internal"})
    assert hidden["in_client_mdl"] is False


def test_contract_item_can_be_hidden_manually(api_client, project, contract):
    hidden = _call(api_client, "PATCH", project, f"document-types/{contract['a']['id']}",
                   {"in_client_mdl": False})
    assert (hidden["origin"], hidden["in_client_mdl"]) == ("contract", False)
    diff = _call(api_client, "GET", project, "mdl-versions/compare?from=0&to=current")
    assert [d["name"] for d in diff["document_types"]["removed"]] == ["Spec"]


# ---------------------------------------------------------------- 版本与对比


def test_rev0_snapshot(api_client, project, contract):
    assert contract["rev0"].rev_no == 0
    detail = _call(api_client, "GET", project, "mdl-versions/0")
    assert detail["label"] == "Rev.0" and detail["document_types"] == 3
    snap = detail["snapshot"]["document_types"][str(contract["a"]["id"])]
    assert (snap["submittal_no"], snap["name"], snap["origin"]) == ("1.1", "Spec", "contract")
    assert (snap["required_date"], snap["basis"]) == ("2026-01-15", "Contract App. D")
    assert detail["description"] == "合同签订"


def test_issue_without_changes_or_empty_refused(project, contract, session):
    with pytest.raises(Conflict) as exc:
        freeze_version(session, project["id"], ACTOR)
    assert exc.value.code == "mdl_version.no_changes"


def test_issue_empty_structure_refused(project, session):
    with pytest.raises(Invalid) as exc:
        freeze_version(session, project["id"], ACTOR)
    assert exc.value.code == "mdl_version.empty"


def test_no_manual_freeze_endpoint(api_client, project, contract):
    resp = api_client.post(_url(project, "mdl-versions"), headers=USER)
    assert resp.status_code == 405  # 版本只在发 MDL 给客户时自动记录


def test_rev1_diff(api_client, project, contract, session):
    a, b, c = contract["a"], contract["b"], contract["c"]
    # 客户要求一个、插在最前面（原来的编号全部往后挪）；改名一个；删掉一个
    _docs(api_client, project, contract["m1"],
          {"name": "Noise report", "required_date": "2026-03-10", "basis": "CL-012"}, position=1)
    _call(api_client, "PATCH", project, f"document-types/{b['id']}", {"name": "Layout (final)"})
    _call(api_client, "DELETE", project, f"document-types/{c['id']}", status=204)
    assert _issue(session, project, "客户函 CL-012").rev_no == 1

    diff = _call(api_client, "GET", project, "mdl-versions/compare?from=0&to=1")
    docs = diff["document_types"]
    assert [(d["submittal_no"], d["name"]) for d in docs["added"]] == [("1.1", "Noise report")]
    assert [(d["submittal_no"], d["name"]) for d in docs["removed"]] == [("1.3", "P&ID")]
    modified = {d["id"]: d["changes"] for d in docs["modified"]}
    # 编号变了也认得是同一个：显示为修改，不算一删一增
    assert modified[a["id"]] == {"submittal_no": ["1.1", "1.2"]}
    assert modified[b["id"]] == {"submittal_no": ["1.2", "1.3"],
                                 "name": ["Layout", "Layout (final)"]}
    assert diff["has_changes"] is True and (diff["from"], diff["to"]) == ("Rev.0", "Rev.1")


def test_internal_types_not_in_mdl(api_client, project, contract):
    _docs(api_client, project, contract["m1"], {"name": "Our checklist", "origin": "internal"})
    diff = _call(api_client, "GET", project, "mdl-versions/compare?from=0&to=current")
    assert diff["has_changes"] is False


def test_compare_with_current_and_modules(api_client, project, contract):
    _call(api_client, "PUT", project, f"document-types/{contract['a']['id']}/modules",
          {"module_names": ["Alpha"]})
    diff = _call(api_client, "GET", project, "mdl-versions/compare?from=0&to=current")
    [changed] = diff["document_types"]["modified"]
    assert changed["changes"]["modules"] == [[], ["Alpha"]]
    assert changed["changes"]["per_module"] == [False, True]
    assert diff["to"] == "current"


def test_list_versions_and_errors(api_client, project, contract, session):
    _docs(api_client, project, contract["m1"], "More")
    _issue(session, project)
    versions = _call(api_client, "GET", project, "mdl-versions")
    assert [v["label"] for v in versions] == ["Rev.0", "Rev.1"]
    assert versions[0]["frozen_by_name"] == "alice"
    assert api_client.get(_url(project, "mdl-versions/9")).status_code == 404
    resp = api_client.get(_url(project, "mdl-versions/compare?from=0&to=abc"))
    assert resp.json()["detail"]["code"] == "mdl_version.bad_to"


def test_issue_recorded_in_change_log(api_client, project, contract):
    logs = api_client.get("/api/change-logs", params={
        "project_id": project["id"], "entity_type": "mdl_versions"}).json()
    [log] = logs["items"]
    assert log["changes"]["rev_no"] == [None, 0]
    assert log["changes"]["document_types"] == [None, 3]


# ---------------------------------------------------------------- 复制、模板


def test_copy_structure_resets_requirement(api_client, project, contract):
    _docs(api_client, project, contract["m1"], {"name": "Extra", "basis": "CL-1"})
    target = api_client.post("/api/projects", json={"code": "MV-2", "name": "copy"},
                             headers=USER).json()
    _call(api_client, "POST", target, "copy-structure", {"source_project_id": project["id"]})
    docs = {d["name"]: d for d in api_client.get(_url(target, "document-types")).json()}
    assert (docs["Extra"]["origin"], docs["Extra"]["basis"]) == ("contract", None)
    assert docs["Spec"]["required_date"] is None


def test_template_requirement_columns(api_client):
    from tests.test_structure_template import _fill

    data = _fill(
        [("Design",)],
        [("M1", None, "Spec") + (None,) * 10 + ("客户要求", "2026-03-10", "CL-7", None, None, "是"),
         ("M1", None, "Checklist") + (None,) * 10 + ("内部要求",)],
    )
    other = api_client.post("/api/projects", json={"code": "MV-3", "name": "tpl"},
                            headers=USER).json()
    preview = api_client.post(f"/api/projects/{other['id']}/structure-import/preview",
                              files={"file": ("s.xlsx", data)}, headers=USER).json()
    assert preview["ok"], preview["errors"]
    api_client.post(f"/api/projects/{other['id']}/structure-import/confirm",
                    json={"token": preview["token"]}, headers=USER)
    docs = {d["name"]: d for d in api_client.get(_url(other, "document-types")).json()}
    spec, checklist = docs["Spec"], docs["Checklist"]
    assert (spec["origin"], spec["required_date"], spec["basis"]) == (
        "client", "2026-03-10", "CL-7")
    assert (checklist["origin"], checklist["in_client_mdl"]) == ("internal", False)
