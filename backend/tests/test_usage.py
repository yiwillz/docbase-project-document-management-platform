"""第 17F 步：没用过的设备、模块、设备种类能真删除（编号释放），用过的只能停用。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import ChangeLog, Equipment, Module

CSRF = {"X-Docbase": "1"}


def _url(project, path):
    return f"/api/projects/{project['id']}/{path}"


def call(api_client, method, project, path, body=None, status=200, **kw):
    resp = api_client.request(method, _url(project, path), json=body, headers=CSRF, **kw)
    assert resp.status_code == status, resp.text
    return resp.json() if resp.content and resp.headers.get("content-type", "").startswith("application/json") else None


@pytest.fixture
def setup(api_client):
    """项目：Alpha 种类、设备 71 / 72，模块 A（对应 Alpha）；1.1 Spec 按模块 A，1.2 Datasheet 按设备。"""
    p = api_client.post("/api/projects", json={"code": "US-1", "name": "Usage demo"}, headers=CSRF).json()
    alpha = call(api_client, "POST", p, "equipment-types", {"name": "Alpha"}, 201)
    e71 = call(api_client, "POST", p, "equipment", {"tag": "71", "name": "Etcher", "equipment_type_id": alpha["id"]}, 201)
    e72 = call(api_client, "POST", p, "equipment", {"tag": "72", "name": "Mixer", "equipment_type_id": alpha["id"]}, 201)
    m = call(api_client, "POST", p, "milestones", {"name": "Design"}, 201)
    spec, ds = call(api_client, "POST", p, "document-types/bulk",
                    {"milestone_id": m["id"], "rows": [{"name": "Spec"}, {"name": "Datasheet"}]}, 201)
    call(api_client, "PUT", p, f"document-types/{spec['id']}/modules", {"module_names": ["A"]})
    mod = next(x for x in call(api_client, "GET", p, "modules") if x["name"] == "A")
    call(api_client, "PUT", p, f"modules/{mod['id']}/equipment-types", {"equipment_type_ids": [alpha["id"]]})
    call(api_client, "POST", p, "document-types/bulk-equipment",
         {"document_type_ids": [ds["id"]], "all_equipment": True, "mode": "add"})
    items = call(api_client, "GET", p, "deliverables")["items"]
    d72 = next(i for i in items if i["equipment_tag"] == "72")
    dspec = next(i for i in items if i["module_name"] == "A" and i["submittal_no"] == "1.1")
    return {"p": p, "alpha": alpha, "e71": e71, "e72": e72, "module": mod, "spec": spec, "ds": ds,
            "d72": d72, "dspec": dspec}


def _store(api_client, p, did, name):
    r = api_client.post(_url(p, "files/preview"), data={"deliverable_ids": str(did)}, headers=CSRF,
                        files={"file": (name, name.encode(), "application/pdf")})
    call(api_client, "POST", p, f"files/uploads/{r.json()['token']}/confirm", {})


def test_unused_equipment_is_deleted_and_tag_reusable(api_client, session, setup):
    p, e72 = setup["p"], setup["e72"]
    assert call(api_client, "GET", p, f"equipment/{e72['id']}/usage") == {"deletable": True, "usage": {}}
    call(api_client, "DELETE", p, f"equipment/{e72['id']}", status=204, params={"reason": "录错了"})
    assert session.get(Equipment, e72["id"]) is None
    assert all(i["equipment_tag"] != "72" for i in call(api_client, "GET", p, "deliverables")["items"])
    log = session.scalars(select(ChangeLog).where(ChangeLog.entity_type == "equipment",
                                                  ChangeLog.entity_id == str(e72["id"]),
                                                  ChangeLog.action == "delete")).one()
    assert log.reason == "录错了" and log.changes["tag"] == ["72", None]
    # 编号释放，可以再用
    again = call(api_client, "POST", p, "equipment", {"tag": "72", "name": "New mixer"}, 201)
    assert again["tag"] == "72"


@pytest.mark.parametrize("how", ["file", "transmittal", "baseline"])
def test_used_equipment_can_only_be_deactivated(api_client, setup, how):
    p, e72, d72 = setup["p"], setup["e72"], setup["d72"]
    if how == "file":
        _store(api_client, p, d72["id"], "ds.pdf")
    elif how == "transmittal":  # 交过、但平台里没有文件
        call(api_client, "POST", p, "transmittals", {"deliverable_ids": [d72["id"]]}, 201)
    else:
        call(api_client, "POST", p, "baselines", {"name": "Freeze"}, 201)
    key = {"file": "files", "transmittal": "transmittals", "baseline": "baselines"}[how]
    usage = call(api_client, "GET", p, f"equipment/{e72['id']}/usage")
    assert usage["deletable"] is False and usage["usage"][key] == 1
    r = api_client.delete(_url(p, f"equipment/{e72['id']}"), headers=CSRF)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "equipment.in_use"
    call(api_client, "POST", p, f"equipment/{e72['id']}/deactivate", {"reason": "停用"})


def test_equipment_seen_in_issued_mdl(api_client, session, setup):
    p, e72 = setup["p"], setup["e72"]
    call(api_client, "POST", p, "mdl/issue", {})  # Rev.0
    assert call(api_client, "GET", p, f"equipment/{e72['id']}/usage")["usage"] == {"mdl_issued": 1}
    # 发 MDL 之后建的设备：客户没见过，能删
    later = call(api_client, "POST", p, "equipment", {"tag": "80", "name": "Dryer"}, 201)
    session.get(Equipment, later["id"]).created_at = datetime.now(UTC) + timedelta(hours=1)
    session.flush()
    assert call(api_client, "GET", p, f"equipment/{later['id']}/usage")["deletable"] is True
    # 结构没变再发一次（沿用 Rev.0）也会留记录
    call(api_client, "POST", p, "mdl/issue", {})
    logs = session.scalars(select(ChangeLog).where(ChangeLog.project_id == p["id"],
                                                   ChangeLog.entity_type == "mdl_versions")).all()
    assert len(logs) == 2 and logs[-1].changes["reissued"] == [None, "Rev.0"]


def test_bulk_delete_skips_used_equipment(api_client, setup):
    p, e71, e72, d72 = setup["p"], setup["e71"], setup["e72"], setup["d72"]
    _store(api_client, p, d72["id"], "ds.pdf")
    result = call(api_client, "POST", p, "equipment/bulk-delete", {"ids": [e71["id"], e72["id"]]})
    assert result["deleted"] == [e71["id"]]
    assert [(s["tag"], s["usage"]) for s in result["skipped"]] == [("72", {"files": 1})]
    call(api_client, "POST", p, "equipment/bulk-deactivate", {"ids": [e72["id"]]})
    assert call(api_client, "GET", p, "equipment") == []  # 默认不列停用的


def test_module_delete(api_client, session, setup):
    p, mod, dspec = setup["p"], setup["module"], setup["dspec"]
    assert call(api_client, "GET", p, f"modules/{mod['id']}/usage")["deletable"] is True
    _store(api_client, p, dspec["id"], "spec.pdf")
    r = api_client.delete(_url(p, f"modules/{mod['id']}"), headers=CSRF)
    assert r.status_code == 409 and r.json()["detail"]["params"]["files"] == 1
    # 另一个没用过的模块能删，连同分区
    b = call(api_client, "POST", p, "modules", {"name": "B"}, 201)
    call(api_client, "DELETE", p, f"modules/{b['id']}", status=204)
    assert session.get(Module, b["id"]) is None


def test_equipment_type_delete_needs_no_equipment(api_client, setup):
    p, alpha, e71, e72 = setup["p"], setup["alpha"], setup["e71"], setup["e72"]
    assert call(api_client, "GET", p, f"equipment-types/{alpha['id']}/usage") == {"deletable": False,
                                                                                 "usage": {"equipment": 2}}
    r = api_client.delete(_url(p, f"equipment-types/{alpha['id']}"), headers=CSRF)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "equipment_type.in_use"
    for e in (e71, e72):
        call(api_client, "DELETE", p, f"equipment/{e['id']}", status=204)
    call(api_client, "DELETE", p, f"equipment-types/{alpha['id']}", status=204)
    assert call(api_client, "GET", p, "equipment-types") == []


def test_document_type_in_transmittal_cannot_be_deleted(api_client, setup):
    """补漏洞：补记的传送单（没有文件）也算用过，不能删除文件种类。"""
    p, spec, dspec = setup["p"], setup["spec"], setup["dspec"]
    call(api_client, "POST", p, "transmittals", {"deliverable_ids": [dspec["id"]]}, 201)
    r = api_client.delete(_url(p, f"document-types/{spec['id']}"), headers=CSRF)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "document_type.in_transmittal"


def test_delete_endpoints_need_project_config():
    """删除接口要"项目配置"权限（和改结构一样）；规则表核对。"""
    from app.services import access

    for method, path in (("DELETE", "equipment/{equipment_id}"), ("POST", "equipment/bulk-delete"),
                         ("DELETE", "modules/{module_id}"), ("DELETE", "equipment-types/{type_id}")):
        assert access.rule_for(method, f"{access.PREFIX}/{path}").permission == "project.config"
