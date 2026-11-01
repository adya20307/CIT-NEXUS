import json
import runpy
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app.scheduler.exam_scheduler import (
    solve_exam_schedule,
    validate_exam_schedule,
)
from app.api.exams import (
    export_complete_exam_timetable,
    export_exam_timetable,
    export_exam_timetable_pdf,
    get_exam_timetable,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = (
    PROJECT_ROOT
    / "starter-pack"
    / "CrunchWeek_StarterPack"
    / "data"
    / "cit_public.json"
)
VALIDATOR_PATH = (
    PROJECT_ROOT
    / "starter-pack"
    / "CrunchWeek_StarterPack"
    / "validator.py"
)


class ExamSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with DATASET_PATH.open(encoding="utf-8") as file:
            cls.data = json.load(file)
        cls.result = solve_exam_schedule(cls.data)

    def test_schedules_all_exams_and_passes_internal_validation(self):
        self.assertIn(self.result["solver_status"], ("OPTIMAL", "FEASIBLE"))
        self.assertEqual(len(self.result["assignments"]), 41)
        self.assertTrue(
            self.result["validation"]["hard_constraints_satisfied"],
            self.result["validation"]["hard_violations"],
        )

    def test_passes_official_exam_validator(self):
        validator = runpy.run_path(str(VALIDATOR_PATH))
        violations, penalty = validator["check_exams"](
            validator["Ctx"](self.data),
            self.result["assignments"],
        )

        self.assertEqual(violations, [])
        self.assertEqual(penalty, self.result["objective"])

    def test_room_closures_are_respected(self):
        periods = {
            period["id"]: period
            for period in self.data["calendar"]["periods"]
        }
        sessions = {
            session["id"]: session
            for session in self.data["calendar"]["exam_week"]["sessions"]
        }

        def minutes(value):
            hours, minutes = value.split(":")
            return int(hours) * 60 + int(minutes)

        for assignment in self.result["assignments"]:
            session = sessions[assignment["session"]]
            for closure in self.data["room_unavailability"]:
                if assignment["day"] != closure["day"]:
                    continue
                if closure["room_id"] not in assignment["rooms"]:
                    continue

                for period_id in closure["periods"]:
                    period = periods[period_id]
                    overlaps = (
                        minutes(session["start"]) < minutes(period["end"])
                        and minutes(period["start"]) < minutes(session["end"])
                    )
                    self.assertFalse(
                        overlaps,
                        (
                            assignment,
                            closure,
                            period,
                        ),
                    )

    def test_missing_exam_is_reported(self):
        incomplete = self.result["assignments"][:-1]
        validation = validate_exam_schedule(incomplete, self.data)

        self.assertFalse(validation["hard_constraints_satisfied"])
        self.assertTrue(
            any(
                violation.startswith("Exam not scheduled:")
                for violation in validation["hard_violations"]
            )
        )

    def test_faculty_conflict_check_is_reported_as_unverified(self):
        self.assertTrue(
            self.result["validation"]["unverified_constraints"]
        )

    def test_api_reports_actual_capacity_of_assigned_rooms(self):
        with patch("app.api.exams.solve_exam_schedule", return_value=self.result):
            response = get_exam_timetable()

        exams = response["data"]["exams"]
        self.assertEqual(len(exams), 41)
        self.assertTrue(any(len(exam["rooms"]) > 1 for exam in exams))
        for exam in exams:
            allocated = sum(room["exam_capacity"] for room in exam["rooms"])
            self.assertEqual(exam["allocated_capacity"], allocated)
            self.assertEqual(exam["student_count"], exam["size"])
            self.assertGreaterEqual(allocated, exam["size"])
            self.assertTrue(exam["capacity_sufficient"])

    def test_complete_json_export_contains_all_enriched_exam_data(self):
        with patch("app.api.exams.solve_exam_schedule", return_value=self.result):
            response = export_complete_exam_timetable()

        payload = json.loads(response.body)
        exams = payload["data"]["exams"]
        self.assertEqual(len(exams), 41)
        self.assertEqual(
            {exam["exam_id"] for exam in exams},
            {exam["exam_id"] for exam in self.result["assignments"]},
        )
        for exam in exams:
            for key in (
                "course_names",
                "groups",
                "date",
                "day",
                "session",
                "start_time",
                "end_time",
                "rooms",
                "total_allocated_capacity",
            ):
                self.assertIn(key, exam)

    def test_pdf_export_is_a_complete_downloadable_pdf(self):
        with patch("app.api.exams.solve_exam_schedule", return_value=self.result):
            response = export_exam_timetable_pdf()

        self.assertEqual(response.media_type, "application/pdf")
        self.assertTrue(response.body.startswith(b"%PDF-1.4"))
        self.assertTrue(response.body.endswith(b"%%EOF\n"))
        startxref = int(
            response.body.rsplit(b"startxref\n", 1)[1].splitlines()[0]
        )
        self.assertEqual(response.body[startxref:startxref + 4], b"xref")
        for field in (
            b"COURSE:",
            b"STUDENT GROUPS:",
            b"DATE: not supplied by dataset",
            b"SESSION:",
            b"TIME:",
            b"ROOMS:",
            b"ALLOCATED CAPACITY:",
        ):
            self.assertIn(field, response.body)
        for assignment in self.result["assignments"]:
            self.assertIn(assignment["exam_id"].encode("ascii"), response.body)

    def test_existing_json_export_contract_is_unchanged(self):
        with patch("app.api.exams.solve_exam_schedule", return_value=self.result):
            response = export_exam_timetable()

        self.assertEqual(json.loads(response.body), self.result["assignments"])

    def test_overlapping_session_times_are_detected(self):
        overlapping_data = deepcopy(self.data)
        sessions = overlapping_data["calendar"]["exam_week"]["sessions"]
        sessions[1]["start"] = "10:30"
        sessions[1]["end"] = "12:30"
        exam_id = overlapping_data["exams"][0]["id"]
        room_id = next(
            room["id"]
            for room in overlapping_data["rooms"]
            if "exam" in room.get("allowed_uses", [])
        )
        assignments = [
            {
                "exam_id": exam_id,
                "day": "MON",
                "session": session_id,
                "rooms": [room_id],
            }
            for session_id in ("AM", "PM")
        ]

        validation = validate_exam_schedule(assignments, overlapping_data)
        self.assertTrue(
            any("Room conflict" in violation for violation in validation["hard_violations"])
        )
        self.assertTrue(
            any(
                "Student-group conflict" in violation
                for violation in validation["hard_violations"]
            )
        )

    def test_exam_duration_must_fit_the_assigned_session(self):
        short_session_data = deepcopy(self.data)
        short_session_data["calendar"]["exam_week"]["sessions"][0]["end"] = "09:45"
        exam = short_session_data["exams"][0]
        room_id = next(
            room["id"]
            for room in short_session_data["rooms"]
            if "exam" in room.get("allowed_uses", [])
        )
        validation = validate_exam_schedule(
            [{
                "exam_id": exam["id"],
                "day": "MON",
                "session": "AM",
                "rooms": [room_id],
            }],
            short_session_data,
        )
        self.assertTrue(
            any(
                "session is too short" in violation
                for violation in validation["hard_violations"]
            )
        )

    def test_nonpositive_exam_duration_is_invalid(self):
        invalid_duration_data = deepcopy(self.data)
        exam = invalid_duration_data["exams"][0]
        exam["duration_hours"] = 0
        room_id = next(
            room["id"]
            for room in invalid_duration_data["rooms"]
            if "exam" in room.get("allowed_uses", [])
        )
        validation = validate_exam_schedule(
            [{
                "exam_id": exam["id"],
                "day": "MON",
                "session": "AM",
                "rooms": [room_id],
            }],
            invalid_duration_data,
        )
        self.assertIn(
            f"Invalid exam duration for {exam['id']}",
            validation["hard_violations"],
        )


if __name__ == "__main__":
    unittest.main()
