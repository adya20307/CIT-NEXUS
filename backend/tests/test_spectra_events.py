import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.main import app
from app.services import spectra_event_store
from app.services.dataset_loader import load_dataset
from app.booking.availability import build_occupancy, read_official_schedule


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = PROJECT_ROOT / "exports" / "schedule.json"


class SpectraEventApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()
        cls.schedule = read_official_schedule(cls.data)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path_patch = patch.object(
            spectra_event_store,
            "DATABASE_PATH",
            Path(self.temp_dir.name) / "spectra-test.sqlite3",
        )
        self.database_path_patch.start()
        self.admin = AuthenticatedPrincipal(
            issuer="test-issuer",
            subject="admin-user-1",
            roles=("ADMIN",),
            departments=(),
            username="event-admin",
        )
        self.client = TestClient(app)
        app.dependency_overrides[get_current_principal] = lambda: self.admin

    def tearDown(self):
        app.dependency_overrides.clear()
        self.database_path_patch.stop()
        self.temp_dir.cleanup()

    def make_available_payload(self, category="club_event"):
        room_busy, _, closed = build_occupancy(self.data, self.schedule)
        event_room_ids = {
            room["id"]
            for room in self.data["rooms"]
            if "event" in room.get("allowed_uses", [])
        }
        periods = sorted(
            self.data["calendar"]["periods"],
            key=lambda item: item["id"],
        )
        monday = date(2027, 1, 4)
        for day_index, day in enumerate(self.data["calendar"]["days"]):
            event_date = monday + timedelta(days=day_index)
            for room in self.data["rooms"]:
                if room["id"] not in event_room_ids or room["capacity"] < 10:
                    continue
                for period in periods:
                    key = (room["id"], day, period["id"])
                    if key in room_busy or key in closed:
                        continue
                    return {
                        "event_name": "Test campus event",
                        "category": category,
                        "room_id": room["id"],
                        "event_date": event_date.isoformat(),
                        "start_time": period["start"],
                        "end_time": period["end"],
                        "expected_participants": 10,
                    }
        raise AssertionError("Dataset has no free supported event period.")

    def create_event(self, payload=None):
        response = self.client.post(
            "/api/spectra/events",
            json=payload or self.make_available_payload(),
        )
        return response

    def test_creation_always_starts_pending_and_records_creator(self):
        response = self.create_event()
        self.assertEqual(response.status_code, 201, response.text)
        event = response.json()["event"]
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["created_by"], "admin-user-1")
        self.assertEqual(event["duration_periods"], 1)
        self.assertTrue(response.json()["availability"]["available"])

        history = self.client.get(
            f"/api/spectra/events/{event['id']}/history"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["history"], [{
            "action": "created",
            "actor": "admin-user-1",
            "timestamp": event["created_at"],
            "reason": "Event submitted for approval.",
        }])

    def test_capacity_is_validated_for_preview_and_creation(self):
        payload = self.make_available_payload()
        room = min(
            (
                item for item in self.data["rooms"]
                if "event" in item.get("allowed_uses", [])
            ),
            key=lambda item: item["capacity"],
        )
        payload["room_id"] = room["id"]
        payload["expected_participants"] = room["capacity"] + 1
        preview = self.client.post(
            "/api/spectra/events/availability",
            json=payload,
        )
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.json()["availability"]["available"])
        self.assertIn(
            "insufficient_capacity",
            {
                item["type"]
                for item in preview.json()["availability"]["conflicts"]
            },
        )
        created = self.client.post("/api/spectra/events", json=payload)
        self.assertEqual(created.status_code, 422)

    def test_invalid_date_weekend_and_unsupported_time_are_rejected(self):
        payload = self.make_available_payload()
        payload["event_date"] = "2027-02-30"
        self.assertEqual(
            self.client.post("/api/spectra/events/availability", json=payload).status_code,
            422,
        )
        payload = self.make_available_payload()
        payload["event_date"] = "2027-01-09"
        self.assertEqual(
            self.client.post("/api/spectra/events/availability", json=payload).status_code,
            422,
        )
        payload = self.make_available_payload()
        payload["start_time"] = "03:00"
        payload["end_time"] = "04:00"
        self.assertEqual(
            self.client.post("/api/spectra/events/availability", json=payload).status_code,
            422,
        )

    def test_maintenance_conflict_is_reported(self):
        payload = self.make_available_payload()
        payload.update({
            "room_id": "LH-4",
            "event_date": "2027-01-07",
            "start_time": "08:00",
            "end_time": "09:00",
        })
        response = self.client.post(
            "/api/spectra/events/availability",
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        availability = response.json()["availability"]
        self.assertFalse(availability["available"])
        self.assertIn(
            "maintenance",
            {item["type"] for item in availability["conflicts"]},
        )

    def test_teaching_conflict_is_reported(self):
        assignment = self.schedule["sessions"][0]
        room = next(
            item for item in self.data["rooms"]
            if item["id"] == assignment["room_id"]
        )
        day_index = self.data["calendar"]["days"].index(assignment["day"])
        period = next(
            item for item in self.data["calendar"]["periods"]
            if item["id"] == assignment["start_period"]
        )
        payload = self.make_available_payload()
        payload.update({
            "room_id": room["id"],
            "event_date": (date(2027, 1, 4) + timedelta(days=day_index)).isoformat(),
            "start_time": period["start"],
            "end_time": period["end"],
        })
        response = self.client.post(
            "/api/spectra/events/availability",
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "teaching",
            {item["type"] for item in response.json()["availability"]["conflicts"]},
        )

    def test_approved_booking_conflict_is_reported(self):
        booking = next(
            item for item in self.schedule["fest_week"]["bookings"]
            if item["status"] == "approved"
        )
        day_index = self.data["calendar"]["days"].index(booking["day"])
        period = next(
            item for item in self.data["calendar"]["periods"]
            if item["id"] == booking["start_period"]
        )
        payload = self.make_available_payload()
        payload.update({
            "room_id": booking["room_id"],
            "event_date": (date(2027, 1, 4) + timedelta(days=day_index)).isoformat(),
            "start_time": period["start"],
            "end_time": period["end"],
        })
        response = self.client.post(
            "/api/spectra/events/availability",
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "approved_booking",
            {item["type"] for item in response.json()["availability"]["conflicts"]},
        )

    def test_priority_blocks_lower_ranked_pending_approval(self):
        payload = self.make_available_payload(category="club_event")
        lower = self.create_event(payload)
        self.assertEqual(lower.status_code, 201, lower.text)
        higher_payload = dict(payload, category="institute_event")
        higher = self.create_event(higher_payload)
        self.assertEqual(higher.status_code, 201, higher.text)
        self.assertEqual(higher.json()["event"]["status"], "pending")

        low_approval = self.client.post(
            f"/api/spectra/events/{lower.json()['event']['id']}/approve",
            json={},
        )
        self.assertEqual(low_approval.status_code, 409)
        self.assertIn(
            "pending_priority_blockers",
            low_approval.json()["detail"]["availability"]["priority"],
        )
        high_approval = self.client.post(
            f"/api/spectra/events/{higher.json()['event']['id']}/approve",
            json={"reason": "Institute event priority."},
        )
        self.assertEqual(high_approval.status_code, 200, high_approval.text)
        self.assertEqual(high_approval.json()["event"]["status"], "approved")

        still_blocked = self.client.post(
            f"/api/spectra/events/{lower.json()['event']['id']}/approve",
            json={},
        )
        self.assertEqual(still_blocked.status_code, 409)
        self.assertIn(
            "approved_event",
            {
                item["type"]
                for item in still_blocked.json()["detail"]["availability"]["conflicts"]
            },
        )

    def test_approval_rechecks_and_records_decision(self):
        created = self.create_event()
        event_id = created.json()["event"]["id"]
        response = self.client.post(
            f"/api/spectra/events/{event_id}/approve",
            json={"reason": "Availability reviewed."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["event"]["status"], "approved")
        history = self.client.get(
            f"/api/spectra/events/{event_id}/history"
        ).json()["history"]
        self.assertEqual([item["action"] for item in history], ["created", "approved"])
        self.assertEqual(history[-1]["reason"], "Availability reviewed.")
        self.assertEqual(history[-1]["actor"], "admin-user-1")

    def test_rejection_requires_reason_and_is_audited(self):
        event = self.create_event().json()["event"]
        missing_reason = self.client.post(
            f"/api/spectra/events/{event['id']}/reject",
            json={},
        )
        self.assertEqual(missing_reason.status_code, 422)
        rejected = self.client.post(
            f"/api/spectra/events/{event['id']}/reject",
            json={"reason": "Venue no longer available."},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["event"]["status"], "rejected")
        history = self.client.get(
            f"/api/spectra/events/{event['id']}/history"
        ).json()["history"]
        self.assertEqual(history[-1]["action"], "rejected")
        self.assertEqual(history[-1]["reason"], "Venue no longer available.")

    def test_cancellation_and_invalid_transitions(self):
        event = self.create_event().json()["event"]
        missing_reason = self.client.post(
            f"/api/spectra/events/{event['id']}/cancel",
            json={},
        )
        self.assertEqual(missing_reason.status_code, 422)
        cancelled = self.client.post(
            f"/api/spectra/events/{event['id']}/cancel",
            json={"reason": "Event withdrawn."},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["event"]["status"], "cancelled")
        after_cancel = self.client.post(
            f"/api/spectra/events/{event['id']}/approve",
            json={},
        )
        self.assertEqual(after_cancel.status_code, 409)

        approved_event = self.create_event().json()["event"]
        approved = self.client.post(
            f"/api/spectra/events/{approved_event['id']}/approve",
            json={},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        approved_cancellation = self.client.post(
            f"/api/spectra/events/{approved_event['id']}/cancel",
            json={"reason": "Approved event cancelled."},
        )
        self.assertEqual(approved_cancellation.status_code, 200)
        rejected = self.client.post(
            f"/api/spectra/events/{approved_event['id']}/reject",
            json={"reason": "Too late to reject."},
        )
        self.assertEqual(rejected.status_code, 409)

    def test_event_list_detail_and_status_filter(self):
        created = self.create_event().json()["event"]
        listing = self.client.get("/api/spectra/events?status=pending")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["count"], 1)
        self.assertEqual(listing.json()["events"][0]["id"], created["id"])
        detail = self.client.get(f"/api/spectra/events/{created['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["event"]["event_name"], "Test campus event")

    def test_history_is_append_only(self):
        event = self.create_event().json()["event"]
        with spectra_event_store.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE spectra_event_history SET reason = ?
                    WHERE event_id = ?
                    """,
                    ("tampered", event["id"]),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM spectra_event_history WHERE event_id = ?",
                    (event["id"],),
                )

    def test_event_requests_are_role_limited_and_open_to_campus_roles(self):
        app.dependency_overrides.clear()
        unauthorized = self.client.get("/api/spectra/events")
        self.assertEqual(unauthorized.status_code, 401)

        faculty = AuthenticatedPrincipal(
            issuer="test-issuer",
            subject="faculty-user",
            roles=("FACULTY",),
            departments=("CSE",),
            username="faculty",
        )
        app.dependency_overrides[get_current_principal] = lambda: faculty
        denied = self.client.get("/api/spectra/events")
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post(
            "/api/spectra/events",
            json=self.make_available_payload(),
        )
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.json()["event"]["created_by"], "faculty-user")

        student = AuthenticatedPrincipal(
            issuer="test-issuer",
            subject="student-user",
            roles=("STUDENT",),
            departments=(),
            username="student",
        )
        app.dependency_overrides[get_current_principal] = lambda: student
        student_request = self.client.post(
            "/api/spectra/events",
            json=self.make_available_payload(),
        )
        self.assertEqual(student_request.status_code, 201)


if __name__ == "__main__":
    unittest.main()
