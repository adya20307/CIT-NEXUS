# Natural-language venue availability

`POST /api/bookings/interpret` accepts a JSON body containing a `message`
(maximum 1,000 characters). It parses English and Hinglish requests into the
same structured request, then checks complete requests against the official
`exports/schedule.json` and `cit_public.json` data.

The parser recognizes room types and IDs, requested dates or weekdays, schedule
period times, duration, attendance, common room features, event purpose, and
explicitly mandatory student groups. Requests with missing or ambiguous
details return `needs_clarification` and language-matched follow-up questions.
Availability questions can omit attendance and duration; the preview then
lists matching free periods.

If a specifically named room cannot meet a stated capacity or feature
requirement, the response includes `room_constraint` with room-fit alternatives
from the dataset. This can be returned alongside clarification questions; it
does not imply that an alternative is available in the schedule.

Relative dates use India-local time. In a booking request, `kal` means tomorrow
unless past-tense context indicates yesterday; `parso` similarly resolves to
the day after or before tomorrow. The dataset provides weekday templates but
does not map weekdays to semester dates, so an absolute date is checked against
its weekday template only.

This endpoint remains a read-only availability preview. It preserves teaching,
maintenance, mandatory-group, and already-approved booking constraints;
pending and alternative-offered bookings are not treated as reservations.
Existing schedule decisions retain the dataset's priority order. The endpoint
does not submit or approve requests, move existing approvals, or accept a
client-supplied role.

When the requested time is unavailable, the preview may also return free
periods on the same requested weekday as `suggested_alternatives`. These are
suggestions only; the user must make a new request to choose a different time.

The authenticated Gemini booking agent is a separate workflow. See
`GEMINI_SETUP.md` for `/api/assistant/agent`, its explicit confirmation step,
and the existing Spectra Events administrator-approval process. It submits
requests through the same Spectra Events SQLite store; it does not create an
independent booking database or bypass schedule, room, capacity, feature,
ownership, or role checks.
