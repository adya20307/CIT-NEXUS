
import json
from pathlib import Path

# Project root: CIT-Nexus/
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DATASET_PATH = (
    PROJECT_ROOT
    / "starter-pack"
    / "CrunchWeek_StarterPack"
    / "data"
    / "cit_public.json"
)
ACTIVE_DATASET_PATH = (
    PROJECT_ROOT
    / "backend"
    / "storage"
    / "starter_pack_management"
    / "active_dataset.json"
)

REQUIRED_KEYS = [
    "dataset_id",
    "calendar",
    "rooms",
    "room_unavailability",
    "faculty",
    "student_groups",
    "courses",
    "sessions",
    "booking_requests",
    "cancellations",
    "exams",
    "priority_order",
    "role_category_cap",
]


def load_original_dataset():
    """Load the immutable starter-pack dataset."""

    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Official dataset not found: {DATASET_PATH}"
        )

    with DATASET_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    validate_required_sections(data)
    return data


def validate_required_sections(data):
    missing_keys = [
        key for key in REQUIRED_KEYS
        if key not in data
    ]

    if missing_keys:
        raise ValueError(
            f"Dataset is missing sections: {missing_keys}"
        )


def load_dataset():
    """Load the administrator's active working copy, or the source dataset."""
    source_path = (
        ACTIVE_DATASET_PATH
        if ACTIVE_DATASET_PATH.exists()
        else DATASET_PATH
    )
    if not source_path.exists():
        raise FileNotFoundError(f"Dataset not found: {source_path}")
    with source_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    validate_required_sections(data)
    return data


def get_dataset_summary():
    """Return actual dataset statistics."""

    data = load_dataset()

    return {
        "dataset_id": data["dataset_id"],
        "institute": data.get("institute"),
        "rooms": len(data["rooms"]),
        "faculty": len(data["faculty"]),
        "student_groups": len(data["student_groups"]),
        "courses": len(data["courses"]),
        "sessions": len(data["sessions"]),
        "booking_requests": len(data["booking_requests"]),
        "cancellations": len(data["cancellations"]),
        "exams": len(data["exams"]),
        "teaching_days": data["calendar"]["days"],
        "teaching_periods": [
            period["id"]
            for period in data["calendar"]["periods"]
            if period["teaching"]
        ],
    }
