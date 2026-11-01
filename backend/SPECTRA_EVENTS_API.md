# Spectra Events API

Event-management endpoints require an authenticated principal. The API derives
the actor from that principal; clients cannot select a role or actor. Event
requesters with `ADMIN`, `FACULTY`, or `STUDENT` may create and manage their own
requests, within their Keycloak role's event-category priority. Only `ADMIN`
may list all events or approve/reject requests. Owners may view, reschedule, or
cancel their own events; administrators may access all events. All event dates
use `YYYY-MM-DD`, and times must match contiguous periods in the supplied
campus calendar. Weekends and unsupported periods are rejected.

The current timetable is a weekday/period model, not an academic-date calendar.
Availability checks apply the supplied weekly schedule to the requested
weekday; holidays and term dates are not inferred.

## Event request

`POST /api/spectra/events/availability` and `POST /api/spectra/events` accept:

```json
{
  "event_name": "Annual student showcase",
  "category": "institute_event",
  "room_id": "AUD",
  "event_date": "2027-01-04",
  "start_time": "09:00",
  "end_time": "11:00",
  "expected_participants": 120,
  "required_features": ["projector"]
}
```

`category` is one of `institute_event`, `guest_lecture`,
`department_event`, or `club_event`. Time and period values are validated
against the campus dataset; clients do not provide period numbers or weekday.
The role-category limits are `STUDENT` → `club_event`, `FACULTY` →
`guest_lecture` and lower, and `ADMIN` → all event categories.

## Endpoints

| Method and path | Behavior |
| --- | --- |
| `GET /api/spectra/events?status=pending` | List events, optionally filtered by `pending`, `approved`, `rejected`, or `cancelled`. Returns `{ "events": [...], "count": n }`. |
| `GET /api/spectra/events/my` | List only the caller's events; available to event requesters. |
| `GET /api/spectra/events/{id}` | Return `{ "event": {...} }`. |
| `GET /api/spectra/events/{id}/history` | Return `{ "event_id": "...", "history": [...] }`; history rows contain `action`, `actor`, `timestamp`, and `reason`. |
| `POST /api/spectra/events/availability` | Preview `{ "event": {...computed period fields...}, "availability": {...} }`; does not create an event. |
| `POST /api/spectra/events` | Create an event with `pending` status and return HTTP 201 with `{ "event": {...}, "availability": {...} }`. Event creation is never approval. |
| `PATCH /api/spectra/events/my/{id}/reschedule` | Reschedule an owned pending or approved event. Checks the new slot inside a write transaction and records old/new values in append-only modification history. |
| `POST /api/spectra/events/{id}/approve` | Recheck availability and priority within a write transaction. Accepts an optional `{ "reason": "..." }`; returns the approved event and availability report. |
| `POST /api/spectra/events/{id}/reject` | Reject a pending event. Requires `{ "reason": "..." }`; returns `{ "event": {...} }`. |
| `POST /api/spectra/events/{id}/cancel` | Cancel a pending or approved event. Requires `{ "reason": "..." }`; returns `{ "event": {...} }`. |

The Gemini assistant stages create, reschedule, and cancellation actions in
owner-scoped, expiring proposals. A separate authenticated confirmation is
required before the backend executes the change. Confirmation rechecks
availability in the same SQLite transaction as the event update. A new
assistant-submitted request remains `pending` until an administrator approves
it. The assistant uses this API workflow and database; it is not a second
booking store.

Availability reports include `available`, individual `checks`, `conflicts`,
and the applied `priority` information. A request may be recorded as pending
even when the preview reports conflicts, so administrators can review it;
approval is denied until the live recheck passes. Room suitability and
participant capacity are validated when creating an event.

Allowed transitions are `pending` to `approved`, `rejected`, or `cancelled`,
and `approved` to `cancelled`. Rejected and cancelled events are terminal.
Approval decisions, rejections, and cancellations are recorded in append-only
SQLite history. Reschedules use a separate append-only modification-history
table. The SQLite database is stored under `backend/storage/`, outside
`exports/`.
