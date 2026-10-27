"""第 16 步：登录与权限。这里的测试把"测试库上认 X-User / 匿名"都关掉，按真实的登录走。

虚构项目 AC-1：M1 / 1.1 Spec，一个文件；用户 root（系统管理员）、pm（项目管理员）、eng（普通）、viewer（只读）、
outsider（不是成员）。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuthSession, ChangeLog, User
from app.services import access, auth
from app.services import users as users_svc
from app.services.history import Actor

PW = "correct horse"
CSRF = {"X-Docbase": "1"}


@pytest.fixture
def strict(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_dev_anonymous", False)
    monkeypatch.setattr(get_settings(), "auth_dev_header", False)


@pytest.fixture
def make_client(session, strict):
    """每个用户一个 TestClient（各自的 cookie），和测试共用同一个数据库会话。"""
    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    clients = []

    def make(username: str | None = None, password: str = PW) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        if username:
            r = c.post("/api/auth/login", json={"username": username, "password": password})
            assert r.status_code == 200, r.text
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def people(session):
    boot = Actor(name="setup")
    out = {}
    for name, sysadmin in (("root", True), ("pm", False), ("eng", False), ("viewer", False), ("outsider", False)):
        out[name] = users_svc.create_user(session, {"username": name, "password": PW, "is_sysadmin": sysadmin}, boot)
    session.commit()
    return out


@pytest.fixture
def project(make_client, people, session):
    root = make_client("root")
    p = root.post("/api/projects", json={"code": "AC-1", "name": "Access demo"}, headers=CSRF).json()
    pid = p["id"]
    for name, role in (("pm", "admin"), ("eng", "member"), ("viewer", "readonly")):
        r = root.put(f"/api/projects/{pid}/members/{people[name].id}", json={"role": role}, headers=CSRF)
        assert r.status_code == 200, r.text
    m1 = root.post(f"/api/projects/{pid}/milestones", json={"name": "Design"}, headers=CSRF).json()
    root.post(f"/api/projects/{pid}/document-types/bulk", json={"milestone_id": m1["id"], "rows": [{"name": "Spec"}]},
              headers=CSRF)
    d = root.get(f"/api/projects/{pid}/deliverables").json()["items"][0]["id"]
    r = root.post(f"/api/projects/{pid}/files/preview", headers=CSRF, data={"deliverable_ids": str(d)},
                  files={"file": ("Spec.pdf", b"spec")})
    stored = root.post(f"/api/projects/{pid}/files/uploads/{r.json()['token']}/confirm", json={}, headers=CSRF).json()
    return {"id": pid, "deliverable": d, "milestone": m1["id"], "version": stored["version_id"]}


# ---------------------------------------------------------------- 规则表


def test_every_route_has_a_rule():
    from app.main import app

    missing = [(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops
               if access.rule_for(m.upper(), p) is None]
    assert missing == []


def test_rule_examples():
    P = "/api/projects/{project_id}"
    assert access.rule_for("GET", f"{P}/files").permission == "file.read"
    assert access.rule_for("GET", f"{P}/mdl/export").permission == "export"
    assert access.rule_for("POST", f"{P}/files/preview").permission == "file.write"
    assert access.rule_for("PATCH", f"{P}/milestones/{{milestone_id}}").permission == "project.config"
    assert access.rule_for("POST", f"{P}/milestones/{{milestone_id}}/close").permission == "milestone.close"
    assert access.rule_for("GET", f"{P}/imports").permission == "file.import"
    assert access.rule_for("POST", f"{P}/naming/preview").permission == "file.read"  # 只算不存，只读用户也能看示例
    assert access.rule_for("PATCH", P).permission == "project.config"  # 保存命名模板走修改项目
    assert access.rule_for("DELETE", "/api/projects/{project_id}/nope") is None


# ---------------------------------------------------------------- 登录


def test_login_logout_and_me(make_client, people):
    c = make_client()
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"username": "eng", "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "eng", "password": PW})
    assert r.status_code == 200 and r.json()["username"] == "eng"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert c.get("/api/auth/me").json()["is_sysadmin"] is False
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


def test_password_hashing():
    h = auth.hash_password("secret pass")
    assert h.startswith("scrypt$") and "secret" not in h
    assert auth.verify_password("secret pass", h) and not auth.verify_password("other", h)
    assert auth.hash_password("secret pass") != h  # 每次加不同的盐


def test_lockout_after_five_failures(make_client, people, session):
    c = make_client()
    for _ in range(5):
        r = c.post("/api/auth/login", json={"username": "eng", "password": "wrong"})
        assert r.json()["detail"]["code"] == "auth.bad_credentials"
    r = c.post("/api/auth/login", json={"username": "eng", "password": PW})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "auth.locked"
    root = make_client("root")
    assert root.post(f"/api/users/{people['eng'].id}/password", json={"password": "new password 1"},
                     headers=CSRF).status_code == 200  # 重置密码解除锁定
    assert c.post("/api/auth/login", json={"username": "eng", "password": "new password 1"}).status_code == 200


def test_session_expires_and_slides(make_client, people, session):
    c = make_client("eng")
    s = session.scalar(select(AuthSession).where(AuthSession.user_id == people["eng"].id))
    s.last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()
    c.get("/api/auth/me")
    session.refresh(s)
    assert s.expires_at > datetime.now(UTC) + timedelta(hours=11)  # 用到就往后顺延
    s.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert c.get("/api/auth/me").status_code == 401


def test_writes_need_csrf_header(make_client, people, project):
    c = make_client("eng")
    r = c.patch(f"/api/projects/{project['id']}/deliverables/{project['deliverable']}", json={"remarks": "x"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "auth.csrf"
    r = c.patch(f"/api/projects/{project['id']}/deliverables/{project['deliverable']}", json={"remarks": "x"},
                headers=CSRF)
    assert r.status_code == 200


def test_change_password_revokes_other_sessions(make_client, people):
    a, b = make_client("eng"), make_client("eng")
    r = a.post("/api/auth/password", json={"old_password": "wrong", "new_password": "another pass"}, headers=CSRF)
    assert r.json()["detail"]["code"] == "auth.bad_password"
    r = a.post("/api/auth/password", json={"old_password": PW, "new_password": "short"}, headers=CSRF)
    assert r.json()["detail"]["code"] == "auth.password_too_short"
    assert a.post("/api/auth/password", json={"old_password": PW, "new_password": "another pass"},
                  headers=CSRF).status_code == 200
    assert a.get("/api/auth/me").status_code == 200 and b.get("/api/auth/me").status_code == 401


def test_deactivated_user_is_logged_out(make_client, people):
    c = make_client("eng")
    root = make_client("root")
    assert root.patch(f"/api/users/{people['eng'].id}", json={"active": False}, headers=CSRF).status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    r = make_client().post("/api/auth/login", json={"username": "eng", "password": PW})
    assert r.status_code == 401
    resp = root.patch(f"/api/users/{people['root'].id}", json={"active": False}, headers=CSRF)
    assert resp.json()["detail"]["code"] == "user.self_lockout"


def test_dev_header_ignored_outside_test_database(make_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_dev_header", True)
    c = make_client()
    assert c.get("/api/projects", headers={"X-User": "alice"}).status_code == 200  # 测试库：认
    monkeypatch.setattr(auth, "dev_mode_allowed", lambda: False)
    assert c.get("/api/projects", headers={"X-User": "alice"}).status_code == 401  # 正式库：不认


# ---------------------------------------------------------------- 权限


def test_project_visibility(make_client, people, project):
    out = make_client("outsider")
    assert out.get("/api/projects").json() == []
    r = out.get(f"/api/projects/{project['id']}/deliverables")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "access.not_member"
    assert [p["code"] for p in make_client("viewer").get("/api/projects").json()] == ["AC-1"]
    assert len(make_client("root").get("/api/projects").json()) >= 1


def test_role_matrix(make_client, people, project):
    pid, d, ms, vid = project["id"], project["deliverable"], project["milestone"], project["version"]
    viewer, eng, pm = make_client("viewer"), make_client("eng"), make_client("pm")

    def status(c, method, path, **kw):
        return c.request(method, f"/api/projects/{pid}/{path}", headers=CSRF, **kw).status_code

    # 只读：看、下载、预览；不能写、不能导出
    assert status(viewer, "GET", "deliverables") == 200
    assert status(viewer, "GET", f"versions/{vid}/download") == 200
    assert status(viewer, "GET", f"versions/{vid}/preview") == 200
    assert status(viewer, "GET", "mdl/export") == 403
    assert status(viewer, "PATCH", f"deliverables/{d}", json={"remarks": "x"}) == 403
    assert status(viewer, "POST", "files/preview", data={"deliverable_ids": str(d)},
                  files={"file": ("a.pdf", b"a")}) == 403
    # 普通：存文件、改状态、导出；不能改结构、不能关里程碑、不能存量导入、不能管成员
    assert status(eng, "PATCH", f"deliverables/{d}", json={"remarks": "x"}) == 200
    assert status(eng, "GET", "mdl/export") == 200
    assert status(eng, "POST", "files/preview", data={"deliverable_ids": str(d)}, files={"file": ("b.pdf", b"b")}) == 200
    assert status(eng, "PATCH", f"milestones/{ms}", json={"name": "X"}) == 403
    assert status(eng, "POST", f"milestones/{ms}/close", json={"force": True, "reason": "x"}) == 403
    assert status(eng, "GET", "imports") == 403
    assert status(eng, "PUT", f"members/{people['outsider'].id}", json={"role": "member"}) == 403
    # 项目管理员：都可以
    assert status(pm, "PATCH", f"milestones/{ms}", json={"name": "Design 2"}) == 200
    assert status(pm, "POST", f"milestones/{ms}/close", json={"force": True, "reason": "x"}) == 200
    assert status(pm, "GET", "imports") == 200
    assert status(pm, "PUT", f"members/{people['outsider'].id}", json={"role": "readonly"}) == 200
    # 系统管理员才能做的：建项目、管用户
    assert pm.post("/api/projects", json={"code": "AC-2", "name": "x"}, headers=CSRF).status_code == 403
    assert pm.get("/api/users").status_code == 403
    assert pm.get("/api/change-logs").status_code == 403  # 不按项目查只有系统管理员
    assert pm.get(f"/api/change-logs?project_id={pid}").status_code == 200


def test_members_api_and_actor_in_change_log(make_client, people, project, session):
    pid = project["id"]
    pm = make_client("pm")
    rows = pm.get(f"/api/projects/{pid}/members").json()
    assert {(r["username"], r["role"]) for r in rows} == {("pm", "admin"), ("eng", "member"), ("viewer", "readonly")}
    assert pm.put(f"/api/projects/{pid}/members/{people['eng'].id}", json={"role": "readonly"},
                  headers=CSRF).status_code == 200
    assert pm.delete(f"/api/projects/{pid}/members/{people['viewer'].id}", headers=CSRF).status_code == 200
    assert make_client("viewer").get(f"/api/projects/{pid}/deliverables").status_code == 403
    # 加成员时选人：只列不是成员的启用用户；不能管成员的人看不到
    names = {u["username"] for u in pm.get(f"/api/projects/{pid}/members/candidates").json()}
    assert "viewer" in names and not names & {"pm", "eng"}
    assert make_client("eng").get(f"/api/projects/{pid}/members/candidates").status_code == 403
    r = pm.delete(f"/api/projects/{pid}/members/{people['pm'].id}", headers=CSRF)
    assert r.json()["detail"]["code"] == "member.self_remove"
    log = session.scalars(select(ChangeLog).where(ChangeLog.entity_type == "project_members")
                          .order_by(ChangeLog.id.desc())).first()
    assert log.actor_name == "pm" and log.actor_id == people["pm"].id  # 变更记录里是真实登录的账号


def test_user_admin(make_client, people, session):
    root = make_client("root")
    r = root.post("/api/users", json={"username": "new.user", "password": "long enough", "display_name": "New"},
                  headers=CSRF)
    assert r.status_code == 201 and r.json()["display_name"] == "New"
    assert root.post("/api/users", json={"username": "new.user", "password": "long enough"},
                     headers=CSRF).json()["detail"]["code"] == "user.username_taken"
    assert root.post("/api/users", json={"username": "x y", "password": "long enough"},
                     headers=CSRF).json()["detail"]["code"] == "user.bad_username"
    assert root.post("/api/users", json={"username": "short", "password": "1234"},
                     headers=CSRF).json()["detail"]["code"] == "auth.password_too_short"
    u = session.scalar(select(User).where(User.username == "new.user"))
    assert u.password_hash.startswith("scrypt$")
    logs = session.scalars(select(ChangeLog).where(ChangeLog.entity_type == "users",
                                                   ChangeLog.entity_id == str(u.id))).all()
    assert logs and all("password_hash" not in log.changes for log in logs)  # 不记密码
    assert {x["code"] for x in root.get("/api/roles").json()} >= {"admin", "member", "readonly"}


def test_system_status_hides_backup_details_from_normal_users(make_client, people):
    from app.services import system

    system._cache.update(at=0.0, value=None)
    r = make_client("eng").get("/api/system/status").json()
    assert r["backup"] is None and r["storage"]["error"] is None
    assert "backup" in make_client("root").get("/api/system/status").json()


def test_change_log_export_needs_export_permission(make_client, project):
    url = f"/api/projects/{project['id']}/change-logs/export.xlsx"
    assert make_client("viewer").get(url).status_code == 403  # 只读用户不能导出
    assert make_client("viewer").get("/api/change-logs", params={"project_id": project["id"]}).status_code == 200
    assert make_client("eng").get(url).status_code == 200


def test_only_sysadmin_deletes_projects(make_client, project):
    pm = make_client("pm")  # 项目管理员也不行
    assert pm.get(f"/api/projects/{project['id']}/delete-check").status_code == 403
    r = pm.post(f"/api/projects/{project['id']}/delete", json={"confirm_code": "AC-1"}, headers=CSRF)
    assert r.status_code == 403
    assert make_client("root").get(f"/api/projects/{project['id']}/delete-check").status_code == 200
