
import json
from pathlib import Path


def project_root():
    return Path(__file__).resolve().parents[3]


def read_json(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Required preview file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def export_final_schedule():

    root = project_root()
    exports = root / "exports"

    # Read the three generated previews
    sessions_data = read_json(
        exports / "sessions_preview.json"
    )

    bookings_data = read_json(
        exports / "bookings_preview.json"
    )

    exams_data = read_json(
        exports / "exams_preview.json"
    )

    # Verify dataset consistency
    dataset_id = sessions_data["dataset_id"]

    if sessions_data.get("dataset_id") != dataset_id:
        raise ValueError("Session dataset mismatch.")

    # Official schedule structure
    schedule = {
        "team": "CIT Nexus",
        "dataset_id": dataset_id,

        "sessions": sessions_data["sessions"],

        "fest_week": {
            "bookings": bookings_data,

            # No regular sessions are displaced
            # in this first version.
            "session_overrides": []
        },

        "exams": exams_data
    }

    # Basic completeness checks
    if len(schedule["sessions"]) != 189:
        raise ValueError(
            f"Expected 189 sessions, got "
            f"{len(schedule['sessions'])}"
        )

    if len(schedule["fest_week"]["bookings"]) != 25:
        raise ValueError(
            "Expected 25 booking decisions."
        )

    if len(schedule["exams"]) != 41:
        raise ValueError(
            f"Expected 41 exams, got "
            f"{len(schedule['exams'])}"
        )

    # Export official JSON
    output_path = exports / "schedule.json"

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            schedule,
            file,
            indent=2
        )

    print("\nCIT NEXUS FINAL SCHEDULE EXPORTED")
    print("----------------------------------")

    print("Dataset:", dataset_id)
    print("Regular sessions:", len(schedule["sessions"]))
    print("Booking decisions:", len(schedule["fest_week"]["bookings"]))
    print("Exam assignments:", len(schedule["exams"]))

    print("Session overrides:", len(schedule["fest_week"]["session_overrides"]))

    print("\nFile generated:")
    print(output_path)

    return output_path


if __name__ == "__main__":
    export_final_schedule()
