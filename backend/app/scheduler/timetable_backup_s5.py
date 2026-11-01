
import json
from pathlib import Path

from ortools.sat.python import cp_model

from app.services.dataset_loader import load_dataset
from app.scheduler.constraints import add_hard_constraints
from app.scheduler.soft_objectives import add_soft_objectives

def generate_timetable():
    # Load the official hackathon dataset.
    data = load_dataset()

    model = cp_model.CpModel()

    sessions = data["sessions"]
    rooms = data["rooms"]
    faculty = data["faculty"]
    groups = data["student_groups"]
    calendar = data["calendar"]

    days = calendar["days"]

    periods = [
        period["id"]
        for period in calendar["periods"]
        if period["teaching"]
    ]

    faculty_by_id = {
        member["id"]: member
        for member in faculty
    }

    groups_by_id = {
        group["id"]: group
        for group in groups
    }

    # Build room unavailability lookup.
    room_closures = {}

    for closure in data["room_unavailability"]:
        key = (closure["room_id"], closure["day"])

        room_closures.setdefault(key, set()).update(
            closure["periods"]
        )

    assignments = {}

    # Generate candidate assignments for every session.
    for session in sessions:

        session_id = session["id"]
        duration = session["duration"]

        session_faculty = faculty_by_id[
            session["faculty_id"]
        ]

        # Check accessibility requirements.
        needs_accessible = (
            session.get("needs_accessible", False)
            or session_faculty.get("needs_accessible", False)
            or any(
                groups_by_id[group_id].get(
                    "needs_accessible", False
                )
                for group_id in session["groups"]
            )
        )

        choices = []
        candidates = []

        for day in days:

            for start_period in periods:

                occupied_periods = list(
                    range(
                        start_period,
                        start_period + duration
                    )
                )

                # Every occupied period must be a teaching period.
                if not all(
                    period in periods
                    for period in occupied_periods
                ):
                    continue

                # Do not cross the lunch break.
                if (
                    start_period <= 5
                    < start_period + duration - 1
                ):
                    continue

                # Labs can start only at permitted periods.
                if session["kind"] == "lab":
                    if (
                        start_period
                        not in calendar["lab_start_periods"]
                    ):
                        continue

                # Check faculty unavailability.
                faculty_unavailable = any(
                    item["day"] == day
                    and set(item["periods"]).intersection(
                        occupied_periods
                    )
                    for item in session_faculty.get(
                        "unavailable", []
                    )
                )

                if faculty_unavailable:
                    continue

                for room in rooms:

                    # Check room type.
                    if room["type"] not in session["room_types"]:
                        continue

                    # Check room capacity.
                    if room["capacity"] < session["size"]:
                        continue

                    # Check permitted room usage.
                    if (
                        session["kind"]
                        not in room.get("allowed_uses", [])
                    ):
                        continue

                    # Check accessibility.
                    if (
                        needs_accessible
                        and not room.get("accessible", False)
                    ):
                        continue

                    # Check room unavailability.
                    closed_periods = room_closures.get(
                        (room["id"], day),
                        set()
                    )

                    if set(occupied_periods).intersection(
                        closed_periods
                    ):
                        continue

                    # Create a decision variable.
                    selected = model.NewBoolVar(
                        f"select_{session_id}_{day}_"
                        f"{start_period}_{room['id']}"
                    )

                    # Create an optional interval.
                    interval = model.NewOptionalIntervalVar(
                        start_period,
                        duration,
                        start_period + duration,
                        selected,
                        f"interval_{session_id}_{day}_"
                        f"{start_period}_{room['id']}"
                    )

                    choices.append(selected)

                    candidates.append({
                        "selected": selected,
                        "interval": interval,
                        "room_id": room["id"],
                        "day": day,
                        "start_period": start_period
                    })

        assignments[session_id] = {
            "choices": choices,
            "candidates": candidates
        }

    # Add scheduling constraints.
    add_hard_constraints(
        model,
        sessions,
        assignments,
        faculty_by_id
    )

    # Add soft objectives.
    add_soft_objectives(
        model,
        sessions,
        assignments
    )

    return model, assignments, data


def extract_scheduled_sessions(solver, assignments, sessions):
    """
    Extract the selected room, day and period
    for every successfully scheduled session.
    """

    scheduled_sessions = []

    for session in sessions:

        session_id = session["id"]

        selected_candidate = next(
            (
                candidate
                for candidate in assignments[session_id]["candidates"]
                if solver.Value(candidate["selected"]) == 1
            ),
            None
        )

        if selected_candidate is None:
            raise ValueError(
                f"No selected assignment found for {session_id}"
            )

        scheduled_sessions.append({
            "session_id": session_id,
            "day": selected_candidate["day"],
            "start_period": selected_candidate["start_period"],
            "room_id": selected_candidate["room_id"]
        })

    return scheduled_sessions


def export_sessions_preview(data, scheduled_sessions):
    """
    Export the timetable sessions as a preview.
    This is not yet the final official schedule.json.
    """

    project_root = Path(__file__).resolve().parents[3]

    export_folder = project_root / "exports"

    export_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    preview = {
        "team": "CIT Nexus",
        "dataset_id": data["dataset_id"],
        "sessions": scheduled_sessions
    }

    output_file = export_folder / "sessions_preview.json"

    output_file.write_text(
        json.dumps(preview, indent=2),
        encoding="utf-8"
    )

    print("\nSession preview exported successfully!")
    print("Total scheduled sessions:", len(scheduled_sessions))
    print("Output file:", output_file)


def main():

    print("Creating CIT Nexus timetable model...\n")

    model, assignments, data = generate_timetable()

    print("CIT Nexus timetable model created successfully.")
    print("Dataset:", data["dataset_id"])
    print("Sessions:", len(data["sessions"]))

    total_variables = sum(
        len(item["choices"])
        for item in assignments.values()
    )

    print("Decision variables:", total_variables)

    # Validate the model before solving.
    validation = model.Validate()

    if validation:
        print("\nModel validation failed:")
        print(validation)
        return

    print("Model validation: PASSED")

    # Start OR-Tools solver.
    print("\nStarting timetable solver...")

    solver = cp_model.CpSolver()

    solver.parameters.max_time_in_seconds = 60
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    if status in (
        cp_model.OPTIMAL,
        cp_model.FEASIBLE
    ):

        print("\nTIMETABLE GENERATED SUCCESSFULLY!")

        print("Solver status:", solver.StatusName(status))
        print("Objective value:", solver.ObjectiveValue())

        # Extract all selected assignments.
        scheduled_sessions = extract_scheduled_sessions(
            solver,
            assignments,
            data["sessions"]
        )

        # Export the timetable preview.
        export_sessions_preview(
            data,
            scheduled_sessions
        )

    else:

        print("\nNo feasible timetable found.")

        print("Solver status:", solver.StatusName(status))


if __name__ == "__main__":
    main()
