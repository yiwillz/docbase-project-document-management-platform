import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db import Base, get_engine
from app.models import (
    Deliverable,
    DeliverableStatus,
    DocumentType,
    Equipment,
    Milestone,
    Project,
    Role,
    StatusMapping,
)


def test_all_tables_created(migrated_db):
    tables = set(inspect(get_engine()).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


def test_models_match_migrations(migrated_db):
    """models.py 改了却没生成迁移时，这里会失败。"""
    with get_engine().connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []


def test_builtin_roles_seeded(session):
    roles = {r.code: r for r in session.scalars(select(Role))}
    assert set(roles) == {"admin", "member", "readonly"}
    assert all(r.builtin for r in roles.values())
    assert roles["readonly"].permissions == ["file.read", "history.read"]


def test_default_status_mappings_seeded(session):
    rows = session.scalars(select(StatusMapping)).all()
    assert rows and all(r.project_id is None for r in rows)
    approved = next(r for r in rows if r.pattern == "approved")
    assert approved.status is DeliverableStatus.APPROVED


def _project_with_type(session) -> tuple[Project, DocumentType]:
    project = Project(code="DEMO-001", name="Demo")
    session.add(project)
    session.flush()
    milestone = Milestone(project_id=project.id, code="M4", number="4")
    session.add(milestone)
    session.flush()
    doc_type = DocumentType(
        project_id=project.id, milestone_id=milestone.id, submittal_no="4.10", name="Manual"
    )
    session.add(doc_type)
    session.flush()
    return project, doc_type


def test_submittal_no_is_text(session):
    project, doc_type = _project_with_type(session)
    session.add(
        DocumentType(
            project_id=project.id,
            milestone_id=doc_type.milestone_id,
            submittal_no="4.1",
            name="Other",
        )
    )
    session.flush()
    numbers = set(
        session.scalars(
            select(DocumentType.submittal_no).where(DocumentType.project_id == project.id)
        )
    )
    assert numbers == {"4.1", "4.10"}


def test_deliverable_unique_with_nulls(session):
    """同一种类、设备和模块都为空的应交项只能有一个（NULLS NOT DISTINCT）。"""
    project, doc_type = _project_with_type(session)
    session.add(Deliverable(project_id=project.id, document_type_id=doc_type.id))
    session.flush()
    session.add(Deliverable(project_id=project.id, document_type_id=doc_type.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_equipment_tag_not_reused(session):
    """停用的设备编号仍然占用，不能复用。"""
    project, _ = _project_with_type(session)
    session.add(Equipment(project_id=project.id, tag="72", name="Mixer", active=False))
    session.flush()
    session.add(Equipment(project_id=project.id, tag="72", name="New"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_status_check_constraint(session):
    """数据库层面拒绝非法状态值。"""
    project, doc_type = _project_with_type(session)
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO deliverables (project_id, document_type_id, status) "
                "VALUES (:p, :d, 'bogus')"
            ),
            {"p": project.id, "d": doc_type.id},
        )
