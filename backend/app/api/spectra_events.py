import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import AuthenticatedPrincipal, require_roles
from app.booking.availability import read_official_schedule
from app.booking.booking_engine import effective_category
from app.booking.natural_language import local_date_today
from app.services.dataset_loader import load_dataset
from app.services.spectra_event_availability import (
    check_event_availability,
    validate_event_slot,
)
from app.services.spectra_event_store import (
    connect,
    event_record,
    insert_history,
    insert_modification_history,
)


EventStatus = Literal["pending", "approved", "rejected", "cancelled"]
EventCategory = Literal[
    "institute_event",
    "guest_lecture",
    "department_event",
    "club_event",
]

router = APIRouter(prefix="/api/spectra/events", tags=["Spectra Events"])


require_admin = require_roles("ADMIN")
require_event_requester = require_roles("ADMIN", "FACULTY", "STUDENT")
ROLE_CATEGORY_CAP = {
    "ADMIN": "admin",
    "FACULTY": "faculty",
    "STUDENT": "club",
}


class EventRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_name: str = Field(min_length=1, max_length=160)
    category: EventCategory
    room_id: str = Field(min_length=1, max_length=80)
    event_date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    expected_participants: int = Field(strict=True, ge=1, le=100000)
    required_features: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("required_features")
    @classmethod
    def unique_features(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("required_features must not contain duplicates.")
        return values

    @field_validator("event_date", mode="before")
    @classmethod
    def require_iso_date(cls, value):
        if isinstance(value, date):
            return value
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            value,
        ):
            raise ValueError("event_date must use YYYY-MM-DD format.")
        return date.fromisoformat(value)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class RequiredReasonRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class RescheduleRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    room_id: str | None = Field(default=None, min_length=1, max_length=80)
    expected_participants: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        le=100000,
    )
    required_features: list[str] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def unique_features(self):
        if (
            self.required_features is not None
            and len(set(self.required_features)) != len(self.required_features)
        ):
            raise ValueError("required_features must not contain duplicates.")
        return self


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _actor(principal):
    return principal.subject


def _prepare_event(data, body, event_id=None, created_at=None):
    event = body.model_dump()
    if event_id is not None:
        event["id"] = event_id
    if created_at is not None:
        event["created_at"] = created_at

    if event["event_date"] < local_date_today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Venue events must be scheduled for today or a future date.",
        )

    slot = validate_event_slot(data, event)
    if slot is None:
        weekday = event["event_date"].strftime("%A")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"The date must fall on a supported teaching weekday and the "
                f"time must match contiguous supported schedule periods; got {weekday}."
            ),
        )
    event.update(slot)
    return event, slot


def _require_category_permission(data, category, principal):
    role_category = next(
        (
            ROLE_CATEGORY_CAP[role]
            for role in principal.roles
            if role in ROLE_CATEGORY_CAP
        ),
        None,
    )
    if role_category is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account cannot submit venue booking requests.",
        )
    effective = effective_category(
        data,
        {"role": role_category, "requested_category": category},
    )
    if effective != category:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The requested event category exceeds your role's booking priority.",
        )


def _check(event, connection, data=None, schedule=None, exclude_event_id=None):
    data = data or load_dataset()
    schedule = schedule or read_official_schedule(data)
    slot = validate_event_slot(data, event)
    if slot is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The event no longer matches a supported teaching date and period.",
        )
    event.update(slot)
    if "created_at" not in event:
        event["created_at"] = _now()
    if "id" not in event:
        event["id"] = f"preview-{uuid.uuid4()}"
    return check_event_availability(
        connection,
        data,
        schedule,
        event,
        slot,
        exclude_event_id=exclude_event_id,
    )


def _require_room_capacity(availability):
    checks = availability["checks"]
    if not checks["room_exists"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected room does not exist in the campus dataset.",
        )
    if not checks["room_allowed_for_events"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The selected room is not designated for events.",
        )
    if not checks["capacity_sufficient"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "The selected room cannot hold the expected participants.",
                "conflicts": availability["conflicts"],
            },
        )


def _event_for_storage(event, actor, timestamp):
    return {
        "id": event["id"],
        "event_name": event["event_name"],
        "category": event["category"],
        "room_id": event["room_id"],
        "event_date": event["event_date"].isoformat(),
        "start_time": event["start_time"],
        "end_time": event["end_time"],
        "start_period": event["start_period"],
        "end_period": event["end_period"],
        "duration_periods": event["duration_periods"],
        "expected_participants": event["expected_participants"],
        "required_features": event.get("required_features", []),
        "status": "pending",
        "created_by": actor,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def create_event_in_transaction(
    connection,
    body,
    principal,
    data,
    event_id,
    timestamp,
    require_available=False,
):
    _require_category_permission(data, body.category, principal)
    event, _ = _prepare_event(
        data,
        body,
        event_id=event_id,
        created_at=timestamp,
    )
    availability = _check(event, connection, data=data)
    _require_room_capacity(availability)
    if require_available and not availability["available"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The selected room is no longer available.",
                "availability": availability,
            },
        )
    stored = _event_for_storage(event, _actor(principal), timestamp)
    connection.execute(
        """
        INSERT INTO spectra_events (
            id, event_name, category, room_id, event_date, start_time,
            end_time, start_period, end_period, duration_periods,
            expected_participants, required_features, status, created_by,
            created_at, updated_at
        ) VALUES (
            :id, :event_name, :category, :room_id, :event_date, :start_time,
            :end_time, :start_period, :end_period, :duration_periods,
            :expected_participants, :required_features, :status, :created_by,
            :created_at, :updated_at
        )
        """,
        {
            **stored,
            "required_features": json.dumps(stored["required_features"]),
        },
    )
    insert_history(
        connection,
        event_id,
        "created",
        _actor(principal),
        timestamp,
        "Event submitted for approval.",
    )
    return {"event": stored, "availability": availability}


def reschedule_event_in_transaction(
    connection,
    event_id,
    body,
    principal,
    data,
    timestamp,
):
    row = connection.execute(
        "SELECT * FROM spectra_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    previous = event_record(row)
    if (
        "ADMIN" not in principal.roles
        and previous["created_by"] != _actor(principal)
    ):
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    if previous["status"] not in ("pending", "approved"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reschedule an event with status '{previous['status']}'.",
        )
    _require_category_permission(data, previous["category"], principal)

    updated_request = EventRequest(
        event_name=previous["event_name"],
        category=previous["category"],
        room_id=body.room_id or previous["room_id"],
        event_date=body.event_date,
        start_time=body.start_time,
        end_time=body.end_time,
        expected_participants=(
            body.expected_participants
            if body.expected_participants is not None
            else previous["expected_participants"]
        ),
        required_features=(
            body.required_features
            if body.required_features is not None
            else previous["required_features"]
        ),
    )
    updated, _ = _prepare_event(
        data,
        updated_request,
        event_id=event_id,
        created_at=previous["created_at"],
    )
    availability = _check(
        updated,
        connection,
        data=data,
        exclude_event_id=event_id,
    )
    _require_room_capacity(availability)
    if not availability["available"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The event could not be rescheduled because the new "
                    "slot is unavailable. The original booking is unchanged."
                ),
                "availability": availability,
            },
        )

    updated_storage = _event_for_storage(
        updated,
        previous["created_by"],
        previous["created_at"],
    )
    updated_storage["status"] = previous["status"]
    updated_storage["updated_at"] = timestamp
    connection.execute(
        """
        UPDATE spectra_events
        SET room_id = ?, event_date = ?, start_time = ?, end_time = ?,
            start_period = ?, end_period = ?, duration_periods = ?,
            expected_participants = ?, required_features = ?, updated_at = ?
        WHERE id = ? AND status IN ('pending', 'approved')
        """,
        (
            updated_storage["room_id"],
            updated_storage["event_date"],
            updated_storage["start_time"],
            updated_storage["end_time"],
            updated_storage["start_period"],
            updated_storage["end_period"],
            updated_storage["duration_periods"],
            updated_storage["expected_participants"],
            json.dumps(updated_storage["required_features"]),
            timestamp,
            event_id,
        ),
    )
    insert_modification_history(
        connection,
        event_id,
        _actor(principal),
        timestamp,
        "Event rescheduled after availability recheck.",
        previous,
        updated_storage,
    )
    saved = connection.execute(
        "SELECT * FROM spectra_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    return {"event": event_record(saved), "availability": availability}


@router.get("")
def list_events(
    event_status: EventStatus | None = Query(default=None, alias="status"),
    principal: AuthenticatedPrincipal = Depends(require_admin),
):
    del principal
    with connect() as connection:
        if event_status is None:
            rows = connection.execute(
                """
                SELECT * FROM spectra_events
                ORDER BY event_date, start_period, created_at, id
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM spectra_events
                WHERE status = ?
                ORDER BY event_date, start_period, created_at, id
                """,
                (event_status,),
            ).fetchall()
    events = [event_record(row) for row in rows]
    return {"events": events, "count": len(events)}


@router.get("/my")
def list_my_events(
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM spectra_events
            WHERE created_by = ?
            ORDER BY event_date, start_period, created_at, id
            """,
            (_actor(principal),),
        ).fetchall()
    events = [event_record(row) for row in rows]
    return {"events": events, "count": len(events)}


@router.get("/{event_id}")
def get_event(
    event_id: str,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    event = event_record(row)
    if "ADMIN" not in principal.roles and event["created_by"] != _actor(principal):
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    return {"event": event}


@router.get("/{event_id}/history")
def get_event_history(
    event_id: str,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    with connect() as connection:
        event_row = connection.execute(
            "SELECT created_by FROM spectra_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event_row is None:
            raise HTTPException(status_code=404, detail="Spectra event not found.")
        if (
            "ADMIN" not in principal.roles
            and event_row["created_by"] != _actor(principal)
        ):
            raise HTTPException(status_code=404, detail="Spectra event not found.")
        rows = connection.execute(
            """
            SELECT action, actor, timestamp, reason
            FROM spectra_event_history
            WHERE event_id = ?
            ORDER BY id
            """,
            (event_id,),
        ).fetchall()
        modifications = connection.execute(
            """
            SELECT action, actor, timestamp, reason,
                   previous_event_json, updated_event_json
            FROM spectra_event_modification_history
            WHERE event_id = ?
            ORDER BY id
            """,
            (event_id,),
        ).fetchall()
    history = [dict(row) for row in rows]
    history.extend({
        **dict(row),
        "previous_event": json.loads(row["previous_event_json"]),
        "updated_event": json.loads(row["updated_event_json"]),
    } for row in modifications)
    history.sort(key=lambda item: item["timestamp"])
    return {
        "event_id": event_id,
        "history": history,
    }


@router.post("/availability")
def preview_event_availability(
    body: EventRequest,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    data = load_dataset()
    _require_category_permission(data, body.category, principal)
    event, _ = _prepare_event(data, body)
    with connect() as connection:
        availability = _check(event, connection, data=data)
    return {
        "event": {
            key: event[key]
            for key in (
                "event_name",
                "category",
                "room_id",
                "event_date",
                "start_time",
                "end_time",
                "start_period",
                "end_period",
                "duration_periods",
                "expected_participants",
            )
        },
        "availability": availability,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_event(
    body: EventRequest,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    data = load_dataset()
    event_id = str(uuid.uuid4())
    timestamp = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = create_event_in_transaction(
            connection,
            body,
            principal,
            data,
            event_id,
            timestamp,
        )
    return result


@router.patch("/my/{event_id}/reschedule")
def reschedule_my_event(
    event_id: str,
    body: RescheduleRequest,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    data = load_dataset()
    timestamp = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = reschedule_event_in_transaction(
            connection,
            event_id,
            body,
            principal,
            data,
            timestamp,
        )
    return result


@router.post("/{event_id}/approve")
def approve_event(
    event_id: str,
    body: DecisionRequest | None = None,
    principal: AuthenticatedPrincipal = Depends(require_admin),
):
    decision_reason = (
        body.reason.strip()
        if body is not None and body.reason and body.reason.strip()
        else "Approved after availability and priority recheck."
    )
    data = load_dataset()
    schedule = read_official_schedule(data)
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Spectra event not found.")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve an event with status '{row['status']}'.",
            )
        event = event_record(row)
        event["event_date"] = date.fromisoformat(event["event_date"])
        availability = _check(
            event,
            connection,
            data=data,
            schedule=schedule,
            exclude_event_id=event_id,
        )
        _require_room_capacity(availability)
        if not availability["available"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Event cannot be approved because availability or priority checks failed.",
                    "availability": availability,
                },
            )
        timestamp = _now()
        connection.execute(
            """
            UPDATE spectra_events
            SET status = 'approved', updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (timestamp, event_id),
        )
        insert_history(
            connection,
            event_id,
            "approved",
            _actor(principal),
            timestamp,
            decision_reason,
        )
        updated = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return {
        "event": event_record(updated),
        "availability": availability,
    }


def transition_event_in_transaction(
    connection,
    event_id,
    new_status,
    reason,
    principal,
    timestamp,
):
    row = connection.execute(
        "SELECT status, created_by FROM spectra_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    if (
        "ADMIN" not in principal.roles
        and row["created_by"] != _actor(principal)
    ):
        raise HTTPException(status_code=404, detail="Spectra event not found.")
    if new_status == "rejected" and "ADMIN" not in principal.roles:
        raise HTTPException(
            status_code=403,
            detail="Only administrators can reject venue requests.",
        )
    current_status = row["status"]
    allowed = (
        current_status == "pending" and new_status in ("rejected", "cancelled")
    ) or (
        current_status == "approved" and new_status == "cancelled"
    )
    if not allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition an event from '{current_status}' to '{new_status}'.",
        )
    connection.execute(
        """
        UPDATE spectra_events SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_status, timestamp, event_id),
    )
    insert_history(
        connection,
        event_id,
        new_status,
        _actor(principal),
        timestamp,
        reason,
    )
    updated = connection.execute(
        "SELECT * FROM spectra_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    return event_record(updated)


def _transition_event(event_id, new_status, reason, principal):
    timestamp = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        updated = transition_event_in_transaction(
            connection,
            event_id,
            new_status,
            reason,
            principal,
            timestamp,
        )
    return updated


@router.post("/{event_id}/reject")
def reject_event(
    event_id: str,
    body: RequiredReasonRequest,
    principal: AuthenticatedPrincipal = Depends(require_admin),
):
    event = _transition_event(
        event_id,
        "rejected",
        body.reason,
        principal,
    )
    return {"event": event}


@router.post("/{event_id}/cancel")
def cancel_event(
    event_id: str,
    body: RequiredReasonRequest,
    principal: AuthenticatedPrincipal = Depends(require_event_requester),
):
    event = _transition_event(
        event_id,
        "cancelled",
        body.reason,
        principal,
    )
    return {"event": event}
