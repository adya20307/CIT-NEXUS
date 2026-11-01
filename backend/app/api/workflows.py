import copy
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import AuthenticatedPrincipal, require_roles
from app.booking.booking_engine import generate_booking_schedule
from app.booking.natural_language import detect_language
from app.rescheduler.crisis_engine import (
    apply_disruption,
    generate_crisis_schedule,
)
from app.scheduler.exam_scheduler import solve_exam_schedule
from app.scheduler.timetable import solve_timetable
from app.services.dataset_loader import ACTIVE_DATASET_PATH, load_dataset
from app.services.gemini_service import (
    GeminiAuthenticationError,
    GeminiConfigurationError,
    GeminiModelError,
    GeminiQuotaError,
    GeminiRequestError,
    GeminiTimeoutError,
    GeminiUnavailableError,
    generate_assistant_reply,
)
from app.services.starter_pack_manager import (
    VERSIONS_ROOT,
    get_active_version_id,
    get_working_snapshot,
)
from app.services.workflow_store import (
    WorkflowStoreUnavailable,
    audit,
    connection,
    create_baseline,
    get_current_timetable,
    migrate,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["Persistent workflows"])
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule.json"
EXPORTS_ROOT = PROJECT_ROOT / "exports"


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(default="", max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class AdminProfileUpdate(ProfileUpdate):
    department: str | None = Field(default=None, max_length=120)
    faculty_id: str | None = Field(default=None, max_length=80)
    student_group_id: str | None = Field(default=None, max_length=80)


class TimetableProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(default="Administrator-generated timetable proposal", max_length=500)


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def rejection_requires_reason(self):
        if self.action == "reject" and not (self.reason or "").strip():
            raise ValueError("A rejection reason is required.")
        return self


class FacultyScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_type: Literal["leave", "reschedule", "availability"]
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=80)
    preferred_days: list[str] = Field(default_factory=list, max_length=5)
    preferred_periods: list[int] = Field(default_factory=list, max_length=9)
    idempotency_key: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=100)

    @model_validator(mode="after")
    def reschedule_requires_slot(self):
        if self.request_type == "reschedule":
            if not self.session_id:
                raise ValueError("Select the session to reschedule.")
            if not self.preferred_days or not self.preferred_periods:
                raise ValueError("Rescheduling requires at least one preferred day and period.")
            if len(self.preferred_days) != 1 or len(self.preferred_periods) != 1:
                raise ValueError("Choose exactly one preferred day and period for a single-session reschedule.")
        if self.request_type != "reschedule" and self.session_id:
            raise ValueError("A session_id is only valid for a reschedule request.")
        return self


class FacultyRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)
    alternative_index: int = Field(default=0, ge=0, le=9)

    @model_validator(mode="after")
    def rejection_requires_reason(self):
        if self.action == "reject" and not (self.reason or "").strip():
            raise ValueError("A rejection reason is required.")
        return self


def _actor(principal):
    return principal.subject


def _db_error(error):
    logger.error("Persistent workflow storage unavailable: %s", type(error).__name__)
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "PostgreSQL workflow storage is unavailable. Configure DATABASE_URL and apply migrations.",
    ) from error


def _profile(conn, subject_id):
    return conn.execute(
        "SELECT * FROM workflow_profiles WHERE subject_id = %s",
        (subject_id,),
    ).fetchone()


def _upsert_identity(conn, principal):
    return conn.execute(
        """
        INSERT INTO workflow_profiles (subject_id, username, roles, department)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (subject_id) DO UPDATE SET
            username = EXCLUDED.username,
            roles = EXCLUDED.roles,
            department = COALESCE(workflow_profiles.department, EXCLUDED.department),
            updated_at = now()
        RETURNING *
        """,
        (
            principal.subject,
            principal.username,
            list(principal.roles),
            principal.departments[0] if principal.departments else None,
        ),
    ).fetchone()


def _schedule_snapshot():
    try:
        return json.loads(OFFICIAL_SCHEDULE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A valid official schedule is required before workflow proposals can be created.",
        ) from error


def _session_changes(original, proposed, data):
    old = {item["session_id"]: item for item in original.get("sessions", [])}
    new = {item["session_id"]: item for item in proposed.get("sessions", [])}
    changed = [
        {"session_id": key, "before": old[key], "after": new[key]}
        for key in sorted(old.keys() & new.keys())
        if old[key] != new[key]
    ]
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    removed = [old[key] for key in sorted(old.keys() - new.keys())]
    durations = {item["id"]: item.get("duration", 1) for item in data["sessions"]}
    occupied_old = {}
    for session_id, slot in old.items():
        for period in range(slot["start_period"], slot["start_period"] + durations.get(session_id, 1)):
            occupied_old.setdefault((slot["day"], period, slot["room_id"]), set()).add(session_id)
    displaced = []
    for change in changed:
        session_id = change["session_id"]
        slot = change["after"]
        for period in range(slot["start_period"], slot["start_period"] + durations.get(session_id, 1)):
            for other_id in occupied_old.get((slot["day"], period, slot["room_id"]), set()):
                if other_id != session_id:
                    displaced.append({
                        "session_id": other_id,
                        "displaced_by": session_id,
                        "slot": {"day": slot["day"], "period": period, "room_id": slot["room_id"]},
                    })
    return {
        "changed": changed,
        "added": added,
        "removed": removed,
        "displaced": displaced,
    }


def _official_candidate(data, sessions, baseline):
    booking_decisions = generate_booking_schedule(
        data,
        {"sessions": sessions, "dataset_id": data["dataset_id"]},
    )
    exams = solve_exam_schedule(data)
    if exams["assignments"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Exam allocation could not be generated ({exams['solver_status']}).",
        )
    return {
        "team": "CIT Nexus",
        "dataset_id": data["dataset_id"],
        "sessions": sessions,
        "fest_week": {
            "bookings": booking_decisions,
            "session_overrides": baseline.get("fest_week", {}).get("session_overrides", []),
        },
        "exams": exams["assignments"],
    }


def _validate_official_candidate(schedule, data_path=ACTIVE_DATASET_PATH):
    from app.api.admin_dataset import _run_validator

    temp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        dir=EXPORTS_ROOT,
        delete=False,
    )
    temp_path = Path(temp.name)
    try:
        with temp:
            json.dump(schedule, temp)
        report = _run_validator(temp_path, data_path)
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The official schedule validator could not validate the proposal.",
        ) from error
    finally:
        temp_path.unlink(missing_ok=True)
    if not report.get("valid"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "Proposal failed official validation.", "report": report},
        )
    return report


def _validate_initial_baseline(schedule, data_path):
    try:
        _validate_official_candidate(schedule, data_path=data_path)
    except HTTPException as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The initial baseline timetable failed official validation.",
        ) from error


def _ensure_current(conn, actor, data=None, dataset_version_id=None):
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (743281906,),
    )
    current = conn.execute(
        """
        SELECT v.* FROM timetable_current c
        JOIN timetable_versions v ON v.id = c.version_id
        WHERE c.singleton = TRUE FOR UPDATE OF c
        """
    ).fetchone()
    if current:
        return current
    data = data or load_dataset()
    baseline = _schedule_snapshot()
    if baseline.get("dataset_id") != data.get("dataset_id"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The published schedule and active dataset differ. Generate and validate the active dataset schedule first.",
        )
    _validate_official_candidate(baseline)
    return create_baseline(
        conn,
        baseline,
        data["dataset_id"],
        dataset_version_id if dataset_version_id is not None else get_active_version_id(),
        actor,
    )


def _audit_response(row):
    return {
        **row,
        "occurred_at": row["occurred_at"].isoformat(),
    }


@router.get("/profile")
def get_my_profile(
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY", "STUDENT")),
):
    try:
        migrate()
        with connection() as conn:
            previous = _profile(conn, principal.subject)
            profile = _upsert_identity(conn, principal)
            if previous is None:
                audit(
                    conn,
                    principal.subject,
                    principal.roles,
                    "profile.created",
                    "profile",
                    principal.subject,
                    after={"username": principal.username, "roles": list(principal.roles)},
                )
            return {"profile": profile}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.put("/profile")
def update_my_profile(
    update: ProfileUpdate,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY", "STUDENT")),
):
    try:
        migrate()
        with connection() as conn:
            previous = _profile(conn, principal.subject)
            _upsert_identity(conn, principal)
            updated = conn.execute(
                """
                UPDATE workflow_profiles
                SET display_name = %s, phone = %s, updated_at = now()
                WHERE subject_id = %s RETURNING *
                """,
                (update.display_name, update.phone, principal.subject),
            ).fetchone()
            audit(conn, principal.subject, principal.roles, "profile.updated", "profile", principal.subject,
                  before=previous, after=updated)
            return {"profile": updated}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/admin/profiles")
def list_profiles(
    search: str = Query(default="", max_length=100),
    role: Literal["ADMIN", "FACULTY", "STUDENT"] | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        migrate()
        pattern = f"%{search.strip()}%"
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT subject_id, username, email, roles, display_name, department,
                       faculty_id, student_group_id, phone, created_at, updated_at
                FROM workflow_profiles
                WHERE (%s OR username ILIKE %s OR email ILIKE %s OR display_name ILIKE %s)
                  AND (%s::text IS NULL OR %s = ANY(roles))
                ORDER BY lower(username), subject_id
                OFFSET %s LIMIT %s
                """,
                (not search.strip(), pattern, pattern, pattern, role, role, offset, limit),
            ).fetchall()
            total = conn.execute(
                """
                SELECT count(*) AS total FROM workflow_profiles
                WHERE (%s OR username ILIKE %s OR email ILIKE %s OR display_name ILIKE %s)
                  AND (%s::text IS NULL OR %s = ANY(roles))
                """,
                (not search.strip(), pattern, pattern, pattern, role, role),
            ).fetchone()["total"]
            return {"profiles": rows, "total": total, "offset": offset, "limit": limit}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.put("/admin/profiles/{subject_id}")
def update_admin_profile(
    subject_id: str,
    update: AdminProfileUpdate,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    data, _ = get_working_snapshot(principal.subject)
    faculty_ids = {item["id"] for item in data["faculty"]}
    group_ids = {item["id"] for item in data["student_groups"]}
    if update.faculty_id and update.faculty_id not in faculty_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown faculty ID.")
    if update.student_group_id and update.student_group_id not in group_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown student group ID.")
    try:
        migrate()
        with connection() as conn:
            previous = _profile(conn, subject_id)
            if previous is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found. The user must sign in before profile assignment.")
            if update.faculty_id and "FACULTY" not in previous["roles"]:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a Keycloak FACULTY profile can be mapped to a faculty record.")
            if update.student_group_id and "STUDENT" not in previous["roles"]:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a Keycloak STUDENT profile can be mapped to a student group.")
            updated = conn.execute(
                """
                UPDATE workflow_profiles SET
                    display_name = %s, phone = %s, department = %s,
                    faculty_id = %s, student_group_id = %s, updated_at = now()
                WHERE subject_id = %s RETURNING *
                """,
                (
                    update.display_name, update.phone, update.department,
                    update.faculty_id, update.student_group_id, subject_id,
                ),
            ).fetchone()
            audit(conn, principal.subject, principal.roles, "profile.mapping_updated", "profile", subject_id,
                  before=previous, after=updated)
            return {"profile": updated}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/timetable/current")
def current_timetable(
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY", "STUDENT")),
):
    del principal
    try:
        version = get_current_timetable()
        if version is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "No approved timetable is available. An administrator must publish a validated baseline or approve a timetable proposal.",
            )
        if version["decision"] == "baseline":
            dataset_version_id = version.get("dataset_version_id")
            version_path = (
                VERSIONS_ROOT / f"{dataset_version_id}.json"
                if dataset_version_id
                else None
            )
            if not version_path or not version_path.is_file():
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "The dataset version associated with the initial baseline is unavailable.",
                )
            try:
                with version_path.open("r", encoding="utf-8") as file:
                    baseline_data = json.load(file)
            except (OSError, json.JSONDecodeError) as error:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "The dataset version associated with the initial baseline is invalid or unavailable.",
                ) from error
            if (
                not isinstance(baseline_data, dict)
                or baseline_data.get("dataset_id") != version["dataset_id"]
            ):
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Initial baseline timetable and dataset version do not match.",
                )
            _validate_initial_baseline(version["schedule"], version_path)
        return {
            "version": version["version_number"],
            "version_id": str(version["id"]),
            "dataset_version_id": version["dataset_version_id"],
            "schedule": version["schedule"],
        }
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/timetable/versions")
def timetable_versions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    try:
        migrate()
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, version_number, dataset_id, dataset_version_id,
                       created_by, created_at, source_proposal_id, decision
                FROM timetable_versions ORDER BY version_number DESC OFFSET %s LIMIT %s
                """,
                (offset, limit),
            ).fetchall()
            return {"versions": rows, "offset": offset, "limit": limit}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/timetable/proposals")
def list_timetable_proposals(
    status_filter: Literal["pending", "approved", "rejected", "stale"] | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        migrate()
        conflict_message = None
        updated = None
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, proposal_number, base_version_id, dataset_id,
                       original_schedule, proposed_schedule, changes, created_by,
                       reason, status, rejection_reason, decision_by, decision_at, created_at
                FROM timetable_proposals
                WHERE (%s::text IS NULL OR status = %s)
                ORDER BY created_at DESC OFFSET %s LIMIT %s
                """,
                (status_filter, status_filter, offset, limit),
            ).fetchall()
            total = conn.execute(
                "SELECT count(*) AS total FROM timetable_proposals WHERE (%s::text IS NULL OR status = %s)",
                (status_filter, status_filter),
            ).fetchone()["total"]
            return {"proposals": rows, "total": total, "offset": offset, "limit": limit}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.post("/timetable/proposals", status_code=status.HTTP_201_CREATED)
def create_timetable_proposal(
    request: TimetableProposalRequest,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        data, dataset_version_id = get_working_snapshot(principal.subject)
        migrate()
        with connection() as conn:
            current = _ensure_current(conn, principal.subject, data, dataset_version_id)
            original = current["schedule"]
            base_version_id = current["id"]
        solved = solve_timetable(data)
        if solved["assignments"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No feasible schedule ({solved['solver_status']}).")
        proposed = _official_candidate(data, solved["assignments"], original)
        validation = _validate_official_candidate(proposed)
        changes = _session_changes(original, proposed, data)
        proposal_id = uuid.uuid4()
        if get_active_version_id() != dataset_version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed while the proposal was being generated.")
        with connection() as conn:
            locked = conn.execute(
                "SELECT version_id FROM timetable_current WHERE singleton = TRUE FOR UPDATE"
            ).fetchone()
            if locked is None or str(locked["version_id"]) != str(base_version_id):
                raise HTTPException(status.HTTP_409_CONFLICT, "The approved timetable changed while the proposal was generated.")
            row = conn.execute(
                """
                INSERT INTO timetable_proposals (
                    id, base_version_id, dataset_id, dataset_version_id,
                    original_schedule, proposed_schedule, changes, created_by, reason, status
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, 'pending')
                RETURNING id, proposal_number, base_version_id, dataset_id, changes, reason, status, created_at
                """,
                (
                    proposal_id, base_version_id, data["dataset_id"], dataset_version_id,
                    json.dumps(original), json.dumps(proposed), json.dumps(changes),
                    principal.subject, request.reason,
                ),
            ).fetchone()
            audit(conn, principal.subject, principal.roles, "timetable.proposal_created",
                  "timetable_proposal", proposal_id, after={"status": "pending", "changes": changes})
            return {"proposal": row, "validation": validation}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.post("/timetable/proposals/{proposal_id}/decision")
def decide_timetable_proposal(
    proposal_id: uuid.UUID,
    decision: ProposalDecision,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        data, dataset_version_id = get_working_snapshot(principal.subject)
        migrate()
        with connection() as conn:
            current = conn.execute(
                "SELECT version_id FROM timetable_current WHERE singleton = TRUE FOR UPDATE"
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM timetable_proposals WHERE id = %s FOR UPDATE",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Timetable proposal not found.")
            if row["status"] != "pending":
                raise HTTPException(status.HTTP_409_CONFLICT, "Proposal has already been decided.")
            if current is None or str(current["version_id"]) != str(row["base_version_id"]):
                conn.execute(
                    "UPDATE timetable_proposals SET status = 'stale', updated_at = now() WHERE id = %s",
                    (proposal_id,),
                )
                audit(conn, principal.subject, principal.roles, "timetable.proposal_stale",
                      "timetable_proposal", proposal_id, before={"status": "pending"}, after={"status": "stale"})
                conflict_message = "Proposal is stale; generate a new proposal against the current approved version."
            elif decision.action == "reject":
                updated = conn.execute(
                    """
                    UPDATE timetable_proposals SET status = 'rejected',
                        rejection_reason = %s, decision_by = %s, decision_at = now(), updated_at = now()
                    WHERE id = %s RETURNING id, proposal_number, status, rejection_reason, decision_at
                    """,
                    (decision.reason.strip(), principal.subject, proposal_id),
                ).fetchone()
                audit(conn, principal.subject, principal.roles, "timetable.proposal_rejected",
                      "timetable_proposal", proposal_id, before={"status": "pending"},
                      after=updated, details={"reason": decision.reason.strip()})
                conn.execute(
                    """
                    INSERT INTO workflow_notifications (id, recipient_subject, proposal_id, title, message)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), row["created_by"], proposal_id,
                        "Timetable proposal rejected",
                        decision.reason.strip(),
                    ),
                )
            else:
                active_data_version = get_active_version_id()
                if row["dataset_version_id"] and active_data_version != row["dataset_version_id"]:
                    conn.execute(
                        "UPDATE timetable_proposals SET status = 'stale', updated_at = now() WHERE id = %s",
                        (proposal_id,),
                    )
                    audit(conn, principal.subject, principal.roles, "timetable.proposal_stale",
                          "timetable_proposal", proposal_id, before={"status": "pending"}, after={"status": "stale"})
                    conflict_message = "Dataset changed since proposal generation."
                else:
                    next_version = conn.execute(
                        "SELECT COALESCE(max(version_number), 0) + 1 AS next FROM timetable_versions"
                    ).fetchone()["next"]
                    version_id = uuid.uuid4()
                    conn.execute(
                    """
                    INSERT INTO timetable_versions (
                        id, version_number, dataset_id, dataset_version_id,
                        schedule, created_by, source_proposal_id, decision
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, 'approved')
                    """,
                    (
                        version_id, next_version, row["dataset_id"], row["dataset_version_id"],
                        json.dumps(row["proposed_schedule"]), principal.subject, proposal_id,
                    ),
                    )
                    conn.execute(
                    "UPDATE timetable_current SET version_id = %s, updated_at = now() WHERE singleton = TRUE",
                    (version_id,),
                    )
                    updated = conn.execute(
                    """
                    UPDATE timetable_proposals SET status = 'approved', decision_by = %s,
                        decision_at = now(), updated_at = now()
                    WHERE id = %s RETURNING id, proposal_number, status, decision_at
                    """,
                    (principal.subject, proposal_id),
                    ).fetchone()
                    conn.execute(
                    """
                    INSERT INTO workflow_notifications (id, recipient_subject, proposal_id, title, message)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), row["created_by"], proposal_id,
                        "Timetable proposal approved",
                        f"Proposal {row['proposal_number']} was approved as version {next_version}.",
                    ),
                    )
                    audit(conn, principal.subject, principal.roles, "timetable.proposal_approved",
                      "timetable_proposal", proposal_id, before={"status": "pending"},
                      after={"status": "approved", "version_id": str(version_id), "version_number": next_version})
        if conflict_message:
            raise HTTPException(status.HTTP_409_CONFLICT, conflict_message)
        return {"proposal": updated}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/audit")
def get_audit_history(
    search: str = Query(default="", max_length=100),
    action: str | None = Query(default=None, max_length=100),
    entity_type: str | None = Query(default=None, max_length=100),
    actor: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    try:
        migrate()
        filters = []
        params = []
        if action:
            filters.append("action = %s")
            params.append(action)
        if entity_type:
            filters.append("entity_type = %s")
            params.append(entity_type)
        if actor:
            filters.append("actor_subject = %s")
            params.append(actor)
        if search:
            filters.append("(action ILIKE %s OR entity_type ILIKE %s OR entity_id ILIKE %s OR actor_subject ILIKE %s)")
            needle = f"%{search}%"
            params.extend([needle] * 4)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with connection() as conn:
            total = conn.execute(f"SELECT count(*) AS total FROM workflow_audit_log{where}", params).fetchone()["total"]
            rows = conn.execute(
                f"""
                SELECT id, actor_subject, actor_roles, action, entity_type, entity_id,
                       occurred_at, before_state, after_state, result, details
                FROM workflow_audit_log{where}
                ORDER BY occurred_at DESC, id DESC OFFSET %s LIMIT %s
                """,
                [*params, offset, limit],
            ).fetchall()
            return {"entries": [_audit_response(row) for row in rows], "total": total, "offset": offset, "limit": limit}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.post("/faculty-requests", status_code=status.HTTP_201_CREATED)
def create_faculty_request(
    request: FacultyScheduleRequest,
    principal: AuthenticatedPrincipal = Depends(require_roles("FACULTY")),
):
    try:
        data, dataset_version_id = get_working_snapshot(principal.subject)
        if request.request_type in {"leave", "availability"} and (
            not request.preferred_days or not request.preferred_periods
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Leave/availability requires preferred days and periods.",
            )
        migrate()
        with connection() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"faculty-request:{principal.subject}:{request.idempotency_key}",),
            )
            existing = conn.execute(
                "SELECT id, status FROM faculty_schedule_requests WHERE created_by = %s AND idempotency_key = %s",
                (principal.subject, request.idempotency_key),
            ).fetchone()
            if existing:
                return {"request_id": str(existing["id"]), "status": existing["status"], "duplicate": True}
            profile = _profile(conn, principal.subject)
            if not profile or not profile["faculty_id"]:
                raise HTTPException(status.HTTP_409_CONFLICT, "Ask an administrator to map your Keycloak profile to a faculty record.")
            faculty_id = profile["faculty_id"]
            current = _ensure_current(conn, principal.subject, data, dataset_version_id)
            baseline = current["schedule"]
            base_version_id = current["id"]

        if not any(item["id"] == faculty_id for item in data["faculty"]):
            raise HTTPException(status.HTTP_409_CONFLICT, "Your faculty mapping is not present in the active dataset.")
        valid_days = set(data["calendar"]["days"])
        valid_periods = {period["id"] for period in data["calendar"]["periods"] if period["teaching"]}
        if (len(set(request.preferred_days)) != len(request.preferred_days)
                or not set(request.preferred_days).issubset(valid_days)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Preferred days are invalid.")
        if (len(set(request.preferred_periods)) != len(request.preferred_periods)
                or not set(request.preferred_periods).issubset(valid_periods)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Preferred periods are invalid.")
        faculty_sessions = {
            item["id"]: item for item in data["sessions"]
            if item["faculty_id"] == faculty_id
        }
        if request.request_type == "reschedule" and request.session_id not in faculty_sessions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Selected session is not assigned to your faculty record.")

        try:
            language = detect_language(request.message)
            interpretation = generate_assistant_reply(
                "Interpret the faculty scheduling request below. Restate intent and missing details only. "
                "Do not assign rooms, choose schedule placements, approve, or claim any action was performed.\n"
                + request.message,
                [],
                json.dumps({"request_type": request.request_type, "selected_session": request.session_id,
                            "preferred_days": request.preferred_days, "preferred_periods": request.preferred_periods}),
                language,
            ).answer
        except (
            GeminiConfigurationError, GeminiAuthenticationError, GeminiModelError,
            GeminiQuotaError, GeminiRequestError, GeminiTimeoutError, GeminiUnavailableError,
        ) as error:
            logger.warning("Faculty request interpretation failed: %s", type(error).__name__)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Gemini could not interpret this request. Please retry.") from error

        changed_data = copy.deepcopy(data)
        disruption_id = str(uuid.uuid4())
        if request.request_type in {"leave", "availability"}:
            disruption = {
                "id": disruption_id,
                "dataset": data["dataset_id"],
                "changes": [{
                    "type": "faculty_unavailable",
                    "faculty_id": faculty_id,
                    "days": request.preferred_days,
                    "periods": request.preferred_periods,
                    "reason": f"Faculty request {disruption_id}",
                }],
            }
            changed_data = apply_disruption(changed_data, disruption)
        else:
            session = faculty_sessions[request.session_id]
            old = next((item for item in baseline["sessions"] if item["session_id"] == request.session_id), None)
            if old is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "Selected session is not in the approved timetable.")
            target_day = request.preferred_days[0]
            target_period = request.preferred_periods[0]
            changed_data["faculty"] = [
                ({**member, "unavailable": [
                    *member.get("unavailable", []),
                    {"day": old["day"], "periods": list(range(old["start_period"], old["start_period"] + session["duration"])),
                     "reason": f"Reschedule request {disruption_id}"},
                ]} if member["id"] == faculty_id else member)
                for member in changed_data["faculty"]
            ]
            changed_data["sessions"] = [
                ({**item, "fixed_slot": {"day": target_day, "start_period": target_period}}
                 if item["id"] == request.session_id else item)
                for item in changed_data["sessions"]
            ]
            disruption = {
                "id": disruption_id,
                "dataset": data["dataset_id"],
                "changes": [{
                    "type": "faculty_unavailable",
                    "faculty_id": faculty_id,
                    "days": [old["day"]],
                    "periods": list(range(old["start_period"], old["start_period"] + session["duration"])),
                    "reason": f"Reschedule request {disruption_id}",
                }],
            }

        try:
            alternatives, solver_evidence = generate_crisis_schedule(
                changed_data, baseline, return_solver_evidence=True
            )
        except (ValueError, KeyError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"No feasible alternative: {error}") from error
        candidate = copy.deepcopy(baseline)
        candidate["sessions"] = alternatives
        candidate_data = changed_data
        if request.request_type == "reschedule":
            candidate_data = copy.deepcopy(data)
        candidate = _official_candidate(candidate_data, alternatives, baseline)
        validation = _validate_official_candidate(candidate)
        alternative = {
            "schedule": candidate,
            "solver": solver_evidence.get("status"),
            "objective": solver_evidence.get("objective_value"),
            "validation": validation,
        }
        request_id = uuid.uuid4()
        if get_active_version_id() != dataset_version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed while the alternative schedule was being generated.")
        with connection() as conn:
            current = conn.execute(
                "SELECT version_id FROM timetable_current WHERE singleton = TRUE FOR UPDATE"
            ).fetchone()
            if current is None or str(current["version_id"]) != str(base_version_id):
                raise HTTPException(status.HTTP_409_CONFLICT, "Approved timetable changed while your alternative was being generated.")
            row = conn.execute(
                """
                INSERT INTO faculty_schedule_requests (
                    id, created_by, faculty_id, request_type, session_id,
                    preferred_days, preferred_periods, message, interpretation,
                    alternatives, base_version_id, dataset_version_id, status, idempotency_key
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, 'pending', %s)
                RETURNING id, status, created_at
                """,
                (
                    request_id, principal.subject, faculty_id, request.request_type,
                    request.session_id, request.preferred_days, request.preferred_periods,
                    request.message, json.dumps({"summary": interpretation}),
                    json.dumps([alternative]), base_version_id, dataset_version_id,
                    request.idempotency_key,
                ),
            ).fetchone()
            audit(conn, principal.subject, principal.roles, "faculty_request.created",
                  "faculty_schedule_request", request_id, after={"status": "pending", "session_id": request.session_id})
            return {"request": row, "interpretation": interpretation, "alternative_count": 1}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/faculty-requests")
def list_faculty_requests(
    status_filter: Literal["pending", "approved", "rejected", "stale"] | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY")),
):
    try:
        migrate()
        with connection() as conn:
            admin = "ADMIN" in principal.roles
            where = "            WHERE (%s OR created_by = %s) AND (%s::text IS NULL OR status = %s)"
            params = (admin, principal.subject, status_filter, status_filter, offset, limit)
            rows = conn.execute(
                f"""
                SELECT id, created_by, faculty_id, request_type, session_id,
                       preferred_days, preferred_periods, message, interpretation,
                       alternatives, base_version_id, status, decision_by,
                       decision_reason, decision_at, created_at
                FROM faculty_schedule_requests {where}
                ORDER BY created_at DESC OFFSET %s LIMIT %s
                """,
                params,
            ).fetchall()
            return {"requests": rows, "offset": offset, "limit": limit}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.post("/faculty-requests/{request_id}/decision")
def decide_faculty_request(
    request_id: uuid.UUID,
    decision: FacultyRequestDecision,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        migrate()
        conflict_message = None
        updated = None
        published_version = None
        with connection() as conn:
            current = conn.execute(
                """
                SELECT c.version_id, v.dataset_version_id
                FROM timetable_current c
                JOIN timetable_versions v ON v.id = c.version_id
                WHERE c.singleton = TRUE FOR UPDATE OF c
                """
            ).fetchone()
            row = conn.execute(
                "SELECT * FROM faculty_schedule_requests WHERE id = %s FOR UPDATE",
                (request_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Faculty request not found.")
            if row["status"] != "pending":
                raise HTTPException(status.HTTP_409_CONFLICT, "Faculty request has already been decided.")
            if decision.action == "approve" and (
                current is None
                or str(current["version_id"]) != str(row["base_version_id"])
                or (
                    row["dataset_version_id"]
                    and current["dataset_version_id"] != row["dataset_version_id"]
                )
                or (
                    row["dataset_version_id"]
                    and get_active_version_id() != row["dataset_version_id"]
                )
            ):
                conflict_message = "Request alternative is stale; regenerate it against the current approved timetable and dataset."
                updated = conn.execute(
                    """
                    UPDATE faculty_schedule_requests SET status = 'stale',
                        decision_by = %s, decision_reason = %s, decision_at = now(), updated_at = now()
                    WHERE id = %s RETURNING id, status, decision_reason, decision_at
                    """,
                    (principal.subject, conflict_message, request_id),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO workflow_notifications (id, recipient_subject, request_id, title, message)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), row["created_by"], request_id,
                        "Faculty scheduling request is stale", conflict_message,
                    ),
                )
                audit(conn, principal.subject, principal.roles, "faculty_request.stale",
                      "faculty_schedule_request", request_id, before={"status": "pending"},
                      after={"status": "stale", "reason": conflict_message})
            else:
                if decision.action == "approve":
                    alternatives = row["alternatives"]
                    if decision.alternative_index >= len(alternatives):
                        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Selected alternative does not exist.")
                    schedule = alternatives[decision.alternative_index]["schedule"]
                    next_version = conn.execute(
                        "SELECT COALESCE(max(version_number), 0) + 1 AS next FROM timetable_versions"
                    ).fetchone()["next"]
                    proposal_id = uuid.uuid4()
                    version_id = uuid.uuid4()
                    original = conn.execute(
                        "SELECT schedule FROM timetable_versions WHERE id = %s",
                        (row["base_version_id"],),
                    ).fetchone()["schedule"]
                    changes = _session_changes(original, schedule, load_dataset())
                    conn.execute(
                        """
                        INSERT INTO timetable_proposals (
                            id, base_version_id, dataset_id, dataset_version_id,
                            original_schedule, proposed_schedule, changes, created_by,
                            reason, status, decision_by, decision_at
                        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                                  %s, %s, 'approved', %s, now())
                        """,
                        (
                            proposal_id, row["base_version_id"], schedule["dataset_id"],
                            row["dataset_version_id"], json.dumps(original),
                            json.dumps(schedule), json.dumps(changes), principal.subject,
                            f"Approved faculty request {request_id}", principal.subject,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO timetable_versions (
                            id, version_number, dataset_id, dataset_version_id,
                            schedule, created_by, source_proposal_id, decision
                        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, 'approved')
                        """,
                        (
                            version_id, next_version, schedule["dataset_id"],
                            row["dataset_version_id"], json.dumps(schedule),
                            principal.subject, proposal_id,
                        ),
                    )
                    conn.execute(
                        "UPDATE timetable_current SET version_id = %s, updated_at = now() WHERE singleton = TRUE",
                        (version_id,),
                    )
                    after = {
                        "status": "approved",
                        "version_number": next_version,
                        "proposal_id": str(proposal_id),
                    }
                    published_version = next_version
                else:
                    after = {"status": "rejected", "reason": decision.reason.strip()}
                updated = conn.execute(
                    """
                    UPDATE faculty_schedule_requests SET status = %s, decision_by = %s,
                        decision_reason = %s, decision_at = now(), updated_at = now()
                    WHERE id = %s RETURNING id, status, decision_reason, decision_at
                    """,
                    (
                        "approved" if decision.action == "approve" else "rejected",
                        principal.subject,
                        decision.reason.strip() if decision.reason else None,
                        request_id,
                    ),
                ).fetchone()
                notification_message = (
                    decision.reason.strip()
                    if decision.reason and decision.reason.strip()
                    else f"Approved timetable version {published_version}."
                    if published_version is not None
                    else "Your scheduling request was approved."
                )
                conn.execute(
                    """
                    INSERT INTO workflow_notifications (id, recipient_subject, request_id, title, message)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        uuid.uuid4(), row["created_by"], request_id,
                        f"Faculty scheduling request {updated['status']}",
                        notification_message,
                    ),
                )
                audit(conn, principal.subject, principal.roles, f"faculty_request.{updated['status']}",
                      "faculty_schedule_request", request_id, before={"status": "pending"},
                      after=after)
        if conflict_message:
            raise HTTPException(status.HTTP_409_CONFLICT, conflict_message)
        return {"request": updated, "timetable_version": published_version}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.get("/notifications")
def my_notifications(
    unread_only: bool = False,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY", "STUDENT")),
):
    try:
        migrate()
        with connection() as conn:
            rows = conn.execute(
                """
                SELECT id, request_id, proposal_id, title, message, read_at, created_at
                FROM workflow_notifications
                WHERE recipient_subject = %s AND (%s = FALSE OR read_at IS NULL)
                ORDER BY created_at DESC LIMIT 100
                """,
                (principal.subject, unread_only),
            ).fetchall()
            return {"notifications": rows}
    except WorkflowStoreUnavailable as error:
        _db_error(error)


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: uuid.UUID,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN", "FACULTY", "STUDENT")),
):
    try:
        migrate()
        with connection() as conn:
            row = conn.execute(
                """
                UPDATE workflow_notifications SET read_at = now()
                WHERE id = %s AND recipient_subject = %s
                RETURNING id, read_at
                """,
                (notification_id, principal.subject),
            ).fetchone()
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")
            return {"notification": row}
    except WorkflowStoreUnavailable as error:
        _db_error(error)
