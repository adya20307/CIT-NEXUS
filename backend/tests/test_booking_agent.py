import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.genai import types

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.booking.availability import build_occupancy, read_official_schedule
from app.booking.natural_language import local_date_today
from app.main import app
from app.services import spectra_event_store
from app.services.booking_agent_service import execute_gemini_tool
from app.services.dataset_loader import load_dataset
from app.services.gemini_agent_service import generate_booking_agent_reply


def _principal(subject="student-agent", roles=("STUDENT",)):
    return AuthenticatedPrincipal(
        issuer="test-issuer",
        subject=subject,
        roles=roles,
        departments=(),
        username=subject,
    )


def _available_event_payload(data, schedule, category="club_event"):
    room_busy, _, closed = build_occupancy(data, schedule)
    periods = sorted(data["calendar"]["periods"], key=lambda item: item["id"])
    today = local_date_today()
    dates = [
        today + timedelta(days=offset)
        for offset in range(1, 15)
        if (today + timedelta(days=offset)).weekday() < len(data["calendar"]["days"])
    ]
    for event_date in dates:
        day = data["calendar"]["days"][event_date.weekday()]
        for room in data["rooms"]:
            if "event" not in room.get("allowed_uses", []) or room["capacity"] < 10:
                continue
            for period in periods:
                key = (room["id"], day, period["id"])
                if key in room_busy or key in closed:
                    continue
                return {
                    "event_name": "Agent booking test",
                    "category": category,
                    "room_id": room["id"],
                    "event_date": event_date.isoformat(),
                    "start_time": period["start"],
                    "end_time": period["end"],
                    "expected_participants": 10,
                    "required_features": [],
                }
    raise AssertionError("No future unoccupied event venue slot found.")


class BookingAgentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()
        cls.schedule = read_official_schedule(cls.data)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path_patch = patch.object(
            spectra_event_store,
            "DATABASE_PATH",
            Path(self.temp_dir.name) / "booking-agent.sqlite3",
        )
        self.database_path_patch.start()
        self.principal = _principal()
        app.dependency_overrides[get_current_principal] = lambda: self.principal
        self.client = TestClient(app)
        self.payload = _available_event_payload(self.data, self.schedule)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.database_path_patch.stop()
        self.temp_dir.cleanup()

    def _create_proposal(self, payload=None):
        body = payload or self.payload
        check = execute_gemini_tool(
            "check_resource_availability",
            {
                **body,
                "room_type": None,
            },
            self.principal,
        )
        self.assertTrue(any(
            room["available"] for room in check["rooms"]
            if room["room_id"] == body["room_id"]
        ))
        proposal = execute_gemini_tool("create_booking", body, self.principal)
        self.assertEqual(proposal["status"], "awaiting_confirmation")
        return proposal

    def _confirm(self, proposal, confirmed=True):
        return self.client.post(
            "/api/assistant/confirm",
            json={
                "proposal_id": proposal["proposal_id"],
                "confirmed": confirmed,
            },
        )

    def test_create_requires_confirmation_and_commits_pending_event(self):
        proposal = self._create_proposal()
        self.assertEqual(
            self.client.get("/api/spectra/events/my").json()["count"],
            0,
        )
        self.assertEqual(
            len(self.client.get("/api/assistant/proposals").json()["proposals"]),
            1,
        )

        confirmed = self._confirm(proposal)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        result = confirmed.json()
        self.assertEqual(result["event"]["status"], "pending")
        self.assertEqual(result["event"]["created_by"], self.principal.subject)

        repeated = self._confirm(proposal)
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()["already_confirmed"])
        self.assertEqual(
            repeated.json()["event"]["id"],
            result["event"]["id"],
        )
        self.assertEqual(
            self.client.get("/api/spectra/events/my").json()["count"],
            1,
        )

    def test_missing_tool_arguments_are_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            execute_gemini_tool(
                "create_booking",
                {"room_id": self.payload["room_id"]},
                self.principal,
            )
        self.assertEqual(raised.exception.status_code, 422)

    def test_unavailable_schedule_slot_and_capacity_are_reported(self):
        assignment = next(
            item
            for item in self.schedule["sessions"]
            if any(
                room["id"] == item["room_id"]
                and "event" in room.get("allowed_uses", [])
                for room in self.data["rooms"]
            )
        )
        room = next(item for item in self.data["rooms"] if item["id"] == assignment["room_id"])
        session = next(
            item for item in self.data["sessions"]
            if item["id"] == assignment["session_id"]
        )
        date_index = self.data["calendar"]["days"].index(assignment["day"])
        event_date = (
            local_date_today()
            + timedelta(days=1)
        )
        while event_date.weekday() != date_index:
            event_date += timedelta(days=1)
        period = next(
            item for item in self.data["calendar"]["periods"]
            if item["id"] == assignment["start_period"]
        )
        blocked = {
            **self.payload,
            "room_id": room["id"],
            "event_date": event_date.isoformat(),
            "start_time": period["start"],
            "end_time": next(
                item for item in self.data["calendar"]["periods"]
                if item["id"] == assignment["start_period"] + session["duration"] - 1
            )["end"],
            "expected_participants": min(room["capacity"], 10),
        }
        response = execute_gemini_tool(
            "check_resource_availability",
            blocked,
            self.principal,
        )
        chosen = next(
            item for item in response["rooms"] if item["room_id"] == room["id"]
        )
        self.assertFalse(chosen["available"])
        self.assertIn("teaching", {item["type"] for item in chosen["conflicts"]})

        small_room = min(
            (
                room for room in self.data["rooms"]
                if "event" in room.get("allowed_uses", [])
            ),
            key=lambda room: room["capacity"],
        )
        too_many = {
            **self.payload,
            "room_id": small_room["id"],
            "expected_participants": small_room["capacity"] + 1,
        }
        capacity = execute_gemini_tool(
            "check_resource_availability",
            too_many,
            self.principal,
        )
        self.assertFalse(any(room["available"] for room in capacity["rooms"]))

        missing_feature = {
            **self.payload,
            "required_features": ["feature-not-in-campus-dataset"],
        }
        feature_check = execute_gemini_tool(
            "check_resource_availability",
            missing_feature,
            self.principal,
        )
        self.assertFalse(any(room["available"] for room in feature_check["rooms"]))

    def test_cancellation_requires_confirmation_and_enforces_owner(self):
        proposal = self._create_proposal()
        event = self._confirm(proposal).json()["event"]
        cancel = execute_gemini_tool(
            "cancel_booking",
            {"booking_id": event["id"]},
            self.principal,
        )
        self.assertEqual(
            self.client.get(f"/api/spectra/events/{event['id']}").json()["event"]["status"],
            "pending",
        )
        canceled = self._confirm(cancel)
        self.assertEqual(canceled.status_code, 200, canceled.text)
        self.assertEqual(canceled.json()["event"]["status"], "cancelled")

        other_client = TestClient(app)
        app.dependency_overrides[get_current_principal] = lambda: _principal("other-user")
        try:
            response = other_client.get(f"/api/spectra/events/{event['id']}")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                other_client.get("/api/spectra/events/my").json()["count"],
                0,
            )
            self.assertEqual(
                other_client.get("/api/assistant/proposals").json()["proposals"],
                [],
            )
            with self.assertRaises(HTTPException) as raised:
                execute_gemini_tool(
                    "get_booking_details",
                    {"booking_id": event["id"]},
                    _principal("other-user"),
                )
            self.assertEqual(raised.exception.status_code, 404)
        finally:
            app.dependency_overrides[get_current_principal] = lambda: self.principal

    def test_reschedule_rechecks_and_preserves_original_on_conflict(self):
        proposal = self._create_proposal()
        event = self._confirm(proposal).json()["event"]
        original_date = event["event_date"]
        assignment = next(
            item for item in self.schedule["sessions"]
            if item["room_id"] == event["room_id"]
            and item["day"] == self.data["calendar"]["days"][
                date.fromisoformat(original_date).weekday()
            ]
        )
        period = next(
            item for item in self.data["calendar"]["periods"]
            if item["id"] == assignment["start_period"]
        )
        blocked = self.client.patch(
            f"/api/spectra/events/my/{event['id']}/reschedule",
            json={
                "event_date": original_date,
                "start_time": period["start"],
                "end_time": period["end"],
            },
        )
        self.assertEqual(blocked.status_code, 409)
        still_original = self.client.get(
            f"/api/spectra/events/{event['id']}"
        ).json()["event"]
        self.assertEqual(still_original["event_date"], original_date)
        self.assertEqual(still_original["start_time"], event["start_time"])

        chosen = None
        periods = sorted(
            self.data["calendar"]["periods"],
            key=lambda item: item["id"],
        )
        for offset in range(1, 15):
            candidate_date = local_date_today() + timedelta(days=offset)
            if candidate_date.weekday() >= len(self.data["calendar"]["days"]):
                continue
            for candidate_period in periods:
                if (
                    candidate_date.isoformat() == original_date
                    and candidate_period["start"] == event["start_time"]
                ):
                    continue
                candidate = {
                    **self.payload,
                    "room_id": event["room_id"],
                    "event_name": event["event_name"],
                    "category": event["category"],
                    "event_date": candidate_date.isoformat(),
                    "start_time": candidate_period["start"],
                    "end_time": candidate_period["end"],
                    "expected_participants": event["expected_participants"],
                }
                availability = execute_gemini_tool(
                    "check_resource_availability",
                    candidate,
                    self.principal,
                )
                available_room = next(
                    (
                        item for item in availability["rooms"]
                        if item["room_id"] == event["room_id"]
                        and item["available"]
                    ),
                    None,
                )
                if available_room is not None:
                    chosen = {
                        **available_room,
                        "event_date": candidate_date.isoformat(),
                        "start_time": candidate_period["start"],
                        "end_time": candidate_period["end"],
                    }
                    break
            if chosen is not None:
                break
        self.assertIsNotNone(chosen)
        reschedule = execute_gemini_tool(
            "reschedule_booking",
            {
                "booking_id": event["id"],
                "event_date": chosen["event_date"],
                "start_time": chosen["start_time"],
                "end_time": chosen["end_time"],
                "room_id": event["room_id"],
            },
            self.principal,
        )
        result = self._confirm(reschedule)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["event"]["event_date"], chosen["event_date"])
        history = self.client.get(
            f"/api/spectra/events/{event['id']}/history"
        ).json()["history"]
        self.assertIn("rescheduled", [item["action"] for item in history])

    def test_student_cannot_submit_above_role_booking_category(self):
        privileged = {
            **self.payload,
            "category": "institute_event",
        }
        with self.assertRaises(HTTPException) as raised:
            execute_gemini_tool(
                "check_resource_availability",
                privileged,
                self.principal,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_faculty_can_check_permitted_category_not_admin_category(self):
        faculty = _principal("faculty-agent", ("FACULTY",))
        permitted = {
            **self.payload,
            "category": "guest_lecture",
        }
        result = execute_gemini_tool(
            "check_resource_availability",
            permitted,
            faculty,
        )
        self.assertTrue(result["rooms"])
        with self.assertRaises(HTTPException) as raised:
            execute_gemini_tool(
                "check_resource_availability",
                {**permitted, "category": "institute_event"},
                faculty,
            )
        self.assertEqual(raised.exception.status_code, 403)

    def test_unauthenticated_agent_request_is_rejected(self):
        dependency = app.dependency_overrides.pop(get_current_principal)
        try:
            response = TestClient(app).post(
                "/api/assistant/agent",
                json={"message": "Show my upcoming bookings."},
            )
            self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides[get_current_principal] = dependency

    def test_authenticated_agent_request_reaches_booking_agent(self):
        response_data = {
            "answer": "I can help with your campus booking.",
            "model": "configured-model",
            "proposals": [],
            "bookings": [],
            "availability": [],
        }
        with patch(
            "app.services.gemini_agent_service.generate_booking_agent_reply",
            return_value=response_data,
        ) as generate:
            response = self.client.post(
                "/api/assistant/agent",
                json={"message": "What can you do?"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], response_data["answer"])
        self.assertFalse(response.json()["read_only"])
        self.assertIs(generate.call_args.args[4], self.principal)

    def test_invalid_agent_payload_returns_422(self):
        response = self.client.post(
            "/api/assistant/agent",
            json={"message": " "},
        )
        self.assertEqual(response.status_code, 422)

    def test_gemini_tool_call_is_executed_by_authenticated_service(self):
        function_call = types.Part(
            function_call=types.FunctionCall(
                name="get_my_bookings",
                args={},
            )
        )
        tool_response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=types.Content(role="model", parts=[function_call])
                )
            ]
        )
        text_response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="You have no bookings yet.")],
                    )
                )
            ]
        )
        client = MagicMock()
        with (
            patch(
                "app.services.gemini_agent_service.create_gemini_client",
                return_value=client,
            ),
            patch(
                "app.services.gemini_agent_service.generate_content_with_fallback",
                side_effect=(
                    (tool_response, "primary-model"),
                    (text_response, "primary-model"),
                ),
            ) as generate,
        ):
            result = generate_booking_agent_reply(
                "Show my upcoming bookings.",
                [],
                "{}",
                "english",
                self.principal,
            )

        self.assertEqual(result["answer"], "You have no bookings yet.")
        self.assertEqual(result["bookings"], [])
        self.assertEqual(generate.call_count, 2)
        client.close.assert_called_once()

    def test_concurrent_admin_approvals_do_not_double_book(self):
        self.principal = _principal("admin-agent", ("ADMIN",))
        payload = _available_event_payload(self.data, self.schedule)
        first = self.client.post("/api/spectra/events", json=payload).json()["event"]
        second = self.client.post("/api/spectra/events", json=payload).json()["event"]

        def approve(event_id):
            return TestClient(app).post(
                f"/api/spectra/events/{event_id}/approve",
                json={},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(
                approve,
                (first["id"], second["id"]),
            ))
        self.assertEqual(sorted(statuses), [200, 409])


if __name__ == "__main__":
    unittest.main()
