import csv
import io
import json
import os
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from app.services.dataset_loader import (
    ACTIVE_DATASET_PATH,
    DATASET_PATH,
    REQUIRED_KEYS,
    load_dataset,
    load_original_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
STARTER_ROOT = PROJECT_ROOT / "starter-pack" / "CrunchWeek_StarterPack"
CSV_ROOT = STARTER_ROOT / "data" / "csv"
VALIDATOR_PATH = STARTER_ROOT / "validator.py"
MANAGER_ROOT = ACTIVE_DATASET_PATH.parent
VERSIONS_ROOT = MANAGER_ROOT / "versions"
HISTORY_PATH = MANAGER_ROOT / "history.json"
DISRUPTION_STATE_PATH = MANAGER_ROOT / "disruption_state.json"
SCHEDULE_STATE_PATH = MANAGER_ROOT / "schedule_state.json"
ENTITY_NAMES = (
    "rooms",
    "room_unavailability",
    "faculty",
    "student_groups",
    "courses",
    "sessions",
    "booking_requests",
    "cancellations",
    "exams",
)
ENTITY_KEY_FIELDS = {
    entity: "id"
    for entity in ENTITY_NAMES
    if entity != "room_unavailability"
}
ENTITY_KEY_FIELDS["cancellations"] = "request_id"
CSV_FILENAMES = {path.stem: path.name for path in CSV_ROOT.glob("*.csv")}
_LOCK = threading.RLock()
MAX_DATASET_BYTES = 10 * 1024 * 1024


class DatasetValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(value, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _entity_sample(original, entity):
    records = original.get(entity, [])
    for record in records:
        if isinstance(record, dict):
            return record
    return {}


def _field_sample(records, key):
    for record in records:
        if not isinstance(record, dict) or key not in record:
            continue
        value = record[key]
        if value is not None and value != [] and value != {}:
            return value
    for record in records:
        if isinstance(record, dict) and key in record:
            return record[key]
    return None


def _validate_value(value, sample, label, errors):
    if sample is None:
        return
    if isinstance(sample, bool):
        if not isinstance(value, bool):
            errors.append(f"{label} must be a boolean.")
    elif isinstance(sample, int) and not isinstance(sample, bool):
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{label} must be an integer.")
    elif isinstance(sample, float):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{label} must be a number.")
    elif isinstance(sample, str):
        if not isinstance(value, str):
            errors.append(f"{label} must be a string.")
    elif isinstance(sample, list):
        if not isinstance(value, list):
            errors.append(f"{label} must be an array.")
            return
        if sample:
            for index, item in enumerate(value):
                _validate_value(item, sample[0], f"{label}[{index}]", errors)
    elif isinstance(sample, dict):
        if not isinstance(value, dict):
            errors.append(f"{label} must be an object.")
            return
        missing = set(sample) - set(value)
        extra = set(value) - set(sample)
        if missing:
            errors.append(f"{label} is missing keys: {sorted(missing)}.")
        if extra:
            errors.append(f"{label} has unsupported keys: {sorted(extra)}.")
        for key in set(sample).intersection(value):
            _validate_value(value[key], sample[key], f"{label}.{key}", errors)


def validate_dataset(data):
    errors = []
    if not isinstance(data, dict):
        return ["Dataset must be a JSON object."]

    original = load_original_dataset()
    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"Dataset is missing required section '{key}'.")
    for key in REQUIRED_KEYS:
        if key in data and key in original:
            if key in ENTITY_NAMES:
                if not isinstance(data[key], list):
                    errors.append(f"{key} must be an array.")
            else:
                _validate_value(data[key], original[key], key, errors)
    if not isinstance(data.get("dataset_id"), str) or not data["dataset_id"].strip():
        errors.append("dataset_id must be a non-empty string.")

    for entity in ENTITY_NAMES:
        records = data.get(entity)
        if not isinstance(records, list):
            continue
        if not records:
            continue
        record_sample = _entity_sample(original, entity)
        required_fields = set(record_sample)
        seen_ids = set()
        key_field = ENTITY_KEY_FIELDS.get(entity)
        for index, record in enumerate(records):
            label = f"{entity}[{index}]"
            if not isinstance(record, dict):
                errors.append(f"{label} must be an object.")
                continue
            missing = required_fields - set(record)
            extra = set(record) - required_fields
            if missing:
                errors.append(f"{label} is missing keys: {sorted(missing)}.")
            if extra:
                errors.append(f"{label} has unsupported keys: {sorted(extra)}.")
            for key in required_fields.intersection(record):
                if record[key] is None and any(
                    reference.get(key) is None
                    for reference in original[entity]
                ):
                    continue
                sample = _field_sample(original[entity], key)
                _validate_value(record[key], sample, f"{label}.{key}", errors)
            record_id = record.get(key_field) if key_field else None
            if record_id is not None:
                if record_id in seen_ids:
                    errors.append(f"{entity} contains duplicate id '{record_id}'.")
                seen_ids.add(record_id)

    if errors:
        return list(dict.fromkeys(errors))
    errors.extend(_validate_official_constraints(data))
    errors.extend(_validate_references(data))
    return list(dict.fromkeys(errors))


def _validate_official_constraints(data):
    errors = []
    calendar = data.get("calendar")
    if not isinstance(calendar, dict):
        return ["calendar must be an object."]

    if calendar.get("days") != ["MON", "TUE", "WED", "THU", "FRI"]:
        errors.append("Teaching days must be MON through FRI.")
    periods = calendar.get("periods", [])
    period_ids = {
        item.get("id"): item.get("teaching")
        for item in periods
        if isinstance(item, dict)
    }
    if set(period_ids) != set(range(1, 14)):
        errors.append("Calendar must define periods P1 through P13.")
    else:
        if any(period_ids.get(period) is not True for period in range(1, 10)):
            errors.append("P1 through P9 must be teaching periods.")
        if any(period_ids.get(period) is not False for period in range(10, 14)):
            errors.append("P10 through P13 must be event-only, non-teaching periods.")
    if calendar.get("event_only_periods") != [10, 11, 12, 13]:
        errors.append("Event-only periods must be P10 through P13.")
    if calendar.get("lunch", {}).get("after_period") != 5:
        errors.append("Lunch must remain between P5 and P6.")
    if calendar.get("lab_start_periods") != [2, 6]:
        errors.append("Three-hour labs must start at P2 or P6.")

    exam_week = calendar.get("exam_week", {})
    if exam_week.get("days") != ["MON", "TUE", "WED", "THU", "FRI", "SAT"]:
        errors.append("Exam week must run MON through SAT.")
    exam_sessions = {
        item.get("id"): (item.get("start"), item.get("end"))
        for item in exam_week.get("sessions", [])
        if isinstance(item, dict)
    }
    if exam_sessions != {
        "AM": ("09:30", "11:30"),
        "PM": ("14:00", "16:00"),
    }:
        errors.append("Exam sessions must be AM 09:30-11:30 and PM 14:00-16:00.")

    priority_order = data.get("priority_order")
    role_caps = data.get("role_category_cap")
    if not isinstance(priority_order, list) or not priority_order:
        errors.append("priority_order must be a non-empty array.")
    elif (
        not isinstance(role_caps, dict)
        or any(category not in priority_order for category in role_caps.values())
    ):
        errors.append("role_category_cap must reference categories in priority_order.")

    for session in data.get("sessions", []):
        if not isinstance(session, dict):
            continue
        if session.get("kind") not in {"lecture", "tutorial", "lab"}:
            errors.append(f"Session '{session.get('id')}' has an unsupported kind.")
        if (
            not isinstance(session.get("duration"), int)
            or isinstance(session.get("duration"), bool)
            or session["duration"] < 1
        ):
            errors.append(f"Session '{session.get('id')}' must have a positive integer duration.")
        if session.get("kind") == "lab" and session.get("duration") != 3:
            errors.append(
                f"Lab session '{session.get('id')}' must have a three-hour duration."
            )
        fixed_slot = session.get("fixed_slot")
        if fixed_slot is not None:
            if (
                not isinstance(fixed_slot, dict)
                or set(fixed_slot) != {"day", "start_period"}
                or fixed_slot.get("day") not in calendar.get("days", [])
                or not isinstance(fixed_slot.get("start_period"), int)
                or isinstance(fixed_slot.get("start_period"), bool)
                or fixed_slot["start_period"] not in range(1, 10)
            ):
                errors.append(
                    f"Session '{session.get('id')}' has an invalid fixed_slot."
                )
    return errors


def _validate_references(data):
    errors = []
    valid_days = set(data.get("calendar", {}).get("days", []))
    valid_periods = {
        item.get("id")
        for item in data.get("calendar", {}).get("periods", [])
        if isinstance(item, dict)
    }
    ids_by_entity = {
        entity: {
            record.get("id")
            for record in data.get(entity, [])
            if isinstance(record, dict)
        }
        for entity in ("rooms", "faculty", "student_groups", "courses", "sessions", "booking_requests")
    }

    for item in data.get("sessions", []):
        if item.get("faculty_id") not in ids_by_entity["faculty"]:
            errors.append(f"Session '{item.get('id')}' references an unknown faculty member.")
        if item.get("course_id") not in ids_by_entity["courses"]:
            errors.append(f"Session '{item.get('id')}' references an unknown course.")
        for group_id in item.get("groups", []):
            if group_id not in ids_by_entity["student_groups"]:
                errors.append(f"Session '{item.get('id')}' references unknown group '{group_id}'.")
    for item in data.get("room_unavailability", []):
        if item.get("room_id") not in ids_by_entity["rooms"]:
            errors.append(f"Room closure references unknown room '{item.get('room_id')}'.")
        if item.get("day") not in valid_days:
            errors.append(f"Room closure has an invalid teaching day '{item.get('day')}'.")
        if any(period not in valid_periods for period in item.get("periods", [])):
            errors.append(f"Room closure for '{item.get('room_id')}' references an invalid period.")
    faculty_by_id = {
        faculty["id"]: faculty
        for faculty in data.get("faculty", [])
    }
    for faculty in faculty_by_id.values():
        for unavailable in faculty.get("unavailable", []):
            if unavailable.get("day") not in valid_days:
                errors.append(
                    f"Faculty '{faculty['id']}' has an invalid unavailable day."
                )
            if any(
                period not in valid_periods
                for period in unavailable.get("periods", [])
            ):
                errors.append(
                    f"Faculty '{faculty['id']}' has an invalid unavailable period."
                )
    for item in data.get("cancellations", []):
        if item.get("request_id") not in ids_by_entity["booking_requests"]:
            errors.append(f"Cancellation references unknown request '{item.get('request_id')}'.")
    for item in data.get("exams", []):
        for course_id in item.get("course_ids", []):
            if course_id not in ids_by_entity["courses"]:
                errors.append(f"Exam '{item.get('id')}' references unknown course '{course_id}'.")
        for group_id in item.get("groups", []):
            if group_id not in ids_by_entity["student_groups"]:
                errors.append(f"Exam '{item.get('id')}' references unknown group '{group_id}'.")
    return errors


def _history():
    if not HISTORY_PATH.exists():
        return []
    value = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("Dataset version history is invalid.")
    return value


def _create_version(data, reason, actor):
    errors = validate_dataset(data)
    if errors:
        raise DatasetValidationError(errors)
    now = datetime.now(timezone.utc)
    version_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    metadata = {
        "version_id": version_id,
        "dataset_id": data["dataset_id"],
        "created_at": now.isoformat(),
        "reason": reason,
        "actor": actor,
        "record_counts": {
            entity: len(data.get(entity, []))
            for entity in ENTITY_NAMES
        },
    }
    _write_json_atomic(VERSIONS_ROOT / f"{version_id}.json", data)
    _write_json_atomic(ACTIVE_DATASET_PATH, data)
    history = _history()
    history.insert(0, metadata)
    _write_json_atomic(HISTORY_PATH, history)
    return metadata


def ensure_working_dataset(actor="system"):
    with _LOCK:
        if not ACTIVE_DATASET_PATH.exists():
            _create_version(
                load_original_dataset(),
                "Loaded immutable starter-pack dataset",
                actor,
            )
        return load_dataset()


def get_working_snapshot(actor="system"):
    with _LOCK:
        data = ensure_working_dataset(actor)
        history = _history()
        if not history:
            raise RuntimeError("The active dataset has no recorded version.")
        return data, history[0]["version_id"]


def get_active_version_id():
    with _LOCK:
        if not ACTIVE_DATASET_PATH.exists():
            return None
        history = _history()
        return history[0]["version_id"] if history else None


def get_dataset_metadata():
    data = ensure_working_dataset()
    history = _history()
    return {
        "dataset_id": data["dataset_id"],
        "version_id": history[0]["version_id"] if history else None,
        "record_counts": {
            entity: len(data.get(entity, []))
            for entity in ENTITY_NAMES
        },
        "entities": list(ENTITY_NAMES),
        "csv_files": CSV_FILENAMES,
        "history": history,
        "source_dataset_path": str(DATASET_PATH),
        "original_is_read_only": True,
    }


def get_entity_records(entity):
    if entity not in ENTITY_NAMES:
        raise ValueError(f"Unsupported dataset entity: {entity}")
    return deepcopy(ensure_working_dataset().get(entity, []))


def save_entity_record(entity, record_id, record, actor):
    if entity not in ENTITY_NAMES or not isinstance(record, dict):
        raise ValueError("An allowed entity and a record object are required.")
    with _LOCK:
        data = ensure_working_dataset(actor)
        records = data[entity]
        if entity == "room_unavailability":
            try:
                index = int(record_id)
            except ValueError:
                index = -1
            if index < 0 or index >= len(records):
                index = None
        else:
            key_field = ENTITY_KEY_FIELDS[entity]
            index = next(
                (
                    i
                    for i, current in enumerate(records)
                    if str(current.get(key_field)) == record_id
                ),
                None,
            )
        if index is None:
            raise KeyError(f"No {entity} record exists with id '{record_id}'.")
        updated = deepcopy(data)
        updated[entity][index] = record
        metadata = _create_version(updated, f"Edited {entity} record {record_id}", actor)
    return metadata


def replace_dataset(data, filename, actor):
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_DATASET_BYTES:
        raise ValueError("Dataset exceeds the 10 MB upload limit.")
    with _LOCK:
        metadata = _create_version(data, f"Uploaded dataset: {filename}", actor)
    return metadata


def load_starter_pack(actor):
    with _LOCK:
        metadata = _create_version(
            load_original_dataset(),
            "Loaded immutable starter-pack dataset",
            actor,
        )
    return metadata


def restore_version(version_id, actor):
    path = VERSIONS_ROOT / f"{version_id}.json"
    if not path.is_file() or path.parent != VERSIONS_ROOT:
        raise KeyError(f"Dataset version '{version_id}' was not found.")
    restored = json.loads(path.read_text(encoding="utf-8"))
    with _LOCK:
        return _create_version(restored, f"Restored version {version_id}", actor)


def _parse_csv_value(raw, sample, label):
    value = raw.strip()
    if sample is None:
        if value == "":
            return None
        if value.startswith(("[", "{")):
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return parsed
        return raw
    if isinstance(sample, bool):
        if value.lower() not in {"true", "false"}:
            raise ValueError(f"{label} must be True or False.")
        return value.lower() == "true"
    if isinstance(sample, int) and not isinstance(sample, bool):
        if value == "":
            raise ValueError(f"{label} must be an integer.")
        return int(value)
    if isinstance(sample, float):
        if value == "":
            raise ValueError(f"{label} must be a number.")
        return float(value)
    if isinstance(sample, (list, dict)):
        if not value:
            return [] if isinstance(sample, list) else {}
        parsed = json.loads(value)
        if not isinstance(parsed, type(sample)):
            raise ValueError(f"{label} must contain JSON {type(sample).__name__}.")
        return parsed
    return value


def import_csv(entity, filename, content, actor):
    if entity not in ENTITY_NAMES or CSV_FILENAMES.get(entity) != filename:
        raise ValueError("CSV filename must match the selected starter-pack entity.")
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        raise ValueError("CSV upload exceeds the 5 MB limit.")
    original = load_original_dataset()
    reference = original[entity]
    try:
        reader = csv.DictReader(io.StringIO(content, newline=""))
        headers = reader.fieldnames or []
        expected = list(_entity_sample(original, entity))
        if headers != expected:
            raise ValueError(
                f"CSV columns must match the starter-pack order: {', '.join(expected)}."
            )
        rows = []
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(row.get(key) is None for key in expected):
                raise ValueError(
                    f"row {row_number} has a different number of columns than the header."
                )
            converted = {}
            for key in expected:
                sample = _field_sample(reference, key)
                converted[key] = _parse_csv_value(
                    row[key] or "",
                    sample,
                    f"row {row_number}, {key}",
                )
            rows.append(converted)
            if len(rows) > 10_000:
                raise ValueError("CSV contains more than 10,000 records.")
    except (csv.Error, json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"Invalid CSV content: {error}") from error
    with _LOCK:
        updated = ensure_working_dataset(actor)
        updated[entity] = rows
        metadata = _create_version(updated, f"Imported {filename}", actor)
    return metadata


def export_entity_csv(entity):
    if entity not in ENTITY_NAMES:
        raise ValueError(f"Unsupported dataset entity: {entity}")
    data = ensure_working_dataset()
    records = data.get(entity, [])
    fields = list(_entity_sample(load_original_dataset(), entity))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
    writer.writeheader()
    for record in records:
        row = {}
        for key in fields:
            value = record.get(key)
            if isinstance(value, (list, dict)):
                row[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            elif isinstance(value, bool):
                row[key] = str(value)
            elif value is None:
                row[key] = ""
            else:
                row[key] = value
        writer.writerow(row)
    return stream.getvalue()


def validate_disruption_schema(disruption):
    if not isinstance(disruption, dict) or not isinstance(disruption.get("changes"), list):
        return ["Disruption must be an object with a changes array."]
    errors = []
    allowed = {
        "faculty_unavailable": {"type", "faculty_id", "days", "periods", "reason"},
        "room_unavailable": {"type", "room_id", "days", "periods", "reason"},
        "add_faculty": {"type", "faculty"},
        "add_session": {"type", "session"},
        "remove_session": {"type", "session_id"},
    }
    required = {
        "faculty_unavailable": {"faculty_id", "days", "periods"},
        "room_unavailable": {"room_id", "days", "periods"},
        "add_faculty": {"faculty"},
        "add_session": {"session"},
        "remove_session": {"session_id"},
    }
    for index, change in enumerate(disruption["changes"]):
        label = f"changes[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{label} must be an object.")
            continue
        change_type = change.get("type")
        if change_type not in allowed:
            errors.append(f"{label} has unsupported disruption type '{change_type}'.")
            continue
        missing = required[change_type] - set(change)
        extra = set(change) - allowed[change_type]
        if missing:
            errors.append(f"{label} is missing keys: {sorted(missing)}.")
        if extra:
            errors.append(f"{label} has unsupported keys: {sorted(extra)}.")
    return errors


def apply_disruption_version(disruption, actor):
    from app.rescheduler.crisis_engine import apply_disruption

    errors = validate_disruption_schema(disruption)
    if errors:
        raise DatasetValidationError(errors)
    with _LOCK:
        current = ensure_working_dataset(actor)
        current_session_ids = {
            session["id"] for session in current["sessions"]
        }
        current_room_ids = {
            room["id"] for room in current["rooms"]
        }
        for index, change in enumerate(disruption["changes"]):
            if (
                change.get("type") == "remove_session"
                and change.get("session_id") not in current_session_ids
            ):
                raise DatasetValidationError([
                    f"changes[{index}] references an unknown session."
                ])
            if (
                change.get("type") == "room_unavailable"
                and change.get("room_id") not in current_room_ids
            ):
                raise DatasetValidationError([
                    f"changes[{index}] references an unknown room."
                ])
        history = _history()
        base_version = history[0]["version_id"] if history else None
        try:
            updated = apply_disruption(current, disruption)
        except (KeyError, ValueError, TypeError) as error:
            raise DatasetValidationError([str(error)]) from error
        validation_errors = validate_dataset(updated)
        if validation_errors:
            raise DatasetValidationError(validation_errors)
        metadata = _create_version(
            updated,
            "Applied starter-pack disruption",
            actor,
        )
        _write_json_atomic(DISRUPTION_STATE_PATH, {
            "disruption": disruption,
            "base_version_id": base_version,
            "applied_version_id": metadata["version_id"],
        })
    return metadata


def get_disruption_state():
    if not DISRUPTION_STATE_PATH.exists():
        return None
    return json.loads(DISRUPTION_STATE_PATH.read_text(encoding="utf-8"))
