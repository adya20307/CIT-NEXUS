from pathlib import Path

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
