from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from app.auth import AuthenticatedPrincipal, require_roles
from app.scheduler.exam_pdf import generate_exam_timetable_pdf
from app.scheduler.exam_scheduler import solve_exam_schedule


router = APIRouter(prefix="/api/exams", tags=["Examinations"])


def _solve_or_raise():
    result = solve_exam_schedule()
    if result["assignments"] is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No feasible examination timetable was found. "
                f"Solver status: {result['solver_status']}"
            ),
        )

    if not result["validation"]["hard_constraints_satisfied"]:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Generated examination timetable failed validation.",
                "violations": result["validation"]["hard_violations"],
            },
        )

    return result


@router.get("")
def get_exam_timetable(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    return _format_exam_timetable(_solve_or_raise())


def _format_exam_timetable(result):
    data = result["data"]
    courses = {
        course["id"]: course
        for course in data["courses"]
    }
    rooms = {
        room["id"]: room
        for room in data["rooms"]
    }
    exam_by_id = {
        exam["id"]: exam
        for exam in data["exams"]
    }
    session_by_id = {
        session["id"]: session
        for session in data["calendar"]["exam_week"]["sessions"]
    }
    group_names = {
        group["id"]: group["name"]
        for group in data["student_groups"]
    }

    exams = []
    for assignment in result["assignments"]:
        exam = exam_by_id[assignment["exam_id"]]
        exam_session = session_by_id[assignment["session"]]
        course_names = [
            courses[course_id]["title"]
            if course_id in courses
            else course_id
            for course_id in exam["course_ids"]
        ]
        allocated_capacity = sum(
            rooms[room_id]["capacity"] // 2
            for room_id in assignment["rooms"]
        )
        exams.append({
            **assignment,
            "course_ids": exam["course_ids"],
            "course_names": course_names,
            "groups": [
                {
                    "id": group_id,
                    "name": group_names.get(group_id, group_id),
                }
                for group_id in exam["groups"]
            ],
            "date": None,
            "size": exam["size"],
            "student_count": exam["size"],
            "allocated_capacity": allocated_capacity,
            "total_allocated_capacity": allocated_capacity,
            "capacity_sufficient": allocated_capacity >= exam["size"],
            "duration_hours": exam["duration_hours"],
            "start_time": exam_session["start"],
            "end_time": exam_session["end"],
            "rooms": [
                {
                    "id": room_id,
                    "name": rooms[room_id]["name"],
                    "building": rooms[room_id]["building"],
                    "exam_capacity": rooms[room_id]["capacity"] // 2,
                    "capacity": rooms[room_id]["capacity"] // 2,
                }
                for room_id in assignment["rooms"]
            ],
        })

    day_order = {
        day: index
        for index, day in enumerate(data["calendar"]["exam_week"]["days"])
    }
    session_order = {
        session["id"]: index
        for index, session in enumerate(
            data["calendar"]["exam_week"]["sessions"]
        )
    }
    exams.sort(
        key=lambda exam: (
            day_order[exam["day"]],
            session_order[exam["session"]],
            exam["exam_id"],
        )
    )

    return {
        "status": "success",
        "data": {
            "solver_status": result["solver_status"],
            "objective": result["objective"],
            "exam_count": len(exams),
            "days": data["calendar"]["exam_week"]["days"],
            "sessions": data["calendar"]["exam_week"]["sessions"],
            "exams": exams,
            "validation": result["validation"],
        },
    }


@router.get("/export")
def export_exam_timetable(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    result = _solve_or_raise()
    return JSONResponse(
        content=result["assignments"],
        headers={
            "Content-Disposition": 'attachment; filename="exams_preview.json"'
        },
    )


@router.get("/export/full")
def export_complete_exam_timetable(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    result = _solve_or_raise()
    response = _format_exam_timetable(result)
    return JSONResponse(
        content=response,
        headers={
            "Content-Disposition": (
                'attachment; filename="examination_timetable.json"'
            )
        },
    )


@router.get("/export/pdf")
def export_exam_timetable_pdf(
    principal: AuthenticatedPrincipal = Depends(
        require_roles("ADMIN", "FACULTY", "STUDENT")
    ),
):
    del principal
    result = _solve_or_raise()
    response = _format_exam_timetable(result)
    pdf = generate_exam_timetable_pdf(response["data"])
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="examination_timetable.pdf"'
            )
        },
    )
