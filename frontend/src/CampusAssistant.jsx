import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  CalendarDays,
  Check,
  Database,
  Loader2,
  MapPin,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import "./CampusAssistant.css";

const INITIAL_MESSAGE = {
  id: "welcome",
  role: "model",
  content: "Hi! I can help with CIT Nexus timetables, rooms, faculty availability, bookings, analytics, Crisis Mode, and exams. Ask in English or Hinglish.",
};
const QUICK_PROMPTS = [
  "How is room utilization calculated?",
  "Kal seminar hall khali hai?",
  "Book an available room for 20 people tomorrow.",
];

async function requestJSON(path, options) {
  const response = await apiFetch(path, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : payload.detail?.message;
    throw new Error(detail || "The booking request could not be completed.");
  }
  return payload;
}

function CampusAssistant() {
  const [messages, setMessages] = useState([INITIAL_MESSAGE]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [bookings, setBookings] = useState([]);
  const [proposals, setProposals] = useState([]);
  const [availabilityChecks, setAvailabilityChecks] = useState([]);
  const [processingProposal, setProcessingProposal] = useState("");
  const endOfMessagesRef = useRef(null);

  useEffect(() => {
    endOfMessagesRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    let active = true;
    Promise.all([
      requestJSON("/api/spectra/events/my"),
      requestJSON("/api/assistant/proposals"),
    ]).then(([bookingPayload, proposalPayload]) => {
      if (!active) return;
      setBookings(bookingPayload.events);
      setProposals(proposalPayload.proposals);
    }).catch(requestError => {
      if (active) setError(requestError.message);
    });
    return () => {
      active = false;
    };
  }, []);

  async function sendMessage(event) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || loading) return;

    const history = messages
      .filter(item => (
        (item.role === "user" && item.status !== "failed")
        || item.role === "model" && item.id !== "welcome" && item.history !== false
      ))
      .slice(-8)
      .map(item => ({ role: item.role, content: item.content }));
    const userMessage = {
      id: `${Date.now()}-user`,
      role: "user",
      content: message,
    };
    setMessages(current => [...current, userMessage]);
    setDraft("");
    setError("");
    setAvailabilityChecks([]);
    setLoading(true);

    try {
      const response = await apiFetch("/api/assistant/agent", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message, history }),
      });
      const payload = await response.json();
      if (!response.ok) {
        const detail = typeof payload.detail === "string"
          ? payload.detail
          : payload.detail?.message;
        throw new Error(detail || "The campus assistant could not respond.");
      }

      setMessages(current => [
        ...current,
        {
          id: `${Date.now()}-model`,
          role: "model",
          content: payload.answer,
          language: payload.language,
          sources: payload.sources,
        },
      ]);
      if (payload.bookings?.length) setBookings(payload.bookings);
      if (payload.availability?.length) {
        setAvailabilityChecks(payload.availability);
      }
      setProposals(current => {
        const byId = new Map(current.map(proposal => [proposal.proposal_id, proposal]));
        for (const proposal of payload.proposals || []) {
          byId.set(proposal.proposal_id, proposal);
        }
        return [...byId.values()];
      });
    } catch (requestError) {
      setMessages(current => current.map(item => (
        item.id === userMessage.id
          ? { ...item, status: "failed" }
          : item
      )));
      setDraft(message);
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function clearConversation() {
    setMessages([INITIAL_MESSAGE]);
    setDraft("");
    setError("");
  }

  async function respondToProposal(proposal, confirmed) {
    if (processingProposal) return;
    setProcessingProposal(proposal.proposal_id);
    setError("");
    try {
      const result = await requestJSON("/api/assistant/confirm", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          proposal_id: proposal.proposal_id,
          confirmed,
        }),
      });
      setProposals(current => current.filter(
        item => item.proposal_id !== proposal.proposal_id,
      ));
      if (result.event) {
        setBookings(current => [
          result.event,
          ...current.filter(item => item.id !== result.event.id),
        ]);
        if (result.action === "create") {
          setMessages(current => [...current, {
            id: `${Date.now()}-confirmation`,
            role: "model",
            history: false,
            content: `Your booking request was submitted for administrator approval. Reference: ${result.event.id}. Current status: ${result.event.status}.`,
          }]);
        } else if (result.action === "reschedule") {
          setMessages(current => [...current, {
            id: `${Date.now()}-confirmation`,
            role: "model",
            history: false,
            content: `Your booking was rescheduled. Its approval status remains ${result.event.status}. Reference: ${result.event.id}.`,
          }]);
        } else if (result.action === "cancel") {
          setMessages(current => [...current, {
            id: `${Date.now()}-confirmation`,
            role: "model",
            history: false,
            content: `Booking ${result.event.id} has been cancelled.`,
          }]);
        }
      } else if (!confirmed) {
        setMessages(current => [...current, {
          id: `${Date.now()}-dismissed`,
          role: "model",
          history: false,
          content: "No changes were made. The proposed action was dismissed.",
        }]);
      }
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setProcessingProposal("");
    }
  }

  function proposalActionLabel(action) {
    if (action === "create") return "Submit booking request";
    if (action === "reschedule") return "Confirm reschedule";
    return "Confirm cancellation";
  }

  return (
    <section className="campus-assistant-page">
      <div className="welcome assistant-welcome">
        <div>
          <span className="eyebrow">CIT NEXUS CONVERSATIONAL INTELLIGENCE</span>
          <h1>AI Campus Assistant</h1>
          <p>Ask campus questions in English or Hinglish. Answers use verified CIT Nexus data.</p>
        </div>
        <span className="assistant-language-badge">
          <Sparkles size={16} />
          English · Hinglish
        </span>
      </div>

      <div className="assistant-safety-note">
        <ShieldCheck size={17} />
        <span>
          Booking changes require your explicit confirmation. New venue
          requests remain pending until an administrator approves them.
        </span>
      </div>

      <div className="panel assistant-chat-panel">
        <div className="assistant-chat-heading">
          <div className="assistant-heading-icon"><Bot size={19} /></div>
          <div>
            <strong>Campus Assistant</strong>
            <span>Grounded in CIT Nexus data</span>
          </div>
          <button
            type="button"
            className="assistant-clear-button"
            onClick={clearConversation}
            disabled={loading}
            aria-label="Clear conversation"
            title="Clear conversation"
          >
            <RotateCcw size={15} />
            <span>New chat</span>
          </button>
        </div>

        <div className="assistant-messages" aria-live="polite">
          {messages.map(item => (
            <article
              className={`assistant-message ${item.role === "user" ? "from-user" : "from-model"}`}
              key={item.id}
            >
              <div className="assistant-message-avatar">
                {item.role === "user" ? <UserRound size={16} /> : <Bot size={16} />}
              </div>
              <div className="assistant-message-content">
                <div className="assistant-message-meta">
                  <strong>{item.role === "user" ? "You" : "CIT Nexus Assistant"}</strong>
                  {item.language && (
                    <span>{item.language === "hinglish" ? "Hinglish" : "English"}</span>
                  )}
                  {item.status === "failed" && <span className="assistant-failed-label">Not sent</span>}
                </div>
                <p>{item.content}</p>
                {item.sources && Object.keys(item.sources).length > 0 && (
                  <div className="assistant-sources">
                    <Database size={13} />
                    <span>Data checked:</span>
                    {Object.entries(item.sources).map(([source, status]) => (
                      <span
                        key={source}
                        className={`assistant-source-chip ${status === "available" ? "source-ok" : "source-unavailable"}`}
                      >
                        {source.replaceAll("_", " ")}
                        {status !== "available" ? " unavailable" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </article>
          ))}
          {proposals.map(proposal => (
            <section className="assistant-booking-card" key={proposal.proposal_id}>
              <div className="assistant-booking-card-heading">
                <strong>
                  {proposal.action === "create"
                    ? "Booking request"
                    : proposal.action === "reschedule"
                      ? "Reschedule request"
                      : "Cancellation request"}
                </strong>
                <span className="assistant-booking-status status-proposed">
                  Awaiting your confirmation
                </span>
              </div>
              <div className="assistant-booking-details">
                <span><MapPin size={14} />{proposal.summary.room_name || proposal.summary.room_id || "Venue"}</span>
                <span><CalendarDays size={14} />{proposal.summary.event_date || "Date not set"}</span>
                <span>{proposal.summary.start_time || "Time not set"}–{proposal.summary.end_time || "Time not set"}</span>
                {proposal.summary.expected_participants && (
                  <span><Users size={14} />{proposal.summary.expected_participants} people</span>
                )}
              </div>
              {proposal.action === "create" && (
                <p className="assistant-booking-note">
                  Confirming submits the request; it does not reserve the room.
                </p>
              )}
              <div className="assistant-booking-actions">
                <button
                  type="button"
                  className="assistant-confirm-button"
                  onClick={() => respondToProposal(proposal, true)}
                  disabled={Boolean(processingProposal)}
                >
                  {processingProposal === proposal.proposal_id
                    ? <Loader2 className="assistant-spinner" size={14} />
                    : <Check size={14} />}
                  {proposalActionLabel(proposal.action)}
                </button>
                <button
                  type="button"
                  className="assistant-dismiss-button"
                  onClick={() => respondToProposal(proposal, false)}
                  disabled={Boolean(processingProposal)}
                >
                  <X size={14} />
                  Dismiss
                </button>
              </div>
            </section>
          ))}
          {availabilityChecks.map((check, index) => (
            <section className="assistant-booking-card" key={`availability-${index}`}>
              <div className="assistant-booking-card-heading">
                <strong>Room availability</strong>
                <span className={`assistant-booking-status ${check.available ? "status-approved" : "status-rejected"}`}>
                  {check.available ? "Available" : "No matching slot"}
                </span>
              </div>
              {check.rooms?.map(room => (
                <div className="assistant-availability-row" key={room.room_id}>
                  <span><MapPin size={14} />{room.room_name}</span>
                  <span>{room.capacity} capacity</span>
                  <span className={`assistant-booking-status ${room.available ? "status-approved" : "status-rejected"}`}>
                    {room.available
                      ? "Available"
                      : room.conflicts.map(item => item.type.replaceAll("_", " ")).join(", ") || "Unavailable"}
                  </span>
                </div>
              ))}
              <p className="assistant-booking-note">
                Checked against the official weekday timetable, room capacity,
                facilities, maintenance and existing event requests.
              </p>
            </section>
          ))}
          {bookings.length > 0 && (
            <section className="assistant-booking-list">
              <div className="assistant-booking-card-heading">
                <strong>Your bookings</strong>
                <span>{bookings.length}</span>
              </div>
              {bookings.slice(0, 5).map(booking => (
                <div className="assistant-booking-list-item" key={booking.id}>
                  <span>{booking.event_name}</span>
                  <span>{booking.event_date} · {booking.start_time}–{booking.end_time}</span>
                  <span className={`assistant-booking-status status-${booking.status}`}>
                    {booking.status}
                  </span>
                </div>
              ))}
            </section>
          )}
          {loading && (
            <div className="assistant-loading" role="status">
              <Loader2 className="assistant-spinner" size={17} />
              Checking campus data and preparing an answer…
            </div>
          )}
          <div ref={endOfMessagesRef} />
        </div>

        {error && (
          <div className="assistant-error" role="alert">
            <AlertCircle size={17} />
            <span>{error}</span>
          </div>
        )}

        {messages.length === 1 && (
          <div className="assistant-quick-prompts">
            <span>Try asking</span>
            {QUICK_PROMPTS.map(prompt => (
              <button
                type="button"
                key={prompt}
                onClick={() => setDraft(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>
        )}

        <form className="assistant-composer" onSubmit={sendMessage}>
          <label className="visually-hidden" htmlFor="assistant-message">
            Ask the CIT Nexus Assistant
          </label>
          <textarea
            id="assistant-message"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your timetable, a room, analytics… / Hinglish mein poochhein…"
            maxLength={2000}
            rows={2}
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !draft.trim()}
            aria-label="Send message"
          >
            {loading ? <Loader2 className="assistant-spinner" size={18} /> : <Send size={17} />}
            <span>Send</span>
          </button>
          <span className="assistant-composer-hint">
            Enter to send · Shift+Enter for a new line
          </span>
        </form>
      </div>
    </section>
  );
}

export default CampusAssistant;
