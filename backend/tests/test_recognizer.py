"""第 12A 步：规则识别器——按描述、文件夹名、文件名定位应交项。

虚构项目 RC-1：
M1
  1.1  整个种类 Spec（关键词 specification）
  1.2  按设备 Equipment datasheet（关键词 DS、cut sheet），对应全部设备
  1.3  按模块 Layout：Alpha area、Beta area
  1.4  整个种类 Noise study
  1.10 整个种类 Test report
设备：72 Mixer A（Vendor A，别名 PM-1）、73 Mixer A（Vendor A）、101 Dryer（Vendor B）、
      R101 Reactor（Vendor B）、没编号的 Beta part（Vendor C，3000 个）
"""

import pytest
from sqlalchemy import select

from app.models import Deliverable
from app.services.recognizer import ProjectIndex, RecognizeInput, RuleRecognizer, has_code, norm

USER = {"X-User": "alice"}


def call(api_client, method, project, path, body=None, status=200):
    resp = api_client.request(method, f"/api/projects/{project['id']}/{path}", json=body, headers=USER)
    assert resp.status_code == status, resp.text
    return resp.json()


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "RC-1", "name": "Recognizer demo"},
                           headers=USER).json()


@pytest.fixture
def ids(api_client, project, session):
    """应交项 id：spec、noise、test、72、73、101、R101、part、alpha、beta。"""
    m1 = call(api_client, "POST", project, "milestones", {"name": "Design"}, 201)
    docs = call(api_client, "POST", project, "document-types/bulk", {
        "milestone_id": m1["id"], "rows": [
            {"name": "Spec", "keywords": ["specification"]},
            {"name": "Equipment datasheet", "keywords": ["DS", "cut sheet", " ds "]},
            {"name": "Layout"}, {"name": "Noise study"}]}, 201)
    for n in range(5, 10):
        call(api_client, "POST", project, "document-types", {"milestone_id": m1["id"], "name": f"Filler {n}"}, 201)
    test_report = call(api_client, "POST", project, "document-types",
                       {"milestone_id": m1["id"], "name": "Test report"}, 201)
    assert test_report["submittal_no"] == "1.10"
    assert docs[1]["keywords"] == ["DS", "cut sheet"]  # 去重（不分大小写）、去空白
    eq = {}
    for e in [{"tag": "72", "name": "Mixer A", "vendor": "Vendor A", "aliases": ["PM-1"]},
              {"tag": "73", "name": "Mixer A", "vendor": "Vendor A"},
              {"tag": "101", "name": "Dryer", "vendor": "Vendor B"},
              {"tag": "R101", "name": "Reactor", "vendor": "Vendor B"},
              {"name": "Beta part", "vendor": "Vendor C", "quantity": 3000}]:
        eq[call(api_client, "POST", project, "equipment", e, 201)["id"]] = e.get("tag") or "part"
    call(api_client, "POST", project, "document-types/bulk-equipment", {
        "document_type_ids": [docs[1]["id"]], "all_equipment": True, "mode": "add"})
    call(api_client, "PUT", project, f"document-types/{docs[2]['id']}/modules",
         {"module_names": ["Alpha area", "Beta area"]})
    modules = {m["id"]: m["name"] for m in call(api_client, "GET", project, "modules")}
    by_doc = {docs[0]["id"]: "spec", docs[3]["id"]: "noise", test_report["id"]: "test"}
    out = {}
    for d in session.scalars(select(Deliverable).where(Deliverable.project_id == project["id"],
                                                       Deliverable.obsolete.is_(False))):
        if d.equipment_id:
            out[eq[d.equipment_id]] = d.id
        elif d.module_id:
            out[modules[d.module_id].split()[0].lower()] = d.id
        elif d.document_type_id in by_doc:
            out[by_doc[d.document_type_id]] = d.id
    return out


def recognize(session, project, filename, **kw):
    return RuleRecognizer(ProjectIndex(session, project["id"])).recognize(
        RecognizeInput(filename=filename, **kw))


def best(result):
    assert result.best is not None, "没有候选"
    return result.best


# ---------------------------------------------------------------- 文字工具


def test_has_code_respects_boundaries():
    assert has_code(norm("datasheet 72 rev"), "72")
    assert not has_code(norm("datasheet 172"), "72")
    assert not has_code(norm("item 3.50"), "3.5") and not has_code(norm("13.5 x"), "3.5")
    assert has_code(norm("1.2 cut sheet"), "1.2")
    assert has_code(norm("P-101 pump"), norm("P-101"))


# ---------------------------------------------------------------- 定位


def test_platform_named_file_comes_back(session, project, ids):
    r = recognize(session, project, "1.2.72&73_Equipment datasheet-Mixer A-20260226-V1.pdf")
    assert sorted(best(r).deliverable_ids) == sorted([ids["72"], ids["73"]])
    assert best(r).level == "high" and best(r).reasons[0].kind == "platform_name"


def test_platform_named_whole_type(session, project, ids):
    r = recognize(session, project, "1.4_Noise study-Area A-20260226-V2.pdf")
    assert best(r).deliverable_ids == [ids["noise"]] and best(r).level == "high"


def test_keyword_and_tag(session, project, ids):
    r = recognize(session, project, "72 cut sheet.pdf")
    assert best(r).deliverable_ids == [ids["72"]] and best(r).level == "high"
    kinds = {x.kind for x in best(r).reasons}
    assert {"keyword", "tag", "not_delivered"} <= kinds


def test_numbers_in_versions_and_dates_are_not_tags(session, project, ids):
    r = recognize(session, project, "DS 101 Rev 72 20260301.pdf")  # "Rev 72" 是版本号，不是设备 72
    assert best(r).deliverable_ids == [ids["101"]] and best(r).level == "high"
    assert all(ids["72"] not in c.deliverable_ids for c in r.candidates)


def test_alphanumeric_tag_survives_version_stripping(session, project, ids):
    r = recognize(session, project, "R101 cut sheet.pdf")
    assert best(r).deliverable_ids == [ids["R101"]] and best(r).level == "high"


def test_alias_and_untagged_equipment(session, project, ids):
    assert best(recognize(session, project, "PM-1 DS.pdf")).deliverable_ids == [ids["72"]]
    r = recognize(session, project, "Beta part DS.pdf")
    assert best(r).deliverable_ids == [ids["part"]] and best(r).level == "high"


def test_same_name_equipment_without_tag_is_ambiguous(session, project, ids):
    r = recognize(session, project, "Mixer A cut sheet.pdf")
    top = [c.deliverable_ids[0] for c in r.candidates[:2]]
    assert sorted(top) == sorted([ids["72"], ids["73"]])
    assert best(r).level == "medium"  # 分不出是哪一台，让人选


def test_several_tags_make_one_file_for_several_equipment(session, project, ids):
    r = recognize(session, project, "cut sheet 72, 73.pdf")
    assert sorted(best(r).deliverable_ids) == sorted([ids["72"], ids["73"]])


def test_module_from_name(session, project, ids):
    r = recognize(session, project, "Alpha area layout.dwg")
    assert best(r).deliverable_ids == [ids["alpha"]] and best(r).level == "high"


def test_whole_type_from_keyword(session, project, ids):
    r = recognize(session, project, "Project specification rev 3.pdf")
    assert best(r).deliverable_ids == [ids["spec"]] and best(r).level == "high"


def test_submittal_no_1_10_not_confused_with_1_1(session, project, ids):
    r = recognize(session, project, "1.10 report.pdf")
    assert best(r).deliverable_ids == [ids["test"]]


def test_folder_and_description_count(session, project, ids):
    r = recognize(session, project, "datasheet.pdf", folder="Vendor B/Dryer")
    assert best(r).deliverable_ids == [ids["101"]]
    assert r.source == "vendor"
    assert any(x.kind == "vendor" and x.where == "folder" for x in best(r).reasons)
    r = recognize(session, project, "scan0001.pdf", description="Dryer DS from Vendor B")
    assert best(r).deliverable_ids == [ids["101"]] and best(r).level == "high"


def test_vendor_limits_equipment(session, project, ids):
    r = recognize(session, project, "Mixer A cut sheet.pdf", vendor="Vendor B")
    assert all(ids["72"] not in c.deliverable_ids and ids["73"] not in c.deliverable_ids
               for c in r.candidates)


def test_nothing_recognized(session, project, ids):
    r = recognize(session, project, "meeting notes 20261001.pdf")
    assert r.best is None or r.best.level == "low"
    assert r.out()["level"] == "low"


def test_delivered_items_get_no_bonus(session, project, ids, api_client):
    resp = api_client.post(f"/api/projects/{project['id']}/files/preview", headers=USER,
                           data={"deliverable_ids": str(ids["72"])}, files={"file": ("a.pdf", b"x72")})
    api_client.post(f"/api/projects/{project['id']}/files/uploads/{resp.json()['token']}/confirm",
                    json={}, headers=USER)
    r = recognize(session, project, "72 cut sheet.pdf")
    assert "not_delivered" not in {x.kind for x in best(r).reasons}


# ---------------------------------------------------------------- 客户退回


def test_client_return_with_transmittal_number_and_code(session, project, ids, api_client):
    t = call(api_client, "POST", project, "transmittals", {"deliverable_ids": [ids["72"], ids["101"]]}, 201)
    r = recognize(session, project, f"{t['number']} 72 cut sheet - Code 1.pdf")
    assert best(r).deliverable_ids == [ids["72"]]
    reply = r.client_reply
    assert reply and reply.code == "1" and reply.transmittal_id == t["id"]
    item_72 = next(i["item_id"] for i in t["items"] if i["deliverable_id"] == ids["72"])
    assert reply.item_ids == [item_72]
    assert r.source == "client"


def test_client_return_by_code_name_uses_latest_open_transmittal(session, project, ids, api_client):
    call(api_client, "POST", project, "transmittals", {"deliverable_ids": [ids["101"]]}, 201)
    r = recognize(session, project, "Dryer DS approved as noted.pdf")
    assert r.client_reply and r.client_reply.code == "2"  # 长的名称优先于 "approved"


def test_code_without_transmittal_is_not_a_reply(session, project, ids):
    r = recognize(session, project, "72 cut sheet approved.pdf")  # 没提交过，不算客户回复
    assert r.client_reply is None and r.source != "client"


def test_bare_number_is_not_a_review_code(session, project, ids, api_client):
    call(api_client, "POST", project, "transmittals", {"deliverable_ids": [ids["101"]]}, 201)
    r = recognize(session, project, "Dryer DS 1.pdf")
    assert r.client_reply is None
