import copy
import json
import unittest
from datetime import date
from pathlib import Path

from app.api.bookings import check_booking_availability
from app.booking.natural_language import parse_natural_language_booking
from app.services.dataset_loader import load_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule.json"


class NaturalLanguageBookingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()
        with SCHEDULE_PATH.open(encoding="utf-8") as file:
            cls.schedule = json.load(file)
        cls.today = date(2026, 11, 1)

    def parse(self, message):
        return parse_natural_language_booking(
            message,
            self.data,
            today=self.today,
        )

    def test_english_example_maps_to_dataset_booking_shape(self):
        result = self.parse(
            "Need a room for 45 people on Tuesday 6 to 8 pm, projector please."
        )
        request = result["structured_request"]
        self.assertEqual(result["status"], "ready_for_availability_check")
        self.assertEqual(request["attendance"], 45)
        self.assertEqual(request["duration_periods"], 2)
        self.assertEqual(
            request["windows"],
            [{"day": "TUE", "from_period": 10, "to_period": 11}],
        )
        self.assertEqual(
            request["room_types"],
            ["classroom", "lecture_hall", "seminar_hall"],
        )
        self.assertEqual(request["needs"], ["projector"])
        self.assertEqual(request["follow_up"], None)

    def test_hinglish_example_resolves_kal_to_tomorrow_and_same_structure(self):
        result = self.parse(
            "seminar hall kal shaam 6 baje, 2 ghante, 100 log, mic chahiye"
        )
        request = result["structured_request"]
        self.assertEqual(result["language"], "hinglish")
        self.assertEqual(result["status"], "ready_for_availability_check")
        self.assertEqual(request["date"], "2026-11-02")
        self.assertEqual(request["weekday"], "MON")
        self.assertEqual(request["attendance"], 100)
        self.assertEqual(request["duration_periods"], 2)
        self.assertEqual(
            request["windows"],
            [{"day": "MON", "from_period": 10, "to_period": 11}],
        )
        self.assertEqual(request["room_types"], ["seminar_hall"])
        self.assertEqual(request["needs"], ["pa_system"])
        self.assertIsNone(request["purpose"])

    def test_kal_and_parso_use_past_tense_context(self):
        yesterday = self.parse("Kal we booked the auditorium.")
        day_before_yesterday = self.parse("Parso booking ki thi auditorium mein.")
        day_after_tomorrow = self.parse(
            "Students ke liye seminar hall parso shaam 6 baje, "
            "2 ghante, 100 log chahiye, book karo."
        )
        self.assertEqual(
            yesterday["structured_request"]["date"],
            "2026-10-31",
        )
        self.assertEqual(
            day_before_yesterday["structured_request"]["date"],
            "2026-10-30",
        )
        self.assertEqual(
            day_after_tomorrow["structured_request"]["date"],
            "2026-11-03",
        )
        self.assertEqual(day_after_tomorrow["language"], "hinglish")
        self.assertEqual(
            day_after_tomorrow["structured_request"]["audience"],
            "students",
        )

    def test_multiple_weekdays_create_multiple_windows(self):
        result = self.parse(
            "Our club meets every Monday and Wednesday 7 to 8 pm, "
            "30 people, any small room is fine."
        )
        self.assertEqual(
            [window["day"] for window in result["structured_request"]["windows"]],
            ["MON", "WED"],
        )
        self.assertTrue(all(
            window["from_period"] == 11 and window["to_period"] == 11
            for window in result["structured_request"]["windows"]
        ))

    def test_missing_and_ambiguous_details_are_followed_up(self):
        incomplete = self.parse("Book a seminar hall for students tomorrow.")
        self.assertEqual(incomplete["status"], "needs_clarification")
        self.assertIsNone(incomplete["availability"])
        self.assertEqual(incomplete["approval_status"], "not_approved")
        self.assertTrue(incomplete["follow_up_questions"])

        ambiguous_time = self.parse(
            "Need a room for 45 people on Tuesday 6-8, projector."
        )
        self.assertEqual(ambiguous_time["status"], "needs_clarification")
        self.assertTrue(any(
            "AM or PM" in question
            for question in ambiguous_time["follow_up_questions"]
        ))

    def test_booking_with_requested_room_accessibility_and_features(self):
        result = self.parse(
            "I want SH-2 on Friday at 10 am for one hour, "
            "wheelchair-accessible poster session, 60 people."
        )
        request = result["structured_request"]
        self.assertEqual(result["status"], "ready_for_availability_check")
        self.assertEqual(request["requested_room"], "SH-2")
        self.assertEqual(request["needs"], ["accessible"])
        self.assertEqual(request["purpose"], "department_event")
        self.assertEqual(request["duration_periods"], 1)
        self.assertEqual(request["windows"][0]["from_period"], 3)
        checked = check_booking_availability(self.data, self.schedule, result)
        self.assertEqual(checked["availability"]["status"], "unavailable")
        self.assertFalse(checked["availability"]["requested_room_available"])
        self.assertFalse(checked["availability"]["available_slots"])
        self.assertTrue(any(
            slot["room_id"] == "SH-1"
            for slot in checked["suggested_alternatives"]
        ))
        self.assertIn("Other available times", checked["message"])

    def test_all_official_plain_language_examples_are_interpreted_or_clarified(self):
        examples = [
            "Need a room for 45 people on Tuesday 6 to 8 pm, projector please.",
            "Book the auditorium for the CSE department farewell, Friday evening, "
            "around 400 people, need stage and mic.",
            "Can I get a lab with computers for a 3-hour coding contest on Thursday "
            "afternoon? About 60 participants.",
            "Our club meets every Monday and Wednesday 7 to 8 pm, 30 people, "
            "any small room is fine.",
            "Mechanical third years need a 2 hour session with an industry speaker "
            "on Wednesday, it's compulsory for them.",
            "seminar hall kal shaam 6 baje, 2 ghante, 100 log, mic chahiye",
            "I want SH-2 on Friday at 10 am for a wheelchair-accessible poster "
            "session, 60 people.",
            "Need somewhere for the quiz finals after lunch on Thursday, maybe "
            "150 people.",
        ]
        results = [self.parse(example) for example in examples]
        self.assertTrue(all(
            result["status"] in (
                "ready_for_availability_check",
                "needs_clarification",
            )
            for result in results
        ))
        self.assertEqual(results[1]["status"], "needs_clarification")
        self.assertEqual(results[2]["status"], "needs_clarification")
        self.assertEqual(results[4]["status"], "needs_clarification")
        self.assertEqual(results[7]["status"], "needs_clarification")
        self.assertEqual(results[6]["status"], "needs_clarification")
        self.assertEqual(
            results[6]["room_constraint"]["unmet_requirements"],
            ["accessible"],
        )
        self.assertEqual(
            results[6]["room_constraint"]["suggested_rooms"][0]["room_id"],
            "SH-1",
        )

    def test_compulsory_student_group_is_checked_but_nonmandatory_group_is_not(self):
        compulsory = self.parse(
            "Mechanical third years need a 2 hour industry speaker session "
            "on Wednesday, compulsory for them, at 2 pm, 45 people."
        )
        nonmandatory = self.parse(
            "Mechanical third years need a 2 hour industry speaker session "
            "on Wednesday, at 2 pm, 45 people."
        )
        self.assertEqual(
            compulsory["structured_request"]["mandatory_for_groups"],
            ["ME-3"],
        )
        self.assertEqual(
            nonmandatory["structured_request"]["mandatory_for_groups"],
            [],
        )

    def test_availability_check_uses_official_schedule_and_never_approves(self):
        original_schedule = copy.deepcopy(self.schedule)
        parsed = self.parse(
            "Need a room for 45 people on Tuesday 6 to 8 pm, projector please."
        )
        result = check_booking_availability(self.data, self.schedule, parsed)
        self.assertEqual(result["status"], "availability_checked")
        self.assertEqual(result["availability"]["status"], "available")
        self.assertTrue(result["availability"]["available_slots"])
        self.assertEqual(result["approval_status"], "not_approved")
        self.assertIn("preview only", result["message"])
        self.assertEqual(result["priority"]["existing_order"], self.data["priority_order"])
        self.assertEqual(self.schedule, original_schedule)

    def test_approved_booking_blocks_a_new_availability_preview(self):
        approved = next(
            item
            for item in self.schedule["fest_week"]["bookings"]
            if item["status"] == "approved"
        )
        period = next(
            item
            for item in self.data["calendar"]["periods"]
            if item["id"] == approved["start_period"]
        )
        text = (
            f"Need {approved['room_id']} on {approved['day']} at "
            f"{period['start']} for one hour, 1 person."
        )
        result = check_booking_availability(
            self.data,
            self.schedule,
            self.parse(text),
        )
        self.assertIn(
            result["availability"]["status"],
            ("unavailable", "alternative_available"),
        )
        self.assertFalse(result["availability"]["requested_room_available"])
        self.assertTrue(any(
            item["room_id"] == approved["room_id"]
            and item["day"] == approved["day"]
            and item["start_period"] == approved["start_period"]
            for item in result["availability"]["blocked"]
        ))
        self.assertEqual(result["approval_status"], "not_approved")

    def test_alternative_offers_are_not_treated_as_confirmed_reservations(self):
        alternative = next(
            item
            for item in self.schedule["fest_week"]["bookings"]
            if item["status"] == "alternative_offered"
        )
        period = next(
            item
            for item in self.data["calendar"]["periods"]
            if item["id"] == alternative["start_period"]
        )
        text = (
            f"Need {alternative['room_id']} on {alternative['day']} at "
            f"{period['start']} for one hour, 1 person."
        )
        result = check_booking_availability(
            self.data,
            self.schedule,
            self.parse(text),
        )
        self.assertTrue(any(
            slot["room_id"] == alternative["room_id"]
            and slot["day_code"] == alternative["day"]
            and slot["start_period"] == alternative["start_period"]
            for slot in result["availability"]["available_slots"]
        ))

    def test_room_maintenance_blocks_specific_room_and_suggests_other_rooms(self):
        result = check_booking_availability(
            self.data,
            self.schedule,
            self.parse(
                "Need LH-4 on Thursday at 8 am for one hour, 20 people."
            ),
        )
        self.assertFalse(result["availability"]["requested_room_available"])
        self.assertTrue(any(
            item["room_id"] == "LH-4" and item["reason"] == "maintenance"
            for item in result["availability"]["blocked"]
        ))
        self.assertEqual(result["availability"]["status"], "alternative_available")
        self.assertTrue(result["availability"]["available_slots"])
        self.assertEqual(result["approval_status"], "not_approved")

    def test_hinglish_availability_query_lists_free_times_without_approving(self):
        result = self.parse("Kal auditorium khali hai kitne baje?")
        result = check_booking_availability(self.data, self.schedule, result)
        self.assertEqual(result["language"], "hinglish")
        self.assertEqual(result["intent"], "availability_query")
        self.assertTrue(result["availability"]["available_slots"])
        self.assertIn("Available times", result["message"])
        self.assertEqual(result["approval_status"], "not_approved")

    def test_english_availability_question_does_not_require_capacity_or_duration(self):
        parsed = self.parse("Is the auditorium free on Friday?")
        self.assertEqual(parsed["intent"], "availability_query")
        self.assertEqual(parsed["status"], "ready_for_availability_check")
        self.assertEqual(len(parsed["structured_request"]["windows"]), 13)


if __name__ == "__main__":
    unittest.main()
