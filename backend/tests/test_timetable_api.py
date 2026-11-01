import copy
import json
import unittest
from pathlib import Path

from app.api.timetable import (
    build_room_utilization,
    build_timetable_data,
    get_room_utilization,
)
from app.rescheduler.crisis_engine import DISRUPTION_PATH, apply_disruption
from app.services.dataset_loader import load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule.json"
CRISIS_SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule_after.json"


class TimetableApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()
        with OFFICIAL_SCHEDULE_PATH.open(encoding="utf-8") as file:
            cls.official_schedule = json.load(file)
        with CRISIS_SCHEDULE_PATH.open(encoding="utf-8") as file:
            cls.crisis_schedule = json.load(file)
        with DISRUPTION_PATH.open(encoding="utf-8") as file:
            cls.crisis_disruption = json.load(file)
        cls.crisis_data = apply_disruption(
            cls.data,
            cls.crisis_disruption,
        )
        cls.views = build_timetable_data(
            cls.data,
            cls.official_schedule,
            cls.crisis_schedule,
            crisis_data=cls.crisis_data,
        )
        cls.analytics = build_room_utilization(
            cls.data,
            cls.official_schedule,
        )

    def test_api_exposes_every_dataset_entity_and_scheduled_session(self):
        self.assertEqual(len(self.views["student_groups"]), 14)
        self.assertEqual(len(self.views["faculty"]), 40)
        self.assertEqual(len(self.views["rooms"]), 27)
        self.assertEqual(len(self.views["baseline_sessions"]), 189)
        self.assertEqual(
            {group["id"] for group in self.views["student_groups"]},
            {group["id"] for group in self.data["student_groups"]},
        )
        self.assertEqual(
            {member["id"] for member in self.views["faculty"]},
            {member["id"] for member in self.data["faculty"]},
        )
        self.assertEqual(
            {room["id"] for room in self.views["rooms"]},
            {room["id"] for room in self.data["rooms"]},
        )

        dataset_session_ids = {session["id"] for session in self.data["sessions"]}
        scheduled_session_ids = {
            session["session_id"]
            for session in self.views["baseline_sessions"]
        }
        self.assertEqual(scheduled_session_ids, dataset_session_ids)

        for session in self.views["baseline_sessions"]:
            self.assertTrue(session["course_id"])
            self.assertTrue(session["course_title"])
            self.assertTrue(session["faculty_name"])
            self.assertTrue(session["room_id"])
            self.assertGreaterEqual(session["end_period"], session["start_period"])
            self.assertTrue(session["groups"])

    def test_booking_status_handling_only_confirms_approved_requests(self):
        self.assertEqual(len(self.views["approved_bookings"]), 20)
        self.assertTrue(all(
            booking["status"] == "approved"
            for booking in self.views["approved_bookings"]
        ))
        self.assertEqual(
            self.views["booking_status_counts"],
            {
                "approved": 20,
                "alternative_offered": 3,
                "rejected": 2,
            },
        )
        self.assertFalse(any(
            booking["status"] != "approved"
            for booking in self.views["approved_bookings"]
        ))

    def test_utilization_uses_actual_periods_and_excludes_maintenance(self):
        teaching = self.analytics["teaching"]
        events = self.analytics["approved_events"]
        self.assertEqual(teaching["occupied_periods"], 243)
        self.assertEqual(teaching["available_periods"], 1202)
        self.assertEqual(teaching["utilization_percent"], 20.22)
        self.assertEqual(events["occupied_periods"], 58)
        self.assertEqual(events["available_periods"], 1742)
        self.assertEqual(events["utilization_percent"], 3.33)
        self.assertEqual(len(self.analytics["underutilized_rooms"]), 7)
        expected_available_capacity = sum(
            metric["capacity"] * (
                metric["teaching_available_periods"]
                - metric["teaching_occupied_periods"]
            )
            for metric in self.analytics["rooms"]
        )
        self.assertEqual(
            self.analytics["available_capacity_seat_periods"],
            expected_available_capacity,
        )

    def test_room_type_metrics_and_room_records_are_dataset_derived(self):
        rooms_by_id = {room["id"]: room for room in self.data["rooms"]}
        metrics_by_id = {
            metric["room_id"]: metric
            for metric in self.analytics["rooms"]
        }
        self.assertEqual(set(metrics_by_id), set(rooms_by_id))
        for room_id, metric in metrics_by_id.items():
            self.assertEqual(metric["capacity"], rooms_by_id[room_id]["capacity"])
            self.assertEqual(metric["room_type"], rooms_by_id[room_id]["type"])
            self.assertLessEqual(
                metric["teaching_occupied_periods"],
                metric["teaching_available_periods"],
            )
        self.assertEqual(
            {item["room_type"] for item in self.analytics["by_room_type"]},
            {room["type"] for room in self.data["rooms"]},
        )

    def test_maintenance_records_are_exposed_and_removed_from_denominators(self):
        self.assertEqual(
            self.views["room_unavailability"],
            self.data["room_unavailability"],
        )
        lh4 = next(
            room
            for room in self.analytics["rooms"]
            if room["room_id"] == "LH-4"
        )
        self.assertEqual(lh4["teaching_available_periods"], 36)
        self.assertEqual(lh4["event_available_periods"], 56)

    def test_crisis_addition_is_enriched_from_the_actual_disruption(self):
        revised = self.views["revised_schedule"]
        self.assertTrue(revised["available"], revised["reason"])
        self.assertEqual(revised["added_count"], 1)
        self.assertEqual(revised["changed_count"], 11)
        added = next(
            session
            for session in revised["sessions"]
            if session["session_id"] == "CS202-T2"
        )
        self.assertEqual(added["change"]["type"], "ADDED")
        self.assertEqual(added["course_id"], "CS202")
        self.assertEqual(added["faculty_id"], "F06")
        self.assertEqual(added["group_ids"], ["CSE-2"])

    def test_crisis_export_without_its_disruption_context_is_not_misrepresented(self):
        result = build_timetable_data(
            self.data,
            self.official_schedule,
            self.crisis_schedule,
        )
        self.assertFalse(result["revised_schedule"]["available"])
        self.assertIn("CS202-T2", result["revised_schedule"]["reason"])
        self.assertEqual(result["revised_schedule"]["sessions"], [])

    def test_compatible_revised_schedule_marks_actual_changes(self):
        crisis = copy.deepcopy(self.official_schedule)
        assignment = crisis["sessions"][0]
        assignment["day"] = "FRI" if assignment["day"] != "FRI" else "MON"
        assignment["room_id"] = next(
            room["id"]
            for room in self.data["rooms"]
            if room["id"] != assignment["room_id"]
        )
        result = build_timetable_data(self.data, self.official_schedule, crisis)
        self.assertTrue(result["revised_schedule"]["available"])
        changed = next(
            session
            for session in result["revised_schedule"]["sessions"]
            if session["session_id"] == assignment["session_id"]
        )
        self.assertEqual(changed["change"]["type"], "TIME_AND_ROOM")

    def test_schedule_conflicts_and_faculty_unavailability_are_indicated(self):
        modified = copy.deepcopy(self.official_schedule)
        first, second = modified["sessions"][:2]
        second["day"] = first["day"]
        second["start_period"] = first["start_period"]
        second["room_id"] = first["room_id"]

        faculty_session = next(
            session
            for session in self.data["sessions"]
            if session["faculty_id"] == "F01"
        )
        unavailable_assignment = next(
            assignment
            for assignment in modified["sessions"]
            if assignment["session_id"] == faculty_session["id"]
        )
        unavailable_assignment.update({
            "day": "WED",
            "start_period": 6,
        })

        result = build_timetable_data(self.data, modified)
        by_id = {
            session["session_id"]: session
            for session in result["baseline_sessions"]
        }
        self.assertTrue(by_id[first["session_id"]]["conflicts"])
        self.assertTrue(by_id[second["session_id"]]["conflicts"])
        self.assertIn(
            "faculty_unavailability",
            {
                conflict["type"]
                for conflict in by_id[faculty_session["id"]]["conflicts"]
            },
        )

    def test_room_utilization_endpoint_returns_success_payload(self):
        analytics = get_room_utilization()
        self.assertEqual(analytics["status"], "success")
        self.assertEqual(analytics["data"]["teaching"]["available_periods"], 1202)


if __name__ == "__main__":
    unittest.main()
