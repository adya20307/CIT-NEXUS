import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.main import app
from app.services.workflow_store import (
    MIGRATIONS_ROOT,
    WorkflowStoreUnavailable,
    _database_url,
)
from app.api.workflows import _session_changes


def _principal(subject, *roles):
    return AuthenticatedPrincipal(
        issuer="test-issuer",
        subject=subject,
        roles=tuple(roles),
        departments=(),
        username=subject,
    )


def test_workflow_admin_endpoints_require_verified_admin_role():
    client = TestClient(app)
    try:
        assert client.get("/api/workflows/admin/profiles").status_code == 401
        app.dependency_overrides[get_current_principal] = lambda: _principal("student", "STUDENT")
        assert client.get("/api/workflows/admin/profiles").status_code == 403
        assert client.post(
            "/api/workflows/timetable/proposals",
            json={"reason": "test"},
        ).status_code == 403
        assert client.post(
            "/api/workflows/faculty-requests",
            json={
                "request_type": "leave",
                "message": "Unavailable Monday",
                "preferred_days": ["MON"],
                "preferred_periods": [1],
            },
        ).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_profiles_reject_client_supplied_roles():
    client = TestClient(app)
    app.dependency_overrides[get_current_principal] = lambda: _principal("student", "STUDENT")
    try:
        response = client.put(
            "/api/workflows/profile",
            json={"display_name": "Student", "roles": ["ADMIN"]},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_rejection_reason_and_single_reschedule_slot_are_validated():
    client = TestClient(app)
    app.dependency_overrides[get_current_principal] = lambda: _principal("admin", "ADMIN")
    try:
        response = client.post(
            "/api/workflows/timetable/proposals/00000000-0000-0000-0000-000000000001/decision",
            json={"action": "reject"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()

    app.dependency_overrides[get_current_principal] = lambda: _principal("faculty", "FACULTY")
    try:
        response = client.post(
            "/api/workflows/faculty-requests",
            json={
                "request_type": "reschedule",
                "session_id": "SESSION-1",
                "message": "Please move my class",
                "preferred_days": ["MON", "TUE"],
                "preferred_periods": [1],
            },
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_faculty_leave_requires_availability_before_database_access(monkeypatch):
    from app.api import workflows

    client = TestClient(app)
    app.dependency_overrides[get_current_principal] = lambda: _principal("faculty", "FACULTY")
    monkeypatch.setattr(workflows, "get_working_snapshot", lambda _actor: ({}, "test-version"))

    def database_should_not_be_reached():
        raise AssertionError("Invalid request should be rejected before database access.")

    monkeypatch.setattr(workflows, "migrate", database_should_not_be_reached)
    try:
        response = client.post(
            "/api/workflows/faculty-requests",
            json={"request_type": "leave", "message": "I need leave"},
        )
        assert response.status_code == 422
        assert "requires preferred days and periods" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_database_configuration_is_required_and_must_be_postgresql(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    try:
        _database_url()
    except WorkflowStoreUnavailable as error:
        assert "not configured" in str(error)
    else:
        raise AssertionError("Missing PostgreSQL URL should be rejected.")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-supported.db")
    try:
        _database_url()
    except WorkflowStoreUnavailable as error:
        assert "PostgreSQL" in str(error)
    else:
        raise AssertionError("Non-PostgreSQL URL should be rejected.")


def test_audit_migration_is_append_only_and_contains_required_indexes():
    assert (MIGRATIONS_ROOT / "001_workflows.sql").is_file()
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "001_workflows.sql"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE workflow_audit_log" in migration
    assert "CREATE TRIGGER workflow_audit_no_update" in migration
    assert "BEFORE UPDATE OR DELETE ON workflow_audit_log" in migration
    assert "workflow_audit_time_idx" in migration
    assert "workflow_audit_actor_idx" in migration


def _setup_timetable_reader(monkeypatch, tmp_path, role, decision="approved"):
    from app.api import timetable
    from app.api import workflows

    data = timetable.load_dataset()
    schedule = json.loads(timetable.OFFICIAL_SCHEDULE_PATH.read_text(encoding="utf-8"))
    dataset_version_id = "reader-test-version"
    (tmp_path / f"{dataset_version_id}.json").write_text(
        json.dumps(data),
        encoding="utf-8",
    )
    publication = {
        "id": "reader-test-id",
        "dataset_id": data["dataset_id"],
        "dataset_version_id": dataset_version_id,
        "schedule": schedule,
        "decision": decision,
        "version_number": 7,
    }
    monkeypatch.setattr(timetable, "VERSIONS_ROOT", tmp_path)
    monkeypatch.setattr(timetable, "get_current_timetable", lambda: publication)
    monkeypatch.setattr(workflows, "VERSIONS_ROOT", tmp_path)
    monkeypatch.setattr(workflows, "get_current_timetable", lambda: publication)
    monkeypatch.setattr(
        timetable,
        "build_timetable_data",
        lambda _data, published_schedule, *_args: {
            "sessions": published_schedule["sessions"],
        },
    )
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        role.lower(),
        role,
    )
    return TestClient(app), schedule, tmp_path / f"{dataset_version_id}.json"


@pytest.mark.parametrize("role", ["STUDENT", "FACULTY"])
def test_approved_timetable_is_visible_to_students_and_faculty(
    monkeypatch,
    tmp_path,
    role,
):
    client, schedule, _version_path = _setup_timetable_reader(
        monkeypatch,
        tmp_path,
        role,
    )
    try:
        response = client.get("/api/timetable")
        assert response.status_code == 200
        assert response.json()["timetable_version"] == 7
        assert response.json()["data"]["sessions"] == schedule["sessions"]
        current_response = client.get("/api/workflows/timetable/current")
        assert current_response.status_code == 200
        assert current_response.json()["schedule"] == schedule
    finally:
        app.dependency_overrides.clear()


def test_admin_can_read_the_published_timetable(monkeypatch, tmp_path):
    client, _schedule, _version_path = _setup_timetable_reader(
        monkeypatch,
        tmp_path,
        "ADMIN",
    )
    try:
        response = client.get("/api/timetable")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_validated_baseline_is_checked_before_it_is_served(
    monkeypatch,
    tmp_path,
):
    from app.api import workflows

    client, _schedule, version_path = _setup_timetable_reader(
        monkeypatch,
        tmp_path,
        "STUDENT",
        decision="baseline",
    )
    validated = []
    monkeypatch.setattr(
        workflows,
        "_validate_official_candidate",
        lambda schedule, data_path: validated.append((schedule, data_path)),
    )
    try:
        response = client.get("/api/timetable")
        assert response.status_code == 200
        assert len(validated) == 1
        assert validated[0][1] == version_path
        current_response = client.get("/api/workflows/timetable/current")
        assert current_response.status_code == 200
        assert len(validated) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("proposal_status", ["pending", "rejected", "stale", "draft"])
def test_current_timetable_query_excludes_unapproved_proposals(
    monkeypatch,
    proposal_status,
):
    import app.services.workflow_store as workflow_store
    from app.api import timetable

    query_seen = []

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, query):
            query_seen.append(query)
            is_approved_publication = (
                "v.decision = 'approved'" in query
                and "p.status = 'approved'" in query
                and proposal_status == "approved"
            )
            return Result(
                {"decision": "approved"} if is_approved_publication else None
            )

    @contextmanager
    def fake_connection():
        yield Connection()

    monkeypatch.setattr(workflow_store, "migrate", lambda: None)
    monkeypatch.setattr(workflow_store, "connection", fake_connection)
    monkeypatch.setattr(
        timetable,
        "get_current_timetable",
        workflow_store.get_current_timetable,
    )
    app.dependency_overrides[get_current_principal] = lambda: _principal(
        "student",
        "STUDENT",
    )
    try:
        response = TestClient(app).get("/api/timetable")
        assert response.status_code == 503
        assert "No approved timetable is available" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
    assert "p.status = 'approved'" in query_seen[0]
    assert "v.decision = 'approved'" in query_seen[0]
    assert "(v.decision = 'baseline' AND v.source_proposal_id IS NULL)" in query_seen[0]


@pytest.mark.parametrize("role", ["STUDENT", "FACULTY"])
def test_timetable_without_published_version_fails_closed(monkeypatch, role):
    from app.api import timetable
    from app.api import workflows

    client = TestClient(app)
    monkeypatch.setattr(timetable, "get_current_timetable", lambda: None)
    monkeypatch.setattr(workflows, "get_current_timetable", lambda: None)
    monkeypatch.setattr(
        timetable,
        "_published_schedule_data",
        lambda _data: pytest.fail("The unverified export fallback must not be used."),
    )
    app.dependency_overrides[get_current_principal] = lambda: _principal(role.lower(), role)
    try:
        response = client.get("/api/timetable")
        assert response.status_code == 503
        assert "No approved timetable is available" in response.json()["detail"]
        current_response = client.get("/api/workflows/timetable/current")
        assert current_response.status_code == 503
        assert "No approved timetable is available" in current_response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_timetable_diff_reports_changed_added_removed_and_displaced_records():
    original = {
        "sessions": [
            {"session_id": "A", "day": "MON", "start_period": 1, "room_id": "R1"},
            {"session_id": "B", "day": "MON", "start_period": 2, "room_id": "R1"},
            {"session_id": "C", "day": "TUE", "start_period": 1, "room_id": "R2"},
        ],
    }
    proposed = {
        "sessions": [
            {"session_id": "A", "day": "MON", "start_period": 2, "room_id": "R1"},
            {"session_id": "B", "day": "MON", "start_period": 2, "room_id": "R2"},
            {"session_id": "D", "day": "TUE", "start_period": 1, "room_id": "R2"},
        ],
    }
    changes = _session_changes(
        original,
        proposed,
        {"sessions": [{"id": "A", "duration": 1}, {"id": "B", "duration": 1}, {"id": "C", "duration": 1}]},
    )
    assert {item["session_id"] for item in changes["changed"]} == {"A", "B"}
    assert [item["session_id"] for item in changes["added"]] == ["D"]
    assert [item["session_id"] for item in changes["removed"]] == ["C"]
    assert changes["displaced"] == [
        {"session_id": "B", "displaced_by": "A", "slot": {"day": "MON", "period": 2, "room_id": "R1"}}
    ]
