import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from google.genai import errors

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.api.assistant import AssistantChatRequest, chat_with_campus_assistant
from app.api.timetable import build_timetable_data
from app.main import app
from app.services import campus_context, gemini_service
from app.services.dataset_loader import load_dataset


def mocked_gemini_response(answer, model="test-gemini-model"):
    return gemini_service.GeminiAssistantResponse(answer=answer, model=model)


class AssistantRequestTests(unittest.TestCase):
    def test_history_message_limit_matches_current_message_limit(self):
        request = AssistantChatRequest(
            message="follow up",
            history=[
                {"role": "user", "content": "x" * 2000},
                {"role": "model", "content": "answer"},
            ],
        )
        self.assertEqual(len(request.history[0].content), 2000)

        with self.assertRaises(ValidationError):
            AssistantChatRequest(
                message="follow up",
                history=[
                    {"role": "user", "content": "x" * 2001},
                    {"role": "model", "content": "answer"},
                ],
            )

    def test_request_limits_conversation_history_and_total_size(self):
        with self.assertRaises(ValidationError):
            AssistantChatRequest(
                message="question",
                history=[
                    {"role": "model", "content": "not a valid first turn"},
                ],
            )

        with self.assertRaises(ValidationError):
            AssistantChatRequest(
                message="question",
                history=[
                    {"role": "user", "content": "question"},
                    {"role": "model", "content": "answer"},
                    {"role": "user", "content": "incomplete turn"},
                ],
            )

        with self.assertRaises(ValidationError):
            AssistantChatRequest(
                message="x" * 2000,
                history=[
                    {"role": "user", "content": "y" * 1500},
                    {"role": "model", "content": "z" * 1500},
                    {"role": "user", "content": "y" * 1500},
                    {"role": "model", "content": "z" * 1500},
                    {"role": "user", "content": "y" * 1500},
                    {"role": "model", "content": "z" * 1500},
                    {"role": "user", "content": "y" * 1500},
                    {"role": "model", "content": "z" * 1500},
                ],
            )

    def test_valid_history_is_alternating_and_ends_with_model(self):
        request = AssistantChatRequest(
            message="What about Tuesday?",
            history=[
                {"role": "user", "content": "When is F19 free?"},
                {"role": "model", "content": "Here is the weekly schedule."},
            ],
        )
        self.assertEqual(len(request.history), 2)


class AssistantContextTests(unittest.TestCase):
    def setUp(self):
        data = load_dataset()
        schedule_path = campus_context.PROJECT_ROOT / "exports" / "schedule.json"
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        approved_timetable = {
            "status": "success",
            "data": build_timetable_data(data, schedule),
        }
        patcher = patch.object(
            campus_context,
            "get_timetable",
            return_value=approved_timetable,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_context_uses_existing_faculty_and_timetable_data(self):
        context = campus_context.build_campus_context(
            "When is Dr Shalini Gupta free?",
            [],
        )
        self.assertEqual(context["sources"]["timetable"], "available")
        self.assertIn("faculty", context["data"])
        matched = context["data"]["faculty"]["matching_faculty_schedules"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["faculty_id"], "F19")
        self.assertTrue(matched[0]["scheduled_sessions"])

    def test_unavailable_official_schedule_is_explicit_in_context(self):
        with patch(
            "app.services.campus_context.get_timetable",
            side_effect=HTTPException(status_code=503),
        ):
            context = campus_context.build_campus_context(
                "Which room is free?",
                [],
            )
        self.assertEqual(context["sources"]["timetable"], "unavailable")
        self.assertEqual(context["data"]["rooms"]["status"], "unavailable")

    def test_exam_data_is_unavailable_when_saved_schedule_is_missing(self):
        with patch.object(
            campus_context,
            "EXAM_EXPORT_PATH",
            Path("missing-exam-schedule-for-test.json"),
        ):
            context = campus_context.build_campus_context(
                "What is the exam schedule?",
                [],
            )
        self.assertEqual(context["sources"]["exams"], "unavailable")
        self.assertEqual(context["data"]["exams"]["status"], "unavailable")

    def test_booking_context_does_not_include_requester_identity(self):
        context = campus_context.build_campus_context(
            "Show booking status and priority rules.",
            [],
        )
        approved = context["data"]["bookings"]["approved_bookings"]
        self.assertTrue(approved)
        self.assertTrue(all("requester" not in item for item in approved))
        self.assertTrue(all("email" not in item for item in approved))

    def test_saved_exam_export_is_checked_against_dataset(self):
        context = campus_context.build_campus_context(
            "What is the examination schedule?",
            [],
        )
        exams = context["data"]["exams"]
        self.assertEqual(context["sources"]["exams"], "available")
        self.assertEqual(len(exams["assignments"]), 41)
        self.assertTrue(all("start_time" in item for item in exams["assignments"]))

    def test_room_context_includes_approved_event_periods(self):
        context = campus_context.build_campus_context(
            "When is Open Air Theatre available?",
            [],
        )
        room = next(
            item
            for item in context["data"]["rooms"]["rooms"]
            if item["room_id"] == "OAT"
        )
        timetable = campus_context.get_timetable()["data"]
        approved_periods = {
            period
            for booking in timetable["approved_bookings"]
            if booking["room_id"] == "OAT" and booking["day"] == "THU"
            for period in range(
                booking["start_period"],
                booking["end_period"] + 1,
            )
        }
        self.assertTrue(approved_periods)
        self.assertTrue(
            approved_periods.issubset(
                set(room["occupied_periods_by_day"]["THU"])
            )
        )
        self.assertFalse(any(
            "requester" in item
            for item in context["data"]["rooms"]["rooms"]
        ))


class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        principal = AuthenticatedPrincipal(
            issuer="test-issuer",
            subject="assistant-user",
            roles=("STUDENT",),
            departments=(),
            username="assistant-user",
        )
        app.dependency_overrides[get_current_principal] = lambda: principal
        self.context = {
            "sources": {"timetable": "available"},
            "data": {"timetable": {"status": "unavailable"}},
        }

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_english_and_hinglish_are_passed_to_gemini(self):
        for message, language in (
            ("How is room utilization calculated?", "english"),
            ("Kal seminar hall khali hai?", "hinglish"),
        ):
            with (
                patch(
                    "app.api.assistant.build_campus_context",
                    return_value=self.context,
                ),
                patch(
                    "app.api.assistant.generate_assistant_reply",
                    return_value=mocked_gemini_response("A grounded response."),
                ) as generate,
            ):
                result = chat_with_campus_assistant(
                    AssistantChatRequest(message=message)
                )
            self.assertEqual(result["language"], language)
            self.assertTrue(result["read_only"])
            self.assertEqual(result["answer"], "A grounded response.")
            self.assertEqual(generate.call_args.args[3], language)
            self.assertNotIn("api_key", result)

    def test_post_chat_endpoint_supports_english_hinglish_and_follow_up(self):
        client = TestClient(app)
        history = [
            {"role": "user", "content": "When is F19 free?"},
            {"role": "model", "content": "F19's weekly schedule is available."},
        ]
        with (
            patch(
                "app.api.assistant.build_campus_context",
                return_value=self.context,
            ),
            patch(
                "app.api.assistant.generate_assistant_reply",
                side_effect=(
                    mocked_gemini_response(
                        "Room utilization is measured from the schedule."
                    ),
                    mocked_gemini_response(
                        "Kal ki availability schedule par depend karti hai."
                    ),
                    mocked_gemini_response(
                        "F19's Wednesday timetable is available."
                    ),
                ),
            ),
        ):
            english = client.post(
                "/api/assistant/chat",
                json={"message": "How is room utilization calculated?"},
            )
            hinglish = client.post(
                "/api/assistant/chat",
                json={"message": "Kal seminar hall khali hai?"},
            )
            follow_up = client.post(
                "/api/assistant/chat",
                json={"message": "What about Wednesday?", "history": history},
            )

        self.assertEqual(english.status_code, 200, english.text)
        self.assertEqual(english.json()["language"], "english")
        self.assertEqual(hinglish.status_code, 200, hinglish.text)
        self.assertEqual(hinglish.json()["language"], "hinglish")
        self.assertEqual(follow_up.status_code, 200, follow_up.text)
        self.assertTrue(follow_up.json()["read_only"])
        self.assertEqual(follow_up.json()["model"], "test-gemini-model")

    def test_post_chat_endpoint_reports_missing_key_without_gemini_call(self):
        client = TestClient(app)
        with patch(
            "app.api.assistant.generate_assistant_reply",
            side_effect=gemini_service.GeminiConfigurationError(
                "GEMINI_API_KEY is not configured."
            ),
        ):
            response = client.post(
                "/api/assistant/chat",
                json={"message": "Hello"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Set GEMINI_API_KEY", response.json()["detail"])

    def test_chat_endpoint_rejects_unauthenticated_requests(self):
        dependency = app.dependency_overrides.pop(get_current_principal)
        try:
            response = TestClient(app).post(
                "/api/assistant/chat",
                json={"message": "Hello"},
            )
        finally:
            app.dependency_overrides[get_current_principal] = dependency

        self.assertEqual(response.status_code, 401)

    def test_chat_endpoint_returns_configuration_error_when_key_is_missing(self):
        client = TestClient(app)
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
            patch("app.services.gemini_service.genai.Client") as create_client,
        ):
            response = client.post(
                "/api/assistant/chat",
                json={"message": "Hello, what can you do for CIT Nexus?"},
            )

        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("Set GEMINI_API_KEY", response.json()["detail"])
        create_client.assert_not_called()

    def test_follow_up_context_is_forwarded_to_campus_data_and_gemini(self):
        history = [
            {"role": "user", "content": "When is F19 free?"},
            {"role": "model", "content": "F19 is free on Tuesday."},
        ]
        request = AssistantChatRequest(
            message="What about Wednesday?",
            history=history,
        )
        with (
            patch(
                "app.api.assistant.build_campus_context",
                return_value=self.context,
            ) as build_context,
            patch(
                "app.api.assistant.generate_assistant_reply",
                return_value=mocked_gemini_response(
                    "Wednesday schedule details."
                ),
            ) as generate,
        ):
            result = chat_with_campus_assistant(request)

        self.assertEqual(build_context.call_args.args, (request.message, history))
        self.assertEqual(generate.call_args.args[1], history)
        self.assertEqual(result["answer"], "Wednesday schedule details.")

    def test_unavailable_source_is_included_in_model_context(self):
        unavailable = {
            "sources": {"timetable": "unavailable"},
            "data": {
                "timetable": {
                    "status": "unavailable",
                    "reason": "The official timetable is unavailable.",
                },
            },
        }
        with (
            patch(
                "app.api.assistant.build_campus_context",
                return_value=unavailable,
            ),
            patch(
                "app.api.assistant.generate_assistant_reply",
                return_value=mocked_gemini_response(
                    "The timetable data is unavailable."
                ),
            ) as generate,
        ):
            result = chat_with_campus_assistant(
                AssistantChatRequest(message="Show the timetable.")
            )

        self.assertIn('"status":"unavailable"', generate.call_args.args[2])
        self.assertEqual(result["sources"]["timetable"], "unavailable")

    def test_gemini_failures_map_to_safe_http_responses(self):
        errors = (
            (gemini_service.GeminiConfigurationError, 503),
            (gemini_service.GeminiAuthenticationError, 502),
            (gemini_service.GeminiModelError, 502),
            (gemini_service.GeminiRequestError, 502),
            (gemini_service.GeminiQuotaError, 429),
            (gemini_service.GeminiTimeoutError, 504),
            (gemini_service.GeminiUnavailableError, 502),
        )
        for error_type, status_code in errors:
            with (
                patch(
                    "app.api.assistant.build_campus_context",
                    return_value=self.context,
                ),
                patch(
                    "app.api.assistant.generate_assistant_reply",
                    side_effect=error_type("internal detail"),
                ),
            ):
                with self.assertRaises(HTTPException) as raised:
                    chat_with_campus_assistant(
                        AssistantChatRequest(message="Any campus update?")
                    )
            self.assertEqual(raised.exception.status_code, status_code)
            self.assertNotIn("internal detail", str(raised.exception.detail))

    def test_api_key_is_loaded_from_backend_env_independent_of_current_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "GEMINI_API_KEY=temporary-test-credential\n"
                "GEMINI_MODEL=temporary-test-model\n"
                "GEMINI_FALLBACK_MODEL=temporary-fallback-model\n",
                encoding="utf-8",
            )
            mock_client = unittest.mock.MagicMock()
            mock_client.models.generate_content.return_value.text = "Ready."
            with (
                patch.object(gemini_service, "BACKEND_ENV_PATH", env_path),
                patch.dict(os.environ, clear=True),
                patch(
                    "app.services.gemini_service.genai.Client",
                    return_value=mock_client,
                ) as create_client,
            ):
                response = gemini_service.generate_assistant_reply(
                    "Hello",
                    [],
                    "{}",
                    "english",
                )

        self.assertEqual(response.answer, "Ready.")
        self.assertEqual(response.model, "temporary-test-model")
        self.assertEqual(
            create_client.call_args.kwargs["api_key"],
            "temporary-test-credential",
        )
        self.assertEqual(
            mock_client.models.generate_content.call_args.kwargs["model"],
            "temporary-test-model",
        )

    def test_chat_does_not_modify_the_official_schedule(self):
        root = Path(__file__).resolve().parents[2]
        schedule_path = root / "exports" / "schedule.json"
        original = schedule_path.read_bytes()
        with (
            patch(
                "app.api.assistant.build_campus_context",
                return_value=self.context,
            ),
            patch(
                "app.api.assistant.generate_assistant_reply",
                return_value=mocked_gemini_response(
                    "I cannot modify the schedule."
                ),
            ),
        ):
            result = chat_with_campus_assistant(
                AssistantChatRequest(
                    message="Approve this booking and update the schedule."
                )
            )
        self.assertTrue(result["read_only"])
        self.assertEqual(schedule_path.read_bytes(), original)


class GeminiServiceTests(unittest.TestCase):
    def test_missing_api_key_fails_before_client_creation(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
            patch("app.services.gemini_service.genai.Client") as create_client,
        ):
            with self.assertRaises(gemini_service.GeminiConfigurationError):
                gemini_service.generate_assistant_reply(
                    "Hello",
                    [],
                    "{}",
                    "english",
                )
        create_client.assert_not_called()

    def test_sdk_receives_server_side_key_model_and_capped_generation(self):
        mock_client = unittest.mock.MagicMock()
        mock_client.models.generate_content.return_value.text = "  Verified answer.  "
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key-not-returned",
                    "GEMINI_MODEL": "test-gemini-model",
                },
            ),
            patch(
                "app.services.gemini_service.genai.Client",
                return_value=mock_client,
            ) as create_client,
        ):
            response = gemini_service.generate_assistant_reply(
                "Follow up?",
                [
                    {"role": "user", "content": "First question"},
                    {"role": "model", "content": "First answer"},
                ],
                '{"data":{"status":"verified"}}',
                "hinglish",
            )

        self.assertEqual(response.answer, "Verified answer.")
        self.assertEqual(response.model, "test-gemini-model")
        create_client.assert_called_once()
        self.assertEqual(
            create_client.call_args.kwargs["api_key"],
            "test-key-not-returned",
        )
        call = mock_client.models.generate_content.call_args
        self.assertEqual(call.kwargs["model"], "test-gemini-model")
        self.assertEqual(
            [item.role for item in call.kwargs["contents"]],
            ["user", "model", "user"],
        )
        self.assertEqual(
            call.kwargs["config"].max_output_tokens,
            gemini_service.MAX_OUTPUT_TOKENS,
        )
        system_instruction = call.kwargs["config"].system_instruction
        self.assertIn("read-only", system_instruction)
        self.assertIn("Hinglish", system_instruction)
        mock_client.close.assert_called_once()

    def test_sdk_quota_errors_and_timeouts_are_classified(self):
        cases = (
            (
                errors.APIError(
                    401,
                    {
                        "error": {
                            "status": "UNAUTHENTICATED",
                            "message": "synthetic invalid key",
                        },
                    },
                ),
                gemini_service.GeminiAuthenticationError,
            ),
            (
                errors.APIError(
                    404,
                    {
                        "error": {
                            "status": "NOT_FOUND",
                            "message": "synthetic invalid model",
                        },
                    },
                ),
                gemini_service.GeminiModelError,
            ),
            (
                errors.APIError(
                    400,
                    {
                        "error": {
                            "status": "INVALID_ARGUMENT",
                            "message": "Model is not supported",
                        },
                    },
                ),
                gemini_service.GeminiModelError,
            ),
            (
                errors.APIError(429, {"error": {"message": "quota"}}),
                gemini_service.GeminiQuotaError,
            ),
            (
                errors.APIError(503, {"error": {"message": "unavailable"}}),
                gemini_service.GeminiUnavailableError,
            ),
            (
                httpx.ConnectError("synthetic network failure"),
                gemini_service.GeminiUnavailableError,
            ),
            (
                httpx.ReadTimeout("upstream timed out"),
                gemini_service.GeminiTimeoutError,
            ),
        )
        for error, expected in cases:
            mock_client = unittest.mock.MagicMock()
            mock_client.models.generate_content.side_effect = error
            with (
                patch.dict(
                    os.environ,
                    {
                        "GEMINI_API_KEY": "test-key",
                        "GEMINI_FALLBACK_MODEL": "",
                    },
                ),
                patch(
                    "app.services.gemini_service.genai.Client",
                    return_value=mock_client,
                ),
                patch("app.services.gemini_service.time.sleep"),
            ):
                with self.assertRaises(expected):
                    gemini_service.generate_assistant_reply(
                        "A question",
                        [],
                        "{}",
                        "english",
                    )
            mock_client.close.assert_called_once()

    def test_temporary_503_retries_primary_with_jitter_then_succeeds(self):
        temporary_error = errors.APIError(
            503,
            {
                "error": {
                    "status": "UNAVAILABLE",
                    "message": "synthetic transient provider error",
                },
            },
        )
        mock_client = unittest.mock.MagicMock()
        mock_client.models.generate_content.side_effect = [
            temporary_error,
            unittest.mock.MagicMock(text="Recovered response."),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "GEMINI_MODEL": "primary-model",
                    "GEMINI_FALLBACK_MODEL": "fallback-model",
                },
            ),
            patch(
                "app.services.gemini_service.genai.Client",
                return_value=mock_client,
            ),
            patch("app.services.gemini_service.random.uniform", return_value=0.25),
            patch("app.services.gemini_service.time.sleep") as sleep,
        ):
            response = gemini_service.generate_assistant_reply(
                "A question",
                [],
                "{}",
                "english",
            )

        self.assertEqual(response.answer, "Recovered response.")
        self.assertEqual(response.model, "primary-model")
        self.assertEqual(
            [
                call.kwargs["model"]
                for call in mock_client.models.generate_content.call_args_list
            ],
            ["primary-model", "primary-model"],
        )
        sleep.assert_called_once_with(0.25)
        mock_client.close.assert_called_once()

    def test_exhausted_primary_503_uses_configured_fallback_once(self):
        temporary_error = errors.APIError(
            503,
            {
                "error": {
                    "status": "UNAVAILABLE",
                    "message": "synthetic transient provider error",
                },
            },
        )
        mock_client = unittest.mock.MagicMock()
        mock_client.models.generate_content.side_effect = [
            temporary_error,
            temporary_error,
            temporary_error,
            temporary_error,
            unittest.mock.MagicMock(text="Fallback response."),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "GEMINI_MODEL": "primary-model",
                    "GEMINI_FALLBACK_MODEL": "fallback-model",
                },
            ),
            patch(
                "app.services.gemini_service.genai.Client",
                return_value=mock_client,
            ),
            patch(
                "app.services.gemini_service.random.uniform",
                side_effect=lambda lower, upper: upper,
            ),
            patch("app.services.gemini_service.time.sleep") as sleep,
        ):
            response = gemini_service.generate_assistant_reply(
                "A question",
                [],
                "{}",
                "english",
            )

        self.assertEqual(response.answer, "Fallback response.")
        self.assertEqual(response.model, "fallback-model")
        self.assertEqual(
            [
                call.kwargs["model"]
                for call in mock_client.models.generate_content.call_args_list
            ],
            ["primary-model"] * 4 + ["fallback-model"],
        )
        sleep.assert_has_calls([
            unittest.mock.call(0.5),
            unittest.mock.call(1.0),
            unittest.mock.call(2.0),
        ])
        mock_client.close.assert_called_once()

    def test_transient_gateway_and_network_errors_use_fallback(self):
        errors_by_attempt = (
            errors.APIError(
                502,
                {"error": {"status": "BAD_GATEWAY"}},
            ),
            httpx.ConnectError("synthetic network failure"),
            errors.APIError(
                504,
                {"error": {"status": "GATEWAY_TIMEOUT"}},
            ),
            errors.APIError(
                500,
                {"error": {"status": "INTERNAL"}},
            ),
        )
        mock_client = unittest.mock.MagicMock()
        mock_client.models.generate_content.side_effect = [
            *errors_by_attempt,
            unittest.mock.MagicMock(text="Recovered with fallback."),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key",
                    "GEMINI_MODEL": "primary-model",
                    "GEMINI_FALLBACK_MODEL": "fallback-model",
                },
            ),
            patch(
                "app.services.gemini_service.genai.Client",
                return_value=mock_client,
            ),
            patch(
                "app.services.gemini_service.random.uniform",
                side_effect=lambda lower, upper: upper,
            ),
            patch("app.services.gemini_service.time.sleep") as sleep,
        ):
            response = gemini_service.generate_assistant_reply(
                "A question",
                [],
                "{}",
                "english",
            )

        self.assertEqual(response.answer, "Recovered with fallback.")
        self.assertEqual(response.model, "fallback-model")
        self.assertEqual(
            [
                call.kwargs["model"]
                for call in mock_client.models.generate_content.call_args_list
            ],
            ["primary-model"] * 4 + ["fallback-model"],
        )
        self.assertEqual(sleep.call_count, 3)
        mock_client.close.assert_called_once()

    def test_authentication_and_invalid_request_errors_are_not_retried(self):
        cases = (
            (
                errors.APIError(
                    401,
                    {"error": {"status": "UNAUTHENTICATED"}},
                ),
                gemini_service.GeminiAuthenticationError,
            ),
            (
                errors.APIError(
                    400,
                    {"error": {"status": "INVALID_ARGUMENT"}},
                ),
                gemini_service.GeminiRequestError,
            ),
        )
        for error, expected in cases:
            with self.subTest(error=expected.__name__):
                mock_client = unittest.mock.MagicMock()
                mock_client.models.generate_content.side_effect = error
                with (
                    patch.dict(
                        os.environ,
                        {
                            "GEMINI_API_KEY": "test-key",
                            "GEMINI_MODEL": "primary-model",
                            "GEMINI_FALLBACK_MODEL": "fallback-model",
                        },
                    ),
                    patch(
                        "app.services.gemini_service.genai.Client",
                        return_value=mock_client,
                    ),
                    patch("app.services.gemini_service.time.sleep") as sleep,
                ):
                    with self.assertRaises(expected):
                        gemini_service.generate_assistant_reply(
                            "A question",
                            [],
                            "{}",
                            "english",
                        )

                mock_client.models.generate_content.assert_called_once()
                sleep.assert_not_called()
                mock_client.close.assert_called_once()

    def test_provider_error_logs_only_safe_allowlisted_details(self):
        provider_error = errors.APIError(
            503,
            {
                "error": {
                    "status": "UNAVAILABLE",
                    "message": "never log this user-supplied secret",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "BACKEND_UNAVAILABLE",
                        },
                    ],
                },
            },
        )
        mock_client = unittest.mock.MagicMock()
        mock_client.models.generate_content.side_effect = provider_error
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "test-key-secret",
                    "GEMINI_FALLBACK_MODEL": "",
                },
            ),
            patch(
                "app.services.gemini_service.genai.Client",
                return_value=mock_client,
            ),
            patch("app.services.gemini_service.time.sleep"),
            self.assertLogs("app.services.gemini_service", level="WARNING") as logs,
        ):
            with self.assertRaises(gemini_service.GeminiUnavailableError):
                gemini_service.generate_assistant_reply(
                    "prompt containing private data",
                    [],
                    "{}",
                    "english",
                )

        output = "\n".join(logs.output)
        self.assertIn("http_status=503", output)
        self.assertIn("provider_code=UNAVAILABLE", output)
        self.assertIn("diagnostic_reason=BACKEND_UNAVAILABLE", output)
        self.assertNotIn("test-key-secret", output)
        self.assertNotIn("private data", output)
        self.assertNotIn("never log this user-supplied secret", output)


if __name__ == "__main__":
    unittest.main()
