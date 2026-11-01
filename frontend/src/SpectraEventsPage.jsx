import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  Check,
  Clock3,
  History,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  X,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import "./SpectraEventsPage.css";

const STATUSES = ["pending", "approved", "rejected", "cancelled"];
const CATEGORIES = [
  ["institute_event", "Institute event"],
  ["guest_lecture", "Guest lecture"],
  ["department_event", "Department event"],
  ["club_event", "Club event"],
];

function errorMessage(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  if (detail) return JSON.stringify(detail);
  return fallback;
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await apiFetch(path, {
    ...options,
    headers,
    body: options.body === undefined
      ? undefined
      : JSON.stringify(options.body),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(errorMessage(
      payload?.detail,
      `The request failed (HTTP ${response.status}).`,
    ));
  }
  return payload;
}

function getEndPeriods(periods, startTime) {
  const startIndex = periods.findIndex(period => period.start === startTime);
  if (startIndex < 0) return [];
  const result = [];
  for (let index = startIndex; index < periods.length; index += 1) {
    if (index > startIndex && periods[index - 1].end !== periods[index].start) {
      break;
    }
    result.push(periods[index]);
  }
  return result;
}

function describeConflict(conflict) {
  const labels = {
    teaching: "Scheduled teaching",
    approved_booking: "Approved venue booking",
    approved_event: "Another approved Spectra event",
    pending_event: "Overlapping pending event",
    maintenance: "Room maintenance",
    insufficient_capacity: "Room capacity is insufficient",
    room_not_allowed_for_events: "Room is not designated for events",
    unknown_room: "Room is not in the campus dataset",
    unavailable: "The requested room and period are unavailable",
  };
  const label = labels[conflict.type] || conflict.type.replaceAll("_", " ");
  const periods = conflict.periods?.length
    ? ` · Period${conflict.periods.length === 1 ? "" : "s"} ${conflict.periods.join(", ")}`
    : conflict.period
      ? ` · Period ${conflict.period}`
      : "";
  const reason = conflict.reason ? ` · ${conflict.reason}` : "";
  const event = conflict.event_name ? ` · ${conflict.event_name}` : "";
  return `${label}${periods}${event}${reason}`;
}

function SpectraEventsPage({ auth }) {
  const isAdmin = auth.status === "authenticated" &&
    auth.identity?.roles?.includes("ADMIN");
  const canRequest = auth.status === "authenticated" &&
    auth.identity?.roles?.some(role =>
      ["ADMIN", "FACULTY", "STUDENT"].includes(role),
    );
  const [rooms, setRooms] = useState([]);
  const [periods, setPeriods] = useState([]);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({
    event_name: "",
    category: "club_event",
    room_id: "",
    event_date: "",
    start_time: "",
    end_time: "",
    expected_participants: "",
  });
  const [preview, setPreview] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [suggestionError, setSuggestionError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const [createMessage, setCreateMessage] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [history, setHistory] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [action, setAction] = useState(null);
  const [actionReason, setActionReason] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    if (!canRequest) return undefined;
    let cancelled = false;
    async function loadPage() {
      setLoading(true);
      setError("");
      try {
        const [eventPayload, timetableResponse] = await Promise.all([
          isAdmin ? request("/api/spectra/events") : Promise.resolve({ events: [] }),
          apiFetch("/api/timetable"),
        ]);
        const timetablePayload = await timetableResponse.json();
        if (!timetableResponse.ok || timetablePayload.status !== "success") {
          throw new Error(errorMessage(
            timetablePayload.detail,
            "Campus room and period data could not be loaded.",
          ));
        }
        if (!cancelled) {
          setEvents(eventPayload.events || []);
          setRooms((timetablePayload.data.rooms || []).filter(
            room => room.allowed_uses?.includes("event"),
          ));
          setPeriods((timetablePayload.data.periods || [])
            .slice()
            .sort((left, right) => left.id - right.id));
        }
      } catch (loadError) {
        if (!cancelled) setError(loadError.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadPage();
    return () => {
      cancelled = true;
    };
  }, [canRequest, isAdmin, reload]);

  useEffect(() => {
    if (!selectedId || !isAdmin) return undefined;
    let cancelled = false;
    async function loadDetails() {
      setDetailLoading(true);
      try {
        const [detail, audit] = await Promise.all([
          request(`/api/spectra/events/${encodeURIComponent(selectedId)}`),
          request(`/api/spectra/events/${encodeURIComponent(selectedId)}/history`),
        ]);
        if (!cancelled) {
          setSelectedEvent(detail.event);
          setHistory(audit.history || []);
        }
      } catch (detailError) {
        if (!cancelled) setActionError(detailError.message);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    }
    loadDetails();
    return () => {
      cancelled = true;
    };
  }, [isAdmin, selectedId, reload]);

  const selectedRoom = rooms.find(room => room.id === form.room_id);
  const endPeriods = useMemo(
    () => getEndPeriods(periods, form.start_time),
    [periods, form.start_time],
  );
  const formPayload = useMemo(() => ({
    ...form,
    expected_participants: Number(form.expected_participants),
  }), [form]);
  const formKey = JSON.stringify(formPayload);
  const previewIsCurrent = preview?.key === formKey;
  const counts = useMemo(() => STATUSES.reduce((result, value) => {
    result[value] = events.filter(event => event.status === value).length;
    return result;
  }, {}), [events]);
  const visibleEvents = useMemo(() => {
    const query = search.trim().toLowerCase();
    return events.filter(event => {
      const statusMatches = statusFilter === "all" ||
        event.status === statusFilter;
      const room = rooms.find(item => item.id === event.room_id);
      const searchMatches = !query ||
        `${event.event_name} ${event.room_id} ${room?.name || ""} ${event.category}`
          .toLowerCase()
          .includes(query);
      return statusMatches && searchMatches;
    });
  }, [events, rooms, search, statusFilter]);

  function updateForm(field, value) {
    setForm(current => {
      const next = { ...current, [field]: value };
      if (field === "start_time") {
        const validEnds = getEndPeriods(periods, value);
        if (!validEnds.some(period => period.end === next.end_time)) {
          next.end_time = validEnds[0]?.end || "";
        }
      }
      return next;
    });
    setPreview(null);
    setSuggestions([]);
    setSuggestionError("");
    setCreateMessage("");
  }

  function formIsComplete() {
    return Boolean(
      form.event_name.trim() &&
      form.room_id &&
      form.event_date &&
      form.start_time &&
      form.end_time &&
      Number(form.expected_participants) > 0,
    );
  }

  async function checkAvailability(event) {
    event.preventDefault();
    setPreviewLoading(true);
    setPreview(null);
    setSuggestions([]);
    setSuggestionError("");
    setCreateMessage("");
    try {
      const result = await request("/api/spectra/events/availability", {
        method: "POST",
        body: formPayload,
      });
      setPreview({ key: formKey, result });
      if (!result.availability?.available) {
        const duration = result.event?.duration_periods;
        const starts = [];
        for (let index = 0; index + duration <= periods.length; index += 1) {
          const block = periods.slice(index, index + duration);
          if (block.length !== duration || block.some((period, offset) =>
            offset > 0 && block[offset - 1].end !== period.start,
          )) continue;
          if (block[0].start === form.start_time) continue;
          starts.push({
            ...formPayload,
            start_time: block[0].start,
            end_time: block.at(-1).end,
          });
        }
        if (starts.length) {
          try {
            const checks = await Promise.all(starts.map(async candidate => {
              const response = await apiFetch(
                "/api/spectra/events/availability",
                {
                  method: "POST",
                  headers: {
                    Accept: "application/json",
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify(candidate),
                },
              );
              const payload = await response.json();
              if (!response.ok) {
                throw new Error(errorMessage(payload.detail, "Alternative availability check failed."));
              }
              return payload;
            }));
            setSuggestions(checks
              .filter(item => item.availability?.available)
              .slice(0, 4)
              .map(item => item.event));
          } catch (suggestionLoadError) {
            setSuggestionError(suggestionLoadError.message);
          }
        }
      }
    } catch (previewError) {
      setError(previewError.message);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function createEvent(event) {
    event.preventDefault();
    if (!previewIsCurrent) return;
    setCreateLoading(true);
    setError("");
    setCreateMessage("");
    try {
      const result = await request("/api/spectra/events", {
        method: "POST",
        body: formPayload,
      });
      setCreateMessage(
        `${result.event.event_name} was submitted as pending. It has not been approved.`,
      );
      setForm({
        event_name: "",
        category: "club_event",
        room_id: "",
        event_date: "",
        start_time: "",
        end_time: "",
        expected_participants: "",
      });
      setPreview(null);
      setSuggestions([]);
      setReload(value => value + 1);
    } catch (createError) {
      setError(createError.message);
    } finally {
      setCreateLoading(false);
    }
  }

  async function submitDecision(event) {
    event.preventDefault();
    if (!action || (action.type !== "approve" && !actionReason.trim())) return;
    setActionLoading(true);
    setActionError("");
    try {
      await request(
        `/api/spectra/events/${encodeURIComponent(action.event.id)}/${action.type}`,
        {
          method: "POST",
          body: action.type === "approve" && !actionReason.trim()
            ? {}
            : { reason: actionReason.trim() },
        },
      );
      setAction(null);
      setActionReason("");
      setSelectedId(action.event.id);
      setReload(value => value + 1);
    } catch (decisionError) {
      setActionError(decisionError.message);
    } finally {
      setActionLoading(false);
    }
  }

  if (!canRequest) {
    return (
      <section className="spectra-page">
        <div className="spectra-access-state panel" role="alert">
          <ShieldAlert size={28} />
          <div>
            <h1>Event request access required</h1>
            <p>Your verified account needs an ADMIN, FACULTY, or STUDENT realm role to submit an event request.</p>
            {auth.error && <p className="spectra-error-text">{auth.error}</p>}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="spectra-page">
      <header className="welcome spectra-heading">
        <div>
          <span className="eyebrow">
            {isAdmin ? "EVENTS · REVIEW · AUDIT" : "CAMPUS EVENT REQUEST"}
          </span>
          <h1>Spectra Events</h1>
          <p>
            {isAdmin
              ? "Submit campus events, check real room availability, and review decisions."
              : "Submit a campus event request for administrator review."}
          </p>
        </div>
        {isAdmin && <button
          type="button"
          className="spectra-secondary"
          onClick={() => setReload(value => value + 1)}
          disabled={loading}
        >
          {loading ? <Loader2 className="spectra-spin" size={16} /> : <RefreshCw size={16} />}
          Refresh
        </button>}
      </header>

      {error && (
        <div className="spectra-alert error" role="alert">
          <AlertCircle size={18} />
          <span>{error}</span>
          <button type="button" onClick={() => setError("")} aria-label="Dismiss error"><X size={16} /></button>
        </div>
      )}
      {createMessage && (
        <div className="spectra-alert success" role="status">
          <Check size={18} /><span>{createMessage}</span>
        </div>
      )}

      {isAdmin && (
        <div className="spectra-stats" aria-label="Event counts">
          {STATUSES.map(status => (
            <article className={`spectra-stat ${status}`} key={status}>
              <span>{status}</span>
              <strong>{counts[status] || 0}</strong>
            </article>
          ))}
        </div>
      )}

      <div className="spectra-layout">
        <section className="panel spectra-form-panel">
          <div className="spectra-section-heading">
            <div className="spectra-icon"><Plus size={18} /></div>
            <div>
              <h2>Create event request</h2>
              <p>Review availability before submitting. Submission creates a pending request only.</p>
            </div>
          </div>
          {rooms.length === 0 && !loading && (
            <div className="spectra-inline-warning" role="status">
              No event venues are available from the campus timetable data.
            </div>
          )}
          <form onSubmit={checkAvailability} className="spectra-form">
            <label className="spectra-field full">
              Event name
              <input
                value={form.event_name}
                onChange={event => updateForm("event_name", event.target.value)}
                maxLength={160}
                required
                placeholder="e.g. Annual student showcase"
              />
            </label>
            <label className="spectra-field">
              Category
              <select
                value={form.category}
                onChange={event => updateForm("category", event.target.value)}
                required
              >
                {CATEGORIES.map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
            </label>
            <label className="spectra-field">
              Expected participants
              <input
                type="number"
                min="1"
                max="100000"
                step="1"
                value={form.expected_participants}
                onChange={event => updateForm("expected_participants", event.target.value)}
                required
                placeholder="e.g. 120"
              />
            </label>
            <label className="spectra-field full">
              Venue
              <select
                value={form.room_id}
                onChange={event => updateForm("room_id", event.target.value)}
                required
              >
                <option value="">Select an event venue</option>
                {rooms.map(room => (
                  <option value={room.id} key={room.id}>
                    {room.name} · {room.id} · Capacity {room.capacity}
                  </option>
                ))}
              </select>
              {selectedRoom && (
                <small>{selectedRoom.type.replaceAll("_", " ")} · Capacity {selectedRoom.capacity}</small>
              )}
            </label>
            <label className="spectra-field">
              Date
              <input
                type="date"
                value={form.event_date}
                onChange={event => updateForm("event_date", event.target.value)}
                required
              />
              <small>Weekday dates only; the campus uses a weekly schedule template.</small>
            </label>
            <label className="spectra-field">
              Starts at
              <select
                value={form.start_time}
                onChange={event => updateForm("start_time", event.target.value)}
                required
              >
                <option value="">Select a supported time</option>
                {periods.map(period => (
                  <option value={period.start} key={period.id}>
                    {period.start} · Period {period.id}
                  </option>
                ))}
              </select>
            </label>
            <label className="spectra-field">
              Ends at
              <select
                value={form.end_time}
                onChange={event => updateForm("end_time", event.target.value)}
                required
                disabled={!form.start_time}
              >
                <option value="">Select an end time</option>
                {endPeriods.map(period => (
                  <option value={period.end} key={period.id}>{period.end}</option>
                ))}
              </select>
              <small>Only contiguous schedule periods are offered.</small>
            </label>

            {previewIsCurrent && (
              <div className={`spectra-preview full ${preview.result.availability?.available ? "available" : "conflict"}`}>
                <div className="spectra-preview-title">
                  {preview.result.availability?.available
                    ? <Check size={18} />
                    : <AlertCircle size={18} />}
                  <strong>
                    {preview.result.availability?.available
                      ? "No conflicts found"
                      : "Availability conflicts found"}
                  </strong>
                  <span>{preview.result.event.duration_periods} period(s)</span>
                </div>
                {preview.result.availability?.conflicts?.length > 0 && (
                  <ul className="spectra-conflict-list">
                    {preview.result.availability.conflicts.map((item, index) => (
                      <li key={`${item.type}-${item.event_id || item.period || index}`}>
                        {describeConflict(item)}
                      </li>
                    ))}
                  </ul>
                )}
                {!preview.result.availability?.available && (
                  <p className="spectra-pending-note">
                    You may still submit this request as pending for administrator review. It cannot be approved while conflicts remain.
                  </p>
                )}
                {suggestions.length > 0 && (
                  <div className="spectra-suggestions">
                    <strong>Available alternative times for this venue and date</strong>
                    <div>
                      {suggestions.map((item, index) => (
                        <button
                          type="button"
                          key={`${item.start_time}-${index}`}
                          onClick={() => {
                            setForm(current => ({
                              ...current,
                              start_time: item.start_time,
                              end_time: item.end_time,
                            }));
                            setPreview(null);
                            setSuggestions([]);
                          }}
                        >
                          {item.start_time}–{item.end_time}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {suggestionError && (
                  <p className="spectra-muted">Alternative checks unavailable: {suggestionError}</p>
                )}
              </div>
            )}

            <div className="spectra-form-actions full">
              <button
                type="submit"
                className="spectra-secondary"
                disabled={!formIsComplete() || previewLoading || loading}
              >
                {previewLoading ? <Loader2 className="spectra-spin" size={16} /> : <Search size={16} />}
                Check availability
              </button>
              <button
                type="button"
                className="spectra-primary"
                disabled={!previewIsCurrent || createLoading}
                onClick={createEvent}
              >
                {createLoading ? <Loader2 className="spectra-spin" size={16} /> : <Plus size={16} />}
                Submit as pending
              </button>
            </div>
          </form>
        </section>

        {isAdmin && <section className="panel spectra-events-panel">
          <div className="spectra-section-heading">
            <div className="spectra-icon"><CalendarDays size={18} /></div>
            <div>
              <h2>Event requests</h2>
              <p>Review status, make decisions, and inspect the audit history.</p>
            </div>
          </div>
          <div className="spectra-list-controls">
            <label className="spectra-search">
              <Search size={15} />
              <input
                type="search"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder="Search events or venues"
                aria-label="Search events or venues"
              />
            </label>
            <select
              value={statusFilter}
              onChange={event => setStatusFilter(event.target.value)}
              aria-label="Filter by status"
            >
              <option value="all">All statuses</option>
              {STATUSES.map(status => (
                <option value={status} key={status}>{status}</option>
              ))}
            </select>
          </div>
          {loading ? (
            <div className="spectra-state" role="status">
              <Loader2 className="spectra-spin" size={20} /> Loading events and campus venues…
            </div>
          ) : visibleEvents.length === 0 ? (
            <div className="spectra-empty">
              <CalendarDays size={25} />
              <strong>{events.length ? "No events match these filters" : "No event requests yet"}</strong>
              <span>{events.length ? "Change the status or search filter." : "Create a request to begin the approval history."}</span>
            </div>
          ) : (
            <div className="spectra-event-list">
              {visibleEvents.map(event => {
                const room = rooms.find(item => item.id === event.room_id);
                return (
                  <article
                    className={`spectra-event-card ${selectedId === event.id ? "selected" : ""}`}
                    key={event.id}
                  >
                    <button
                      type="button"
                      className="spectra-event-select"
                      onClick={() => {
                        setSelectedId(current => current === event.id ? "" : event.id);
                        setActionError("");
                      }}
                    >
                      <span className={`spectra-status ${event.status}`}>{event.status}</span>
                      <strong>{event.event_name}</strong>
                      <span>{room?.name || event.room_id} · {event.event_date}</span>
                      <small><Clock3 size={13} /> {event.start_time}–{event.end_time} · {event.expected_participants} participants</small>
                    </button>
                    {event.status === "pending" && (
                      <div className="spectra-card-actions">
                        <button
                          type="button"
                          className="spectra-approve"
                          onClick={() => {
                            setAction({ type: "approve", event });
                            setActionReason("");
                            setActionError("");
                          }}
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          className="spectra-reject"
                          onClick={() => {
                            setAction({ type: "reject", event });
                            setActionReason("");
                            setActionError("");
                          }}
                        >
                          Reject
                        </button>
                      </div>
                    )}
                    {event.status === "approved" && (
                      <button
                        type="button"
                        className="spectra-reject"
                        onClick={() => {
                          setAction({ type: "cancel", event });
                          setActionReason("");
                          setActionError("");
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>}
      </div>

      {isAdmin && selectedId && (
        <section className="panel spectra-detail-panel">
          <div className="spectra-section-heading">
            <div className="spectra-icon"><History size={18} /></div>
            <div>
              <h2>Event details and history</h2>
              <p>{selectedEvent?.event_name || "Loading selected event…"}</p>
            </div>
            <button
              type="button"
              className="spectra-close-detail"
              onClick={() => setSelectedId("")}
              aria-label="Close event details"
            >
              <X size={18} />
            </button>
          </div>
          {detailLoading ? (
            <div className="spectra-state"><Loader2 className="spectra-spin" size={18} /> Loading event history…</div>
          ) : selectedEvent ? (
            <div className="spectra-detail-content">
              <dl className="spectra-detail-grid">
                <div><dt>Status</dt><dd><span className={`spectra-status ${selectedEvent.status}`}>{selectedEvent.status}</span></dd></div>
                <div><dt>Category</dt><dd>{selectedEvent.category.replaceAll("_", " ")}</dd></div>
                <div><dt>Venue</dt><dd>{selectedEvent.room_id}</dd></div>
                <div><dt>Date and time</dt><dd>{selectedEvent.event_date} · {selectedEvent.start_time}–{selectedEvent.end_time}</dd></div>
                <div><dt>Participants</dt><dd>{selectedEvent.expected_participants}</dd></div>
                <div><dt>Created by</dt><dd>{selectedEvent.created_by}</dd></div>
              </dl>
              <div className="spectra-history">
                <h3>Decision history</h3>
                {history.length === 0 ? (
                  <p className="spectra-muted">No history entries are available.</p>
                ) : history.map((entry, index) => (
                  <article className="spectra-history-entry" key={`${entry.timestamp}-${index}`}>
                    <span className={`spectra-status ${entry.action === "created" ? "pending" : entry.action}`}>
                      {entry.action}
                    </span>
                    <div>
                      <strong>{entry.reason}</strong>
                      <small>{entry.actor} · {new Date(entry.timestamp).toLocaleString()}</small>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ) : (
            <div className="spectra-alert error" role="alert">{actionError || "Event details could not be loaded."}</div>
          )}
        </section>
      )}

      {action && (
        <div className="spectra-modal-backdrop" role="presentation">
          <section
            className="spectra-modal panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="spectra-decision-title"
          >
            <button
              type="button"
              className="spectra-modal-close"
              onClick={() => setAction(null)}
              aria-label="Close decision dialog"
              disabled={actionLoading}
            >
              <X size={18} />
            </button>
            <h2 id="spectra-decision-title">
              {action.type === "approve" ? "Approve event" : action.type === "reject" ? "Reject event" : "Cancel approved event"}
            </h2>
            <p>{action.event.event_name} · {action.event.event_date} · {action.event.start_time}–{action.event.end_time}</p>
            {action.type === "approve" && (
              <div className="spectra-inline-warning">
                The backend will recheck live availability and priority before approval.
              </div>
            )}
            <form onSubmit={submitDecision}>
              <label className="spectra-field full">
                {action.type === "approve" ? "Decision note (optional)" : "Reason (required)"}
                <textarea
                  value={actionReason}
                  onChange={event => setActionReason(event.target.value)}
                  maxLength={1000}
                  required={action.type !== "approve"}
                  rows={3}
                />
              </label>
              {actionError && <p className="spectra-error-text" role="alert">{actionError}</p>}
              <div className="spectra-form-actions">
                <button
                  type="button"
                  className="spectra-secondary"
                  onClick={() => setAction(null)}
                  disabled={actionLoading}
                >
                  Back
                </button>
                <button
                  type="submit"
                  className={action.type === "approve" ? "spectra-primary" : "spectra-danger"}
                  disabled={actionLoading || (action.type !== "approve" && !actionReason.trim())}
                >
                  {actionLoading && <Loader2 className="spectra-spin" size={16} />}
                  Confirm {action.type}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}

export default SpectraEventsPage;
