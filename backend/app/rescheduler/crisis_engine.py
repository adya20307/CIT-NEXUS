
import json
import time
from pathlib import Path
from copy import deepcopy
from collections import defaultdict

from ortools.sat.python import cp_model

from app.services.dataset_loader import load_dataset
from app.booking.booking_engine import generate_booking_schedule


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = PROJECT_ROOT / "exports"

BASELINE_PATH = EXPORT_DIR / "schedule_baseline_98.json"

DISRUPTION_PATH = (
    PROJECT_ROOT
    / "starter-pack"
    / "CrunchWeek_StarterPack"
    / "examples"
    / "sample_disruption.json"
)

OUTPUT_PATH = EXPORT_DIR / "schedule_after.json"
EXPLANATIONS_PATH = EXPORT_DIR / "crisis_explanations.json"


def _lunch_after_period(calendar):
    return calendar.get(
        "lunch", {}
    ).get("after_period", calendar.get("lunch_after_period", 5))


def _session_needs_accessible_room(session, data):
    faculty = next(
        (
            member
            for member in data["faculty"]
            if member["id"] == session["faculty_id"]
        ),
        {},
    )
    groups = {
        group["id"]: group
        for group in data["student_groups"]
    }
    return (
        session.get("needs_accessible", False)
        or faculty.get("needs_accessible", False)
        or any(
            groups.get(group_id, {}).get("needs_accessible", False)
            for group_id in session.get("groups", [])
        )
    )


# ============================================================
# JSON HELPERS
# ============================================================

def read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


def placement_hard_constraint_evidence(
    session,
    placement,
    data,
    teaching_periods,
):
    evidence = []
    session_id = session["id"]
    day = placement["day"]
    start = placement["start_period"]
    room_id = placement["room_id"]
    duration = session["duration"]
    occupied = list(range(start, start + duration))
    calendar_days = data["calendar"]["days"]
    rooms = {room["id"]: room for room in data["rooms"]}
    faculty = {
        item["id"]: item
        for item in data["faculty"]
    }.get(session["faculty_id"])

    def add(constraint, detail, **values):
        evidence.append({
            "constraint": constraint,
            "session_id": session_id,
            "detail": detail,
            **values,
        })

    if day not in calendar_days:
        add("valid_teaching_day", f"{day} is not a teaching day.")

    if not all(period in teaching_periods for period in occupied):
        add(
            "teaching_periods",
            f"The placement occupies non-teaching periods {occupied}.",
            periods=occupied,
        )

    lunch = _lunch_after_period(data["calendar"])
    if start <= lunch < start + duration - 1:
        add(
            "lunch_boundary",
            f"The placement crosses lunch after period {lunch}.",
            periods=occupied,
        )

    if session["kind"] == "lab" and start not in [2, 6]:
        add(
            "lab_start_period",
            f"A lab must start at period 2 or 6, not period {start}.",
            period=start,
        )

    fixed = session.get("fixed_slot")
    if fixed and (
        fixed["day"] != day
        or fixed["start_period"] != start
    ):
        add(
            "fixed_slot",
            f"The session is fixed at {fixed['day']} period "
            f"{fixed['start_period']}.",
            fixed_slot=fixed,
        )

    if faculty is None:
        add(
            "known_faculty",
            f"Faculty {session['faculty_id']} is not present in the dataset.",
            faculty_id=session["faculty_id"],
        )
    else:
        for unavailable in faculty.get("unavailable", []):
            overlap = sorted(
                set(occupied).intersection(unavailable["periods"])
            )
            if unavailable["day"] == day and overlap:
                add(
                    "faculty_unavailability",
                    f"{faculty['id']} is unavailable on {day} during "
                    f"periods {overlap}.",
                    faculty_id=faculty["id"],
                    day=day,
                    periods=overlap,
                    reason=unavailable.get("reason", ""),
                )

    room = rooms.get(room_id)
    if room is None:
        add(
            "known_room",
            f"Room {room_id} is not present in the dataset.",
            room_id=room_id,
        )
    else:
        if room["type"] not in session["room_types"]:
            add(
                "room_type",
                f"Room type {room['type']} is not allowed for this session.",
                room_id=room_id,
                room_type=room["type"],
                allowed_room_types=session["room_types"],
            )
        if room["capacity"] < session["size"]:
            add(
                "room_capacity",
                f"Room capacity {room['capacity']} is below session size "
                f"{session['size']}.",
                room_id=room_id,
                capacity=room["capacity"],
                session_size=session["size"],
            )
        if session["kind"] not in room.get("allowed_uses", []):
            add(
                "room_allowed_use",
                f"Room {room_id} does not allow {session['kind']} sessions.",
                room_id=room_id,
                session_kind=session["kind"],
            )
        if _session_needs_accessible_room(session, data) and not room.get("accessible", False):
            add(
                "accessible_room",
                f"Room {room_id} is not accessible.",
                room_id=room_id,
            )

    for unavailable in data.get("room_unavailability", []):
        overlap = sorted(
            set(occupied).intersection(unavailable["periods"])
        )
        if unavailable["room_id"] == room_id and unavailable["day"] == day and overlap:
            add(
                "room_unavailability",
                f"Room {room_id} is unavailable on {day} during "
                f"periods {overlap}.",
                room_id=room_id,
                day=day,
                periods=overlap,
                reason=unavailable.get("reason", ""),
            )

    return evidence


def counterfactual_conflicts(
    session,
    placement,
    revised_sessions,
    session_map,
    faculty_map,
):
    conflicts = []
    session_id = session["id"]
    day = placement["day"]
    start = placement["start_period"]
    end = start + session["duration"]
    basket_id = session.get("basket_slot")
    faculty_day_hours = session["duration"]
    basket_representatives = {}
    for other_session in session_map.values():
        other_basket = other_session.get("basket_slot")
        if other_basket and other_basket not in basket_representatives:
            basket_representatives[other_basket] = other_session["id"]

    for other_placement in revised_sessions:
        other_id = other_placement["session_id"]
        if other_id == session_id:
            continue

        other = session_map.get(other_id)
        if other is None:
            continue

        other_start = other_placement["start_period"]
        same_basket = (
            basket_id is not None
            and other.get("basket_slot") == basket_id
        )

        if (
            same_basket
            and (
                other_placement["day"] != day
                or other_start != start
            )
        ):
            conflicts.append({
                "constraint": "basket_synchronization",
                "other_session_id": other_id,
                "detail": f"Basket {basket_id} must be synchronized "
                f"with {other_id}.",
            })

        if other_placement["day"] != day:
            continue

        other_end = other_start + other["duration"]
        overlaps = start < other_end and other_start < end

        if (
            overlaps
            and placement["room_id"] == other_placement["room_id"]
        ):
            conflicts.append({
                "constraint": "room_non_overlap",
                "other_session_id": other_id,
                "detail": f"Room {placement['room_id']} overlaps "
                f"{other_id} in the revised schedule.",
            })

        if overlaps and session["faculty_id"] == other["faculty_id"]:
            conflicts.append({
                "constraint": "faculty_non_overlap",
                "other_session_id": other_id,
                "detail": f"Faculty {session['faculty_id']} overlaps "
                f"{other_id} in the revised schedule.",
            })

        if (
            overlaps
            and not same_basket
            and (
                not other.get("basket_slot")
                or basket_representatives.get(
                    other["basket_slot"]
                ) == other_id
            )
            and set(session["groups"]).intersection(other["groups"])
        ):
            conflicts.append({
                "constraint": "student_group_non_overlap",
                "other_session_id": other_id,
                "detail": f"Student groups overlap with {other_id} "
                f"in the revised schedule.",
            })

        if other["faculty_id"] == session["faculty_id"]:
            faculty_day_hours += other["duration"]

    maximum = faculty_map.get(session["faculty_id"], {}).get(
        "max_hours_per_day", 4
    )
    if faculty_day_hours > maximum:
        conflicts.append({
            "constraint": "faculty_daily_maximum",
            "faculty_id": session["faculty_id"],
            "day": day,
            "hours": faculty_day_hours,
            "maximum": maximum,
            "detail": f"Faculty {session['faculty_id']} would have "
            f"{faculty_day_hours} hours on {day}, above the maximum "
            f"of {maximum}.",
        })

    return conflicts


# ============================================================
# APPLY DISRUPTION
# ============================================================

def apply_disruption(data, disruption):
    updated_data = deepcopy(data)

    faculty_map = {
        faculty["id"]: faculty
        for faculty in updated_data["faculty"]
    }

    updated_data.setdefault("room_unavailability", [])

    for change in disruption.get("changes", []):
        change_type = change.get("type")

        if change_type == "faculty_unavailable":
            faculty_id = change["faculty_id"]

            if faculty_id not in faculty_map:
                raise ValueError(f"Unknown faculty: {faculty_id}")

            faculty = faculty_map[faculty_id]

            for day in change["days"]:
                faculty.setdefault("unavailable", []).append({
                    "day": day,
                    "periods": change["periods"],
                    "reason": change.get(
                        "reason", "Crisis disruption"
                    )
                })

        elif change_type == "room_unavailable":
            for day in change["days"]:
                updated_data["room_unavailability"].append({
                    "room_id": change["room_id"],
                    "day": day,
                    "periods": change["periods"],
                    "reason": change.get(
                        "reason", "Crisis disruption"
                    )
                })

        elif change_type == "add_session":
            updated_data["sessions"].append(
                deepcopy(change["session"])
            )

        elif change_type == "remove_session":
            session_id = change["session_id"]

            updated_data["sessions"] = [
                session
                for session in updated_data["sessions"]
                if session["id"] != session_id
            ]

        elif change_type == "add_faculty":
            updated_data["faculty"].append(
                deepcopy(change["faculty"])
            )

        else:
            raise ValueError(
                f"Unsupported disruption type: {change_type}"
            )

    return updated_data


# ============================================================
# DETERMINE WHICH SESSIONS MUST MOVE
# ============================================================

def identify_affected_sessions(updated_data, baseline):
    baseline_map = {
        item["session_id"]: item
        for item in baseline["sessions"]
    }

    session_map = {
        session["id"]: session
        for session in updated_data["sessions"]
    }

    room_unavailable = defaultdict(set)
    faculty_unavailable = defaultdict(set)

    for item in updated_data.get("room_unavailability", []):
        for period in item["periods"]:
            room_unavailable[item["room_id"]].add(
                (item["day"], period)
            )

    for faculty in updated_data["faculty"]:
        for item in faculty.get("unavailable", []):
            for period in item["periods"]:
                faculty_unavailable[faculty["id"]].add(
                    (item["day"], period)
                )

    affected = set()

    for session_id, placement in baseline_map.items():
        if session_id not in session_map:
            continue

        session = session_map[session_id]

        start = placement["start_period"]
        end = start + session["duration"] - 1
        occupied = range(start, end + 1)
        day = placement["day"]

        room_id = placement["room_id"]
        faculty_id = session["faculty_id"]

        room_conflict = any(
            (day, period) in room_unavailable[room_id]
            for period in occupied
        )

        faculty_conflict = any(
            (day, period) in faculty_unavailable[faculty_id]
            for period in occupied
        )

        if room_conflict or faculty_conflict:
            affected.add(session_id)

    # New sessions have no baseline placement.
    for session_id in session_map:
        if session_id not in baseline_map:
            affected.add(session_id)

    # If a basket is affected, move its entire basket.
    basket_members = defaultdict(list)

    for session in updated_data["sessions"]:
        basket_id = session.get("basket_slot")

        if basket_id:
            basket_members[basket_id].append(session["id"])

    for basket_id, members in basket_members.items():
        if affected.intersection(members):
            affected.update(members)

    return affected


# ============================================================
# PARTIAL CRISIS SCHEDULER
# ============================================================

def generate_crisis_schedule(
    updated_data,
    baseline,
    return_solver_evidence=False,
):

    model = cp_model.CpModel()

    sessions = updated_data["sessions"]
    rooms = updated_data["rooms"]
    days = updated_data["calendar"]["days"]

    teaching_periods = {
        period["id"]
        for period in updated_data["calendar"]["periods"]
        if period["teaching"]
    }

    baseline_map = {
        item["session_id"]: item
        for item in baseline["sessions"]
    }

    session_map = {
        session["id"]: session
        for session in sessions
    }

    faculty_map = {
        faculty["id"]: faculty
        for faculty in updated_data["faculty"]
    }

    affected_ids = identify_affected_sessions(
        updated_data, baseline
    )

    fixed_ids = set(session_map) - affected_ids

    print("\nPARTIAL RESCHEDULING DIAGNOSTICS")
    print("--------------------------------")
    print("Affected existing sessions:",
          len(affected_ids.intersection(baseline_map)))
    print("New sessions:",
          len(affected_ids - set(baseline_map)))
    print("Fixed unaffected sessions:", len(fixed_ids))

    # --------------------------------------------------------
    # Availability maps
    # --------------------------------------------------------

    room_unavailable = defaultdict(set)
    faculty_unavailable = defaultdict(set)

    for item in updated_data.get("room_unavailability", []):
        for period in item["periods"]:
            room_unavailable[item["room_id"]].add(
                (item["day"], period)
            )

    for faculty in updated_data["faculty"]:
        for item in faculty.get("unavailable", []):
            for period in item["periods"]:
                faculty_unavailable[faculty["id"]].add(
                    (item["day"], period)
                )

    # --------------------------------------------------------
    # Elective basket representatives
    # --------------------------------------------------------

    basket_sessions = defaultdict(list)

    for session in sessions:
        basket_id = session.get("basket_slot")

        if basket_id:
            basket_sessions[basket_id].append(session)

    basket_representatives = {
        basket_id: basket[0]["id"]
        for basket_id, basket in basket_sessions.items()
    }

    # --------------------------------------------------------
    # Occupancy and fixed sessions
    # --------------------------------------------------------

    intervals = defaultdict(list)
    fixed_daily_hours = defaultdict(int)

    print("Registering unaffected baseline sessions...")

    for sid in fixed_ids:
        session = session_map[sid]
        placement = baseline_map[sid]

        day = placement["day"]
        start = placement["start_period"]
        duration = session["duration"]
        room_id = placement["room_id"]
        faculty_id = session["faculty_id"]

        if room_id not in {room["id"] for room in rooms}:
            raise ValueError(
                f"Unknown baseline room {room_id} for {sid}"
            )

        if any(
            (day, period) in room_unavailable[room_id]
            for period in range(start, start + duration)
        ):
            raise ValueError(
                f"Affected session was incorrectly fixed: {sid}"
            )

        if any(
            (day, period) in faculty_unavailable[faculty_id]
            for period in range(start, start + duration)
        ):
            raise ValueError(
                f"Unavailable faculty session was incorrectly fixed: {sid}"
            )

        end = start + duration

        fixed_interval = model.NewFixedSizeIntervalVar(
            start,
            duration,
            f"fixed_{sid}"
        )

        intervals[("room", room_id, day)].append(fixed_interval)

        intervals[("faculty", faculty_id, day)].append(
            fixed_interval
        )

        fixed_daily_hours[(faculty_id, day)] += duration

        basket_id = session.get("basket_slot")

        include_group_interval = (
            basket_id is None
            or basket_representatives[basket_id] == sid
        )

        if include_group_interval:
            for group_id in session["groups"]:
                intervals[("group", group_id, day)].append(
                    fixed_interval
                )

    # --------------------------------------------------------
    # Generate choices only for affected sessions
    # --------------------------------------------------------

    choices = {}
    selected = defaultdict(list)

    print("Building choices for affected sessions...")

    room_ids = {room["id"] for room in rooms}

    for session in sessions:
        sid = session["id"]

        if sid not in affected_ids:
            continue

        duration = session["duration"]
        faculty_id = session["faculty_id"]
        needs_accessible = _session_needs_accessible_room(
            session,
            updated_data,
        )

        if faculty_id not in faculty_map:
            raise ValueError(
                f"Unknown faculty {faculty_id} for {sid}"
            )

        for day in days:
            for start in sorted(teaching_periods):

                end = start + duration - 1
                occupied = range(start, end + 1)

                if not all(
                    period in teaching_periods
                    for period in occupied
                ):
                    continue

                lunch = _lunch_after_period(updated_data["calendar"])

                if start <= lunch < end:
                    continue

                if (
                    session["kind"] == "lab"
                    and start not in [2, 6]
                ):
                    continue

                fixed = session.get("fixed_slot")

                if fixed and (
                    fixed["day"] != day
                    or fixed["start_period"] != start
                ):
                    continue

                if any(
                    (day, period) in faculty_unavailable[faculty_id]
                    for period in occupied
                ):
                    continue

                for room in rooms:
                    rid = room["id"]

                    if room["type"] not in session["room_types"]:
                        continue

                    if room["capacity"] < session["size"]:
                        continue

                    if session["kind"] not in room.get(
                        "allowed_uses", []
                    ):
                        continue

                    if (
                        needs_accessible
                        and not room.get("accessible", False)
                    ):
                        continue

                    if any(
                        (day, period) in room_unavailable[rid]
                        for period in occupied
                    ):
                        continue

                    key = (sid, day, start, rid)

                    var = model.NewBoolVar(
                        f"x_{sid}_{day}_{start}_{rid}"
                    )

                    choices[key] = var
                    selected[sid].append(var)

                    interval = model.NewOptionalFixedSizeIntervalVar(
                        start,
                        duration,
                        var,
                        f"interval_{sid}_{day}_{start}_{rid}"
                    )

                    intervals[("room", rid, day)].append(interval)

                    intervals[("faculty", faculty_id, day)].append(
                        interval
                    )

                    basket_id = session.get("basket_slot")

                    include_group_interval = (
                        basket_id is None
                        or basket_representatives[basket_id] == sid
                    )

                    if include_group_interval:
                        for group_id in session["groups"]:
                            intervals[
                                ("group", group_id, day)
                            ].append(interval)

        if not selected[sid]:
            raise ValueError(
                f"No feasible slot found for affected session {sid}"
            )

        model.AddExactlyOne(selected[sid])

    print("Affected-session scheduling choices:", len(choices))

    # --------------------------------------------------------
    # Resource conflict constraints
    # --------------------------------------------------------

    for resource_intervals in intervals.values():
        if len(resource_intervals) > 1:
            model.AddNoOverlap(resource_intervals)

    # --------------------------------------------------------
    # Faculty daily maximum hours
    # --------------------------------------------------------

    variable_daily_hours = defaultdict(list)

    for (sid, day, start, rid), var in choices.items():
        session = session_map[sid]
        faculty_id = session["faculty_id"]

        variable_daily_hours[(faculty_id, day)].append(
            session["duration"] * var
        )

    all_daily_keys = (
        set(fixed_daily_hours)
        | set(variable_daily_hours)
    )

    for faculty_id, day in all_daily_keys:
        maximum = faculty_map[faculty_id].get(
            "max_hours_per_day", 4
        )

        model.Add(
            fixed_daily_hours[(faculty_id, day)]
            + sum(variable_daily_hours[(faculty_id, day)])
            <= maximum
        )

    # --------------------------------------------------------
    # Elective basket synchronization
    # --------------------------------------------------------

    for basket_id, basket in basket_sessions.items():

        if not any(
            session["id"] in affected_ids
            for session in basket
        ):
            continue

        first_id = basket[0]["id"]

        common_slots = {
            (day, start)
            for (sid, day, start, rid) in choices
            if sid == first_id
        }

        for session in basket[1:]:
            sid = session["id"]

            session_slots = {
                (day, start)
                for (choice_sid, day, start, rid) in choices
                if choice_sid == sid
            }

            common_slots.intersection_update(session_slots)

        if not common_slots:
            raise ValueError(
                f"No common slot for basket {basket_id}"
            )

        basket_slot_vars = []

        for day, start in sorted(common_slots):
            slot_var = model.NewBoolVar(
                f"basket_{basket_id}_{day}_{start}"
            )

            basket_slot_vars.append(slot_var)

            for session in basket:
                sid = session["id"]

                matching_choices = [
                    var
                    for (
                        choice_sid,
                        choice_day,
                        choice_start,
                        rid
                    ), var in choices.items()
                    if (
                        choice_sid == sid
                        and choice_day == day
                        and choice_start == start
                    )
                ]

                model.Add(
                    sum(matching_choices) == slot_var
                )

        model.AddExactlyOne(basket_slot_vars)

    # --------------------------------------------------------
    # Combined objective: stability + official validator penalties
    # --------------------------------------------------------

    objective_terms = []
    move_penalties = []
    session_objective_terms = defaultdict(
        lambda: defaultdict(list)
    )

    for sid in affected_ids:
        if sid not in baseline_map:
            continue

        old = baseline_map[sid]

        matching = choices.get((
            sid,
            old["day"],
            old["start_period"],
            old["room_id"]
        ))

        if matching is not None:
            penalty = 10 * (1 - matching)
            move_penalties.append(penalty)
            session_objective_terms[sid]["stability"].append(penalty)

    if move_penalties:
        objective_terms.extend(move_penalties)

    # S1: faculty avoid preferences for affected placements.
    for sid in affected_ids:
        session = session_map[sid]
        faculty = faculty_map[session["faculty_id"]]

        for pref in faculty.get("preferences", []):
            if pref.get("type") != "avoid":
                continue

            allowed_days = pref.get("days", days)

            for (choice_sid, day, start, rid), var in choices.items():
                if choice_sid != sid:
                    continue
                if day not in allowed_days:
                    continue

                overlap = sum(
                    1
                    for period in range(
                        start,
                        start + session["duration"]
                    )
                    if period in pref.get("periods", [])
                )

                if overlap:
                    term = 2 * overlap * var
                    objective_terms.append(term)
                    session_objective_terms[sid]["S1"].append(term)

    # S2, S3, S4: official validator structure using fixed + selected sessions.
    group_day_periods = defaultdict(lambda: defaultdict(list))
    group_day_hours = defaultdict(list)
    ordinary_lectures = defaultdict(list)
    basket_lectures = defaultdict(list)
    group_day_session_ids = defaultdict(set)
    ordinary_lecture_session_ids = defaultdict(set)
    basket_lecture_session_ids = defaultdict(set)

    for sid, session in session_map.items():
        basket_id = session.get("basket_slot")
        representative = (
            basket_id is None
            or basket_representatives[basket_id] == sid
        )

        if sid in fixed_ids:
            placement = baseline_map[sid]
            day = placement["day"]
            start = placement["start_period"]
            duration = session["duration"]

            if representative:
                for group_id in session["groups"]:
                    group_day_session_ids[(group_id, day)].add(sid)
                    for period in range(start, start + duration):
                        group_day_periods[(group_id, day)][period].append(1)
                    group_day_hours[(group_id, day)].append(duration)

                    if session["kind"] == "lecture" and basket_id is None:
                        ordinary_lectures[(group_id, day, session["course_id"])].append(1)
                        ordinary_lecture_session_ids[
                            (group_id, day, session["course_id"])
                        ].add(sid)

                if basket_id:
                    basket_lectures[(session["course_id"], day)].append(1)
                    basket_lecture_session_ids[
                        (session["course_id"], day)
                    ].add(sid)

            continue

        for (choice_sid, choice_day, choice_start, rid), var in choices.items():
            if choice_sid != sid:
                continue

            if not representative:
                continue

            duration = session["duration"]

            for group_id in session["groups"]:
                group_day_session_ids[(group_id, choice_day)].add(sid)
                for period in range(choice_start, choice_start + duration):
                    group_day_periods[(group_id, choice_day)][period].append(var)
                group_day_hours[(group_id, choice_day)].append(duration * var)

                if session["kind"] == "lecture" and basket_id is None:
                    ordinary_lectures[(group_id, choice_day, session["course_id"])].append(var)
                    ordinary_lecture_session_ids[
                        (group_id, choice_day, session["course_id"])
                    ].add(sid)

            if basket_id:
                basket_lectures[(session["course_id"], choice_day)].append(var)
                basket_lecture_session_ids[
                    (session["course_id"], choice_day)
                ].add(sid)

    shared_objective_terms = []

    s2_index = 0
    for (group_id, day), period_terms in group_day_periods.items():
        occupied = {}

        for period in range(1, 10):
            terms = period_terms.get(period, [])
            occupied_var = model.NewBoolVar(
                f"s2_occupied_{s2_index}_{period}"
            )

            if terms:
                model.AddMaxEquality(occupied_var, terms)
            else:
                model.Add(occupied_var == 0)

            occupied[period] = occupied_var

        first = model.NewIntVar(1, 9, f"s2_first_{s2_index}")
        last = model.NewIntVar(1, 9, f"s2_last_{s2_index}")

        for period in range(1, 10):
            model.Add(first <= period).OnlyEnforceIf(occupied[period])
            if period > 1:
                model.Add(first >= period).OnlyEnforceIf([
                    occupied[period],
                    *[
                        occupied[p].Not()
                        for p in range(1, period)
                    ]
                ])

            model.Add(last >= period).OnlyEnforceIf(occupied[period])
            if period < 9:
                model.Add(last <= period).OnlyEnforceIf([
                    occupied[period],
                    *[
                        occupied[p].Not()
                        for p in range(period + 1, 10)
                    ]
                ])

        gap_terms = []
        for period in range(2, 9):
            gap = model.NewBoolVar(f"s2_gap_{s2_index}_{period}")
            before = model.NewBoolVar(f"s2_before_{s2_index}_{period}")
            after = model.NewBoolVar(f"s2_after_{s2_index}_{period}")

            model.Add(first <= period).OnlyEnforceIf(before)
            model.Add(first >= period + 1).OnlyEnforceIf(before.Not())
            model.Add(last >= period).OnlyEnforceIf(after)
            model.Add(last <= period - 1).OnlyEnforceIf(after.Not())
            model.AddBoolAnd([
                occupied[period].Not(),
                before,
                after
            ]).OnlyEnforceIf(gap)
            model.AddBoolOr([
                occupied[period],
                before.Not(),
                after.Not()
            ]).OnlyEnforceIf(gap.Not())

            objective_terms.append(gap)
            gap_terms.append(gap)

        shared_objective_terms.append({
            "objective": "S2",
            "scope": "group_day",
            "group_id": group_id,
            "day": day,
            "session_ids": sorted(
                group_day_session_ids[(group_id, day)]
            ),
            "terms": gap_terms,
        })

        s2_index += 1

    for index, (group_day_key, total_terms) in enumerate(group_day_hours.items()):
        total_hours = sum(total_terms)
        excess = model.NewIntVar(0, 100, f"s3_excess_{index}")
        model.AddMaxEquality(excess, [total_hours - 6, 0])
        term = 3 * excess
        objective_terms.append(term)
        group_id, day = group_day_key
        shared_objective_terms.append({
            "objective": "S3",
            "scope": "group_day",
            "group_id": group_id,
            "day": day,
            "session_ids": sorted(
                group_day_session_ids[group_day_key]
            ),
            "terms": [term],
        })

    for index, (lecture_key, total_terms) in enumerate(ordinary_lectures.items()):
        lecture_count = sum(total_terms)
        excess = model.NewIntVar(0, max(0, len(total_terms) - 1), f"s4_ordinary_excess_{index}")
        model.AddMaxEquality(excess, [lecture_count - 1, 0])
        term = 3 * excess
        objective_terms.append(term)
        group_id, day, course_id = lecture_key
        shared_objective_terms.append({
            "objective": "S4",
            "scope": "group_course_day",
            "group_id": group_id,
            "course_id": course_id,
            "day": day,
            "session_ids": sorted(
                ordinary_lecture_session_ids[lecture_key]
            ),
            "terms": [term],
        })

    for index, (lecture_key, total_terms) in enumerate(basket_lectures.items()):
        lecture_count = sum(total_terms)
        excess = model.NewIntVar(0, max(0, len(total_terms) - 1), f"s4_basket_excess_{index}")
        model.AddMaxEquality(excess, [lecture_count - 1, 0])
        term = 3 * excess
        objective_terms.append(term)
        course_id, day = lecture_key
        shared_objective_terms.append({
            "objective": "S4",
            "scope": "basket_course_day",
            "course_id": course_id,
            "day": day,
            "session_ids": sorted(
                basket_lecture_session_ids[lecture_key]
            ),
            "terms": [term],
        })

    # S5: underutilized rooms.
    for (choice_sid, day, start, rid), var in choices.items():
        session = session_map[choice_sid]
        room = next(room for room in rooms if room["id"] == rid)

        if room["capacity"] and session["size"] / room["capacity"] < 0.4:
            objective_terms.append(var)
            session_objective_terms[choice_sid]["S5"].append(var)

    # S6: sessions occupying the last teaching period.
    for (choice_sid, day, start, rid), var in choices.items():
        session = session_map[choice_sid]
        if start + session["duration"] - 1 == 9:
            objective_terms.append(var)
            session_objective_terms[choice_sid]["S6"].append(var)

    if objective_terms:
        model.Minimize(sum(objective_terms))
    else:
        model.Minimize(0)

    # --------------------------------------------------------
    # Solve
    # --------------------------------------------------------

    solver = cp_model.CpSolver()

    solver.parameters.max_time_in_seconds = 45
    solver.parameters.num_search_workers = 8

    print("\nStarting partial crisis solver...")

    solve_start = time.perf_counter()
    status = solver.Solve(model)
    solve_time = time.perf_counter() - solve_start

    print("\nPARTIAL SOLVER DIAGNOSTICS")
    print("--------------------------")
    print("Status:", solver.StatusName(status))
    print("Wall time:", round(solve_time, 2), "seconds")
    print("Branches:", solver.NumBranches())
    print("Conflicts:", solver.NumConflicts())

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise ValueError(
            "No feasible partial crisis timetable found. "
            f"Solver status: {solver.StatusName(status)}"
        )

    # --------------------------------------------------------
    # Extract complete timetable
    # --------------------------------------------------------

    result = []

    # Preserve all unaffected sessions exactly.
    for sid in fixed_ids:
        result.append(deepcopy(baseline_map[sid]))

    # Add the newly scheduled placements.
    for sid in affected_ids:
        found = False

        for (choice_sid, day, start, rid), var in choices.items():
            if choice_sid == sid and solver.Value(var):
                result.append({
                    "session_id": sid,
                    "day": day,
                    "start_period": start,
                    "room_id": rid
                })

                found = True
                break

        if not found:
            raise ValueError(
                f"Could not extract placement for {sid}"
            )

    if len(result) != len(sessions):
        raise ValueError(
            f"Schedule extraction mismatch: "
            f"{len(result)} of {len(sessions)} sessions."
        )

    result.sort(key=lambda item: item["session_id"])

    session_hard_evidence = {}
    counterfactual_evidence = {}
    for sid in affected_ids.intersection(baseline_map):
        session = session_map[sid]
        original = baseline_map[sid]
        session_hard_evidence[sid] = placement_hard_constraint_evidence(
            session,
            original,
            updated_data,
            teaching_periods,
        )
        counterfactual_evidence[sid] = {
            "candidate_available": (
                sid,
                original["day"],
                original["start_period"],
                original["room_id"],
            ) in choices,
            "conflicts": counterfactual_conflicts(
                session,
                original,
                result,
                session_map,
                faculty_map,
            ),
        }

    individual_contributions = {}
    for sid, categories in session_objective_terms.items():
        individual_contributions[sid] = {
            category: sum(
                solver.Value(term)
                for term in terms
            )
            for category, terms in categories.items()
        }

    selected_map = {
        item["session_id"]: item
        for item in result
    }
    shared_effects = []
    for item in shared_objective_terms:
        penalty = sum(
            solver.Value(term)
            for term in item["terms"]
        )
        if penalty:
            if item["scope"] == "group_day":
                actual_session_ids = [
                    sid
                    for sid, session in session_map.items()
                    if item["group_id"] in session["groups"]
                    and selected_map[sid]["day"] == item["day"]
                ]
            elif item["scope"] == "group_course_day":
                actual_session_ids = [
                    sid
                    for sid, session in session_map.items()
                    if session["course_id"] == item["course_id"]
                    and session["kind"] == "lecture"
                    and not session.get("basket_slot")
                    and item["group_id"] in session["groups"]
                    and selected_map[sid]["day"] == item["day"]
                ]
            else:
                actual_session_ids = [
                    sid
                    for sid, session in session_map.items()
                    if session["course_id"] == item["course_id"]
                    and session.get("basket_slot")
                    and selected_map[sid]["day"] == item["day"]
                ]
            shared_effects.append({
                key: value
                for key, value in item.items()
                if key not in {"terms", "session_ids"}
            } | {
                "session_ids": sorted(actual_session_ids),
                "penalty_points": penalty,
            })

    solver_evidence = {
        "status": solver.StatusName(status),
        "objective_value": solver.ObjectiveValue(),
        "best_objective_bound": solver.BestObjectiveBound(),
        "individual_objective_contributions": individual_contributions,
        "shared_objective_effects": shared_effects,
        "original_hard_constraint_evidence": session_hard_evidence,
        "original_counterfactual_evidence": counterfactual_evidence,
    }

    if return_solver_evidence:
        return result, solver_evidence
    return result


def build_crisis_explanations(
    updated_data,
    baseline,
    revised_sessions,
    solver_evidence,
):
    baseline_map = {
        item["session_id"]: item
        for item in baseline.get("sessions", [])
    }
    revised_map = {
        item["session_id"]: item
        for item in revised_sessions
    }
    session_map = {
        item["id"]: item
        for item in updated_data["sessions"]
    }
    course_map = {
        item["id"]: item
        for item in updated_data.get("courses", [])
    }
    faculty_map = {
        item["id"]: item
        for item in updated_data.get("faculty", [])
    }
    group_map = {
        item["id"]: item
        for item in updated_data.get("student_groups", [])
    }
    room_map = {
        item["id"]: item
        for item in updated_data.get("rooms", [])
    }

    explanations = []
    for session_id, revised in sorted(revised_map.items()):
        original = baseline_map.get(session_id)
        if original is None:
            continue
        if all(
            original.get(key) == revised.get(key)
            for key in ("day", "start_period", "room_id")
        ):
            continue

        session = session_map.get(session_id)
        if session is None:
            continue

        time_changed = any(
            original[key] != revised[key]
            for key in ("day", "start_period")
        )
        room_changed = original["room_id"] != revised["room_id"]
        change_type = (
            "TIME_AND_ROOM"
            if time_changed and room_changed
            else "TIME_ONLY"
            if time_changed
            else "ROOM_ONLY"
        )

        hard_evidence = solver_evidence[
            "original_hard_constraint_evidence"
        ].get(session_id, [])
        counterfactual = solver_evidence[
            "original_counterfactual_evidence"
        ].get(session_id, {})
        original_available = counterfactual.get(
            "candidate_available", False
        )
        counterfactual_conflict_list = counterfactual.get(
            "conflicts", []
        )
        feasible_with_revised_assignments = (
            original_available
            and not counterfactual_conflict_list
        )

        if hard_evidence and not original_available:
            explanation_type = "HARD_CONSTRAINT"
            confidence = "HIGH"
            explanation = (
                "The original placement was excluded by recorded hard "
                "constraint(s): "
                + " ".join(item["detail"] for item in hard_evidence)
            )
        elif feasible_with_revised_assignments:
            explanation_type = "OPTIMIZATION_TRADE_OFF"
            confidence = "MEDIUM"
            explanation = (
                "The original placement was a feasible candidate alongside "
                "the solver's selected placements, but the solver selected "
                "another arrangement. This confirms a feasible alternative; "
                "it does not establish the optimizer's precise causal reason."
            )
        else:
            explanation_type = "INSUFFICIENT_EVIDENCE"
            confidence = "LOW"
            explanation = (
                "Precise cause unavailable. The recorded evidence does not "
                "establish whether the original placement could be retained "
                "in a complete feasible schedule or which objective effect "
                "determined this move."
            )

        faculty_id = session.get("faculty_id")
        course_id = session.get("course_id")
        relevant_shared_effects = [
            effect
            for effect in solver_evidence["shared_objective_effects"]
            if session_id in effect["session_ids"]
        ]
        session_objective_contributions = solver_evidence[
            "individual_objective_contributions"
        ].get(session_id, {})

        explanations.append({
            "session_id": session_id,
            "course": {
                "id": course_id,
                "name": course_map.get(course_id, {}).get(
                    "title", course_id
                ),
            },
            "original": original,
            "revised": revised,
            "change_type": change_type,
            "original_placement_excluded_by_hard_constraint": bool(
                hard_evidence and not original_available
            ),
            "original_placement_feasible_with_revised_assignments": (
                feasible_with_revised_assignments
            ),
            "explanation_type": explanation_type,
            "confidence": confidence,
            "explanation": explanation,
            "hard_constraint_evidence": hard_evidence,
            "counterfactual_conflicts": counterfactual_conflict_list,
            "objective_contributions": {
                "stability_move_penalty": session_objective_contributions.get(
                    "stability", 0
                ),
                "S1": session_objective_contributions.get("S1", 0),
                "S5": session_objective_contributions.get("S5", 0),
                "S6": session_objective_contributions.get("S6", 0),
            },
            "shared_objective_effects": relevant_shared_effects,
            "faculty": {
                "id": faculty_id,
                "name": faculty_map.get(faculty_id, {}).get(
                    "name", faculty_id
                ),
            },
            "groups": [
                {
                    "id": group_id,
                    "name": group_map.get(group_id, {}).get(
                        "name", group_id
                    ),
                    "size": group_map.get(group_id, {}).get("size"),
                }
                for group_id in session.get("groups", [])
            ],
            "rooms": {
                "original": {
                    "id": original["room_id"],
                    "name": room_map.get(
                        original["room_id"], {}
                    ).get("name", original["room_id"]),
                    "type": room_map.get(
                        original["room_id"], {}
                    ).get("type"),
                    "capacity": room_map.get(
                        original["room_id"], {}
                    ).get("capacity"),
                },
                "revised": {
                    "id": revised["room_id"],
                    "name": room_map.get(
                        revised["room_id"], {}
                    ).get("name", revised["room_id"]),
                    "type": room_map.get(
                        revised["room_id"], {}
                    ).get("type"),
                    "capacity": room_map.get(
                        revised["room_id"], {}
                    ).get("capacity"),
                },
            },
            "student_impact": {
                "groups": session.get("groups", []),
                "session_size": session.get("size"),
            },
            "duration_periods": session.get("duration"),
        })

    shared_totals = defaultdict(int)
    for effect in solver_evidence["shared_objective_effects"]:
        shared_totals[effect["objective"]] += effect["penalty_points"]

    return {
        "schema_version": 1,
        "solver": {
            "status": solver_evidence["status"],
            "objective_value": solver_evidence["objective_value"],
            "best_objective_bound": solver_evidence[
                "best_objective_bound"
            ],
            "causality_note": (
                "These explanations report recorded hard-filter evidence, "
                "selected objective values, and feasible counterfactual "
                "checks. They are not proof that any one factor caused the "
                "solver's decision."
            ),
        },
        "shared_objective_totals": dict(shared_totals),
        "shared_objective_effects": solver_evidence[
            "shared_objective_effects"
        ],
        "moved_sessions": explanations,
    }


def run_what_if_simulation(disruption):
    """Generate a crisis schedule in memory without changing official exports."""
    original_data = load_dataset()
    baseline = read_json(BASELINE_PATH)
    updated_data = apply_disruption(original_data, disruption)

    sessions, solver_evidence = generate_crisis_schedule(
        updated_data,
        baseline,
        return_solver_evidence=True,
    )

    updated_schedule = deepcopy(baseline)
    updated_schedule["sessions"] = sessions
    booking_decisions = generate_booking_schedule(
        data=updated_data,
        schedule=updated_schedule,
    )
    updated_schedule.setdefault("fest_week", {})["bookings"] = (
        booking_decisions
    )

    explanations = build_crisis_explanations(
        updated_data,
        baseline,
        sessions,
        solver_evidence,
    )

    return {
        "baseline": baseline,
        "schedule": updated_schedule,
        "explanations": explanations,
        "solver_evidence": solver_evidence,
        "bookings_processed": len(booking_decisions),
    }


# ============================================================
# RUN CRISIS MODE
# ============================================================

def run_crisis_mode():

    total_start = time.perf_counter()

    print("\nCIT NEXUS - CAMPUS CRISIS MODE")
    print("=" * 45)

    start = time.perf_counter()

    original_data = load_dataset()
    baseline = read_json(BASELINE_PATH)
    disruption = read_json(DISRUPTION_PATH)

    print("Original dataset loaded.")
    print("Baseline timetable loaded.")
    print("Disruption loaded:", disruption["id"])

    print(
        "Data loading:",
        round(time.perf_counter() - start, 2),
        "seconds"
    )

    # Apply disruption.
    start = time.perf_counter()

    updated_data = apply_disruption(
        original_data,
        disruption
    )

    print("Campus disruption applied.")

    print(
        "Disruption processing:",
        round(time.perf_counter() - start, 2),
        "seconds"
    )

    # Partial timetable rescheduling.
    start = time.perf_counter()

    sessions, solver_evidence = generate_crisis_schedule(
        updated_data,
        baseline,
        return_solver_evidence=True,
    )

    print(
        "Partial timetable generation:",
        round(time.perf_counter() - start, 2),
        "seconds"
    )

    updated_schedule = deepcopy(baseline)
    updated_schedule["sessions"] = sessions

    # Regenerate bookings using the new timetable.
    print("\nRegenerating venue bookings...")

    start = time.perf_counter()

    booking_decisions = generate_booking_schedule(
        data=updated_data,
        schedule=updated_schedule
    )

    print(
        "Booking regeneration:",
        round(time.perf_counter() - start, 2),
        "seconds"
    )

    updated_schedule["fest_week"]["bookings"] = (
        booking_decisions
    )

    explanations = build_crisis_explanations(
        updated_data,
        baseline,
        sessions,
        solver_evidence,
    )

    # Export.
    start = time.perf_counter()

    save_json(
        OUTPUT_PATH,
        updated_schedule
    )
    save_json(EXPLANATIONS_PATH, explanations)

    print(
        "Schedule export:",
        round(time.perf_counter() - start, 2),
        "seconds"
    )

    total_time = time.perf_counter() - total_start

    print("\nPartial crisis schedule exported:")
    print(OUTPUT_PATH)
    print("Crisis explanations exported:")
    print(EXPLANATIONS_PATH)

    print(
        "\nTOTAL CRISIS ENGINE TIME:",
        round(total_time, 2),
        "seconds"
    )

    return {
        "status": "success",
        "sessions_scheduled": len(sessions),
        "bookings_processed": len(booking_decisions),
        "execution_time_seconds": round(total_time, 2),
        "output_file": str(OUTPUT_PATH),
        "explanations_file": str(EXPLANATIONS_PATH),
        "explained_moved_sessions": len(explanations["moved_sessions"]),
    }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    result = run_crisis_mode()
    print(result)
