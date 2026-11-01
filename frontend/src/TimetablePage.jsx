import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Search,
  Users,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import {
  buildBookingMap,
  buildOccupancyMap,
  buildUnavailabilityMap,
  countFreePeriods,
  getApprovedBookings,
  getDailyWorkload,
  getEntitySessions,
} from "./timetableUtils.js";
import "./TimetablePage.css";

const DAY_NAMES = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
};

function getErrorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  return payload?.detail?.message || payload?.message || fallback;
}

function TimetablePage({ searchRequest }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState(searchRequest?.kind || "group");
  const [selectedId, setSelectedId] = useState(searchRequest?.id || "");
  const [search, setSearch] = useState(searchRequest?.id || "");
  const [filter, setFilter] = useState("ALL");
  const [scheduleMode, setScheduleMode] = useState("official");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function loadTimetable() {
      setLoading(true);
      setError("");
      try {
        const response = await apiFetch("/api/timetable");
        const payload = await response.json();
        if (!response.ok || payload.status !== "success") {
          throw new Error(
            getErrorMessage(payload, "Could not load the timetable."),
          );
        }
        if (!cancelled) {
          setData(payload.data);
          setSelectedId(
            searchRequest?.id || payload.data.student_groups[0]?.id || "",
          );
        }
      } catch (loadError) {
        if (!cancelled) setError(loadError.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadTimetable();
    return () => {
      cancelled = true;
    };
  }, [reload, searchRequest?.id]);

  const entities = useMemo(() => {
    if (!data) return [];
    if (tab === "group") return data.student_groups;
    if (tab === "faculty") return data.faculty;
    return data.rooms;
  }, [data, tab]);

  const filteredEntities = useMemo(() => {
    const query = search.trim().toLowerCase();
    return entities.filter(entity => {
      const searchable = `${entity.id} ${entity.name || ""} ${entity.department || ""} ${entity.building || ""} ${entity.type || ""}`.toLowerCase();
      const matchesSearch = !query || searchable.includes(query);
      const matchesFilter = filter === "ALL" ||
        (tab === "group" && String(entity.year) === filter) ||
        (tab === "faculty" && entity.department === filter) ||
        (tab === "room" && entity.type === filter);
      return matchesSearch && matchesFilter;
    });
  }, [entities, filter, search, tab]);

  const selectedEntity = filteredEntities.find(entity => entity.id === selectedId)
    || filteredEntities[0];
  const selectedEntityId = selectedEntity?.id || "";
  const currentSessions = scheduleMode === "crisis" && data?.revised_schedule.available
    ? data.revised_schedule.sessions
    : data?.baseline_sessions || [];
  const visiblePeriods = tab === "room"
    ? data?.periods || []
    : (data?.periods || []).filter(period => period.teaching);
  const teachingPeriodIds = (data?.periods || [])
    .filter(period => period.teaching)
    .map(period => period.id);
  const selectedSessions = selectedEntityId
    ? getEntitySessions(currentSessions, tab, selectedEntityId)
    : [];
  const removedSessions = scheduleMode === "crisis" && data?.revised_schedule.available
    ? getEntitySessions(data.revised_schedule.removed_sessions || [], tab, selectedEntityId)
    : [];
  const sessionOccupancy = buildOccupancyMap(selectedSessions);
  const bookings = tab === "room" && selectedEntityId && data
    ? getApprovedBookings(
      scheduleMode === "crisis" && data.revised_schedule.available
        ? data.revised_schedule.approved_bookings
        : data.approved_bookings,
    ).filter(booking => booking.room_id === selectedEntityId)
    : [];
  const bookingOccupancy = buildBookingMap(bookings);

  const unavailableByDay = new Map(
    (data?.days || []).map(day => {
      const source = tab === "faculty"
        ? selectedEntity?.unavailable
        : tab === "room"
          ? (data?.room_unavailability || []).filter(
            item => item.room_id === selectedEntityId,
          )
          : [];
      const dayMap = buildUnavailabilityMap(source, day);
      return [day, new Set(dayMap.keys())];
    }),
  );
  const totalFreePeriods = data && tab !== "room"
    ? countFreePeriods(
      data.days,
      teachingPeriodIds,
      sessionOccupancy,
      tab === "faculty" ? unavailableByDay : new Map(),
    )
    : null;
  const dailyWorkload = tab === "faculty" && data
    ? getDailyWorkload(selectedSessions, data.days)
    : null;
  const totalTeachingHours = dailyWorkload
    ? Object.values(dailyWorkload).reduce((total, hours) => total + hours, 0)
    : 0;
  const conflictCount = selectedSessions.filter(
    session => session.conflicts.length > 0,
  ).length;
  const changedCount = selectedSessions.filter(session => session.change).length;

  function chooseTab(nextTab) {
    setTab(nextTab);
    setSearch("");
    setFilter("ALL");
    setSelectedId("");
  }

  function renderSession(session, periodId) {
    const startsHere = session.start_period === periodId;
    const conflictsHere = session.conflicts.some(
      conflict => conflict.period === periodId,
    );
    return (
      <article
        className={`timetable-entry ${conflictsHere ? "has-conflict" : ""} ${session.change ? "has-change" : ""}`}
        key={session.session_id}
      >
        {startsHere ? (
          <>
            <strong>{session.course_id}</strong>
            <span>{session.course_title}</span>
            {tab !== "faculty" && <small>{session.faculty_name}</small>}
            {tab !== "room" && (
              <small>{session.room_name} · {session.room_id}</small>
            )}
            {session.change && (
              <span className="timetable-change-badge">
                {session.change.type === "ADDED"
                  ? "Added by crisis"
                  : `Changed · ${session.change.type.replaceAll("_", " ")}`}
              </span>
            )}
            {conflictsHere && (
              <span className="timetable-conflict-badge">
                Conflict
              </span>
            )}
          </>
        ) : (
          <>
            <span className="timetable-continuation">
              Continues · {session.course_id}
            </span>
            {conflictsHere && <span className="timetable-conflict-badge">Conflict</span>}
          </>
        )}
      </article>
    );
  }

  function renderBooking(booking, periodId) {
    const startsHere = booking.start_period === periodId;
    const conflictsHere = booking.conflicts.some(conflict =>
      conflict.period === periodId ||
      conflict.periods?.some(item =>
        typeof item === "number" ? item === periodId : item.period === periodId,
      ),
    );
    return (
      <article
        className={`timetable-entry event-entry ${conflictsHere ? "has-conflict" : ""}`}
        key={booking.request_id}
      >
        {startsHere ? (
          <>
            <strong>{booking.title}</strong>
            <span>Approved event booking</span>
            <small>{booking.requester}</small>
            {conflictsHere && <span className="timetable-conflict-badge">Conflict</span>}
          </>
        ) : (
          <>
            <span className="timetable-continuation">Event booking continues</span>
            {conflictsHere && <span className="timetable-conflict-badge">Conflict</span>}
          </>
        )}
      </article>
    );
  }

  return (
    <section className="timetable-page">
      <div className="welcome timetable-page-heading">
        <div>
          <span className="eyebrow">WEEKLY CAMPUS SCHEDULE</span>
          <h1>Timetable</h1>
          <p>Explore scheduled classes, faculty workload and room availability.</p>
        </div>
        <label className="timetable-version">
          Schedule
          <select
            value={scheduleMode}
            onChange={event => setScheduleMode(event.target.value)}
          >
            <option value="official">Official baseline</option>
            <option
              value="crisis"
              disabled={!data?.revised_schedule.available}
            >
              Crisis revised
            </option>
          </select>
        </label>
      </div>

      {loading && <div className="panel timetable-state">Loading the official schedule…</div>}
      {!loading && error && (
        <div className="timetable-alert error" role="alert">
          <AlertCircle size={19} />
          <div>
            <strong>Timetable unavailable</strong>
            <p>{error}</p>
            <button type="button" onClick={() => setReload(value => value + 1)}>
              Try again
            </button>
          </div>
        </div>
      )}
      {removedSessions.length > 0 && (
        <div className="panel timetable-removed-sessions">
          <h3>Sessions removed by the crisis revision</h3>
          {removedSessions.map(session => (
            <p key={session.session_id}>
              <strong>{session.course_id} · {session.course_title}</strong>
              <span>
                {session.session_id} · {session.day} period {session.start_period} · {session.room_id}
              </span>
            </p>
          ))}
        </div>
      )}

      {!loading && data && (
        <>
          {!data.revised_schedule.available && (
            <div className="timetable-alert warning" role="status">
              <AlertCircle size={19} />
              <div>
                <strong>Crisis revision not displayed</strong>
                <p>{data.revised_schedule.reason}</p>
              </div>
            </div>
          )}
          {scheduleMode === "crisis" && data.revised_schedule.available && (
            <div className="timetable-alert success" role="status">
              <CheckCircle2 size={19} />
              <div>
                <strong>Showing the compatible crisis revision</strong>
                <p>
                  {data.revised_schedule.added_count} added ·{" "}
                  {data.revised_schedule.changed_count} rescheduled ·{" "}
                  {data.revised_schedule.removed_sessions.length} removed.
                  {" "}Changes are marked against the official baseline.
                </p>
              </div>
            </div>
          )}

          <div className="timetable-tabs" role="tablist" aria-label="Timetable view">
            {[
              ["group", "Student Group"],
              ["faculty", "Faculty"],
              ["room", "Room"],
            ].map(([value, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={tab === value}
                className={tab === value ? "selected" : ""}
                key={value}
                onClick={() => chooseTab(value)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="panel timetable-controls">
            <label className="timetable-search">
              <Search size={16} aria-hidden="true" />
              <span className="visually-hidden">Search {tab} options</span>
              <input
                type="search"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder={`Search ${tab === "group" ? "groups" : tab === "faculty" ? "faculty" : "rooms"}`}
              />
            </label>
            <label>
              {tab === "group" ? "Year" : tab === "faculty" ? "Department" : "Room type"}
              <select value={filter} onChange={event => setFilter(event.target.value)}>
                <option value="ALL">All</option>
                {tab === "group" && [...new Set(entities.map(entity => entity.year))]
                  .sort((a, b) => a - b)
                  .map(year => <option key={year} value={String(year)}>Year {year}</option>)}
                {tab === "faculty" && [...new Set(entities.map(entity => entity.department))]
                  .sort()
                  .map(department => <option key={department} value={department}>{department}</option>)}
                {tab === "room" && [...new Set(entities.map(entity => entity.type))]
                  .sort()
                  .map(type => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}
              </select>
            </label>
            <label>
              Select {tab === "group" ? "group" : tab}
              <select value={selectedEntityId} onChange={event => setSelectedId(event.target.value)}>
                {filteredEntities.map(entity => (
                  <option value={entity.id} key={entity.id}>
                    {entity.id} · {entity.name || entity.id}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {!selectedEntity ? (
            <div className="panel timetable-state">No matching records.</div>
          ) : (
            <>
              <div className="timetable-summary">
                <div className="timetable-summary-card">
                  <span>{tab === "room" ? "Room capacity" : "Scheduled sessions"}</span>
                  <strong>{tab === "room" ? selectedEntity.capacity : selectedSessions.length}</strong>
                </div>
                {tab === "group" && (
                  <div className="timetable-summary-card">
                    <span>Free teaching periods</span>
                    <strong>{totalFreePeriods}</strong>
                  </div>
                )}
                {tab === "faculty" && (
                  <>
                    <div className="timetable-summary-card">
                      <span>Scheduled teaching hours</span>
                      <strong>{totalTeachingHours} hrs</strong>
                    </div>
                    <div className="timetable-summary-card">
                      <span>Free teaching periods</span>
                      <strong>{totalFreePeriods}</strong>
                    </div>
                  </>
                )}
                {tab === "room" && (
                  <>
                    <div className="timetable-summary-card">
                      <span>Room type</span>
                      <strong>{selectedEntity.type.replaceAll("_", " ")}</strong>
                    </div>
                    <div className="timetable-summary-card">
                      <span>Approved event bookings</span>
                      <strong>{bookings.length}</strong>
                    </div>
                  </>
                )}
                <div className={`timetable-summary-card ${conflictCount ? "attention" : ""}`}>
                  <span>Schedule conflicts</span>
                  <strong>{conflictCount}</strong>
                </div>
                {scheduleMode === "crisis" && (
                  <div className="timetable-summary-card">
                    <span>Changed or added sessions</span>
                    <strong>{changedCount}</strong>
                  </div>
                )}
              </div>

              {tab === "faculty" && (
                <div className="faculty-workload-strip">
                  <Clock3 size={17} />
                  <span>Daily workload</span>
                  {data.days.map(day => (
                    <strong key={day}>{DAY_NAMES[day]}: {dailyWorkload[day]}h</strong>
                  ))}
                </div>
              )}

              {tab === "room" && (
                <div className="room-details-strip">
                  <span>{selectedEntity.building}</span>
                  <span>Capacity {selectedEntity.capacity}</span>
                  <span>{selectedEntity.allowed_uses.join(" · ")}</span>
                  <span>
                    {data.booking_status_counts.approved || 0} approved bookings campus-wide
                  </span>
                </div>
              )}

              <div className="panel timetable-calendar-panel">
                <div className="timetable-calendar-heading">
                  <div>
                    <h2>{selectedEntity.name || selectedEntity.id}</h2>
                    <p>
                      {tab === "group" && `${selectedEntity.id} · ${selectedEntity.size} students`}
                      {tab === "faculty" && `${selectedEntity.id} · ${selectedEntity.department}`}
                      {tab === "room" && `${selectedEntity.id} · ${selectedEntity.building}`}
                    </p>
                  </div>
                  <div className="timetable-legend">
                    {tab === "room" ? (
                      <>
                        <span><i className="legend-class" />Teaching</span>
                        <span><i className="legend-event" />Approved event</span>
                        <span><i className="legend-maintenance" />Maintenance</span>
                        <span><i className="legend-free" />Vacant</span>
                      </>
                    ) : (
                      <>
                        <span><i className="legend-class" />Teaching</span>
                        <span><i className="legend-free" />Free period</span>
                        {tab === "faculty" && <span><i className="legend-maintenance" />Unavailable</span>}
                      </>
                    )}
                  </div>
                </div>

                <div className="timetable-scroll">
                  <table className="weekly-timetable">
                    <thead>
                      <tr>
                        <th scope="col">Period</th>
                        {data.days.map(day => (
                          <th scope="col" key={day}>{DAY_NAMES[day]}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visiblePeriods.map(period => (
                        <tr key={period.id}>
                          <th scope="row">
                            <strong>P{period.id}</strong>
                            <small>{period.start}–{period.end}</small>
                          </th>
                          {data.days.map(day => {
                            const key = `${day}:${period.id}`;
                            const sessions = sessionOccupancy.get(key) || [];
                            const dayUnavailable = unavailableByDay.get(day);
                            const unavailable = dayUnavailable?.has(period.id);
                            const reasons = tab === "faculty"
                              ? (selectedEntity.unavailable || [])
                                .filter(item => item.day === day && item.periods.includes(period.id))
                                .map(item => item.reason)
                              : (data.room_unavailability || [])
                                .filter(item => item.room_id === selectedEntityId && item.day === day && item.periods.includes(period.id))
                                .map(item => item.reason);
                            const cellBookings = bookingOccupancy.get(key) || [];
                            const maintenance = tab === "room" && unavailable;
                            const classConflict = sessions.some(session =>
                              session.conflicts.some(conflict =>
                                conflict.day === day && conflict.period === period.id,
                              ),
                            );
                            const bookingConflict = cellBookings.some(booking =>
                              booking.conflicts.some(conflict =>
                                conflict.period === period.id ||
                                conflict.periods?.some(item =>
                                  typeof item === "number"
                                    ? item === period.id
                                    : item.period === period.id,
                                ),
                              ),
                            );
                            const hasConflict = classConflict || bookingConflict ||
                              (sessions.length > 0 && maintenance) ||
                              (sessions.length > 0 && cellBookings.length > 0);
                            const statusClass = hasConflict
                              ? "conflict"
                              : maintenance
                                ? "maintenance"
                                : unavailable
                                  ? "unavailable"
                                  : sessions.length
                                    ? "occupied"
                                    : cellBookings.length
                                      ? "event"
                                      : "free";
                            return (
                              <td
                                key={day}
                                className={`weekly-slot ${statusClass}`}
                                aria-label={`${DAY_NAMES[day]} period ${period.id}`}
                              >
                                {sessions.map(session => renderSession(session, period.id))}
                                {cellBookings.map(booking => renderBooking(booking, period.id))}
                                {maintenance && (sessions.length > 0 || cellBookings.length > 0) && (
                                  <span className="timetable-conflict-badge">Maintenance conflict</span>
                                )}
                                {sessions.length === 0 && cellBookings.length === 0 && (
                                  <span className="empty-slot">
                                    {maintenance
                                      ? `Maintenance${reasons[0] ? ` · ${reasons[0]}` : ""}`
                                      : unavailable
                                        ? `Unavailable${reasons[0] ? ` · ${reasons[0]}` : ""}`
                                        : tab === "room" && !period.teaching
                                          ? "Available"
                                          : tab === "room"
                                            ? "Vacant"
                                            : "Free period"}
                                  </span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {tab === "room" && (
                  <p className="room-calendar-note">
                    Only approved bookings appear as reservations. Alternative offers and rejected requests are not confirmed.
                  </p>
                )}
              </div>

              {tab === "faculty" && selectedEntity.unavailable?.length > 0 && (
                <div className="panel faculty-unavailability">
                  <h3><Users size={17} /> Faculty unavailability</h3>
                  {selectedEntity.unavailable.map((item, index) => (
                    <p key={`${item.day}-${index}`}>
                      <strong>{DAY_NAMES[item.day]} · periods {item.periods.join(", ")}</strong>
                      {item.reason}
                    </p>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

export default TimetablePage;
