from datetime import datetime, timezone

from app.booking.availability import build_occupancy
from app.booking.booking_engine import (
    effective_category,
    request_priority,
    slot_is_available,
)


def event_periods(data, event):
    periods = sorted(
        data["calendar"]["periods"],
        key=lambda item: item["id"],
    )
    start_index = next(
        (
            index
            for index, period in enumerate(periods)
            if period["start"] == event["start_time"]
        ),
        None,
    )
    end_index = next(
        (
            index
            for index, period in enumerate(periods)
            if period["end"] == event["end_time"]
        ),
        None,
    )
    if start_index is None or end_index is None or end_index < start_index:
        return None

    selected = periods[start_index:end_index + 1]
    if any(
        current["end"] != following["start"]
        for current, following in zip(selected, selected[1:])
    ):
        return None
    return selected


def validate_event_slot(data, event):
    weekday = event["event_date"].weekday()
    day_codes = data["calendar"]["days"]
    if weekday >= len(day_codes):
        return None
    day = day_codes[weekday]
    periods = event_periods(data, event)
    if periods is None:
        return None
    return {
        "day": day,
        "start_period": periods[0]["id"],
        "end_period": periods[-1]["id"],
        "duration_periods": len(periods),
    }


def _event_priority(data, event):
    request = {
        "id": event["id"],
        "role": "admin",
        "requested_category": event["category"],
        "submitted_at": event["created_at"],
    }
    return request_priority(data, request)


def check_event_availability(
    connection,
    data,
    schedule,
    event,
    slot,
    exclude_event_id=None,
):
    room = next(
        (item for item in data["rooms"] if item["id"] == event["room_id"]),
        None,
    )
    if room is None:
        return {
            "available": False,
            "checks": {
                "room_exists": False,
                "room_allowed_for_events": False,
                "capacity_sufficient": False,
                "features_satisfied": False,
            },
            "conflicts": [{"type": "unknown_room", "room_id": event["room_id"]}],
            "priority": None,
        }

    checks = {
        "room_exists": True,
        "room_allowed_for_events": "event" in room.get("allowed_uses", []),
        "capacity_sufficient": room["capacity"] >= event["expected_participants"],
    }
    room_features = set(room.get("features", []))
    if room.get("accessible"):
        room_features.add("accessible")
    required_features = set(event.get("required_features", []))
    checks["features_satisfied"] = required_features.issubset(room_features)
    conflicts = []
    if not checks["room_allowed_for_events"]:
        conflicts.append({
            "type": "room_not_allowed_for_events",
            "room_id": room["id"],
        })
    if not checks["capacity_sufficient"]:
        conflicts.append({
            "type": "insufficient_capacity",
            "room_id": room["id"],
            "capacity": room["capacity"],
            "expected_participants": event["expected_participants"],
        })
    if not checks["features_satisfied"]:
        conflicts.append({
            "type": "required_features_unavailable",
            "room_id": room["id"],
            "missing_features": sorted(required_features - room_features),
        })

    requested_periods = set(
        range(slot["start_period"], slot["end_period"] + 1)
    )
    day = slot["day"]
    room_id = room["id"]
    closures = {
        (item["room_id"], item["day"], period): item.get("reason", "")
        for item in data.get("room_unavailability", [])
        for period in item["periods"]
    }
    sessions = {item["id"]: item for item in data["sessions"]}
    for assignment in schedule["sessions"]:
        session = sessions[assignment["session_id"]]
        overlap = requested_periods.intersection(
            range(
                assignment["start_period"],
                assignment["start_period"] + session["duration"],
            )
        )
        if (
            assignment["room_id"] == room_id
            and assignment["day"] == day
            and overlap
        ):
            conflicts.append({
                "type": "teaching",
                "session_id": assignment["session_id"],
                "periods": sorted(overlap),
            })

    bookings = schedule.get("fest_week", {}).get("bookings", [])
    for booking in bookings:
        if (
            booking.get("status") != "approved"
            or booking.get("room_id") != room_id
            or booking.get("day") != day
        ):
            continue
        overlap = requested_periods.intersection(
            range(booking["start_period"], booking["end_period"] + 1)
        )
        if overlap:
            conflicts.append({
                "type": "approved_booking",
                "request_id": booking.get("request_id"),
                "periods": sorted(overlap),
            })

    for period in sorted(requested_periods):
        closure = closures.get((room_id, day, period))
        if closure is not None:
            conflicts.append({
                "type": "maintenance",
                "period": period,
                "reason": closure,
            })

    room_busy, group_busy, room_unavailable = build_occupancy(data, schedule)
    current_priority = _event_priority(data, event)
    pending_priority_blockers = []
    rows = connection.execute(
        """
        SELECT id, event_name, category, created_at, start_period, end_period, status
        FROM spectra_events
        WHERE room_id = ? AND event_date = ? AND status IN ('pending', 'approved')
          AND (? IS NULL OR id != ?)
        """,
        (room_id, event["event_date"].isoformat(), exclude_event_id, exclude_event_id),
    ).fetchall()
    for existing in rows:
        overlap = requested_periods.intersection(
            range(existing["start_period"], existing["end_period"] + 1)
        )
        if not overlap:
            continue
        if existing["status"] == "approved":
            conflicts.append({
                "type": "approved_event",
                "event_id": existing["id"],
                "event_name": existing["event_name"],
                "periods": sorted(overlap),
            })
            room_busy.update({
                (room_id, day, period)
                for period in range(
                    existing["start_period"],
                    existing["end_period"] + 1,
                )
            })
            continue

        pending = {
            "id": existing["id"],
            "role": "admin",
            "requested_category": existing["category"],
            "submitted_at": existing["created_at"],
        }
        existing_priority = request_priority(data, pending)
        conflict = {
            "type": "pending_event",
            "event_id": existing["id"],
            "event_name": existing["event_name"],
            "category": effective_category(data, pending),
            "periods": sorted(overlap),
        }
        conflicts.append(conflict)
        if existing_priority < current_priority:
            pending_priority_blockers.append(conflict)

    # Apply the same slot check used by Venue Booking, including maintenance.
    candidate = {
        "day": day,
        "start_period": slot["start_period"],
        "end_period": slot["end_period"],
    }
    slot_available = slot_is_available(
        {
            "mandatory_for_groups": [],
        },
        room,
        candidate,
        room_busy,
        group_busy,
        room_unavailable,
    )
    checks["slot_available"] = slot_available
    if not slot_available and not any(
        conflict["type"] in ("teaching", "approved_booking", "maintenance", "approved_event")
        for conflict in conflicts
    ):
        conflicts.append({"type": "unavailable"})

    priority = {
        "requested_category": event["category"],
        "effective_category": effective_category(
            data,
            {
                "role": "admin",
                "requested_category": event["category"],
            },
        ),
        "order": data["priority_order"],
        "pending_priority_blockers": pending_priority_blockers,
        "approved_reservations_are_never_displaced": True,
    }
    hard_conflicts = any(
        conflict["type"] not in ("pending_event",)
        for conflict in conflicts
    )
    return {
        "available": (
            checks["room_allowed_for_events"]
            and checks["capacity_sufficient"]
            and checks["features_satisfied"]
            and not hard_conflicts
            and not pending_priority_blockers
        ),
        "checks": checks,
        "conflicts": conflicts,
        "priority": priority,
    }
