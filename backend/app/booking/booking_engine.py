
import json
from pathlib import Path

from app.services.dataset_loader import load_dataset


# Official validator booking limits and scoring weights
MAX_PERIOD = 13

CATEGORY_WEIGHTS = {
    "institute_event": 5,
    "guest_lecture": 4,
    "department_event": 3,
    "club_event": 2,
}


def project_root():
    return Path(__file__).resolve().parents[3]


def effective_category(data, request):
    """
    Apply the official role-category cap.
    A role cannot claim a category above its permitted authority.
    """

    order = data["priority_order"]

    cap = data["role_category_cap"].get(
        request["role"],
        "club_event"
    )

    requested = request["requested_category"]

    requested_index = (
        order.index(requested)
        if requested in order
        else len(order) - 1
    )

    cap_index = order.index(cap)

    return order[max(requested_index, cap_index)]


def request_priority(data, request):
    """
    Higher priority categories come first.
    Earlier submission time breaks ties.
    """

    category = effective_category(data, request)

    return (
        data["priority_order"].index(category),
        request["submitted_at"],
        request["id"],
    )


def load_base_occupancy(data, schedule=None):
    """
    Load regular timetable occupancy and room closures.
    """

    root = project_root()

    preview_path = (
        root / "exports" / "sessions_preview.json"
    )

    if not preview_path.exists():
        raise FileNotFoundError(
            "Regular timetable preview is missing. "
            "Generate the timetable first."
        )

    if schedule is None:
        preview = json.loads(
            preview_path.read_text(encoding="utf-8")
        )
    else:
        preview = schedule

    session_data = {
        session["id"]: session
        for session in data["sessions"]
    }

    room_busy = set()
    group_busy = set()

    # Regular timetable occupancy
    for assignment in preview["sessions"]:

        session = session_data.get(
            assignment["session_id"]
        )

        if not session:
            continue

        day = assignment["day"]
        start = assignment["start_period"]
        duration = session["duration"]
        room_id = assignment["room_id"]

        for period in range(start, start + duration):

            room_busy.add(
                (room_id, day, period)
            )

            for group_id in session["groups"]:

                group_busy.add(
                    (group_id, day, period)
                )

    # Explicit room closures
    room_unavailable = set()

    for closure in data.get(
        "room_unavailability", []
    ):

        for period in closure["periods"]:

            room_unavailable.add((
                closure["room_id"],
                closure["day"],
                period
            ))

    return (
        room_busy,
        group_busy,
        room_unavailable
    )


def valid_room(request, room):
    """
    Check whether a room satisfies the request's
    room type, capacity, event-use and feature requirements.
    """

    if "event" not in room.get("allowed_uses", []):
        return False

    if room["type"] not in request["room_types"]:
        return False

    if room["capacity"] < request["attendance"]:
        return False

    features = set(room.get("features", []))

    if room.get("accessible"):
        features.add("accessible")

    required_features = set(
        request.get("needs", [])
    )

    if not required_features.issubset(features):
        return False

    return True


def window_candidates(request):
    """
    Generate all valid start times inside requested windows.
    End period is inclusive.
    """

    candidates = []

    duration = request["duration_periods"]

    for window in request["windows"]:

        day = window["day"]

        first = window["from_period"]
        last = window["to_period"]

        latest_start = last - duration + 1

        for start in range(
            first,
            latest_start + 1
        ):

            end = start + duration - 1

            if end <= MAX_PERIOD:

                candidates.append({
                    "day": day,
                    "start_period": start,
                    "end_period": end,
                    "inside_window": True,
                })

    return candidates


def alternative_candidates(request, days):
    """
    Generate alternatives outside all requested windows.
    """

    candidates = []

    duration = request["duration_periods"]

    for day in days:

        latest_start = MAX_PERIOD - duration + 1

        for start in range(
            1,
            latest_start + 1
        ):

            end = start + duration - 1

            inside_any_window = any(
                window["day"] == day
                and window["from_period"] <= start
                and end <= window["to_period"]
                for window in request["windows"]
            )

            if inside_any_window:
                continue

            candidates.append({
                "day": day,
                "start_period": start,
                "end_period": end,
                "inside_window": False,
            })

    return candidates


def room_candidates(request, rooms):
    """
    Filter rooms and prefer the requested room.
    If no requested room is suitable, prefer the
    smallest suitable room that can accommodate attendance.
    """

    eligible = [
        room
        for room in rooms
        if valid_room(request, room)
    ]

    requested_room = request.get("requested_room")

    eligible.sort(
        key=lambda room: (
            0 if room["id"] == requested_room else 1,
            room["capacity"] - request["attendance"],
            room["id"],
        )
    )

    return eligible


def slot_is_available(
    request,
    room,
    candidate,
    room_busy,
    group_busy,
    room_unavailable,
):
    """
    Check all periods occupied by a booking.
    """

    room_id = room["id"]
    day = candidate["day"]

    for period in range(
        candidate["start_period"],
        candidate["end_period"] + 1
    ):

        # Regular class or previously accepted booking
        if (room_id, day, period) in room_busy:
            return False

        # Official room closure
        if (room_id, day, period) in room_unavailable:
            return False

        # Mandatory student group timetable conflict
        for group_id in request.get(
            "mandatory_for_groups", []
        ):

            if (group_id, day, period) in group_busy:
                return False

    return True


def reserve_slot(
    room,
    candidate,
    room_busy,
):
    """
    Reserve the room for the entire booking duration.
    """

    room_id = room["id"]
    day = candidate["day"]

    for period in range(
        candidate["start_period"],
        candidate["end_period"] + 1
    ):

        room_busy.add(
            (room_id, day, period)
        )


def make_decision(
    request,
    status,
    reason,
    room=None,
    candidate=None,
):
    """
    Create a booking decision in preview format.
    """

    result = {
        "request_id": request["id"],
        "status": status,
        "reason": reason,
    }

    if room is not None and candidate is not None:

        result.update({
            "room_id": room["id"],
            "day": candidate["day"],
            "start_period": candidate["start_period"],
            "end_period": candidate["end_period"],
        })

    return result


def generate_booking_schedule(data=None, schedule=None):

    if data is None:
        data = load_dataset()

    requests = data["booking_requests"]
    rooms = data["rooms"]

    # Official cancellations
    cancellations = {
        item["request_id"]
        for item in data.get("cancellations", [])
    }

    (
        room_busy,
        group_busy,
        room_unavailable
    ) = load_base_occupancy(data, schedule)

    # Collect all event days
    days = list(dict.fromkeys(
        window["day"]
        for request in requests
        for window in request["windows"]
    ))

    decisions = []

    # Process requests in official priority order
    ordered_requests = sorted(
        requests,
        key=lambda request: request_priority(
            data,
            request
        )
    )

    for request in ordered_requests:

        request_id = request["id"]

        # Cancelled requests cannot receive an alternative
        if request_id in cancellations:

            decisions.append(
                make_decision(
                    request,
                    "rejected",
                    "Request was officially cancelled."
                )
            )

            continue

        eligible_rooms = room_candidates(
            request,
            rooms
        )

        if not eligible_rooms:

            decisions.append(
                make_decision(
                    request,
                    "rejected",
                    "No venue satisfies the requested "
                    "room type, capacity and feature requirements."
                )
            )

            continue

        selected = None

        # ------------------------------------------------
        # PHASE 1: Try requested windows
        # ------------------------------------------------

        candidates = window_candidates(request)

        for candidate in candidates:

            for room in eligible_rooms:

                if slot_is_available(
                    request,
                    room,
                    candidate,
                    room_busy,
                    group_busy,
                    room_unavailable,
                ):

                    selected = (
                        room,
                        candidate
                    )

                    break

            if selected:
                break

        # Approve if requested window is available
        if selected:

            room, candidate = selected

            reserve_slot(
                room,
                candidate,
                room_busy
            )

            decisions.append(
                make_decision(
                    request,
                    "approved",
                    "Venue and time satisfy the request "
                    "and all checked constraints.",
                    room,
                    candidate,
                )
            )

            continue

        # ------------------------------------------------
        # PHASE 2: Search alternative times
        # ------------------------------------------------

        alternatives = alternative_candidates(
            request,
            days
        )

        selected = None

        for candidate in alternatives:

            for room in eligible_rooms:

                if slot_is_available(
                    request,
                    room,
                    candidate,
                    room_busy,
                    group_busy,
                    room_unavailable,
                ):

                    selected = (
                        room,
                        candidate
                    )

                    break

            if selected:
                break

        if selected:

            room, candidate = selected

            reserve_slot(
                room,
                candidate,
                room_busy
            )

            decisions.append(
                make_decision(
                    request,
                    "alternative_offered",
                    "The requested windows were unavailable. "
                    "An alternative time outside the requested "
                    "windows is offered for consideration.",
                    room,
                    candidate,
                )
            )

        else:

            decisions.append(
                make_decision(
                    request,
                    "rejected",
                    "No feasible venue and time combination "
                    "was found after checking requested "
                    "windows and alternative times."
                )
            )

    return decisions


def export_booking_preview(decisions):

    root = project_root()

    export_dir = root / "exports"

    export_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_path = (
        export_dir / "bookings_preview.json"
    )

    output_path.write_text(
        json.dumps(
            decisions,
            indent=2
        ),
        encoding="utf-8"
    )

    return output_path


def main():

    print("Generating Spectra venue bookings...")

    data = load_dataset()

    decisions = generate_booking_schedule(data)

    output_path = export_booking_preview(
        decisions
    )

    approved = sum(
        item["status"] == "approved"
        for item in decisions
    )

    alternatives = sum(
        item["status"] == "alternative_offered"
        for item in decisions
    )

    rejected = sum(
        item["status"] == "rejected"
        for item in decisions
    )

    print("\nBOOKING ENGINE COMPLETED")

    print("Total requests:", len(decisions))
    print("Approved:", approved)
    print("Alternatives:", alternatives)
    print("Rejected:", rejected)

    print("\nBooking decisions:")

    for item in decisions:

        print(
            item["request_id"],
            "|",
            item["status"],
            "|",
            item.get("room_id", "No room"),
            "|",
            item.get("day", "-"),
            "|",
            item.get("start_period", "-"),
            "-",
            item.get("end_period", "-"),
        )

    print("\nPreview exported:", output_path)


if __name__ == "__main__":
    main()
