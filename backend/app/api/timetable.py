import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthenticatedPrincipal, require_roles

from app.services.dataset_loader import load_dataset
from app.services.starter_pack_manager import (
    SCHEDULE_STATE_PATH,
    VERSIONS_ROOT,
)
from app.services.workflow_store import WorkflowStoreUnavailable, get_current_timetable


router = APIRouter(tags=["Timetable and Analytics"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = PROJECT_ROOT / "exports"
OFFICIAL_SCHEDULE_PATH = EXPORT_DIR / "schedule.json"


def _read_schedule(path):
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Required official schedule not found: {path.name}",
        ) from error
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Official schedule is not valid JSON: {path.name}",
        ) from error


def _schedule_state():
    if not SCHEDULE_STATE_PATH.is_file():
        return {}
    with SCHEDULE_STATE_PATH.open("r", encoding="utf-8") as file:
        value = json.load(file)
    return value if isinstance(value, dict) else {}


def _published_schedule_data(working_data):
    version_id = _schedule_state().get("schedule_version_id")
    if not version_id:
        return working_data
    version_path = VERSIONS_ROOT / f"{version_id}.json"
    if not version_path.is_file():
        return working_data
    with version_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _schedule_errors(schedule, data):
    if not isinstance(schedule, dict):
        return ["Schedule must be a JSON object."]
    if schedule.get("dataset_id") != data["dataset_id"]:
        return [
            "Schedule dataset does not match the supplied "
            f"dataset ({data['dataset_id']})."
        ]

    assignments = schedule.get("sessions")
    if not isinstance(assignments, list):
        return ["Schedule does not contain a session assignment list."]
    if any(not isinstance(item, dict) for item in assignments):
        return ["Schedule contains an invalid session assignment."]

    expected = {session["id"] for session in data["sessions"]}
    if len(expected) != len(data["sessions"]):
        return ["Dataset contains duplicate session IDs."]
    actual = [item.get("session_id") for item in assignments]
    actual_set = {session_id for session_id in actual if isinstance(session_id, str)}
    errors = []

    duplicates = sorted(
        session_id
        for session_id, count in Counter(
            session_id
            for session_id in actual
            if isinstance(session_id, str)
        ).items()
        if count > 1
    )
    unknown = sorted(actual_set - expected)
    missing = sorted(expected - actual_set)
    if len(actual_set) != len(actual):
        errors.append("Schedule contains a missing or invalid session ID.")
    if duplicates:
        errors.append(f"Duplicate session assignments: {duplicates}.")
    if unknown:
        errors.append(f"Unknown session IDs: {unknown}.")
    if missing:
        errors.append(f"Missing session assignments: {missing}.")

    session_by_id = {session["id"]: session for session in data["sessions"]}
    room_ids = {room["id"] for room in data["rooms"]}
    valid_days = set(data["calendar"]["days"])
    teaching_periods = {
        period["id"]
        for period in data["calendar"]["periods"]
        if period.get("teaching")
    }

    for assignment in assignments:
        session_id = assignment.get("session_id")
        session = (
            session_by_id.get(session_id)
            if isinstance(session_id, str)
            else None
        )
        if session is None:
            continue
        day = assignment.get("day")
        start = assignment.get("start_period")
        room_id = assignment.get("room_id")
        if not isinstance(day, str) or day not in valid_days:
            errors.append(f"{session_id} has invalid teaching day {day!r}.")
        if not isinstance(room_id, str) or room_id not in room_ids:
            errors.append(f"{session_id} has unknown room {room_id!r}.")
        if type(start) is not int:
            errors.append(f"{session_id} has an invalid start period.")
            continue
        occupied = set(range(start, start + session["duration"]))
        if not occupied.issubset(teaching_periods):
            errors.append(
                f"{session_id} occupies a non-teaching period: "
                f"{sorted(occupied - teaching_periods)}."
            )

    return errors


def _placement_conflicts(assignments, data):
    session_by_id = {session["id"]: session for session in data["sessions"]}
    occupancy = defaultdict(set)
    conflicts_by_session = defaultdict(set)

    for assignment in assignments:
        session = session_by_id[assignment["session_id"]]
        start = assignment["start_period"]
        end = start + session["duration"]
        for period in range(start, end):
            occupancy[("room", assignment["room_id"], assignment["day"], period)].add(
                assignment["session_id"]
            )
            occupancy[("faculty", session["faculty_id"], assignment["day"], period)].add(
                assignment["session_id"]
            )
            for group_id in session["groups"]:
                occupancy[("group", group_id, assignment["day"], period)].add(
                    assignment["session_id"]
                )

    basket_by_session = {
        session["id"]: session.get("basket_slot")
        for session in data["sessions"]
    }
    for (resource_type, resource_id, day, period), session_ids in occupancy.items():
        if len(session_ids) < 2:
            continue
        ordered_ids = sorted(session_ids)
        for index, first_id in enumerate(ordered_ids):
            for second_id in ordered_ids[index + 1:]:
                if (
                    resource_type == "group"
                    and basket_by_session[first_id]
                    and basket_by_session[first_id] == basket_by_session[second_id]
                ):
                    continue
                conflict = (
                    resource_type,
                    resource_id,
                    day,
                    period,
                    first_id,
                    second_id,
                )
                conflicts_by_session[first_id].add(conflict)
                conflicts_by_session[second_id].add(conflict)

    room_closures = {
        (closure["room_id"], closure["day"], period): closure
        for closure in data.get("room_unavailability", [])
        for period in closure["periods"]
    }
    faculty_by_id = {member["id"]: member for member in data["faculty"]}

    for assignment in assignments:
        session_id = assignment["session_id"]
        session = session_by_id[session_id]
        start = assignment["start_period"]
        for period in range(start, start + session["duration"]):
            closure = room_closures.get(
                (assignment["room_id"], assignment["day"], period)
            )
            if closure:
                conflicts_by_session[session_id].add((
                    "maintenance",
                    assignment["room_id"],
                    assignment["day"],
                    period,
                    session_id,
                    closure.get("reason", ""),
                ))
            faculty = faculty_by_id[session["faculty_id"]]
            if any(
                unavailable["day"] == assignment["day"]
                and period in unavailable["periods"]
                for unavailable in faculty.get("unavailable", [])
            ):
                conflicts_by_session[session_id].add((
                    "faculty_unavailability",
                    session["faculty_id"],
                    assignment["day"],
                    period,
                    session_id,
                    "",
                ))

    result = {}
    for session_id, conflicts in conflicts_by_session.items():
        result[session_id] = [
            {
                "type": item[0],
                "resource_id": item[1],
                "day": item[2],
                "period": item[3],
                "other_session_id": item[5] if item[0] in ("room", "faculty", "group") else None,
                "reason": item[5] if item[0] == "maintenance" else None,
            }
            for item in sorted(conflicts)
        ]
    return result


def _enrich_assignments(data, schedule, baseline_by_id=None):
    sessions = {session["id"]: session for session in data["sessions"]}
    courses = {course["id"]: course for course in data["courses"]}
    faculty = {member["id"]: member for member in data["faculty"]}
    groups = {group["id"]: group for group in data["student_groups"]}
    rooms = {room["id"]: room for room in data["rooms"]}
    conflicts = _placement_conflicts(schedule["sessions"], data)

    enriched = []
    for assignment in schedule["sessions"]:
        session = sessions[assignment["session_id"]]
        course = courses[session["course_id"]]
        faculty_member = faculty[session["faculty_id"]]
        room = rooms[assignment["room_id"]]
        start = assignment["start_period"]
        end = start + session["duration"] - 1
        item = {
            "session_id": session["id"],
            "course_id": course["id"],
            "course_title": course["title"],
            "kind": session["kind"],
            "group_ids": session["groups"],
            "groups": [
                {"id": group_id, "name": groups[group_id]["name"]}
                for group_id in session["groups"]
            ],
            "faculty_id": faculty_member["id"],
            "faculty_name": faculty_member["name"],
            "room_id": room["id"],
            "room_name": room["name"],
            "room_type": room["type"],
            "room_capacity": room["capacity"],
            "day": assignment["day"],
            "start_period": start,
            "end_period": end,
            "duration": session["duration"],
            "conflicts": conflicts.get(session["id"], []),
            "change": None,
        }
        if baseline_by_id is not None:
            baseline = baseline_by_id.get(session["id"])
            if baseline is None:
                item["change"] = {
                    "type": "ADDED",
                    "original": None,
                }
                enriched.append(item)
                continue
            time_changed = (
                baseline["day"] != assignment["day"]
                or baseline["start_period"] != start
            )
            room_changed = baseline["room_id"] != room["id"]
            if time_changed or room_changed:
                change_type = (
                    "TIME_AND_ROOM"
                    if time_changed and room_changed
                    else "TIME_ONLY"
                    if time_changed
                    else "ROOM_ONLY"
                )
                item["change"] = {
                    "type": change_type,
                    "original": {
                        "day": baseline["day"],
                        "start_period": baseline["start_period"],
                        "room_id": baseline["room_id"],
                    },
                }
        enriched.append(item)
    return enriched


def _enrich_approved_bookings(data, schedule, teaching_assignments):
    requests = {request["id"]: request for request in data["booking_requests"]}
    rooms = {room["id"]: room for room in data["rooms"]}
    closures = {
        (item["room_id"], item["day"], period): item.get("reason", "")
        for item in data.get("room_unavailability", [])
        for period in item["periods"]
    }
    scheduled_room_periods = defaultdict(set)
    sessions = {session["id"]: session for session in data["sessions"]}
    for assignment in teaching_assignments:
        session = sessions[assignment["session_id"]]
        for period in range(
            assignment["start_period"],
            assignment["start_period"] + session["duration"],
        ):
            scheduled_room_periods[
                (assignment["room_id"], assignment["day"], period)
            ].add(assignment["session_id"])

    decisions = schedule.get("fest_week", {}).get("bookings", [])
    if not isinstance(decisions, list):
        raise ValueError("Official schedule booking decisions must be a list.")
    if any(not isinstance(decision, dict) for decision in decisions):
        raise ValueError("Official schedule contains an invalid booking decision.")
    status_counts = defaultdict(int)
    for decision in decisions:
        status_counts[decision.get("status", "unknown")] += 1

    approved = []
    for decision in decisions:
        if decision.get("status") != "approved":
            continue
        request_id = decision.get("request_id")
        request = requests.get(request_id) if isinstance(request_id, str) else None
        room_id = decision.get("room_id")
        room = rooms.get(room_id) if isinstance(room_id, str) else None
        start = decision.get("start_period")
        end = decision.get("end_period")
        valid_days = set(data["calendar"]["days"])
        valid_periods = {
            period["id"]
            for period in data["calendar"]["periods"]
        }
        if (
            room is None
            or type(start) is not int
            or type(end) is not int
            or end < start
            or not isinstance(decision.get("day"), str)
            or decision.get("day") not in valid_days
            or not set(range(start, end + 1)).issubset(valid_periods)
        ):
            raise ValueError(
                "Approved booking has invalid room, day, or period data: "
                f"{decision.get('request_id')}."
            )
        if request is None:
            raise ValueError(
                "Approved booking references an unknown request: "
                f"{decision.get('request_id')}."
            )
        periods = list(range(start, end + 1))
        conflicts = []
        teaching_overlap = sorted({
            session_id
            for period in periods
            for session_id in scheduled_room_periods.get(
                (room["id"], decision.get("day"), period), set()
            )
        })
        if teaching_overlap:
            conflicts.append({
                "type": "scheduled_teaching",
                "session_ids": teaching_overlap,
                "periods": [
                    period
                    for period in periods
                    if scheduled_room_periods.get(
                        (room["id"], decision.get("day"), period)
                    )
                ],
            })
        maintenance_overlap = [
            {
                "period": period,
                "reason": closures[(room["id"], decision.get("day"), period)],
            }
            for period in periods
            if (room["id"], decision.get("day"), period) in closures
        ]
        if maintenance_overlap:
            conflicts.append({
                "type": "maintenance",
                "periods": maintenance_overlap,
            })
        approved.append({
            "request_id": decision["request_id"],
            "status": "approved",
            "title": request.get("title", request_id),
            "requester": request.get("requester", ""),
            "room_id": room["id"],
            "room_name": room["name"],
            "day": decision["day"],
            "start_period": start,
            "end_period": end,
            "conflicts": conflicts,
        })

    booking_by_id = {booking["request_id"]: booking for booking in approved}
    event_occupancy = defaultdict(set)
    for booking in approved:
        for period in range(
            booking["start_period"],
            booking["end_period"] + 1,
        ):
            event_occupancy[
                (booking["room_id"], booking["day"], period)
            ].add(booking["request_id"])
    for (room_id, day, period), request_ids in event_occupancy.items():
        if len(request_ids) < 2:
            continue
        for request_id in request_ids:
            booking_by_id[request_id]["conflicts"].append({
                "type": "approved_booking",
                "period": period,
                "request_ids": sorted(request_ids - {request_id}),
            })
    return approved, dict(status_counts)


def build_timetable_data(
    data,
    official_schedule,
    crisis_schedule=None,
    crisis_schedule_error=None,
    crisis_data=None,
):
    official_errors = _schedule_errors(official_schedule, data)
    if official_errors:
        raise ValueError(
            "Official timetable is incompatible with the supplied dataset: "
            + " ".join(official_errors)
        )

    baseline_by_id = {
        assignment["session_id"]: assignment
        for assignment in official_schedule["sessions"]
    }
    baseline_sessions = _enrich_assignments(data, official_schedule)
    approved_bookings, booking_status_counts = _enrich_approved_bookings(
        data,
        official_schedule,
        official_schedule["sessions"],
    )

    revised = {
        "available": False,
        "reason": "No compatible crisis timetable is available.",
        "sessions": [],
    }
    if crisis_schedule is None:
        revised["reason"] = (
            crisis_schedule_error or "No crisis timetable export is available."
        )
    else:
        effective_crisis_data = crisis_data or data
        crisis_errors = _schedule_errors(crisis_schedule, effective_crisis_data)
        if crisis_errors:
            revised["reason"] = (
                "Crisis timetable was not shown because it does not match "
                "the supplied dataset: " + " ".join(crisis_errors)
            )
        else:
            try:
                revised_bookings, _ = _enrich_approved_bookings(
                    effective_crisis_data,
                    crisis_schedule,
                    crisis_schedule["sessions"],
                )
                revised_sessions = _enrich_assignments(
                    effective_crisis_data,
                    crisis_schedule,
                    baseline_by_id,
                )
            except ValueError as error:
                revised["reason"] = (
                    "Crisis timetable was not shown because its booking "
                    f"data is incompatible: {error}"
                )
            else:
                revised_ids = {
                    assignment["session_id"]
                    for assignment in crisis_schedule["sessions"]
                }
                removed_sessions = []
                for session in baseline_sessions:
                    if session["session_id"] not in revised_ids:
                        removed = dict(session)
                        removed["change"] = {
                            "type": "REMOVED",
                            "original": {
                                "day": session["day"],
                                "start_period": session["start_period"],
                                "room_id": session["room_id"],
                            },
                        }
                        removed_sessions.append(removed)
                revised = {
                    "available": True,
                    "reason": None,
                    "sessions": revised_sessions,
                    "removed_sessions": removed_sessions,
                    "added_count": sum(
                        session["change"] is not None
                        and session["change"]["type"] == "ADDED"
                        for session in revised_sessions
                    ),
                    "changed_count": sum(
                        session["change"] is not None
                        and session["change"]["type"] != "ADDED"
                        for session in revised_sessions
                    ),
                    "approved_bookings": revised_bookings,
                }

    return {
        "dataset_id": data["dataset_id"],
        "days": data["calendar"]["days"],
        "periods": data["calendar"]["periods"],
        "student_groups": data["student_groups"],
        "faculty": data["faculty"],
        "rooms": data["rooms"],
        "room_unavailability": data.get("room_unavailability", []),
        "baseline_sessions": baseline_sessions,
        "revised_schedule": revised,
        "approved_bookings": approved_bookings,
        "booking_status_counts": booking_status_counts,
    }


def build_room_utilization(data, schedule):
    errors = _schedule_errors(schedule, data)
    if errors:
        raise ValueError(
            "Official timetable is incompatible with the supplied dataset: "
            + " ".join(errors)
        )

    days = data["calendar"]["days"]
    all_periods = [period["id"] for period in data["calendar"]["periods"]]
    teaching_periods = [
        period["id"]
        for period in data["calendar"]["periods"]
        if period.get("teaching")
    ]
    session_by_id = {session["id"]: session for session in data["sessions"]}
    closures = {
        (item["room_id"], item["day"], period)
        for item in data.get("room_unavailability", [])
        for period in item["periods"]
    }
    teaching_occupied = defaultdict(set)
    for assignment in schedule["sessions"]:
        session = session_by_id[assignment["session_id"]]
        for period in range(
            assignment["start_period"],
            assignment["start_period"] + session["duration"],
        ):
            teaching_occupied[assignment["room_id"]].add(
                (assignment["day"], period)
            )

    approved_bookings, _ = _enrich_approved_bookings(
        data,
        schedule,
        schedule["sessions"],
    )
    event_occupied = defaultdict(set)
    for booking in approved_bookings:
        for period in range(booking["start_period"], booking["end_period"] + 1):
            event_occupied[booking["room_id"]].add((booking["day"], period))

    room_metrics = []
    type_totals = defaultdict(lambda: {
        "room_count": 0,
        "occupied_periods": 0,
        "available_periods": 0,
    })
    total_teaching_occupied = 0
    total_teaching_available = 0
    total_event_occupied = 0
    total_event_available = 0
    available_teaching_capacity_seat_periods = 0

    for room in data["rooms"]:
        room_id = room["id"]
        teaching_available_slots = {
            (day, period)
            for day in days
            for period in teaching_periods
            if (room_id, day, period) not in closures
        }
        occupied_teaching_slots = (
            teaching_occupied[room_id] & teaching_available_slots
        )
        event_available_slots = {
            (day, period)
            for day in days
            for period in all_periods
            if (room_id, day, period) not in closures
        }
        occupied_event_slots = event_occupied[room_id] & event_available_slots
        free_teaching_slots = (
            teaching_available_slots - occupied_teaching_slots
        )
        available_teaching_capacity_seat_periods += (
            room["capacity"] * len(free_teaching_slots)
        )

        teaching_available_count = len(teaching_available_slots)
        teaching_occupied_count = len(occupied_teaching_slots)
        event_available_count = len(event_available_slots)
        event_occupied_count = len(occupied_event_slots)
        teaching_utilization = (
            100 * teaching_occupied_count / teaching_available_count
            if teaching_available_count
            else 0
        )
        event_utilization = (
            100 * event_occupied_count / event_available_count
            if event_available_count
            else 0
        )
        metric = {
            "room_id": room_id,
            "room_name": room["name"],
            "building": room["building"],
            "room_type": room["type"],
            "capacity": room["capacity"],
            "teaching_occupied_periods": teaching_occupied_count,
            "teaching_available_periods": teaching_available_count,
            "teaching_utilization_percent": round(teaching_utilization, 2),
            "event_occupied_periods": event_occupied_count,
            "event_available_periods": event_available_count,
            "event_utilization_percent": round(event_utilization, 2),
            "available_seat_periods": (
                room["capacity"] * len(free_teaching_slots)
            ),
        }
        room_metrics.append(metric)
        group = type_totals[room["type"]]
        group["room_count"] += 1
        group["occupied_periods"] += teaching_occupied_count
        group["available_periods"] += teaching_available_count
        total_teaching_occupied += teaching_occupied_count
        total_teaching_available += teaching_available_count
        total_event_occupied += event_occupied_count
        total_event_available += event_available_count

    room_metrics.sort(
        key=lambda item: (
            -item["teaching_utilization_percent"],
            item["room_id"],
        )
    )
    least_to_most = sorted(
        room_metrics,
        key=lambda item: (
            item["teaching_utilization_percent"],
            item["room_id"],
        ),
    )
    underutilized_count = math.ceil(len(least_to_most) / 4)
    underutilized = least_to_most[:underutilized_count]

    by_room_type = []
    for room_type, totals in sorted(type_totals.items()):
        by_room_type.append({
            "room_type": room_type,
            "room_count": totals["room_count"],
            "occupied_periods": totals["occupied_periods"],
            "available_periods": totals["available_periods"],
            "utilization_percent": round(
                100 * totals["occupied_periods"] / totals["available_periods"],
                2,
            ) if totals["available_periods"] else 0,
        })

    return {
        "dataset_id": data["dataset_id"],
        "teaching": {
            "occupied_periods": total_teaching_occupied,
            "available_periods": total_teaching_available,
            "utilization_percent": round(
                100 * total_teaching_occupied / total_teaching_available,
                2,
            ) if total_teaching_available else 0,
            "denominator": (
                "room teaching periods across Monday-Friday, excluding "
                "maintenance periods"
            ),
        },
        "approved_events": {
            "occupied_periods": total_event_occupied,
            "available_periods": total_event_available,
            "utilization_percent": round(
                100 * total_event_occupied / total_event_available,
                2,
            ) if total_event_available else 0,
            "denominator": (
                "all room calendar periods across Monday-Friday, excluding "
                "maintenance periods; approved bookings only"
            ),
        },
        "available_capacity_seat_periods": (
            available_teaching_capacity_seat_periods
        ),
        "underutilized_definition": (
            "Bottom quartile by room teaching utilization; "
            f"{underutilized_count} of {len(room_metrics)} rooms."
        ),
        "most_utilized_rooms": room_metrics[:5],
        "underutilized_rooms": underutilized,
        "by_room_type": by_room_type,
        "rooms": room_metrics,
    }


def _official_schedule(data):
    schedule = _read_schedule(OFFICIAL_SCHEDULE_PATH)
    errors = _schedule_errors(schedule, data)
    if errors:
        raise HTTPException(
            status_code=503,
            detail=(
                "Official timetable is incompatible with the supplied "
                "dataset: " + " ".join(errors)
            ),
        )
    return schedule


@router.get("/api/timetable")
def get_timetable(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    try:
        published = get_current_timetable()
    except WorkflowStoreUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail="Persistent timetable publication storage is unavailable.",
        ) from error
    if published is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "No approved timetable is available. An administrator must "
                "publish a validated baseline or approve a timetable proposal."
            ),
        )
    if published.get("decision") not in {"baseline", "approved"}:
        raise HTTPException(
            status_code=503,
            detail="The current timetable is not an approved publication.",
        )

    version_dataset = published.get("dataset_version_id")
    version_path = VERSIONS_ROOT / f"{version_dataset}.json" if version_dataset else None
    if not version_path or not version_path.is_file():
        raise HTTPException(
            status_code=503,
            detail="The dataset version associated with the published timetable is unavailable.",
        )
    try:
        with version_path.open("r", encoding="utf-8") as file:
            published_data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=503,
            detail="The dataset version associated with the published timetable is invalid or unavailable.",
        ) from error
    if (
        not isinstance(published_data, dict)
        or published_data.get("dataset_id") != published["dataset_id"]
    ):
        raise HTTPException(
            status_code=503,
            detail="Published timetable and dataset version do not match.",
        )
    if published["decision"] == "baseline":
        from app.api.workflows import _validate_initial_baseline

        _validate_initial_baseline(published["schedule"], version_path)
    return {
        "status": "success",
        "data": build_timetable_data(
            published_data,
            published["schedule"],
            None,
            "Crisis schedule comparisons are disabled for the database-published timetable.",
        ),
        "timetable_version": published["version_number"],
    }


@router.get("/api/analytics/room-utilization")
def get_room_utilization(
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    data = _published_schedule_data(load_dataset())
    schedule = _official_schedule(data)
    return {
        "status": "success",
        "data": build_room_utilization(data, schedule),
    }
