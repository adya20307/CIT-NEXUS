import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.auth import AuthenticatedPrincipal
from app.booking.natural_language import local_date_today
from app.api.spectra_events import (
    EventRequest,
    RescheduleRequest,
    _actor,
    _check,
    _prepare_event,
    _require_category_permission,
    _require_room_capacity,
    create_event_in_transaction,
    reschedule_event_in_transaction,
    transition_event_in_transaction,
)
from app.services.dataset_loader import load_dataset
from app.services.spectra_event_store import connect, event_record


EventCategory = Literal[
    "institute_event",
    "guest_lecture",
    "department_event",
    "club_event",
]


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AvailabilityArguments(ToolArguments):
    event_name: str = Field(min_length=1, max_length=160)
    category: EventCategory
    event_date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    expected_participants: int = Field(strict=True, ge=1, le=100000)
    room_id: str | None = Field(default=None, min_length=1, max_length=80)
    room_type: str | None = Field(default=None, min_length=1, max_length=80)
    required_features: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_resource(self):
        if self.room_id and self.room_type:
            raise ValueError("Specify a room ID or room type, not both.")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required_features must be unique.")
        return self


class CreateBookingArguments(AvailabilityArguments):
    room_id: str = Field(min_length=1, max_length=80)
    room_type: None = None


class BookingDetailsArguments(ToolArguments):
    booking_id: str = Field(min_length=1, max_length=80)


class RescheduleArguments(ToolArguments):
    booking_id: str = Field(min_length=1, max_length=80)
    event_date: date
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    room_id: str | None = Field(default=None, min_length=1, max_length=80)
    expected_participants: int | None = Field(default=None, strict=True, ge=1, le=100000)
    required_features: list[str] | None = Field(default=None, max_length=20)


class CancelBookingArguments(ToolArguments):
    booking_id: str = Field(min_length=1, max_length=80)


def _schema(properties, required=()):
    return types.Schema(
        type=types.Type.OBJECT,
        properties=properties,
        required=list(required),
    )


def _string(description, enum=None):
    return types.Schema(
        type=types.Type.STRING,
        description=description,
        enum=enum,
    )


def _integer(description):
    return types.Schema(type=types.Type.INTEGER, description=description)


def _features_schema():
    return types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(type=types.Type.STRING),
        description="Required room features such as accessible or projector.",
    )


def gemini_booking_tools():
    categories = [
        "institute_event",
        "guest_lecture",
        "department_event",
        "club_event",
    ]
    shared = {
        "event_name": _string("Purpose or short title for the booking."),
        "category": _string("The requested campus event category.", categories),
        "event_date": _string("Exact date in YYYY-MM-DD format."),
        "start_time": _string("Exact 24-hour local start time, HH:MM."),
        "end_time": _string("Exact 24-hour local end time, HH:MM."),
        "expected_participants": _integer("Number of attendees."),
        "required_features": _features_schema(),
    }
    return [
        types.FunctionDeclaration(
            name="check_resource_availability",
            description=(
                "Check an exact room/date/time against the official timetable, "
                "maintenance, room capacity/features, and persisted venue events."
            ),
            parameters=_schema(
                {
                    **shared,
                    "room_id": _string("Exact room ID, if the user requested one."),
                    "room_type": _string(
                        "Room type such as classroom, seminar_hall, lab_cs, "
                        "lecture_hall, or auditorium."
                    ),
                },
                (
                    "event_name",
                    "category",
                    "event_date",
                    "start_time",
                    "end_time",
                    "expected_participants",
                ),
            ),
        ),
        types.FunctionDeclaration(
            name="create_booking",
            description=(
                "Prepare a booking proposal after checking availability. "
                "This does not create a booking; the user must confirm the returned proposal."
            ),
            parameters=_schema(
                {
                    **shared,
                    "room_id": _string("Exact room ID selected from availability results."),
                },
                (
                    "event_name",
                    "category",
                    "event_date",
                    "start_time",
                    "end_time",
                    "expected_participants",
                    "room_id",
                ),
            ),
        ),
        types.FunctionDeclaration(
            name="get_my_bookings",
            description="List the authenticated user's own venue booking requests.",
            parameters=_schema({}),
        ),
        types.FunctionDeclaration(
            name="get_booking_details",
            description=(
                "Read a booking by reference. Non-admin users can only read "
                "their own bookings."
            ),
            parameters=_schema(
                {"booking_id": _string("Booking reference ID.")},
                ("booking_id",),
            ),
        ),
        types.FunctionDeclaration(
            name="reschedule_booking",
            description=(
                "Prepare a rescheduling proposal for an owned booking after "
                "checking the new slot. The existing booking is unchanged until confirmation."
            ),
            parameters=_schema(
                {
                    "booking_id": _string("Booking reference ID."),
                    "event_date": shared["event_date"],
                    "start_time": shared["start_time"],
                    "end_time": shared["end_time"],
                    "room_id": _string("Optional replacement room ID."),
                    "expected_participants": shared["expected_participants"],
                    "required_features": shared["required_features"],
                },
                ("booking_id", "event_date", "start_time", "end_time"),
            ),
        ),
        types.FunctionDeclaration(
            name="cancel_booking",
            description=(
                "Prepare cancellation of an owned booking. It is not cancelled "
                "until the user confirms the returned proposal."
            ),
            parameters=_schema(
                {"booking_id": _string("Booking reference ID.")},
                ("booking_id",),
            ),
        ),
    ]


def _utc_now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    return value.isoformat(timespec="seconds")


def _authorized_event(row, principal: AuthenticatedPrincipal):
    if row is None:
        raise HTTPException(status_code=404, detail="Booking not found.")
    event = event_record(row)
    if "ADMIN" not in principal.roles and event["created_by"] != _actor(principal):
        raise HTTPException(status_code=404, detail="Booking not found.")
    return event


def _room_matches(room, room_type):
    if not room_type:
        return True
    normalized = room_type.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "computer_lab": "lab_cs",
        "computer_laboratory": "lab_cs",
        "seminar_room": "seminar_hall",
        "class_room": "classroom",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized in {
        room["type"].casefold(),
        room["id"].casefold(),
    }


def _request_body(arguments, room_id=None):
    payload = arguments.model_dump(
        mode="json",
        exclude={"room_type"},
    )
    if room_id is not None:
        payload["room_id"] = room_id
    return EventRequest.model_validate(payload)


def _check_room(data, connection, principal, body, room_id):
    _require_category_permission(data, body.category, principal)
    selected = EventRequest.model_validate(
        {**body.model_dump(), "room_id": room_id}
    )
    event, _ = _prepare_event(data, selected)
    availability = _check(event, connection, data=data)
    return {
        "room_id": room_id,
        "room_name": next(
            room["name"] for room in data["rooms"] if room["id"] == room_id
        ),
        "room_type": next(
            room["type"] for room in data["rooms"] if room["id"] == room_id
        ),
        "capacity": next(
            room["capacity"] for room in data["rooms"] if room["id"] == room_id
        ),
        "available": availability["available"],
        "conflicts": [
            {
                key: conflict[key]
                for key in (
                    "type",
                    "periods",
                    "period",
                    "capacity",
                    "expected_participants",
                    "missing_features",
                )
                if key in conflict
            }
            for conflict in availability["conflicts"]
        ],
    }


def _insert_proposal(connection, principal, action, payload, booking_id=None):
    proposal_id = str(uuid.uuid4())
    now = _utc_now()
    expires = now + timedelta(minutes=10)
    connection.execute(
        """
        INSERT INTO booking_action_proposals (
            id, created_by, action, booking_id, payload_json, status,
            result_event_id, created_at, updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, ?, ?)
        """,
        (
            proposal_id,
            _actor(principal),
            action,
            booking_id,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            _timestamp(now),
            _timestamp(now),
            _timestamp(expires),
        ),
    )
    return {
        "proposal_id": proposal_id,
        "action": action,
        "booking_id": booking_id,
        "status": "awaiting_confirmation",
        "expires_at": _timestamp(expires),
    }


def _stage_proposal(principal, action, payload, booking_id=None):
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        return _insert_proposal(
            connection,
            principal,
            action,
            payload,
            booking_id,
        )


def _check_create_request(principal, arguments):
    data = load_dataset()
    _require_category_permission(data, arguments.category, principal)
    rooms = [
        room for room in data["rooms"]
        if "event" in room.get("allowed_uses", [])
        and (not arguments.room_id or room["id"] == arguments.room_id)
        and _room_matches(room, arguments.room_type)
    ]
    if not rooms:
        return []
    results = []
    with connect() as connection:
        for room in rooms[:30]:
            body = _request_body(arguments, room["id"])
            results.append(_check_room(data, connection, principal, body, room["id"]))
    return results


def _prepare_create_proposal(principal, arguments):
    results = _check_create_request(principal, arguments)
    selected = next(
        (item for item in results if item["room_id"] == arguments.room_id),
        None,
    )
    if selected is None:
        raise HTTPException(
            status_code=422,
            detail="The requested room is not an eligible event venue.",
        )
    if not selected["available"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "That room is not available for the requested slot.",
                "alternatives": [item for item in results if item["available"]][:5],
            },
        )
    return _stage_proposal(
        principal,
        "create",
        _request_body(arguments).model_dump(mode="json"),
    ) | {
        "summary": {
            "event_name": arguments.event_name,
            "category": arguments.category,
            "room_id": selected["room_id"],
            "room_name": selected["room_name"],
            "event_date": arguments.event_date.isoformat(),
            "start_time": arguments.start_time,
            "end_time": arguments.end_time,
            "expected_participants": arguments.expected_participants,
            "required_features": arguments.required_features,
            "availability": "available",
        },
    }


def _prepare_reschedule_proposal(principal, arguments):
    data = load_dataset()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (arguments.booking_id,),
        ).fetchone()
        event = _authorized_event(row, principal)
        target = RescheduleRequest(
            event_date=arguments.event_date,
            start_time=arguments.start_time,
            end_time=arguments.end_time,
            room_id=arguments.room_id,
            expected_participants=arguments.expected_participants,
            required_features=arguments.required_features,
        )
        current = EventRequest(
            event_name=event["event_name"],
            category=event["category"],
            room_id=target.room_id or event["room_id"],
            event_date=target.event_date,
            start_time=target.start_time,
            end_time=target.end_time,
            expected_participants=(
                target.expected_participants
                if target.expected_participants is not None
                else event["expected_participants"]
            ),
            required_features=(
                target.required_features
                if target.required_features is not None
                else event["required_features"]
            ),
        )
        updated, _ = _prepare_event(
            data,
            current,
            event_id=arguments.booking_id,
            created_at=event["created_at"],
        )
        availability = _check(
            updated,
            connection,
            data=data,
            exclude_event_id=arguments.booking_id,
        )
        _require_room_capacity(availability)
        if not availability["available"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "The requested reschedule time is unavailable.",
                    "availability": availability,
                },
            )
        payload = target.model_dump(mode="json", exclude_none=True)
        proposal = _insert_proposal(
            connection,
            principal,
            "reschedule",
            payload,
            booking_id=arguments.booking_id,
        )
        return proposal | {
            "summary": {
                "event_name": event["event_name"],
                "room_id": current.room_id,
                "event_date": target.event_date.isoformat(),
                "start_time": target.start_time,
                "end_time": target.end_time,
                "status": event["status"],
                "availability": "available",
            },
        }


def _prepare_cancel_proposal(principal, arguments):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (arguments.booking_id,),
        ).fetchone()
    event = _authorized_event(row, principal)
    if event["status"] not in ("pending", "approved"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a booking with status '{event['status']}'.",
        )
    return _stage_proposal(
        principal,
        "cancel",
        {"reason": "Cancelled by the booking owner after explicit confirmation."},
        booking_id=arguments.booking_id,
    ) | {
        "summary": {
            "event_name": event["event_name"],
            "room_id": event["room_id"],
            "event_date": event["event_date"],
            "start_time": event["start_time"],
            "end_time": event["end_time"],
            "status": event["status"],
        },
    }


def _my_bookings(principal: AuthenticatedPrincipal):
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM spectra_events
            WHERE created_by = ?
            ORDER BY event_date, start_period, created_at, id
            """,
            (_actor(principal),),
        ).fetchall()
    return {"bookings": [event_record(row) for row in rows]}


def _booking_details(principal: AuthenticatedPrincipal, booking_id):
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM spectra_events WHERE id = ?",
            (booking_id,),
        ).fetchone()
    return {"booking": _authorized_event(row, principal)}


def execute_gemini_tool(
    name,
    arguments,
    principal: AuthenticatedPrincipal,
):
    if name == "get_my_bookings":
        if arguments:
            raise HTTPException(status_code=422, detail="This tool takes no arguments.")
        return _my_bookings(principal)

    argument_types = {
        "check_resource_availability": AvailabilityArguments,
        "create_booking": CreateBookingArguments,
        "get_booking_details": BookingDetailsArguments,
        "reschedule_booking": RescheduleArguments,
        "cancel_booking": CancelBookingArguments,
    }
    model = argument_types.get(name)
    if model is None:
        raise HTTPException(status_code=422, detail="Unsupported booking tool.")
    try:
        parsed = model.model_validate(arguments)
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail="The booking tool arguments are incomplete or invalid.",
        ) from error

    if name == "check_resource_availability":
        results = _check_create_request(principal, parsed)
        return {
            "available": any(item["available"] for item in results),
            "rooms": results,
            "scope": (
                "Checks the selected date against its weekday's official "
                "timetable, existing venue events, room capacity and features."
            ),
        }
    if name == "create_booking":
        return _prepare_create_proposal(principal, parsed)
    if name == "get_booking_details":
        return _booking_details(principal, parsed.booking_id)
    if name == "reschedule_booking":
        return _prepare_reschedule_proposal(principal, parsed)
    if name == "cancel_booking":
        return _prepare_cancel_proposal(principal, parsed)
    raise HTTPException(status_code=422, detail="Unsupported booking tool.")


def confirm_booking_proposal(
    proposal_id,
    confirmed,
    principal: AuthenticatedPrincipal,
):
    now = _utc_now()
    timestamp = _timestamp(now)
    data = load_dataset()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        proposal = connection.execute(
            "SELECT * FROM booking_action_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if proposal is None or proposal["created_by"] != _actor(principal):
            raise HTTPException(status_code=404, detail="Booking proposal not found.")
        if proposal["status"] == "confirmed":
            row = connection.execute(
                "SELECT * FROM spectra_events WHERE id = ?",
                (proposal["result_event_id"],),
            ).fetchone()
            return {
                "confirmed": True,
                "already_confirmed": True,
                "event": event_record(row),
            }
        if proposal["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"This proposal is already {proposal['status']}.",
            )
        if proposal["expires_at"] <= timestamp:
            raise HTTPException(status_code=410, detail="This booking proposal expired.")
        if not confirmed:
            connection.execute(
                """
                UPDATE booking_action_proposals
                SET status = 'dismissed', updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (timestamp, proposal_id),
            )
            return {"confirmed": False, "status": "dismissed"}

        payload = json.loads(proposal["payload_json"])
        if proposal["action"] == "create":
            result = create_event_in_transaction(
                connection,
                EventRequest.model_validate(payload),
                principal,
                data,
                str(uuid.uuid4()),
                timestamp,
                require_available=True,
            )
            event = result["event"]
        elif proposal["action"] == "reschedule":
            result = reschedule_event_in_transaction(
                connection,
                proposal["booking_id"],
                RescheduleRequest.model_validate(payload),
                principal,
                data,
                timestamp,
            )
            event = result["event"]
        elif proposal["action"] == "cancel":
            event = transition_event_in_transaction(
                connection,
                proposal["booking_id"],
                "cancelled",
                payload["reason"],
                principal,
                timestamp,
            )
        else:
            raise HTTPException(status_code=409, detail="Unsupported booking action.")

        connection.execute(
            """
            UPDATE booking_action_proposals
            SET status = 'confirmed', result_event_id = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (event["id"], timestamp, proposal_id),
        )
        return {
            "confirmed": True,
            "already_confirmed": False,
            "action": proposal["action"],
            "event": event,
        }


def list_pending_booking_proposals(principal: AuthenticatedPrincipal):
    now = _timestamp(_utc_now())
    data = load_dataset()
    rooms_by_id = {room["id"]: room for room in data["rooms"]}
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM booking_action_proposals
            WHERE created_by = ? AND status = 'pending' AND expires_at > ?
            ORDER BY created_at
            """,
            (_actor(principal), now),
        ).fetchall()
        proposals = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            booking = None
            if row["booking_id"]:
                event_row = connection.execute(
                    "SELECT * FROM spectra_events WHERE id = ?",
                    (row["booking_id"],),
                ).fetchone()
                booking = event_record(event_row) if event_row else None
            if row["action"] == "create":
                summary = {
                    **payload,
                    "room_name": rooms_by_id.get(
                        payload["room_id"], {}
                    ).get("name"),
                }
            elif row["action"] == "reschedule":
                summary = {
                    "event_name": booking["event_name"] if booking else "Booking",
                    **payload,
                    "room_id": payload.get(
                        "room_id",
                        booking["room_id"] if booking else None,
                    ),
                }
            else:
                summary = {
                    "event_name": booking["event_name"] if booking else "Booking",
                    "room_id": booking["room_id"] if booking else None,
                    "event_date": booking["event_date"] if booking else None,
                    "start_time": booking["start_time"] if booking else None,
                    "end_time": booking["end_time"] if booking else None,
                    "status": booking["status"] if booking else None,
                }
            proposals.append({
                "proposal_id": row["id"],
                "action": row["action"],
                "booking_id": row["booking_id"],
                "status": "awaiting_confirmation",
                "expires_at": row["expires_at"],
                "summary": summary,
            })
    return proposals


def booking_tool_context():
    data = load_dataset()
    return {
        "today": local_date_today().isoformat(),
        "rooms": [
            {
                "id": room["id"],
                "name": room["name"],
                "type": room["type"],
                "capacity": room["capacity"],
                "features": room.get("features", []),
                "accessible": bool(room.get("accessible")),
                "allowed_for_events": "event" in room.get("allowed_uses", []),
            }
            for room in data["rooms"]
        ],
        "supported_periods": [
            {"start": period["start"], "end": period["end"]}
            for period in data["calendar"]["periods"]
            if period["teaching"]
        ],
        "timezone": "Asia/Kolkata",
    }
