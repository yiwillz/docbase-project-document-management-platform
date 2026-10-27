from datetime import date

from sqlalchemy import func, select

from app.models import (
    ChangeAction,
    ChangeLog,
    DeliverableStatus,
    Event,
    MatchType,
    Milestone,
    Project,
    StatusMapping,
)
from app.services.history import Actor, diff, emit, record, snapshot

ACTOR = Actor(name="tester")


def _project(session) -> Project:
    project = Project(code="H-001", name="History Demo")
    session.add(project)
    session.flush()
    return project


def test_snapshot_plain_values(session):
    project = _project(session)
    milestone = Milestone(project_id=project.id, code="M1", number="1", due_date=date(2026, 3, 1))
    mapping = StatusMapping(
        pattern="ok", match_type=MatchType.EQUALS, status=DeliverableStatus.APPROVED
    )
    session.add_all([milestone, mapping])
    session.flush()

    snap = snapshot(milestone)
    assert snap["code"] == "M1"
    assert snap["due_date"] == "2026-03-01"  # 日期转成文字
    assert "created_at" not in snap and "updated_at" not in snap
    assert snapshot(mapping)["status"] == "approved"  # 枚举转成代码


def test_diff_only_changed_fields():
    before = {"name": "A", "code": "X", "note": None}
    after = {"name": "B", "code": "X", "extra": 1}
    assert diff(before, after) == {"name": ["A", "B"], "extra": [None, 1]}
    assert diff(before, dict(before)) == {}


def test_record_skips_when_unchanged(session):
    project = _project(session)
    snap = snapshot(project)
    assert record(session, project, ChangeAction.UPDATE, snap, snap, ACTOR) is None
    session.flush()
    count = session.scalar(select(func.count()).select_from(ChangeLog))
    assert count == 0


def test_record_writes_before_after(session):
    project = _project(session)
    before = snapshot(project)
    project.name = "Renamed"
    log = record(session, project, ChangeAction.UPDATE, before, snapshot(project), ACTOR,
                 reason="typo", project_id=project.id)
    session.flush()
    assert log.entity_type == "projects"
    assert log.entity_id == str(project.id)
    assert log.changes == {"name": ["History Demo", "Renamed"]}
    assert (log.actor_name, log.reason, log.project_id) == ("tester", "typo", project.id)


def test_emit_event(session):
    project = _project(session)
    event = emit(session, "project.updated", project, {"fields": ["name"]}, project_id=project.id)
    session.flush()
    stored = session.get(Event, event.id)
    assert stored.entity_type == "projects"
    assert stored.payload == {"fields": ["name"]}


def test_change_logs_have_readable_labels(api_client):
    """变更记录带对象名称：文件种类写编号和名称，应交项写种类 / 设备，汇总记录为空。"""
    headers = {"X-User": "alice"}
    project = api_client.post("/api/projects", json={"code": "LBL-1", "name": "labels"}, headers=headers).json()
    base = f"/api/projects/{project['id']}"
    ms = api_client.post(f"{base}/milestones", json={"name": "Design"}, headers=headers).json()
    [doc] = api_client.post(f"{base}/document-types/bulk", headers=headers,
                            json={"milestone_id": ms["id"], "rows": [{"name": "Datasheet"}]}).json()
    item = api_client.post(f"{base}/equipment", json={"name": "Beta part", "quantity": 3000},
                           headers=headers).json()
    api_client.post(f"{base}/document-types/bulk-equipment", headers=headers,
                    json={"document_type_ids": [doc["id"]], "equipment_ids": [item["id"]], "mode": "add"})
    deliverable = api_client.get(f"{base}/deliverables").json()["items"][0]
    api_client.patch(f"{base}/deliverables/{deliverable['id']}", json={"status": "submitted"}, headers=headers)

    logs = api_client.get("/api/change-logs", params={"project_id": project["id"], "limit": 100}).json()["items"]
    by_type = {}
    for log in logs:
        by_type.setdefault(log["entity_type"], log["label"])
    assert by_type["projects"] == "LBL-1"
    assert by_type["milestones"] == "M1 Design"
    assert by_type["document_types"] == "1.1 Datasheet"
    assert by_type["equipment"] == "Beta part"
    assert by_type["deliverables"] == "1.1 Datasheet / Beta part"
    summary = next(log for log in logs if not log["entity_id"].isdigit())
    assert summary["label"] is None
