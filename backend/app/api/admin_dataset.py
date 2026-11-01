import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, PlainTextResponse

from app.auth import AuthenticatedPrincipal, require_roles
from app.booking.booking_engine import generate_booking_schedule
from app.rescheduler.crisis_engine import generate_crisis_schedule
from app.scheduler.exam_scheduler import solve_exam_schedule
from app.scheduler.timetable import solve_timetable
from app.services.dataset_loader import ACTIVE_DATASET_PATH
from app.services.starter_pack_manager import (
    ENTITY_NAMES,
    MANAGER_ROOT,
    PROJECT_ROOT,
    SCHEDULE_STATE_PATH,
    STARTER_ROOT,
    VALIDATOR_PATH,
    VERSIONS_ROOT,
    DatasetValidationError,
    ENTITY_KEY_FIELDS,
    _write_json_atomic,
    apply_disruption_version,
    ensure_working_dataset,
    export_entity_csv,
    get_dataset_metadata,
    get_disruption_state,
    get_dataset_metadata,
    get_entity_records,
    get_working_snapshot,
    import_csv,
    load_starter_pack,
    replace_dataset,
    restore_version,
    save_entity_record,
    validate_dataset,
)


router = APIRouter(
    prefix="/api/admin",
    tags=["admin-dataset"],
    dependencies=[Depends(require_roles("ADMIN"))],
)
EXPORTS_ROOT = PROJECT_ROOT / "exports"
MAX_JSON_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CSV_REQUEST_BYTES = 7 * 1024 * 1024


def _actor(principal):
    return principal.username or principal.subject


async def _json_body(request, limit):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Upload exceeds the size limit.")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Upload exceeds the size limit.")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Request must contain valid JSON.") from error


def _raise_dataset_error(error):
    if isinstance(error, DatasetValidationError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "Dataset validation failed.", "errors": error.errors},
        ) from error
    if isinstance(error, KeyError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


def _write_export(name, data):
    EXPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_ROOT / name
    _write_json_atomic(path, data)
    return path


def _temporary_export_path(label):
    return EXPORTS_ROOT / f".{label}-{uuid.uuid4().hex}.json"


def _current_version_id():
    return get_dataset_metadata()["version_id"]


def _schedule_state():
    if not SCHEDULE_STATE_PATH.is_file():
        return {}
    value = json.loads(SCHEDULE_STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Schedule preview version state is invalid.")
    return value


def _record_preview_version(name, version_id):
    state = _schedule_state()
    state[name] = version_id
    _write_json_atomic(SCHEDULE_STATE_PATH, state)


def _official_schedule(data, sessions, bookings, exams, session_overrides=None):
    session_ids = {session["id"] for session in data["sessions"]}
    exam_ids = {exam["id"] for exam in data["exams"]}
    if len(sessions) != len(session_ids) or {
        item.get("session_id") for item in sessions
    } != session_ids:
        raise ValueError("Timetable assignments do not cover every dataset session exactly once.")
    if len(exams) != len(exam_ids) or {
        item.get("exam_id") for item in exams
    } != exam_ids:
        raise ValueError("Exam assignments do not cover every dataset exam exactly once.")
    request_ids = {
        request["id"] for request in data["booking_requests"]
    }
    decision_ids = [item.get("request_id") for item in bookings]
    if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != request_ids:
        raise ValueError("Booking decisions must cover every request exactly once.")
    cancelled_ids = {
        item["request_id"] for item in data.get("cancellations", [])
    }

    official_sessions = [
        {
            "session_id": item["session_id"],
            "day": item["day"],
            "start_period": item["start_period"],
            "room_id": item["room_id"],
        }
        for item in sessions
    ]
    official_bookings = []
    for item in bookings:
        status_value = item["status"]
        if status_value not in {"approved", "rejected", "alternative_offered"}:
            raise ValueError(
                f"Booking {item['request_id']} has an unsupported status."
            )
        if (
            item["request_id"] in cancelled_ids
            and status_value in {"approved", "alternative_offered"}
        ):
            raise ValueError(
                f"Cancelled request {item['request_id']} cannot be approved or offered an alternative."
            )
        record = {"request_id": item["request_id"], "status": status_value}
        if status_value in {"approved", "alternative_offered"}:
            required_slot_fields = ("day", "start_period", "end_period", "room_id")
            if any(key not in item for key in required_slot_fields):
                raise ValueError(
                    f"Booking {item['request_id']} is missing its allocated slot."
                )
            for key in required_slot_fields:
                record[key] = item[key]
        if status_value in {"rejected", "alternative_offered"}:
            reason = str(item.get("reason", "")).strip()
            if not reason:
                raise ValueError(
                    f"Booking decision {item['request_id']} requires a reason."
                )
            record["reason"] = reason
        official_bookings.append(record)

    return {
        "team": "CIT Nexus",
        "dataset_id": data["dataset_id"],
        "sessions": official_sessions,
        "fest_week": {
            "bookings": official_bookings,
            "session_overrides": session_overrides or [],
        },
        "exams": [
            {
                "exam_id": item["exam_id"],
                "day": item["day"],
                "session": item["session"],
                "rooms": item["rooms"],
            }
            for item in exams
        ],
    }


def _run_validator(schedule_path, data_path, disruption_path=None, baseline_path=None):
    command = [
        sys.executable,
        str(VALIDATOR_PATH),
        "--data",
        str(data_path),
        "--schedule",
        str(schedule_path),
        "--json",
    ]
    report_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        dir=MANAGER_ROOT,
        delete=False,
    )
    report_path = Path(report_file.name)
    report_file.close()
    command.append(str(report_path))
    if disruption_path:
        command.extend(["--disruption", str(disruption_path)])
    if baseline_path:
        command.extend(["--baseline", str(baseline_path)])
    try:
        completed = subprocess.run(
            command,
            cwd=STARTER_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if not report_path.exists():
            raise RuntimeError(
                "Official validator did not produce a report. "
                f"Exit code: {completed.returncode}. {completed.stderr[-1000:]}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["valid"] = (
            completed.returncode == 0
            and report.get("hard_violation_count") == 0
        )
        report["validator_exit_code"] = completed.returncode
        report["validator_output"] = completed.stdout[-6000:]
        report["validator_errors"] = completed.stderr[-2000:]
        return report
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise RuntimeError(f"Official validator failed: {error}") from error
    finally:
        report_path.unlink(missing_ok=True)


def _validator_response(report, data, schedule):
    scheduled_sessions = {
        item.get("session_id") for item in schedule.get("sessions", [])
    }
    scheduled_exams = {
        item.get("exam_id") for item in schedule.get("exams", [])
    }
    report["unscheduled_sessions"] = len(
        {item["id"] for item in data.get("sessions", [])} - scheduled_sessions
    )
    report["unscheduled_exams"] = len(
        {item["id"] for item in data.get("exams", [])} - scheduled_exams
    )
    report["scheduling_errors"] = report.get("hard_violations", [])
    report["booking_score"] = report.get("bookings", {}).get("booking_score")
    report["priority_inversions"] = report.get("bookings", {}).get(
        "priority_inversions", []
    )
    return report


@router.get("/dataset")
def dataset_metadata(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    del principal
    try:
        return get_dataset_metadata()
    except (OSError, ValueError) as error:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(error)) from error


@router.post("/dataset/load-starter")
def load_starter(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    try:
        version = load_starter_pack(_actor(principal))
        return {"message": "Starter-pack dataset loaded as a versioned working copy.", "version": version}
    except (OSError, ValueError) as error:
        _raise_dataset_error(error)


@router.get("/dataset/records/{entity}")
def list_records(
    entity: str,
    offset: int = 0,
    limit: int = 500,
    search: str = "",
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    if entity not in ENTITY_NAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown dataset entity.")
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use offset >= 0 and limit from 1 to 1000.")
    records = list(enumerate(get_entity_records(entity)))
    if search:
        query = search.casefold()
        records = [
            (index, record) for index, record in records
            if query in json.dumps(record, ensure_ascii=False).casefold()
        ]
    page_records = records[offset:offset + limit]
    key_field = ENTITY_KEY_FIELDS.get(entity)
    return {
        "entity": entity,
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "records": [record for _, record in page_records],
        "record_keys": [
            str(record.get(key_field)) if key_field else str(index)
            for index, record in page_records
        ],
    }


@router.put("/dataset/records/{entity}/{record_id}")
def edit_record(
    entity: str,
    record_id: str,
    record: dict,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        version = save_entity_record(entity, record_id, record, _actor(principal))
        return {"message": "Validated record saved as a new dataset version.", "version": version}
    except (DatasetValidationError, KeyError, ValueError) as error:
        _raise_dataset_error(error)


@router.post("/dataset/validate")
def validate_working_dataset(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    del principal
    errors = validate_dataset(ensure_working_dataset())
    return {"valid": not errors, "errors": errors}


@router.post("/dataset/upload")
async def upload_dataset(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    body = await _json_body(request, MAX_JSON_UPLOAD_BYTES)
    if not isinstance(body, dict) or not isinstance(body.get("dataset"), dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expected filename and dataset fields.")
    filename = body.get("filename")
    if filename not in {"cit_public.json", "dataset.json"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload a JSON dataset file.")
    try:
        version = replace_dataset(body["dataset"], filename, _actor(principal))
        return {"message": "Validated dataset saved as a versioned working copy.", "version": version}
    except (DatasetValidationError, ValueError, OSError) as error:
        _raise_dataset_error(error)


@router.post("/dataset/import-csv")
async def upload_csv(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    body = await _json_body(request, MAX_CSV_REQUEST_BYTES)
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expected entity, filename, and CSV content.")
    try:
        version = import_csv(
            body.get("entity", ""),
            body.get("filename", ""),
            body["content"],
            _actor(principal),
        )
        return {"message": "CSV imported and validated as a new dataset version.", "version": version}
    except (DatasetValidationError, ValueError, OSError) as error:
        _raise_dataset_error(error)


@router.get("/dataset/export/json")
def export_dataset_json(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    del principal
    ensure_working_dataset()
    return FileResponse(
        ACTIVE_DATASET_PATH,
        media_type="application/json",
        filename="cit_public-working.json",
    )


@router.get("/dataset/export/csv/{entity}")
def export_dataset_csv(
    entity: str,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    try:
        content = export_entity_csv(entity)
    except ValueError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return PlainTextResponse(content, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{entity}.csv"',
    })


@router.post("/dataset/versions/{version_id}/restore")
def restore_dataset_version(
    version_id: str,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    try:
        version = restore_version(version_id, _actor(principal))
        return {"message": "Version restored as a new working-copy version.", "version": version}
    except (KeyError, DatasetValidationError, OSError, ValueError) as error:
        _raise_dataset_error(error)


@router.post("/dataset/disruptions")
async def import_disruption(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    body = await _json_body(request, 1024 * 1024)
    if not isinstance(body, dict) or not isinstance(body.get("disruption"), dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expected filename and disruption fields.")
    filename = body.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix.lower() != ".json":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload a disruption JSON file.")
    try:
        version = apply_disruption_version(body["disruption"], _actor(principal))
        return {"message": "Disruption applied to a new working dataset version.", "version": version}
    except (DatasetValidationError, ValueError, KeyError, OSError) as error:
        _raise_dataset_error(error)


@router.post("/schedule/generate-timetable")
def generate_admin_timetable(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    try:
        data, version_id = get_working_snapshot(_actor(principal))
        result = solve_timetable(data)
        if result["assignments"] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"No feasible timetable was found (OR-Tools: {result['solver_status']}).",
            )
        preview = {"team": "CIT Nexus", "dataset_id": data["dataset_id"], "sessions": result["assignments"]}
        if _current_version_id() != version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed while the timetable was being generated.")
        _write_export("sessions_preview.json", preview)
        _record_preview_version("sessions_version_id", version_id)
        return {
            "message": "Timetable generated by the existing OR-Tools scheduler.",
            "solver_status": result["solver_status"],
            "objective": result["objective"],
            "session_count": len(result["assignments"]),
            "preview": result["assignments"],
        }
    except HTTPException:
        raise
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


@router.post("/schedule/generate-exams")
def generate_admin_exams(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    try:
        data, version_id = get_working_snapshot(_actor(principal))
        result = solve_exam_schedule(data)
        if result["assignments"] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"No feasible exam schedule was found (OR-Tools: {result['solver_status']}).",
            )
        if _current_version_id() != version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed while exams were being generated.")
        _write_export("exams_preview.json", result["assignments"])
        _record_preview_version("exams_version_id", version_id)
        return {
            "message": "Exam allocations generated by the existing OR-Tools scheduler.",
            "solver_status": result["solver_status"],
            "exam_count": len(result["assignments"]),
            "validation": result["validation"],
        }
    except HTTPException:
        raise
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


@router.post("/schedule/process-bookings")
def process_admin_bookings(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    try:
        data, version_id = get_working_snapshot(_actor(principal))
        sessions_path = EXPORTS_ROOT / "sessions_preview.json"
        if not sessions_path.is_file():
            raise HTTPException(status.HTTP_409_CONFLICT, "Generate the timetable before processing bookings.")
        sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
        if sessions.get("dataset_id") != data["dataset_id"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "Timetable preview belongs to a different dataset.")
        if _schedule_state().get("sessions_version_id") != version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Regenerate the timetable after the working dataset changes.")
        decisions = generate_booking_schedule(data, sessions)
        if _current_version_id() != version_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed while booking requests were being processed.")
        _write_export("bookings_preview.json", decisions)
        _record_preview_version("bookings_version_id", version_id)
        return {"message": "Booking requests processed using the starter-pack priority rules.", "decisions": decisions}
    except HTTPException:
        raise
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


@router.post("/schedule/export")
def export_official_schedule(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    del principal
    try:
        data, version_id = get_working_snapshot()
        session_preview = json.loads((EXPORTS_ROOT / "sessions_preview.json").read_text(encoding="utf-8"))
        booking_preview = json.loads((EXPORTS_ROOT / "bookings_preview.json").read_text(encoding="utf-8"))
        exam_preview = json.loads((EXPORTS_ROOT / "exams_preview.json").read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Generate all three previews before export: {error}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid preview output: {error}") from error
    preview_versions = _schedule_state()
    if any(
        preview_versions.get(key) != version_id
        for key in ("sessions_version_id", "exams_version_id", "bookings_version_id")
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Regenerate timetable, exams, and bookings for the active dataset version before exporting.",
        )
    if any(item.get("dataset_id") != data["dataset_id"] for item in (session_preview,)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Preview belongs to a different dataset.")
    try:
        schedule = _official_schedule(
            data,
            session_preview.get("sessions", []),
            booking_preview,
            exam_preview,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Schedule previews do not match the official export schema: {error}",
        ) from error
    temporary = _temporary_export_path("schedule-validation-candidate")
    try:
        _write_json_atomic(temporary, schedule)
        report = _validator_response(
            _run_validator(temporary, ACTIVE_DATASET_PATH),
            data,
            schedule,
        )
        if report["valid"]:
            if _current_version_id() != version_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Dataset changed during official schedule validation.")
            path = _write_export("schedule.json", schedule)
            report["export_path"] = str(path)
            preview_versions["schedule_version_id"] = version_id
            _write_json_atomic(SCHEDULE_STATE_PATH, preview_versions)
        else:
            report["export_path"] = None
        return {"schedule": schedule, "validation": report}
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/schedule/disruption/repair")
def repair_disrupted_schedule(principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN"))):
    del principal
    state = get_disruption_state()
    if not state:
        raise HTTPException(status.HTTP_409_CONFLICT, "Import and apply a disruption first.")
    baseline_path = EXPORTS_ROOT / "schedule.json"
    if not baseline_path.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "Export a validator-approved baseline schedule first.")
    base_version_id = state.get("base_version_id")
    base_data_path = VERSIONS_ROOT / f"{base_version_id}.json"
    if not base_version_id or not base_data_path.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "The baseline dataset version for this disruption is missing.")
    if _schedule_state().get("schedule_version_id") != base_version_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The disruption must be repaired against the version used to export schedule.json.",
        )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    data, active_version_id = get_working_snapshot()
    if active_version_id != state.get("applied_version_id"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Working dataset changed after this disruption was applied.")
    temporary = _temporary_export_path("schedule-after-validation-candidate")
    try:
        sessions, _evidence = generate_crisis_schedule(
            data,
            baseline,
            return_solver_evidence=True,
        )
        changed = dict(baseline)
        changed["dataset_id"] = data["dataset_id"]
        changed["sessions"] = sessions
        decisions = generate_booking_schedule(
            data,
            {"sessions": sessions, "dataset_id": data["dataset_id"]},
        )
        if _current_version_id() != active_version_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Working dataset changed while the disruption was being repaired.",
            )
        changed.setdefault("fest_week", {})["bookings"] = decisions
        _write_json_atomic(temporary, changed)
        disruption_path = MANAGER_ROOT / "active_disruption.json"
        _write_json_atomic(disruption_path, state["disruption"])
        report = _validator_response(
            _run_validator(
                temporary,
                base_data_path,
                disruption_path,
                baseline_path,
            ),
            data,
            changed,
        )
        output_path = None
        if report["valid"]:
            output_path = _write_export("schedule_after.json", changed)
            schedule_state = _schedule_state()
            schedule_state["schedule_after_version_id"] = state["applied_version_id"]
            _write_json_atomic(SCHEDULE_STATE_PATH, schedule_state)
        return {
            "message": "Disruption repair completed using the existing rescheduler.",
            "schedule": changed,
            "validation": report,
            "export_path": str(output_path) if output_path else None,
        }
    except HTTPException:
        raise
    except (ValueError, KeyError, OSError, RuntimeError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/schedule/validate")
def validate_exported_schedule(
    after_disruption: bool = False,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    schedule_path = EXPORTS_ROOT / ("schedule_after.json" if after_disruption else "schedule.json")
    if not schedule_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Export not found: {schedule_path.name}")
    ensure_working_dataset()
    try:
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        if after_disruption:
            state = get_disruption_state()
            if not state:
                raise HTTPException(status.HTTP_409_CONFLICT, "Disruption metadata is missing.")
            version_path = VERSIONS_ROOT / f"{state['base_version_id']}.json"
            disruption_path = MANAGER_ROOT / "active_disruption.json"
            baseline_path = EXPORTS_ROOT / "schedule.json"
            if not version_path.is_file() or not baseline_path.is_file():
                raise HTTPException(status.HTTP_409_CONFLICT, "Disruption baseline files are missing.")
            if _schedule_state().get("schedule_after_version_id") != state.get("applied_version_id"):
                raise HTTPException(status.HTTP_409_CONFLICT, "The disruption repair output is stale.")
            validation_data_path = version_path
            report = _run_validator(schedule_path, validation_data_path, disruption_path, baseline_path)
            validation_data = json.loads(validation_data_path.read_text(encoding="utf-8"))
        else:
            version_id = _schedule_state().get("schedule_version_id")
            validation_data_path = VERSIONS_ROOT / f"{version_id}.json" if version_id else None
            if not validation_data_path or not validation_data_path.is_file():
                raise HTTPException(status.HTTP_409_CONFLICT, "The baseline dataset version for this schedule is missing.")
            report = _run_validator(schedule_path, validation_data_path)
            validation_data = json.loads(validation_data_path.read_text(encoding="utf-8"))
        return _validator_response(report, validation_data, schedule)
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error


@router.post("/schedule/export-after-disruption")
def export_schedule_after_disruption(
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    report = validate_exported_schedule(after_disruption=True, principal=principal)
    if not report["valid"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "schedule_after.json was not exported because the official validator found hard violations.",
        )
    schedule_path = EXPORTS_ROOT / "schedule_after.json"
    try:
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        path = _write_export("schedule_after.json", schedule)
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    return {
        "message": "schedule_after.json passed the official disruption validator and was exported.",
        "export_path": str(path),
        "validation": report,
    }


@router.get("/schedule/download/{filename}")
def download_schedule(
    filename: str,
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    if filename not in {"schedule.json", "schedule_after.json"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown schedule export.")
    path = EXPORTS_ROOT / filename
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{filename} has not been generated.")
    return FileResponse(path, media_type="application/json", filename=filename)
