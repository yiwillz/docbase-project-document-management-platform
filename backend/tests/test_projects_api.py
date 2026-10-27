from sqlalchemy import func, select

from app.models import Event, StatusMapping

USER = {"X-User": "alice"}


def _create(api_client, code="P-001", name="Demo Project", **extra):
    resp = api_client.post("/api/projects", json={"code": code, "name": name, **extra}, headers=USER)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _logs(api_client, project_id):
    resp = api_client.get("/api/change-logs", params={"project_id": project_id})
    assert resp.status_code == 200
    return resp.json()


def test_create_copies_default_status_mappings(api_client, session):
    project = _create(api_client)
    copied = session.scalar(
        select(func.count()).select_from(StatusMapping).where(
            StatusMapping.project_id == project["id"]
        )
    )
    defaults = session.scalar(
        select(func.count()).select_from(StatusMapping).where(StatusMapping.project_id.is_(None))
    )
    assert copied == defaults == 8

    page = _logs(api_client, project["id"])
    assert page["total"] == 1
    assert page["items"][0]["action"] == "create"
    assert page["items"][0]["actor_name"] == "alice"


def test_rename_records_before_after(api_client, session):
    """第 3 步完成标准：改项目名后变更记录里有 before/after。"""
    project = _create(api_client)
    resp = api_client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "New Name", "reason": "客户改名"},
        headers=USER,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"

    latest = _logs(api_client, project["id"])["items"][0]
    assert latest["action"] == "update"
    assert latest["changes"] == {"name": ["Demo Project", "New Name"]}
    assert latest["reason"] == "客户改名"

    events = session.scalars(
        select(Event.event_type).where(Event.project_id == project["id"])
    ).all()
    assert events == ["project.created", "project.updated"]


def test_unchanged_update_writes_no_log(api_client):
    project = _create(api_client)
    resp = api_client.patch(
        f"/api/projects/{project['id']}", json={"name": "Demo Project"}, headers=USER
    )
    assert resp.status_code == 200
    assert _logs(api_client, project["id"])["total"] == 1  # 只有新建那一条


def test_duplicate_code_conflict(api_client):
    _create(api_client, code="P-DUP")
    resp = api_client.post("/api/projects", json={"code": "P-DUP", "name": "x"}, headers=USER)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "project.code_taken"

    other = _create(api_client, code="P-OTHER")
    resp = api_client.patch(f"/api/projects/{other['id']}", json={"code": "P-DUP"}, headers=USER)
    assert resp.status_code == 409


def test_requests_need_login_when_dev_modes_off(api_client, monkeypatch):
    # 第 16 步起：不登录什么都做不了（测试库上的 X-User / 匿名只给自动测试用）
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_dev_anonymous", False)
    monkeypatch.setattr(get_settings(), "auth_dev_header", False)
    assert api_client.post("/api/projects", json={"code": "P-NOUSER", "name": "x"}).status_code == 401
    assert api_client.get("/api/projects").status_code == 401
    assert api_client.get("/api/health").status_code == 200  # 健康检查不用登录


def test_archive_makes_project_read_only(api_client):
    project = _create(api_client)
    pid = project["id"]

    resp = api_client.post(f"/api/projects/{pid}/archive", json={"reason": "完工"}, headers=USER)
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None

    codes = [p["code"] for p in api_client.get("/api/projects").json()]
    assert project["code"] not in codes
    codes = [p["code"] for p in api_client.get("/api/projects?include_archived=true").json()]
    assert project["code"] in codes

    resp = api_client.patch(f"/api/projects/{pid}", json={"name": "x"}, headers=USER)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "project.archived"

    resp = api_client.post(f"/api/projects/{pid}/unarchive", headers=USER)
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is None
    assert api_client.patch(f"/api/projects/{pid}", json={"name": "x"}, headers=USER).status_code == 200

    actions = [log["changes"].keys() for log in _logs(api_client, pid)["items"]]
    assert sum("archived_at" in keys for keys in actions) == 2  # 归档、取消归档各一条


def test_not_found_and_invalid(api_client):
    assert api_client.get("/api/projects/999999").status_code == 404
    resp = api_client.post(
        "/api/projects",
        json={"code": "P-BADDAYS", "name": "x", "review_days": -1},
        headers=USER,
    )
    assert resp.status_code == 422
    resp = api_client.post("/api/projects", json={"code": "   ", "name": "x"}, headers=USER)
    assert resp.status_code == 422


def test_change_log_filters(api_client):
    a = _create(api_client, code="P-A")
    b = _create(api_client, code="P-B")
    api_client.patch(f"/api/projects/{a['id']}", json={"name": "A2"}, headers={"X-User": "bob"})

    by_entity = api_client.get(
        "/api/change-logs", params={"entity_type": "projects", "entity_id": str(a["id"])}
    ).json()
    assert by_entity["total"] == 2

    by_actor = api_client.get("/api/change-logs", params={"actor": "bob"}).json()
    assert [i["entity_id"] for i in by_actor["items"]] == [str(a["id"])]

    paged = api_client.get(
        "/api/change-logs", params={"project_id": b["id"], "limit": 1, "offset": 0}
    ).json()
    assert paged["total"] == 1 and len(paged["items"]) == 1
