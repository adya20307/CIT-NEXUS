from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import AuthenticatedPrincipal, require_roles
from app.booking.availability import (
    build_occupancy as _build_occupancy,
    read_official_schedule as _read_official_schedule,
    slot_conflict_reason as _slot_conflict_reason,
)
from app.booking.booking_engine import (
    alternative_candidates,
    room_candidates,
    slot_is_available,
    window_candidates,
)
from app.booking.natural_language import (
    local_date_today,
    parse_natural_language_booking,
)
from app.services.dataset_loader import load_dataset


router = APIRouter(prefix="/api/bookings", tags=["Venue Booking"])


class NaturalLanguageBookingRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


def _translated_message(language, status, slots, request):
    if language == "hinglish":
        if status == "available":
            if request.get("availability_query"):
                return (
                    "Available times neeche diye hain. Yeh sirf availability preview hai; "
                    "booking submit ya approve nahi hui hai."
                )
            first = slots[0]
            return (
                f"{first['room_name']} ({first['room_id']}) "
                f"{first['day']} ko P{first['start_period']}–P{first['end_period']} "
                "ke liye available lag raha hai. Yeh sirf availability preview hai; "
                "booking submit ya approve nahi hui hai."
            )
        if status == "alternative_available":
            return (
                f"{request['requested_room']} maange gaye time par available nahi hai, "
                "lekin suitable alternate room neeche diya hai. Booking submit ya approve nahi hui hai."
            )
        if status == "no_suitable_room":
            return (
                "Is capacity, room type aur zaroori facilities ke liye suitable "
                "room nahi mila. Booking approve ya submit nahi hui hai."
            )
        if request.get("suggested_alternatives"):
            return (
                "Maange gaye time par room available nahi hai. Usi weekday ke "
                "alternate times neeche diye hain. Booking submit ya approve nahi hui hai."
            )
        if slots:
            return (
                "Maange gaye time par room available nahi hai. Existing approved "
                "bookings, classes aur maintenance ko protect kiya gaya hai. "
                "Booking approve ya submit nahi hui hai."
            )
        return (
            "Is weekday aur duration ke liye free room nahi mila. "
            "Booking approve ya submit nahi hui hai."
        )

    if status == "available":
        if request.get("availability_query"):
            return (
                "Available times are listed below. This is an availability preview "
                "only; no booking was submitted or approved."
            )
        first = slots[0]
        return (
            f"{first['room_name']} ({first['room_id']}) appears available on "
            f"{first['day']} for periods {first['start_period']}–{first['end_period']}. "
            "This is an availability preview only; no booking was submitted or approved."
        )
    if status == "alternative_available":
        return (
            f"{request['requested_room']} is not available at the requested time, "
            "but a suitable alternative is listed below. No booking was submitted or approved."
        )
    if status == "no_suitable_room":
        return (
            "No room matches the requested capacity, room type, and features. "
            "No booking was submitted or approved."
        )
    if request.get("suggested_alternatives"):
        return (
            "No room is available at the requested time. Other available times "
            "on the same weekday are listed below. No booking was submitted or approved."
        )
    if slots:
        return (
            "No room is available at the requested time. Existing approved "
            "bookings, classes, and maintenance remain protected. "
            "No booking was submitted or approved."
        )
    return (
        "No room is free for that weekday and duration. "
        "No booking was submitted or approved."
    )


def _available_slots_for_candidates(
    data,
    request,
    rooms,
    candidates,
    room_busy,
    group_busy,
    room_unavailable,
):
    day_names = {
        "MON": "Monday",
        "TUE": "Tuesday",
        "WED": "Wednesday",
        "THU": "Thursday",
        "FRI": "Friday",
        "SAT": "Saturday",
        "SUN": "Sunday",
    }
    periods = {period["id"]: period for period in data["calendar"]["periods"]}
    available_slots = []
    blocked = []

    for candidate in candidates:
        for room in rooms:
            if slot_is_available(
                request,
                room,
                candidate,
                room_busy,
                group_busy,
                room_unavailable,
            ):
                available_slots.append({
                    "room_id": room["id"],
                    "room_name": room["name"],
                    "room_type": room["type"],
                    "capacity": room["capacity"],
                    "day": day_names[candidate["day"]],
                    "day_code": candidate["day"],
                    "date": request.get("date"),
                    "start_period": candidate["start_period"],
                    "end_period": candidate["end_period"],
                    "start_time": periods[candidate["start_period"]]["start"],
                    "end_time": periods[candidate["end_period"]]["end"],
                })
            else:
                reason = _slot_conflict_reason(
                    room,
                    candidate,
                    request.get("mandatory_for_groups", []),
                    room_busy,
                    group_busy,
                    room_unavailable,
                )
                blocked.append({
                    "room_id": room["id"],
                    "day": candidate["day"],
                    "start_period": candidate["start_period"],
                    "end_period": candidate["end_period"],
                    "reason": reason,
                })

    day_order = {
        day: index
        for index, day in enumerate(data["calendar"]["days"])
    }
    requested_room = request.get("requested_room")
    available_slots.sort(
        key=lambda item: (
            0 if item["room_id"] == requested_room else 1,
            day_order.get(item["day_code"], len(day_order)),
            item["start_period"],
            item["capacity"] - request["attendance"],
            item["room_id"],
        )
    )
    return available_slots, blocked


def check_booking_availability(data, schedule, parsed):
    request = parsed["structured_request"]
    room_busy, group_busy, room_unavailable = _build_occupancy(data, schedule)
    rooms = room_candidates(request, data["rooms"])
    if not rooms:
        result_status = "no_suitable_room"
        response = dict(parsed)
        response.update({
            "status": "availability_checked",
            "availability": {
                "status": result_status,
                "available_slots": [],
                "blocked": [],
            },
            "message": _translated_message(
                parsed["language"], result_status, [], request,
            ),
            "priority": {
                "existing_order": data["priority_order"],
                "existing_approved_bookings_preserved": True,
            },
        })
        return response

    candidates = window_candidates(request)
    available_slots, blocked = _available_slots_for_candidates(
        data,
        request,
        rooms,
        candidates,
        room_busy,
        group_busy,
        room_unavailable,
    )
    alternative_slots = []
    if not available_slots and request.get("windows"):
        requested_days = list(dict.fromkeys(
            window["day"] for window in request["windows"]
        ))
        alternative_slots, _ = _available_slots_for_candidates(
            data,
            request,
            rooms,
            alternative_candidates(request, requested_days),
            room_busy,
            group_busy,
            room_unavailable,
        )
        request["suggested_alternatives"] = alternative_slots[:5]
    requested_room_available = bool(
        request.get("requested_room")
        and any(
            slot["room_id"] == request["requested_room"]
            for slot in available_slots
        )
    )
    result_status = (
        "available"
        if available_slots and (
            not request.get("requested_room") or requested_room_available
        )
        else "alternative_available"
        if available_slots
        else "unavailable"
    )
    response = dict(parsed)
    response.update({
        "status": "availability_checked",
        "availability": {
            "status": result_status,
            "available_slots": available_slots[:40],
            "blocked": blocked[:40],
            "scope": (
                "Recurring weekday schedule for the supplied dataset. "
                "No absolute semester date mapping is present."
            ),
            "requested_room_available": (
                requested_room_available
                if request.get("requested_room")
                else None
            ),
        },
        "message": _translated_message(
            parsed["language"],
            result_status,
            available_slots,
            request,
        ),
        "priority": {
            "existing_order": data["priority_order"],
            "existing_approved_bookings_preserved": True,
            "candidate_requested_category": request.get("requested_category"),
            "note": (
                "Existing schedule decisions reflect the booking engine's "
                "priority rules. This preview does not grant or displace "
                "priority-ranked approvals."
            ),
        },
        "approval_status": "not_approved",
    })
    if request.get("requested_room"):
        response["requested_room"] = request["requested_room"]
        same_time_alternatives = [
            slot
            for slot in available_slots
            if slot["room_id"] != request["requested_room"]
        ][:5]
        if same_time_alternatives:
            response["suggested_alternatives"] = same_time_alternatives
        elif alternative_slots:
            response["suggested_alternatives"] = alternative_slots[:5]
    elif alternative_slots:
        response["suggested_alternatives"] = alternative_slots[:5]
    return response


@router.post("/interpret")
def interpret_natural_language_booking(
    body: NaturalLanguageBookingRequest,
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    data = load_dataset()
    parsed = parse_natural_language_booking(
        body.message,
        data,
        today=local_date_today(),
    )
    if parsed["status"] == "needs_clarification":
        return parsed

    schedule = _read_official_schedule(data)
    return check_booking_availability(data, schedule, parsed)
