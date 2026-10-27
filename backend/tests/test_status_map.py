import pytest

from app.models import DeliverableStatus, MatchType, Responsible, StatusMapping
from app.services.status_map import map_status, normalize

USER = {"X-User": "alice"}


def _rule(i, pattern, match_type, status, responsible=None, definite=True, priority=100):
    return StatusMapping(id=i, pattern=pattern, match_type=MatchType(match_type),
                         status=DeliverableStatus(status),
                         responsible=Responsible(responsible) if responsible else None,
                         is_definite=definite, priority=priority)


RULES = [
    _rule(1, r"^(\d+)\s*/\s*\1\s+approved$", "regex", "approved", "none", priority=5),
    _rule(2, "approved", "equals", "approved", "none", priority=10),
    _rule(3, "resubmit", "contains", "revise_resubmit", "ours", priority=20),
    _rule(4, "pending", "contains", "under_review", "client", priority=30),
    _rule(5, "missing", "contains", "not_submitted", "ours", priority=40),
    _rule(6, r"\d+\s*/\s*\d+\s+approved", "regex", "under_review", "client", definite=False,
          priority=50),
]


def test_normalize():
    assert normalize("  Pending  Client   Approval ") == "pending client approval"


@pytest.mark.parametrize("text, status, responsible, definite", [
    ("Approved", "approved", "none", True),
    ("  APPROVED ", "approved", "none", True),               # 不区分大小写、去空格
    ("69/69 approved", "approved", "none", True),            # 全部批准（正则反向引用）
    ("Pending client approval", "under_review", "client", True),
    ("Pending resubmittal", "revise_resubmit", "ours", False),  # 同时命中两条结论不同的规则
    ("Missing from contractor", "not_submitted", "ours", True),
    ("8/19 approved", "under_review", "client", False),      # 规则本身标为不明确
])
def test_map_status(text, status, responsible, definite):
    result = map_status(RULES, text)
    assert (result.status.value, result.responsible.value, result.definite) == (
        status, responsible, definite)


def test_no_match_and_empty():
    result = map_status(RULES, "Something else")
    assert result.status is None and result.definite is False and result.matched_rule_ids == []
    assert map_status(RULES, "   ") is None
    assert map_status(RULES, None) is None


def test_first_match_by_priority_decides():
    result = map_status(RULES, "Pending resubmittal")
    assert result.rule_id == 3 and result.matched_rule_ids == [3, 4]


# ---------------------------------------------------------------- 接口


@pytest.fixture
def project(api_client):
    return api_client.post("/api/projects", json={"code": "SM-1", "name": "x"},
                           headers=USER).json()


def _url(project, path=""):
    return f"/api/projects/{project['id']}/status-mappings{path}"


def test_new_project_has_default_rules(api_client, project):
    rules = api_client.get(_url(project)).json()
    assert len(rules) == 8
    result = api_client.post(_url(project, "/test"), json={"text": "Approved"}).json()
    assert result["status"] == "approved" and result["definite"] is True


def test_replace_rules_keeps_history(api_client, project, session):
    resp = api_client.put(_url(project), json={"rules": [
        {"pattern": "ok", "match_type": "equals", "status": "approved", "responsible": "none"},
    ], "reason": "测试"}, headers=USER)
    assert resp.status_code == 200, resp.text
    assert [r["pattern"] for r in api_client.get(_url(project)).json()] == ["ok"]
    # 旧规则只是停用
    stored = session.query(StatusMapping).filter_by(project_id=project["id"]).all()
    assert len(stored) == 9 and sum(r.active for r in stored) == 1
    assert api_client.post(_url(project, "/test"), json={"text": "Approved"}).json()["status"] is None


def test_replace_rules_validates_all_first(api_client, project):
    resp = api_client.put(_url(project), json={"rules": [
        {"pattern": "ok", "status": "approved"},
        {"pattern": "([", "match_type": "regex", "status": "approved"},
    ]}, headers=USER)
    assert resp.status_code == 422
    assert resp.json()["detail"]["params"]["row"] == 2
    assert len(api_client.get(_url(project)).json()) == 8  # 一条都没改
