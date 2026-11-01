
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pathlib import Path
from app.auth import AuthenticatedPrincipal, require_roles

from app.rescheduler.crisis_engine import (
    run_crisis_mode,
    run_what_if_simulation,
)
from app.services.dataset_loader import load_dataset


router = APIRouter(
    prefix="/api/crisis",
    tags=["Campus Crisis Mode"]
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = PROJECT_ROOT / "exports"

VALIDATION_PATH = EXPORT_DIR / "crisis_validation.json"
EXPLANATIONS_PATH = EXPORT_DIR / "crisis_explanations.json"
BASELINE_PATH = EXPORT_DIR / "schedule_baseline_98.json"
AFTER_PATH = EXPORT_DIR / "schedule_after.json"


def read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


class WhatIfRequest(BaseModel):
    disruption_type: Literal["faculty_unavailable", "room_unavailable"]
    resource_id: str = Field(min_length=1, max_length=100)
    days: list[str] = Field(min_length=1, max_length=5)
    periods: list[int] = Field(min_length=1, max_length=9)
    reason: str = Field(default="What-if scenario", max_length=200)


def compare_schedules(baseline, revised, explanation_report):
    explanations_by_session = {
        item["session_id"]: item
        for item in explanation_report.get("moved_sessions", [])
    }
    old_map = {
        item["session_id"]: item
        for item in baseline.get("sessions", [])
    }
    new_map = {
        item["session_id"]: item
        for item in revised.get("sessions", [])
    }

    moved = []
    unchanged = []
    added = []
    removed = []

    for session_id, new in new_map.items():
        old = old_map.get(session_id)
        if old is None:
            added.append(new)
            continue

        if any(
            old.get(key) != new.get(key)
            for key in ("day", "start_period", "room_id")
        ):
            explanation = explanations_by_session.get(session_id)
            time_changed = (
                old["day"] != new["day"]
                or old["start_period"] != new["start_period"]
            )
            room_changed = old["room_id"] != new["room_id"]
            change_type = (
                "TIME_AND_ROOM"
                if time_changed and room_changed
                else "TIME_ONLY"
                if time_changed
                else "ROOM_ONLY"
            )
            item = {
                "session_id": session_id,
                "original": old,
                "revised": new,
                "change_type": change_type,
                "explanation_type": (
                    explanation.get(
                        "explanation_type",
                        "INSUFFICIENT_EVIDENCE",
                    )
                    if explanation
                    else "INSUFFICIENT_EVIDENCE"
                ),
                "confidence": (
                    explanation.get("confidence", "LOW")
                    if explanation
                    else "LOW"
                ),
                "explanation": explanation or {
                    "session_id": session_id,
                    "original": old,
                    "revised": new,
                    "change_type": change_type,
                    "explanation_type": "INSUFFICIENT_EVIDENCE",
                    "confidence": "LOW",
                    "explanation": "Precise cause unavailable.",
                    "hard_constraint_evidence": [],
                    "objective_contributions": {},
                    "shared_objective_effects": [],
                },
            }
            if explanation:
                item.update({
                    "course": explanation.get("course"),
                    "faculty": explanation.get("faculty"),
                    "groups": explanation.get("groups", []),
                    "rooms": explanation.get("rooms"),
                })
            moved.append(item)
        else:
            unchanged.append(session_id)

    for session_id, old in old_map.items():
        if session_id not in new_map:
            removed.append(old)

    moved.sort(key=lambda item: item["session_id"])
    return {
        "summary": {
            "original_sessions": len(old_map),
            "revised_sessions": len(new_map),
            "moved_sessions": len(moved),
            "unchanged_sessions": len(unchanged),
            "added_sessions": len(added),
            "removed_sessions": len(removed),
        },
        "moved": moved,
        "unchanged": unchanged,
        "added": added,
        "removed": removed,
        "explanation_summary": {
            "recorded_moved_sessions": len(explanations_by_session),
            "solver": explanation_report.get("solver"),
            "shared_objective_totals": explanation_report.get(
                "shared_objective_totals", {}
            ),
            "shared_objective_effects": explanation_report.get(
                "shared_objective_effects", []
            ),
        },
    }


@router.post("/run")
def run_crisis(
    principal: AuthenticatedPrincipal = Depends(require_roles("ADMIN")),
):
    del principal
    try:
        result = run_crisis_mode()

        return {
            "status": "success",
            "message": "Campus crisis rescheduling completed.",
            "data": result
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


@router.get("/options")
def get_what_if_options(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY")
    ),
):
    del principal
    data = load_dataset()
    return {
        "status": "success",
        "data": {
            "days": data["calendar"]["days"],
            "periods": [
                period["id"]
                for period in data["calendar"]["periods"]
                if period["teaching"]
            ],
            "rooms": [
                {"id": room["id"], "name": room.get("name", room["id"])}
                for room in data["rooms"]
            ],
            "faculty": [
                {
                    "id": faculty["id"],
                    "name": faculty.get("name", faculty["id"]),
                }
                for faculty in data["faculty"]
            ],
        },
    }


@router.post("/what-if")
def run_what_if(
    request: WhatIfRequest,
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY")
    ),
):
    del principal
    data = load_dataset()
    valid_days = set(data["calendar"]["days"])
    valid_periods = {
        period["id"]
        for period in data["calendar"]["periods"]
        if period["teaching"]
    }
    if len(set(request.days)) != len(request.days):
        raise HTTPException(status_code=422, detail="Days must not contain duplicates.")
    if len(set(request.periods)) != len(request.periods):
        raise HTTPException(
            status_code=422,
            detail="Periods must not contain duplicates.",
        )
    if not set(request.days).issubset(valid_days):
        raise HTTPException(status_code=422, detail="One or more days are invalid.")
    if not set(request.periods).issubset(valid_periods):
        raise HTTPException(
            status_code=422,
            detail="One or more periods are not teaching periods.",
        )

    if request.disruption_type == "faculty_unavailable":
        resource_ids = {faculty["id"] for faculty in data["faculty"]}
        resource_key = "faculty_id"
    else:
        resource_ids = {room["id"] for room in data["rooms"]}
        resource_key = "room_id"

    if request.resource_id not in resource_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown resource: {request.resource_id}",
        )

    disruption = {
        "id": "what-if-simulation",
        "dataset": data["dataset_id"],
        "changes": [{
            "type": request.disruption_type,
            resource_key: request.resource_id,
            "days": request.days,
            "periods": request.periods,
            "reason": request.reason.strip() or "What-if scenario",
        }],
    }

    try:
        result = run_what_if_simulation(disruption)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    comparison = compare_schedules(
        result["baseline"],
        result["schedule"],
        result["explanations"],
    )
    solver = result["solver_evidence"]
    return {
        "status": "success",
        "message": "What-if schedule generated. The official timetable was not changed.",
        "simulation_only": True,
        "disruption": disruption["changes"][0],
        **comparison,
        "validation": {
            "solver_status": solver["status"],
            "hard_constraints_satisfied": True,
            "objective_value": solver["objective_value"],
            "best_objective_bound": solver["best_objective_bound"],
        },
        "bookings_processed": result["bookings_processed"],
    }


@router.get("/report")
def get_crisis_report(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY")
    ),
):
    del principal
    if not VALIDATION_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No crisis validation report found. Run Crisis Mode first."
        )

    try:
        return {
            "status": "success",
            "report": read_json(VALIDATION_PATH)
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


@router.get("/compare")
def compare_crisis_schedules(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY")
    ),
):
    del principal
    if not BASELINE_PATH.exists() or not AFTER_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Baseline or revised timetable not found. Run Crisis Mode first."
        )

    try:
        baseline = read_json(BASELINE_PATH)
        revised = read_json(AFTER_PATH)
        explanation_report = (
            read_json(EXPLANATIONS_PATH)
            if EXPLANATIONS_PATH.exists()
            else {}
        )
        return {
            "status": "success",
            **compare_schedules(baseline, revised, explanation_report),
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
