import json
import re
from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException

from app.api.crisis import compare_crisis_schedules, get_crisis_report
from app.api.timetable import get_room_utilization, get_timetable
from app.services.dataset_loader import get_dataset_summary, load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXAM_EXPORT_PATH = PROJECT_ROOT / "exports" / "exams_preview.json"


DOMAIN_KEYWORDS = {
    "timetable": (
        "timetable", "schedule", "class", "classes", "period", "periods",
        "session", "sessions", "free period", "course", "student group",
        "group", "lecture",
    ),
    "rooms": (
        "room", "rooms", "hall", "lab", "venue", "khali", "khaali",
        "capacity", "maintenance",
    ),
    "faculty": (
        "faculty", "professor", "teacher", "instructor", "unavailable",
        "faculty availability",
    ),
    "bookings": (
        "booking", "bookings", "reservation", "approved", "pending",
        "rejected", "priority", "book karo", "chahiye",
    ),
    "analytics": (
        "analytics", "utilization", "utilisation", "underutilized",
        "underutilised", "occupied percentage", "most utilized",
    ),
    "crisis": (
        "crisis", "what-if", "what if", "simulation", "disruption",
        "reschedule", "rescheduled", "moved session",
    ),
    "exams": (
        "exam", "exams", "examination", "midsem", "mid-sem",
    ),
}

FEATURE_GUIDE = [
    "Timetable: weekly student-group, faculty, and room views.",
    "Venue Booking: English/Hinglish availability preview; it does not submit or approve requests.",
    "Analytics: teaching and approved-event room utilization.",
    "Campus Crisis Mode: saved disruption results and a separate what-if simulator.",
    "Examinations: generated examination schedule and exports.",
]


def _contains_keyword(text, keyword):
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])",
        text,
    ))


def _query_text(message, history):
    previous_user_messages = [
        item["content"]
        for item in history
        if item["role"] == "user"
    ]
    return " ".join([*previous_user_messages, message]).lower()


def _requested_domains(text):
    return {
        domain
        for domain, keywords in DOMAIN_KEYWORDS.items()
        if any(_contains_keyword(text, keyword) for keyword in keywords)
    }


def _matches(text, identifier, label=""):
    normalized_text = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    for value in (identifier, label):
        if not value:
            continue
        normalized_value = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        if normalized_value and f" {normalized_value} " in normalized_text:
            return True
    return False


def _source_unavailable(reason):
    return {
        "status": "unavailable",
        "reason": reason,
    }


def _call_source(source_function):
    try:
        result = source_function()
    except (HTTPException, FileNotFoundError, ValueError):
        return None
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    return result.get("data")


def _build_room_context(data, timetable, text):
    room_matches = [
        room
        for room in data["rooms"]
        if _matches(text, room["id"], room["name"])
    ]
    selected_rooms = room_matches or data["rooms"]
    selected_ids = {room["id"] for room in selected_rooms}
    occupied = defaultdict(lambda: defaultdict(set))
    for session in timetable["baseline_sessions"]:
        if session["room_id"] not in selected_ids:
            continue
        for period in range(session["start_period"], session["end_period"] + 1):
            occupied[session["room_id"]][session["day"]].add(period)
    for booking in timetable["approved_bookings"]:
        if booking["room_id"] not in selected_ids:
            continue
        for period in range(booking["start_period"], booking["end_period"] + 1):
            occupied[booking["room_id"]][booking["day"]].add(period)

    return {
        "rooms": [
            {
                "room_id": room["id"],
                "name": room["name"],
                "building": room["building"],
                "type": room["type"],
                "capacity": room["capacity"],
                "features": room["features"],
                "accessible": room.get("accessible", False),
                "allowed_uses": room.get("allowed_uses", []),
                "occupied_periods_by_day": {
                    day: sorted(occupied[room["id"]].get(day, set()))
                    for day in data["calendar"]["days"]
                },
                "maintenance": [
                    {
                        "day": closure["day"],
                        "periods": closure["periods"],
                    }
                    for closure in timetable["room_unavailability"]
                    if closure["room_id"] == room["id"]
                ],
            }
            for room in selected_rooms
        ],
        "periods": data["calendar"]["periods"],
        "days": data["calendar"]["days"],
        "date_mapping_note": (
            "The schedule is a weekday template; the dataset does not map "
            "weekdays to semester calendar dates."
        ),
    }


def _build_faculty_context(data, timetable, text):
    faculty_matches = [
        member
        for member in data["faculty"]
        if _matches(text, member["id"], member["name"])
        or _matches(text, member["department"])
    ]
    schedule = timetable["baseline_sessions"]
    return {
        "faculty_directory": [
            {
                "faculty_id": member["id"],
                "name": member["name"],
                "department": member["department"],
            }
            for member in (faculty_matches or data["faculty"])
        ],
        "matching_faculty_schedules": [
            {
                "faculty_id": member["id"],
                "name": member["name"],
                "unavailable_periods": [
                    {
                        "day": item["day"],
                        "periods": item["periods"],
                    }
                    for item in member.get("unavailable", [])
                ],
                "scheduled_sessions": [
                    {
                        "session_id": session["session_id"],
                        "course_id": session["course_id"],
                        "course_title": session["course_title"],
                        "day": session["day"],
                        "start_period": session["start_period"],
                        "end_period": session["end_period"],
                        "room_id": session["room_id"],
                    }
                    for session in schedule
                    if session["faculty_id"] == member["id"]
                ],
            }
            for member in faculty_matches
        ],
        "schedule_scope": "Weekly Monday-Friday dataset template.",
    }


def _build_timetable_context(data, timetable, text):
    groups = [
        group
        for group in data["student_groups"]
        if _matches(text, group["id"], group["name"])
        or _matches(text, group["department"])
    ]
    sessions = timetable["baseline_sessions"]
    truncated = False
    if groups:
        group_ids = {group["id"] for group in groups}
        sessions = [
            session
            for session in sessions
            if group_ids.intersection(session["group_ids"])
        ]
    else:
        query_terms = [
            token for token in re.findall(r"[a-z0-9-]+", text)
            if len(token) > 2
        ]
        relevant = [
            session
            for session in sessions
            if any(
                term in " ".join((
                    session["session_id"],
                    session["course_id"],
                    session["course_title"],
                    session["faculty_name"],
                    session["room_id"],
                )).lower()
                for term in query_terms
            )
        ]
        if relevant:
           sessions = relevant
        else:
           truncated = len(sessions) > 12
           sessions = sessions[:12]
    truncated = truncated or len(sessions) > 35

    return {
        "days": data["calendar"]["days"],
        "periods": data["calendar"]["periods"],
        "student_groups": [
            {"id": group["id"], "name": group["name"], "size": group["size"]}
            for group in (groups or data["student_groups"])
        ],
        "matching_sessions": sessions[:35],
        "sessions_truncated": truncated,
    }


def _build_booking_context(data, timetable):
    return {
        "status_counts": timetable["booking_status_counts"],
        "approved_bookings": [
            {
                key: booking[key]
                for key in (
                    "request_id",
                    "status",
                    "title",
                    "room_id",
                    "room_name",
                    "day",
                    "start_period",
                    "end_period",
                )
                if key in booking
            }
            for booking in timetable["approved_bookings"]
        ],
        "priority_order": data.get("priority_order", []),
        "role_category_caps": data.get("role_category_cap", {}),
        "policy": (
            "Only status=approved is a confirmed reservation. A preview is not "
            "a submission or approval; priority rules are applied by the existing "
            "booking workflow."
        ),
    }


def _build_analytics_context(text):
    analytics = _call_source(get_room_utilization)
    if analytics is None:
        return _source_unavailable("Room utilization data is unavailable.")

    matching_rooms = [
        room
        for room in analytics["rooms"]
        if _matches(text, room["room_id"], room["room_name"])
    ]
    return {
        "teaching": analytics["teaching"],
        "approved_events": analytics["approved_events"],
        "available_capacity_seat_periods": analytics[
            "available_capacity_seat_periods"
        ],
        "underutilized_definition": analytics["underutilized_definition"],
        "most_utilized_rooms": analytics["most_utilized_rooms"],
        "underutilized_rooms": analytics["underutilized_rooms"],
        "by_room_type": analytics["by_room_type"],
        "matching_rooms": matching_rooms,
    }


def _build_crisis_context(timetable):
    revised = timetable["revised_schedule"]
    if revised.get("available"):
        changed_sessions = [
            {
                key: session[key]
                for key in (
                    "session_id",
                    "course_id",
                    "course_title",
                    "faculty_name",
                    "room_id",
                    "day",
                    "start_period",
                    "end_period",
                    "change",
                )
                if key in session
            }
            for session in revised.get("sessions", [])
            if session.get("change")
        ][:8]
        saved_schedule = {
            "available": True,
            "changed_count": revised.get("changed_count", 0),
            "added_count": revised.get("added_count", 0),
            "removed_count": revised.get("removed_sessions", 0),
            "reason": revised.get("reason"),
            "changed_session_examples": changed_sessions,
        }
    else:
        saved_schedule = {
            "available": False,
            "reason": revised.get("reason"),
        }
    context = {
        "saved_revised_schedule": saved_schedule,
        "simulations_are_read_only": True,
        "what_if_guidance": (
            "A chat response does not run simulations. Use Campus Crisis Mode "
            "to run What-If Simulator; it previews changes and does not replace "
            "the official schedule."
        ),
    }
    try:
        report = get_crisis_report()
    except HTTPException:
        context["validation_report"] = _source_unavailable(
            "No saved Crisis Mode validation report is available."
        )
    else:
        report_data = report.get("report", {})
        context["validation_report"] = {
            key: report_data[key]
            for key in (
                "solver_status",
                "hard_constraints_satisfied",
                "hard_violations",
                "soft_total",
            )
            if key in report_data
        }

    try:
        comparison = compare_crisis_schedules()
    except HTTPException:
        context["schedule_comparison"] = _source_unavailable(
            "No saved Crisis Mode schedule comparison is available."
        )
    else:
        context["schedule_comparison"] = {
            "summary": comparison.get("summary"),
            "moved_sessions": [
                {
                    key: moved[key]
                    for key in (
                        "session_id",
                        "original",
                        "revised",
                        "change_type",
                        "explanation_type",
                        "confidence",
                        "explanation",
                    )
                    if key in moved
                }
                for moved in comparison.get("moved", [])[:8]
            ],
        }
    return context


def _build_exam_context(data):
    try:
        with EXAM_EXPORT_PATH.open("r", encoding="utf-8") as file:
            assignments = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return _source_unavailable(
            "No saved examination timetable export is available."
        )
    if not isinstance(assignments, list):
        return _source_unavailable(
            "The saved examination timetable export is invalid."
        )

    exams = {exam["id"]: exam for exam in data["exams"]}
    exam_sessions = {
        item["id"]: item
        for item in data["calendar"]["exam_week"]["sessions"]
    }
    courses = {course["id"]: course["title"] for course in data["courses"]}
    rooms = {room["id"] for room in data["rooms"]}
    days = set(data["calendar"]["exam_week"]["days"])
    normalized = []
    seen = set()
    for assignment in assignments:
        if not isinstance(assignment, dict):
            return _source_unavailable(
                "The saved examination timetable export is invalid."
            )
        exam_id = assignment.get("exam_id")
        session_id = assignment.get("session")
        room_ids = assignment.get("rooms")
        if (
            not isinstance(exam_id, str)
            or not isinstance(session_id, str)
            or not isinstance(assignment.get("day"), str)
            or not isinstance(room_ids, list)
            or any(not isinstance(room_id, str) for room_id in room_ids)
        ):
            return _source_unavailable(
                "The saved examination timetable export is invalid."
            )
        exam = exams.get(exam_id)
        if (
            exam is None
            or exam_id in seen
            or assignment["day"] not in days
            or session_id not in exam_sessions
            or not set(room_ids).issubset(rooms)
        ):
            return _source_unavailable(
                "The saved examination timetable does not match the dataset."
            )
        seen.add(exam_id)
        session = exam_sessions[session_id]
        normalized.append({
            "exam_id": exam_id,
            "day": assignment["day"],
            "session": session_id,
            "start_time": session["start"],
            "end_time": session["end"],
            "courses": [
                {
                    "course_id": course_id,
                    "title": courses.get(course_id, course_id),
                }
                for course_id in exam["course_ids"]
            ],
            "student_groups": exam["groups"],
            "student_count": exam["size"],
            "rooms": room_ids,
        })
    if seen != set(exams):
        return _source_unavailable(
            "The saved examination timetable is incomplete for this dataset."
        )

    return {
        "assignments": normalized,
        "source": "Validated output from the existing examination scheduler.",
    }


def build_campus_context(message, history):
    query = _query_text(message, history)
    domains = _requested_domains(query)
    context = {
        "feature_guide": FEATURE_GUIDE,
        "sources": {},
        "data": {},
    }

    try:
        data = load_dataset()
        dataset_summary = get_dataset_summary()
    except (FileNotFoundError, ValueError):
        if not domains:
            context["data"]["campus_data"] = {
                "status": "not_requested",
                "note": "No campus data domain was identified in the question.",
            }
            return context
        for domain in domains:
            context["sources"][domain] = "unavailable"
            context["data"][domain] = _source_unavailable(
                "The official campus dataset is unavailable."
            )
        return context

    faculty_entities = [
        item
        for item in data["faculty"]
        if _matches(query, item["id"], item["name"])
    ]
    room_entities = [
        item
        for item in data["rooms"]
        if _matches(query, item["id"], item["name"])
    ]
    if faculty_entities:
        domains.update({"faculty", "timetable"})
    if room_entities:
        domains.add("rooms")
    if (
        re.search(r"\b(?:availability|available|free|khali|khaali)\b", query)
        and not re.search(r"\bfree\s+periods?\b", query)
        and not re.search(r"\b(?:faculty|professor|teacher|instructor)\b", query)
        and not faculty_entities
    ):
        domains.add("rooms")
    if any(_matches(query, item["id"], item["name"]) for item in data["student_groups"]):
        domains.add("timetable")
    if any(_matches(query, item["id"], item["title"]) for item in data["courses"]):
        domains.add("timetable")
    if not domains:
        context["data"]["campus_data"] = {
            "status": "not_requested",
            "note": "No campus data domain was identified in the question.",
        }
        return context

    context["data"]["dataset"] = dataset_summary
    needs_timetable = bool(
        domains.intersection({"timetable", "rooms", "faculty", "bookings", "crisis"})
    )
    timetable = None
    if needs_timetable:
        timetable = _call_source(get_timetable)
        context["sources"]["timetable"] = (
            "available" if timetable is not None else "unavailable"
        )

    if "timetable" in domains:
        if timetable is None:
            context["data"]["timetable"] = _source_unavailable(
                "The official timetable is unavailable."
            )
        else:
            context["data"]["timetable"] = _build_timetable_context(
                data,
                timetable,
                query,
            )

    if "rooms" in domains:
        if timetable is None:
            context["data"]["rooms"] = _source_unavailable(
                "The official room schedule is unavailable."
            )
        else:
            context["data"]["rooms"] = _build_room_context(
                data,
                timetable,
                query,
            )

    if "faculty" in domains:
        if timetable is None:
            context["data"]["faculty"] = _source_unavailable(
                "The official faculty timetable is unavailable."
            )
        else:
            context["data"]["faculty"] = _build_faculty_context(
                data,
                timetable,
                query,
            )

    if "bookings" in domains:
        if timetable is None:
            context["data"]["bookings"] = _source_unavailable(
                "Official venue booking decisions are unavailable."
            )
        else:
            context["data"]["bookings"] = _build_booking_context(data, timetable)

    if "analytics" in domains:
        analytics = _build_analytics_context(query)
        context["data"]["analytics"] = analytics
        context["sources"]["analytics"] = analytics["status"] if (
            analytics.get("status") == "unavailable"
        ) else "available"

    if "crisis" in domains:
        if timetable is None:
            context["data"]["crisis"] = _source_unavailable(
                "Crisis Mode schedule data is unavailable."
            )
        else:
            context["data"]["crisis"] = _build_crisis_context(timetable)

    if "exams" in domains:
        exam_context = _build_exam_context(data)
        context["data"]["exams"] = exam_context
        context["sources"]["exams"] = (
            "unavailable"
            if exam_context.get("status") == "unavailable"
            else "available"
        )

    for domain in domains:
        if domain not in context["sources"]:
            context["sources"][domain] = (
                context["sources"].get("timetable", "available")
            )
    return context


def serialize_campus_context(context):
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))
