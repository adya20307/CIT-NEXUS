import json
from pathlib import Path

from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule.json"


def read_official_schedule(data):
    try:
        with OFFICIAL_SCHEDULE_PATH.open("r", encoding="utf-8") as file:
            schedule = json.load(file)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail="Official schedule is not available for a booking check.",
        ) from error
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=503,
            detail="Official schedule is not valid JSON.",
        ) from error

    if not isinstance(schedule, dict) or schedule.get("dataset_id") != data["dataset_id"]:
        raise HTTPException(
            status_code=503,
            detail="Official schedule does not match the supplied dataset.",
        )
    assignments = schedule.get("sessions")
    if not isinstance(assignments, list):
        raise HTTPException(
            status_code=503,
            detail="Official schedule has no session assignments.",
        )
    return schedule


def build_occupancy(data, schedule):
    room_busy = set()
    group_busy = set()
    sessions = {session["id"]: session for session in data["sessions"]}
    room_ids = {room["id"] for room in data["rooms"]}
    days = set(data["calendar"]["days"])
    teaching_periods = {
        period["id"]
        for period in data["calendar"]["periods"]
        if period["teaching"]
    }
    assignments = schedule["sessions"]
    assignment_ids = []

    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise HTTPException(
                status_code=503,
                detail="Official schedule contains an invalid assignment.",
            )
        session_id = assignment.get("session_id")
        session = sessions.get(session_id) if isinstance(session_id, str) else None
        if session is None:
            raise HTTPException(
                status_code=503,
                detail=f"Official schedule references unknown session: {session_id}.",
            )
        assignment_ids.append(session_id)
        start = assignment.get("start_period")
        day = assignment.get("day")
        room_id = assignment.get("room_id")
        if (
            type(start) is not int
            or not isinstance(day, str)
            or day not in days
            or not isinstance(room_id, str)
            or room_id not in room_ids
        ):
            raise HTTPException(
                status_code=503,
                detail=f"Official schedule has an invalid placement for {session_id}.",
            )
        occupied = set(range(start, start + session["duration"]))
        if not occupied.issubset(teaching_periods):
            raise HTTPException(
                status_code=503,
                detail=f"Official schedule places {session_id} outside teaching periods.",
            )
        for period in range(start, start + session["duration"]):
            room_busy.add((room_id, day, period))
            for group_id in session["groups"]:
                group_busy.add((group_id, day, period))

    if (
        len(assignment_ids) != len(sessions)
        or len(set(assignment_ids)) != len(assignment_ids)
        or set(assignment_ids) != set(sessions)
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "Official schedule does not contain exactly one assignment "
                "per dataset session."
            ),
        )

    room_unavailable = {
        (closure["room_id"], closure["day"], period)
        for closure in data.get("room_unavailability", [])
        for period in closure["periods"]
    }

    decisions = schedule.get("fest_week", {}).get("bookings", [])
    if not isinstance(decisions, list):
        raise HTTPException(
            status_code=503,
            detail="Official schedule booking decisions are invalid.",
        )
    for decision in decisions:
        if not isinstance(decision, dict):
            raise HTTPException(
                status_code=503,
                detail="Official schedule contains an invalid booking decision.",
            )
        if decision.get("status") != "approved":
            continue
        if (
            decision.get("room_id") not in room_ids
            or decision.get("day") not in days
            or type(decision.get("start_period")) is not int
            or type(decision.get("end_period")) is not int
            or decision["end_period"] < decision["start_period"]
        ):
            raise HTTPException(
                status_code=503,
                detail="Official schedule contains an invalid approved booking.",
            )
        for period in range(decision["start_period"], decision["end_period"] + 1):
            room_busy.add((decision["room_id"], decision["day"], period))

    return room_busy, group_busy, room_unavailable


def slot_conflict_reason(room, candidate, group_ids, busy, groups_busy, closed):
    for period in range(candidate["start_period"], candidate["end_period"] + 1):
        key = (room["id"], candidate["day"], period)
        if key in closed:
            return "maintenance"
        if key in busy:
            return "scheduled_teaching_or_approved_booking"
        if any(
            (group_id, candidate["day"], period) in groups_busy
            for group_id in group_ids
        ):
            return "required_student_group_busy"
    return "unavailable"
