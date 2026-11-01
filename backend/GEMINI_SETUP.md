# Gemini AI Campus Assistant

The assistant uses Google's official `google-genai` Python SDK from FastAPI.
The API key is read only by the backend and is never returned to the frontend.
`POST /api/assistant/chat` remains read-only and uses campus information from existing timetable,
analytics, and crisis APIs, the public CIT dataset, and the saved examination
export after validating it against the current dataset. It does not approve
requests, run simulations, or modify timetables.

## Install

From `backend`, install the declared dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

At startup, the backend loads `backend/.env` using a path resolved from
`app/main.py`, independent of the shell's working directory. Variables already
set in the backend process take precedence. Restart the backend after changing
the file. For local PowerShell development, the same values can instead be set
in the process environment:

```powershell
$env:GEMINI_API_KEY = "your-Google-AI-Studio-key"
$env:GEMINI_MODEL = "gemini-3.8-flash"
$env:GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

`GEMINI_MODEL` defaults to `gemini-3.8-flash`; `GEMINI_FALLBACK_MODEL` defaults
to `gemini-3.5-flash-lite`. Set the fallback variable to an empty value to
disable fallback. On HTTP 503 responses only, the backend retries the primary
model up to three times using exponential backoff with jitter, then tries the
configured fallback once. Authentication errors, invalid requests, quota
errors, and model-resolution errors are not retried. Configure these variables
in the backend deployment environment. Do not put the API key in a `VITE_*`
setting, React source, or a committed `.env` file. Provider logs contain only
HTTP status, provider status code, and allowlisted diagnostic reason; they
never include the API key, prompt, or raw provider message. The endpoint
reports the model that actually generated the response.

## Endpoint

`POST /api/assistant/chat`

```json
{
  "message": "Kal seminar hall khali hai?",
  "history": [
    {"role": "user", "content": "I need a room tomorrow."},
    {"role": "model", "content": "What room type do you need?"}
  ]
}
```

The message is limited to 2,000 characters, history to eight alternating
user/model messages, and the combined input to 8,000 characters. Responses
include `answer`, detected `language`, configured `model`, source availability,
and `read_only: true`. Gemini quota errors, timeouts, missing configuration,
and service errors map to explicit HTTP responses.

Campus data uses the existing Monday-Friday schedule template; no semester
date mapping is inferred. Room occupancy includes scheduled teaching,
maintenance, and approved bookings only. Pending or alternative-offered venue
requests are not presented as confirmed reservations. If a data source is
unavailable, that state is included in the model context so the assistant can
state the limitation.

## Booking agent

The authenticated React assistant uses `POST /api/assistant/agent` for
conversational booking actions. The endpoint exposes only validated backend
tools for availability checks, listing and viewing the caller's events, and
preparing create, reschedule, or cancellation proposals. Tool execution uses
the verified Keycloak principal; caller-supplied ownership and role values are
not accepted.

Every write is staged as an owner-scoped proposal and requires a separate
`POST /api/assistant/confirm` request with its `proposal_id` and a boolean
`confirmed` value. `GET /api/assistant/proposals` restores the caller's
unexpired confirmations after reload. Proposals expire after ten minutes and
cannot be confirmed by another account. The frontend displays availability,
booking details, the proposal summary, and explicit confirmation/dismiss
controls.

The agent reuses the existing Spectra Events SQLite workflow and official
weekday schedule; it does not create a second booking database or call SQL
from Gemini. Room capacity, event features, role/category priority, schedule
conflicts, ownership, and current availability are rechecked by the backend.
Admin, faculty, and student roles are constrained to their existing event
priority categories. Booking requests enter the existing `pending` status and
still require administrator approval; user confirmation is not a reservation.
Reschedules are recorded in a separate append-only modification-history table.

The agent does not replace `/api/bookings/interpret`, which remains a read-only
natural-language availability preview.
