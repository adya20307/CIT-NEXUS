
import json
import math
from pathlib import Path

from ortools.sat.python import cp_model

from app.services.dataset_loader import load_dataset


def _minutes(value):
    try:
        hours, minutes = (int(part) for part in value.split(":"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid time value: {value!r}") from error

    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        raise ValueError(f"Invalid time value: {value!r}")

    return hours * 60 + minutes


def _unavailable_exam_slots(data):
    calendar = data["calendar"]
    exam_week = calendar["exam_week"]
    rooms = {room["id"] for room in data["rooms"]}
    days = set(exam_week["days"])
    periods = {
        period["id"]: period
        for period in calendar["periods"]
    }
    unavailable = set()

    for closure in data.get("room_unavailability", []):
        room_id = closure["room_id"]
        day = closure["day"]
        if room_id not in rooms:
            raise ValueError(
                f"Room unavailability references unknown room: {room_id}"
            )
        if day not in days:
            raise ValueError(
                f"Room unavailability references unknown exam day: {day}"
            )

        for period_id in closure["periods"]:
            if period_id not in periods:
                raise ValueError(
                    "Room unavailability references unknown period: "
                    f"{period_id}"
                )

            period = periods[period_id]
            period_start = _minutes(period["start"])
            period_end = _minutes(period["end"])
            for exam_session in exam_week["sessions"]:
                session_start = _minutes(exam_session["start"])
                session_end = _minutes(exam_session["end"])
                if (
                    session_start < period_end
                    and period_start < session_end
                ):
                    unavailable.add(
                        (room_id, day, exam_session["id"])
                    )

    return unavailable


def generate_exam_schedule(data=None):

    if data is None:
        data = load_dataset()

    exams = data["exams"]
    rooms = [
        room for room in data["rooms"]
        if "exam" in room.get("allowed_uses", [])
    ]

    calendar = data["calendar"]["exam_week"]
    days = calendar["days"]
    exam_sessions = [
        session["id"] for session in calendar["sessions"]
    ]
    session_by_id = {
        session["id"]: session
        for session in calendar["sessions"]
    }
    unavailable_slots = _unavailable_exam_slots(data)

    if not rooms:
        raise ValueError("No exam-eligible rooms found.")

    model = cp_model.CpModel()

    # x[e,d,s,r]: exam e uses room r on day d in session s.
    room_choice = {}

    # y[e,d,s]: exam e is scheduled in slot (d,s).
    exam_slot = {}

    for exam in exams:
        eid = exam["id"]
        duration_minutes = int(exam["duration_hours"] * 60)

        for day in days:
            for session in exam_sessions:

                y = model.NewBoolVar(
                    f"slot_{eid}_{day}_{session}"
                )
                exam_slot[(eid, day, session)] = y

                slot = session_by_id[session]
                if (
                    _minutes(slot["end"]) - _minutes(slot["start"])
                    < duration_minutes
                ):
                    model.Add(y == 0)

                choices = []

                for room in rooms:
                    rid = room["id"]

                    x = model.NewBoolVar(
                        f"room_{eid}_{day}_{session}_{rid}"
                    )

                    room_choice[
                        (eid, day, session, rid)
                    ] = x

                    if (rid, day, session) in unavailable_slots:
                        model.Add(x == 0)

                    choices.append(x)

                # Selected rooms exist if and only if the exam
                # is assigned to this day/session.
                model.Add(sum(choices) >= y)
                model.Add(sum(choices) <= len(rooms) * y)

                # Capacity must be sufficient whenever this
                # exam slot is selected.
                capacity = sum(
                    (room["capacity"] // 2)
                    * room_choice[
                        (eid, day, session, room["id"])
                    ]
                    for room in rooms
                )

                model.Add(
                    capacity >= exam["size"] * y
                )

        # Every exam must be assigned to exactly one slot.
        model.Add(
            sum(
                exam_slot[(eid, day, session)]
                for day in days
                for session in exam_sessions
            ) == 1
        )

    # A room cannot host two exams in the same slot.
    for room in rooms:
        rid = room["id"]

        for day in days:
            for session in exam_sessions:

                model.Add(
                    sum(
                        room_choice[
                            (exam["id"], day, session, rid)
                        ]
                        for exam in exams
                    ) <= 1
                )

    # A student group cannot have two exams in the same slot.
    all_groups = sorted({
        group_id
        for exam in exams
        for group_id in exam["groups"]
    })

    for group_id in all_groups:
        relevant_exams = [
            exam for exam in exams
            if group_id in exam["groups"]
        ]

        for day in days:
            for session in exam_sessions:

                model.Add(
                    sum(
                        exam_slot[
                            (exam["id"], day, session)
                        ]
                        for exam in relevant_exams
                    ) <= 1
                )

    # Minimize multiple exams for the same group on one day.
    daily_penalties = []

    for group_id in all_groups:

        relevant_exams = [
            exam for exam in exams
            if group_id in exam["groups"]
        ]

        for day in days:

            daily_exam_indicators = []

            for exam in relevant_exams:

                daily_indicator = model.NewBoolVar(
                    f"daily_{group_id}_{exam['id']}_{day}"
                )

                slots_for_day = [
                    exam_slot[
                        (exam["id"], day, session)
                    ]
                    for session in exam_sessions
                ]

                model.Add(
                    daily_indicator == sum(slots_for_day)
                )

                daily_exam_indicators.append(
                    daily_indicator
                )

            if len(daily_exam_indicators) > 1:

                extra_exams = model.NewIntVar(
                    0,
                    len(daily_exam_indicators) - 1,
                    f"extra_{group_id}_{day}"
                )

                model.Add(
                    extra_exams >=
                    sum(daily_exam_indicators) - 1
                )

                daily_penalties.append(extra_exams)

    # Official exam soft penalty:
    # 5 points per additional exam for a group on one day.
    model.Minimize(
        5 * sum(daily_penalties)
    )

    return model, room_choice, exam_slot, data


def extract_exam_schedule(
    solver,
    room_choice,
    exam_slot,
    data
):

    exams_output = []

    days = data["calendar"]["exam_week"]["days"]

    exam_sessions = [
        session["id"]
        for session in data["calendar"]["exam_week"]["sessions"]
    ]

    rooms = [
        room for room in data["rooms"]
        if "exam" in room.get("allowed_uses", [])
    ]

    for exam in data["exams"]:

        eid = exam["id"]
        selected_day = None
        selected_session = None
        selected_rooms = []

        for day in days:
            for session in exam_sessions:

                if solver.Value(
                    exam_slot[(eid, day, session)]
                ) == 1:

                    selected_day = day
                    selected_session = session

                    selected_rooms = [
                        room["id"]
                        for room in rooms
                        if solver.Value(
                            room_choice[
                                (eid, day, session, room["id"])
                            ]
                        ) == 1
                    ]

                    break

            if selected_day:
                break

        if not selected_day or not selected_rooms:
            raise ValueError(
                f"Exam assignment missing: {eid}"
            )

        exams_output.append({
            "exam_id": eid,
            "day": selected_day,
            "session": selected_session,
            "rooms": selected_rooms
        })

    return exams_output


def validate_exam_schedule(exams_output, data):
    exam_by_id = {exam["id"]: exam for exam in data["exams"]}
    calendar = data["calendar"]["exam_week"]
    valid_days = set(calendar["days"])
    session_by_id = {
        session["id"]: session
        for session in calendar["sessions"]
    }
    rooms = {room["id"]: room for room in data["rooms"]}
    unavailable_slots = _unavailable_exam_slots(data)
    violations = []
    seen_exams = set()
    room_usage = {}
    group_usage = {}

    for assignment in exams_output:
        exam_id = assignment.get("exam_id")
        exam = exam_by_id.get(exam_id)
        if exam is None:
            violations.append(f"Unknown exam assignment: {exam_id}")
            continue
        if exam_id in seen_exams:
            violations.append(f"Exam assigned more than once: {exam_id}")
        seen_exams.add(exam_id)

        day = assignment.get("day")
        session_id = assignment.get("session")
        session = session_by_id.get(session_id)
        if day not in valid_days or session is None:
            violations.append(
                f"Invalid day or exam session for {exam_id}: "
                f"{day} {session_id}"
            )
            continue

        duration_hours = exam["duration_hours"]
        if (
            not isinstance(duration_hours, (int, float))
            or isinstance(duration_hours, bool)
            or not math.isfinite(duration_hours)
            or duration_hours <= 0
        ):
            violations.append(f"Invalid exam duration for {exam_id}")
            required_duration = 0
        else:
            required_duration = duration_hours * 60
        if (
            _minutes(session["end"]) - _minutes(session["start"])
            < required_duration
        ):
            violations.append(
                f"Exam session is too short for {exam_id}: {session_id}"
            )

        session_start = _minutes(session["start"])
        session_end = _minutes(session["end"])
        selected_rooms = assignment.get("rooms", [])
        if not selected_rooms:
            violations.append(f"No room assigned to {exam_id}")
        if len(selected_rooms) != len(set(selected_rooms)):
            violations.append(f"Room assigned more than once for {exam_id}")

        seats = 0
        for room_id in selected_rooms:
            room = rooms.get(room_id)
            if room is None or "exam" not in room.get("allowed_uses", []):
                violations.append(
                    f"Room cannot host exams for {exam_id}: {room_id}"
                )
                continue

            seats += room["capacity"] // 2
            room_day = (room_id, day)
            room_usage.setdefault(room_day, []).append(
                (session_start, session_end, exam_id, session_id)
            )
            if (room_id, day, session_id) in unavailable_slots:
                violations.append(
                    f"Room unavailable during exam {exam_id}: "
                    f"{room_id} on {day} {session_id}"
                )

        if seats < exam["size"]:
            violations.append(
                f"Insufficient exam capacity for {exam_id}: "
                f"{seats} seats for {exam['size']} students"
            )

        for group_id in exam["groups"]:
            group_day = (group_id, day)
            group_usage.setdefault(group_day, []).append(
                (session_start, session_end, exam_id, session_id)
            )

    for exam_id in exam_by_id:
        if exam_id not in seen_exams:
            violations.append(f"Exam not scheduled: {exam_id}")

    for (room_id, day), usages in room_usage.items():
        for index, (start, end, exam_id, session_id) in enumerate(usages):
            for (
                other_start,
                other_end,
                other_exam,
                other_session,
            ) in usages[index + 1:]:
                if start < other_end and other_start < end:
                    violations.append(
                        f"Room conflict for {room_id} on {day}: "
                        f"{exam_id} ({session_id}) overlaps "
                        f"{other_exam} ({other_session})"
                    )

    for (group_id, day), usages in group_usage.items():
        for index, (start, end, exam_id, session_id) in enumerate(usages):
            for (
                other_start,
                other_end,
                other_exam,
                other_session,
            ) in usages[index + 1:]:
                if start < other_end and other_start < end:
                    violations.append(
                        f"Student-group conflict for {group_id} on {day}: "
                        f"{exam_id} ({session_id}) overlaps "
                        f"{other_exam} ({other_session})"
                    )

    return {
        "hard_constraints_satisfied": not violations,
        "checked_constraints": [
            "exactly_once",
            "valid_exam_day_and_session",
            "room_eligibility",
            "room_capacity",
            "room_conflicts",
            "student_group_conflicts",
            "room_availability_and_maintenance",
            "exam_duration",
        ],
        "hard_violations": violations,
        "unverified_constraints": [
            "Faculty/invigilator conflicts cannot be checked because the "
            "supplied exam records do not include faculty assignments."
        ],
    }


def solve_exam_schedule(data=None, max_time_seconds=120):
    model, room_choice, exam_slot, data = generate_exam_schedule(data)
    validation = model.Validate()
    if validation:
        raise ValueError(f"Exam model validation failed: {validation}")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "solver_status": status_name,
            "objective": None,
            "assignments": None,
            "data": data,
            "validation": None,
        }

    assignments = extract_exam_schedule(
        solver,
        room_choice,
        exam_slot,
        data
    )
    schedule_validation = validate_exam_schedule(assignments, data)
    return {
        "solver_status": status_name,
        "objective": solver.ObjectiveValue(),
        "assignments": assignments,
        "data": data,
        "validation": schedule_validation,
    }


def export_exam_preview(exams_output):

    project_root = Path(__file__).resolve().parents[3]

    export_folder = project_root / "exports"
    export_folder.mkdir(parents=True, exist_ok=True)

    output_file = export_folder / "exams_preview.json"

    output_file.write_text(
        json.dumps(exams_output, indent=2),
        encoding="utf-8"
    )

    print("\nExam preview exported successfully!")
    print("Total exams:", len(exams_output))
    print("File:", output_file)


def main():

    print("Creating CIT Nexus examination model...\n")

    print("\nSolving examination timetable...")
    result = solve_exam_schedule()
    if result["assignments"] is None:
        print("No feasible examination timetable found.")
        print("Solver status:", result["solver_status"])
        return

    print("\nEXAM TIMETABLE GENERATED SUCCESSFULLY!")
    print("Solver status:", result["solver_status"])
    print("Exam soft penalty:", result["objective"])
    print("Hard constraints satisfied:", result["validation"]["hard_constraints_satisfied"])
    export_exam_preview(result["assignments"])


if __name__ == "__main__":
    main()
