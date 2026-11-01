import { useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Clock3,
  Loader2,
  MessageCircle,
  Send,
  ShieldCheck,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import "./VenueBookingPage.css";

const examples = [
  {
    label: "English example",
    message: "Need a seminar hall tomorrow at 6 PM for 2 hours, 100 people, mic chahiye.",
  },
  {
    label: "Hinglish example",
    message: "Kal seminar hall shaam 6 baje chahiye, 2 ghante, 100 log, mic chahiye.",
  },
  {
    label: "Ask for free times",
    message: "Kal seminar hall khali hai kitne baje?",
  },
];

const weekdays = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
};

function VenueBookingPage() {
  const [message, setMessage] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function checkRequest(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const response = await apiFetch("/api/bookings/interpret", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const detail = typeof payload.detail === "string"
          ? payload.detail
          : payload.detail?.message;
        throw new Error(detail || "Could not interpret the booking request.");
      }
      setResult(payload);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  const request = result?.structured_request;
  const availability = result?.availability;

  return (
    <section className="venue-booking-page">
      <div className="welcome venue-booking-heading">
        <div>
          <span className="eyebrow">NATURAL LANGUAGE VENUE REQUESTS</span>
          <h1>Venue Booking</h1>
          <p>Describe the request in English or Hinglish. We will clarify missing details before checking the schedule.</p>
        </div>
        <span className="venue-language-badge">
          <MessageCircle size={16} />
          English · Hinglish
        </span>
      </div>

      <div className="venue-booking-grid">
        <form className="panel venue-request-panel" onSubmit={checkRequest}>
          <div className="venue-panel-title">
            <div className="venue-step-number">1</div>
            <div>
              <h2>Describe your request</h2>
              <p>Include a date or weekday, time, duration and expected attendance when known.</p>
            </div>
          </div>

          <label className="venue-message-label" htmlFor="venue-request">
            Your message
          </label>
          <textarea
            id="venue-request"
            value={message}
            onChange={event => setMessage(event.target.value)}
            placeholder="Example: Kal seminar hall shaam 6 baje chahiye, 2 ghante, 100 log, mic chahiye."
            maxLength={1000}
            required
            rows={5}
          />
          <div className="venue-message-meta">
            <span>Dates like “kal” are resolved from the current India date; ambiguous context prompts a follow-up.</span>
            <span>{message.length}/1000</span>
          </div>

          <div className="venue-examples" aria-label="Example requests">
            {examples.map(example => (
              <button
                type="button"
                key={example.label}
                onClick={() => {
                  setMessage(example.message);
                  setError("");
                  setResult(null);
                }}
              >
                {example.label}
              </button>
            ))}
          </div>

          <button
            className="venue-check-button"
            type="submit"
            disabled={loading || !message.trim()}
          >
            {loading ? <Loader2 className="venue-spinner" size={17} /> : <Send size={16} />}
            {loading ? "Understanding request…" : "Parse and check availability"}
          </button>
          {error && (
            <div className="venue-result-alert error" role="alert">
              <AlertCircle size={18} />
              <p>{error}</p>
            </div>
          )}
        </form>

        <aside className="panel venue-policy-panel">
          <div className="venue-policy-icon"><ShieldCheck size={21} /></div>
          <h2>Availability preview, not approval</h2>
          <p>
            The check uses the official schedule, room capacity and features,
            maintenance closures, required student-group availability, and
            existing approved venue bookings.
          </p>
          <ul>
            <li>Pending alternatives are not treated as confirmed reservations.</li>
            <li>Existing approved bookings are preserved under the dataset booking priority order.</li>
            <li>No request is submitted or approved by this preview.</li>
          </ul>
          <div className="venue-policy-footnote">
            The dataset contains weekday templates, not semester-wide date mappings.
            A specific date is checked against its weekday schedule.
          </div>
        </aside>
      </div>

      {result && (
        <section className="panel venue-result-panel" aria-live="polite">
          <div className="venue-result-heading">
            <div className={`venue-result-icon ${
              result.status === "needs_clarification"
                ? "needs-info"
                : availability?.status === "available"
                  ? "is-available"
                  : availability?.status === "alternative_available"
                    ? "has-alternative"
                    : "not-available"
            }`}>
              {result.status === "needs_clarification"
                ? <MessageCircle size={20} />
                : availability?.status === "available"
                  ? <CheckCircle2 size={20} />
                  : <AlertCircle size={20} />}
            </div>
            <div>
              <span className="eyebrow">
                {result.status === "needs_clarification"
                  ? "NEEDS MORE DETAILS"
                  : "AVAILABILITY CHECK"}
              </span>
              <h2>{result.message}</h2>
            </div>
            {result.approval_status && (
              <span className="venue-not-approved">Not approved</span>
            )}
          </div>

          {result.room_constraint && (
            <div className="venue-room-constraint" role="status">
              <strong>Room requirement notice</strong>
              <p>{result.room_constraint.message}</p>
              <span>
                {result.status === "needs_clarification"
                  ? "Room fit is identified, but schedule availability has not been checked yet."
                  : "Room-fit suggestions are subject to the availability shown below."}
              </span>
            </div>
          )}

          {request && (
            <div className="venue-structured-request">
              <div className="venue-structured-heading">
                <h3>Structured request</h3>
                <span>Parsed from your message</span>
              </div>
              <div className="venue-structured-grid">
                <div>
                  <span><CalendarDays size={14} /> Date / weekday</span>
                  <strong>
                    {request.date || (request.weekdays || []).map(day => weekdays[day] || day).join(", ") || "Needs clarification"}
                  </strong>
                </div>
                <div>
                  <span><Clock3 size={14} /> Time window</span>
                  <strong>
                    {request.windows?.length
                      ? request.windows.map(window =>
                        `${weekdays[window.day] || window.day} P${window.from_period}–P${window.to_period}`,
                      ).join(" · ")
                      : "Needs clarification"}
                  </strong>
                </div>
                <div>
                  <span>Room type</span>
                  <strong>{request.room_types?.map(type => type.replaceAll("_", " ")).join(", ") || "Needs clarification"}</strong>
                </div>
                <div>
                  <span>Capacity</span>
                  <strong>{request.attendance ?? "Needs clarification"}</strong>
                </div>
                <div>
                  <span>Duration</span>
                  <strong>{request.duration_periods ? `${request.duration_periods} period(s)` : "Needs clarification"}</strong>
                </div>
                <div>
                  <span>Purpose / priority category</span>
                  <strong>
                    {request.purpose
                      ? request.purpose.replaceAll("_", " ")
                      : "Not specified"}
                  </strong>
                </div>
                {request.audience && (
                  <div>
                      <span>Audience</span>
                      <strong>{request.audience}</strong>
                  </div>
                )}
                <div>
                  <span>Required features</span>
                  <strong>{request.needs?.length ? request.needs.join(", ") : "None specified"}</strong>
                </div>
                <div>
                  <span>Mandatory student groups</span>
                  <strong>{request.mandatory_for_groups?.length ? request.mandatory_for_groups.join(", ") : "None specified"}</strong>
                </div>
              </div>
            </div>
          )}

          {result.follow_up_questions?.length > 0 && (
            <div className="venue-follow-up">
              <strong>Follow-up</strong>
              <ul>
                {result.follow_up_questions.map(question => <li key={question}>{question}</li>)}
              </ul>
              <p>Update your message above with these details, then check again.</p>
            </div>
          )}

          {availability && (
            <>
              {availability.scope && (
                <p className="venue-schedule-scope">{availability.scope}</p>
              )}
              {availability.requested_room_available === false && (
                <div className="venue-alternative-note">
                  The requested room {result.requested_room} does not meet the requirements or is not free at that time.
                  Suitable alternatives are shown below.
                </div>
              )}
              <div className="venue-slots-heading">
                <h3>
                  {availability.available_slots.length
                    ? availability.status === "alternative_available"
                      ? "Available alternatives"
                      : request.availability_query
                        ? "Available times"
                        : "Available rooms"
                    : "No available room"}
                </h3>
                <span>{availability.available_slots.length} option(s)</span>
              </div>
              {availability.available_slots.length > 0 ? (
                <div className="venue-slots-list">
                  {availability.available_slots.map(slot => (
                    <article
                      className="venue-slot-card"
                      key={`${slot.room_id}-${slot.day_code}-${slot.start_period}-${slot.end_period}`}
                    >
                      <div className="venue-slot-icon"><CalendarDays size={18} /></div>
                      <div className="venue-slot-main">
                        <strong>{slot.room_name}</strong>
                        <span>
                          {slot.day}{slot.date ? ` · ${slot.date}` : ""} ·{" "}
                          {slot.start_time}–{slot.end_time}
                        </span>
                      </div>
                      <div className="venue-slot-meta">
                        <strong>{slot.room_id}</strong>
                        <span>{slot.room_type.replaceAll("_", " ")} · capacity {slot.capacity}</span>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="venue-no-slots">
                  Try another weekday/time or adjust the requested room type, capacity or required features.
                </p>
              )}
              {result.suggested_alternatives?.length > 0
                && availability.available_slots.length === 0 && (
                <>
                  <div className="venue-slots-heading">
                    <h3>
                      {availability.available_slots.length
                        ? "Other suitable rooms"
                        : "Other available times on this weekday"}
                    </h3>
                    <span>{result.suggested_alternatives.length} option(s)</span>
                  </div>
                  <div className="venue-slots-list">
                    {result.suggested_alternatives.map(slot => (
                      <article
                        className="venue-slot-card"
                        key={`${slot.room_id}-${slot.day_code}-${slot.start_period}-${slot.end_period}`}
                      >
                        <div className="venue-slot-icon"><CalendarDays size={18} /></div>
                        <div className="venue-slot-main">
                          <strong>{slot.room_name}</strong>
                          <span>
                            {slot.day}{slot.date ? ` · ${slot.date}` : ""} ·{" "}
                            {slot.start_time}–{slot.end_time}
                          </span>
                        </div>
                        <div className="venue-slot-meta">
                          <strong>{slot.room_id}</strong>
                          <span>{slot.room_type.replaceAll("_", " ")} · capacity {slot.capacity}</span>
                        </div>
                      </article>
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          {result.priority && (
            <div className="venue-priority-note">
              <ShieldCheck size={16} />
              {result.priority.note}
            </div>
          )}
        </section>
      )}
    </section>
  );
}

export default VenueBookingPage;
