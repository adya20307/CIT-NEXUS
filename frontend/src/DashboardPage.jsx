import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Building2,
  CalendarDays,
  CheckCircle2,
  Clock3,
  GraduationCap,
  MapPin,
  Users,
  Zap,
} from "lucide-react";
import "./DashboardPage.css";

const DAY_CODES = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const DAY_LABELS = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
  SAT: "Saturday",
  SUN: "Sunday",
};

function getTimeLabel(date) {
  const hour = date.getHours();
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function getDaySessions(timetable, day) {
  return (timetable?.baseline_sessions || [])
    .filter(session => session.day === day)
    .sort((left, right) => (
      left.start_period - right.start_period ||
      left.course_id.localeCompare(right.course_id)
    ));
}

function getCurrentPeriod(timetable, date) {
  const minutes = date.getHours() * 60 + date.getMinutes();
  return timetable?.periods?.find(period => {
    const [startHour, startMinute] = period.start.split(":").map(Number);
    const [endHour, endMinute] = period.end.split(":").map(Number);
    const start = startHour * 60 + startMinute;
    const end = endHour * 60 + endMinute;
    return period.teaching && minutes >= start && minutes < end;
  });
}

function buildMetrics(timetable) {
  const sessions = timetable?.baseline_sessions || [];
  const rooms = timetable?.rooms || [];
  const days = timetable?.days || [];
  const teachingPeriods = (timetable?.periods || [])
    .filter(period => period.teaching);
  const closed = new Set();
  const occupied = new Set();
  const facultyOccupied = new Set();

  for (const item of timetable?.room_unavailability || []) {
    for (const period of item.periods || []) {
      closed.add(`${item.room_id}:${item.day}:${period}`);
    }
  }
  for (const session of sessions) {
    for (let period = session.start_period; period <= session.end_period; period += 1) {
      facultyOccupied.add(`${session.faculty_id}:${session.day}:${period}`);
    }
    for (let period = session.start_period; period <= session.end_period; period += 1) {
      occupied.add(`${session.room_id}:${session.day}:${period}`);
    }
  }
  for (const booking of timetable?.approved_bookings || []) {
    if (booking.status !== "approved") continue;
    for (let period = booking.start_period; period <= booking.end_period; period += 1) {
      occupied.add(`${booking.room_id}:${booking.day}:${period}`);
    }
  }

  let possibleRoomPeriods = 0;
  let occupiedRoomPeriods = 0;
  for (const room of rooms) {
    for (const day of days) {
      for (const period of teachingPeriods) {
        const key = `${room.id}:${day}:${period.id}`;
        if (closed.has(key)) continue;
        possibleRoomPeriods += 1;
        if (occupied.has(key)) occupiedRoomPeriods += 1;
      }
    }
  }

  const conflictCount = sessions.reduce(
    (total, session) => total + (session.conflicts?.length || 0),
    0,
  );
  const weeklyUtilization = possibleRoomPeriods
    ? Math.round((occupiedRoomPeriods / possibleRoomPeriods) * 100)
    : null;

  return {
    sessions,
    rooms,
    faculty: timetable?.faculty || [],
    groups: timetable?.student_groups || [],
    conflictCount,
    possibleRoomPeriods,
    occupiedRoomPeriods,
    weeklyUtilization,
    occupied,
    closed,
    facultyOccupied,
    teachingPeriods,
  };
}

function MetricCard({ icon: Icon, label, value, note, tone }) {
  return (
    <article className={`dashboard-metric ${tone}`}>
      <span className="dashboard-metric-icon"><Icon size={18} /></span>
      <span className="dashboard-metric-label">{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}

function ScheduleList({ sessions, periods, emptyMessage }) {
  if (sessions.length === 0) {
    return <p className="dashboard-empty">{emptyMessage}</p>;
  }

  return (
    <ol className="dashboard-schedule-list">
      {sessions.map(session => (
        <li key={session.session_id}>
          <span className="dashboard-schedule-time">
            {periods.find(period => period.id === session.start_period)?.start ||
              `Period ${session.start_period}`}
            {"–"}
            {periods.find(period => period.id === session.end_period)?.end ||
              `Period ${session.end_period}`}
          </span>
          <span className="dashboard-schedule-marker" aria-hidden="true" />
          <span className="dashboard-schedule-details">
            <strong>{session.course_title || session.course_id}</strong>
            <small>
              {session.faculty_name} · {session.room_name || session.room_id}
            </small>
          </span>
        </li>
      ))}
    </ol>
  );
}

function CalendarWidget({ timetable, selectedDate, onSelectDate }) {
  const [visibleMonth, setVisibleMonth] = useState(() => ({
    year: selectedDate.getFullYear(),
    month: selectedDate.getMonth(),
  }));
  const { year, month } = visibleMonth;
  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const dates = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, index) => index + 1),
  ];
  const scheduledDays = new Set(timetable?.days || []);
  const selectedCode = DAY_CODES[selectedDate.getDay()];
  const selectedSessions = getDaySessions(timetable, selectedCode);

  function moveMonth(offset) {
    setVisibleMonth(current => {
      const next = new Date(current.year, current.month + offset, 1);
      return { year: next.getFullYear(), month: next.getMonth() };
    });
  }

  return (
    <section className="panel dashboard-calendar-panel">
      <div className="dashboard-panel-heading">
        <div>
          <span className="dashboard-overline">WEEKLY TEMPLATE</span>
          <h2>Calendar</h2>
        </div>
        <div className="dashboard-calendar-controls">
          <button type="button" onClick={() => moveMonth(-1)} aria-label="Previous month">‹</button>
          <button type="button" onClick={() => moveMonth(1)} aria-label="Next month">›</button>
        </div>
      </div>
      <div className="dashboard-calendar-month">
        {new Date(year, month, 1).toLocaleDateString(undefined, {
          month: "long",
          year: "numeric",
        })}
      </div>
      <div className="dashboard-calendar-grid" role="grid" aria-label="Select a date">
        {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(day => (
          <span className="dashboard-calendar-weekday" key={day}>{day}</span>
        ))}
        {dates.map((day, index) => {
          if (!day) return <span className="dashboard-calendar-blank" key={`blank-${index}`} />;
          const date = new Date(year, month, day);
          const isSelected = date.toDateString() === selectedDate.toDateString();
          const hasClasses = scheduledDays.has(DAY_CODES[date.getDay()]);
          return (
            <button
              type="button"
              role="gridcell"
              aria-selected={isSelected}
              className={`dashboard-calendar-day ${isSelected ? "selected" : ""}`}
              key={day}
              onClick={() => onSelectDate(date)}
            >
              {day}
              {hasClasses && <i aria-hidden="true" />}
            </button>
          );
        })}
      </div>
      <div className="dashboard-calendar-preview">
        <strong>{selectedDate.toLocaleDateString(undefined, {
          weekday: "long",
          month: "short",
          day: "numeric",
        })}</strong>
        <span>
          {selectedSessions.length
            ? `${selectedSessions.length} classes in the recurring weekly timetable`
            : "No classes listed for this weekday in the weekly template"}
        </span>
      </div>
      <p className="dashboard-footnote">
        Dates show the recurring weekday schedule. Dated events are not available in this calendar feed.
      </p>
    </section>
  );
}

function DashboardPage({
  dataset,
  timetable,
  loading,
  error,
  backend,
  username,
  userRole,
  canAccessAnalytics,
  onNavigate,
}) {
  const [clock, setClock] = useState(() => Date.now());
  const [selectedDateValue, setSelectedDateValue] = useState(null);
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const currentDate = new Date(clock);
  const selectedDate = new Date(selectedDateValue ?? clock);
  const metrics = useMemo(() => buildMetrics(timetable), [timetable]);
  const todayCode = DAY_CODES[currentDate.getDay()];
  const selectedDay = DAY_CODES[selectedDate.getDay()];
  const todaySessions = getDaySessions(timetable, todayCode);
  const selectedSessions = getDaySessions(timetable, selectedDay);
  const currentPeriod = getCurrentPeriod(timetable, currentDate);
  const currentPeriodKey = currentPeriod
    ? `${todayCode}:${currentPeriod.id}`
    : null;
  const currentOccupiedRooms = currentPeriodKey
    ? metrics.rooms.filter(room => metrics.occupied.has(
      `${room.id}:${currentPeriodKey}`,
    )).length
    : null;
  const availableRooms = currentPeriodKey
    ? metrics.rooms.filter(room => (
      !metrics.occupied.has(`${room.id}:${currentPeriodKey}`) &&
      !metrics.closed.has(`${room.id}:${todayCode}:${currentPeriod.id}`)
    )).length
    : null;
  const availableFaculty = currentPeriodKey
    ? metrics.faculty.filter(faculty => (
      !metrics.facultyOccupied.has(`${faculty.id}:${currentPeriodKey}`)
    )).length
    : null;
  const sessionsByDay = (timetable?.days || []).map(day => ({
    day,
    count: metrics.sessions.filter(session => session.day === day).length,
  }));
  const maxDailySessions = Math.max(1, ...sessionsByDay.map(item => item.count));
  const topFaculty = metrics.faculty.map(faculty => ({
    ...faculty,
    count: metrics.sessions.filter(session => session.faculty_id === faculty.id).length,
  })).sort((left, right) => right.count - left.count).slice(0, 5);
  const greeting = getTimeLabel(currentDate);
  const displayName = username || "there";

  return (
    <section className="dashboard-page">
      <header className="dashboard-welcome">
        <div>
          <span className="dashboard-overline">{userRole} DASHBOARD</span>
          <h1>{greeting}, {displayName}</h1>
          <p>
            {currentDate.toLocaleDateString(undefined, {
              weekday: "long",
              month: "long",
              day: "numeric",
              year: "numeric",
            })}
            {" · "}
            {timetable
              ? "Your verified campus schedule at a glance."
              : "Your campus scheduling overview."}
          </p>
        </div>
        <div className="dashboard-welcome-actions">
          <button type="button" className="dashboard-action-secondary" onClick={() => onNavigate("Timetable")}>
            <CalendarDays size={16} /> View timetable
          </button>
          <button type="button" className="dashboard-action-primary" onClick={() => onNavigate("Campus Assistant")}>
            Ask campus assistant <ArrowUpRight size={16} />
          </button>
        </div>
      </header>

      {error && (
        <div className="dashboard-data-alert" role="alert">
          <AlertCircle size={18} />
          <span>Campus timetable unavailable: {error}</span>
        </div>
      )}

      <div className="dashboard-metrics-grid" aria-label="Campus scheduling metrics">
        <MetricCard
          icon={CalendarDays}
          label="Scheduled classes"
          value={loading ? "—" : metrics.sessions.length}
          note="Classes in the weekly baseline"
          tone="blue"
        />
        <MetricCard
          icon={Users}
          label="Faculty roster"
          value={loading ? "—" : metrics.faculty.length}
          note="Faculty listed in campus data"
          tone="violet"
        />
        <MetricCard
          icon={MapPin}
          label="Campus rooms"
          value={loading ? "—" : metrics.rooms.length}
          note="Rooms in the timetable dataset"
          tone="cyan"
        />
        <MetricCard
          icon={AlertCircle}
          label="Recorded conflicts"
          value={loading ? "—" : metrics.conflictCount}
          note="Across baseline sessions"
          tone={metrics.conflictCount ? "amber" : "green"}
        />
      </div>

      <div className="dashboard-main-grid">
        <section className="panel dashboard-chart-panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">SCHEDULE DISTRIBUTION</span>
              <h2>Classes across the week</h2>
              <p>Scheduled sessions from the official recurring timetable.</p>
            </div>
            <span className="dashboard-period-badge">Weekly baseline</span>
          </div>
          {loading ? (
            <div className="dashboard-skeleton" aria-label="Loading weekly schedule" />
          ) : sessionsByDay.length ? (
            <div className="dashboard-bar-chart" role="img" aria-label="Scheduled classes by weekday">
              {sessionsByDay.map(({ day, count }) => (
                <div className="dashboard-bar-row" key={day}>
                  <span>{DAY_LABELS[day] || day}</span>
                  <div className="dashboard-bar-track">
                    <i style={{ width: `${(count / maxDailySessions) * 100}%` }} />
                  </div>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
          ) : (
            <p className="dashboard-empty">Weekly timetable data is not available.</p>
          )}
          {dataset && (
            <div className="dashboard-chart-foot">
              <span><CheckCircle2 size={15} /> Dataset summary loaded</span>
              <span>{dataset.sessions} source sessions · API {backend}</span>
            </div>
          )}
        </section>

        <section className="panel dashboard-utilization-panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">ROOM UTILIZATION</span>
              <h2>Teaching-period occupancy</h2>
            </div>
            <Building2 size={19} />
          </div>
          {loading ? (
            <div className="dashboard-skeleton dashboard-skeleton-short" />
          ) : metrics.weeklyUtilization === null ? (
            <p className="dashboard-empty">Room schedule data is unavailable.</p>
          ) : (
            <>
              <div className="dashboard-utilization-value">
                <strong>{metrics.weeklyUtilization}%</strong>
                <span>of available room teaching periods are occupied</span>
              </div>
              <div
                className="dashboard-utilization-track"
                role="meter"
                aria-label="Weekly room teaching-period utilization"
                aria-valuemin="0"
                aria-valuemax="100"
                aria-valuenow={metrics.weeklyUtilization}
              >
                <i style={{ width: `${metrics.weeklyUtilization}%` }} />
              </div>
              <div className="dashboard-utilization-legend">
                <span>{metrics.occupiedRoomPeriods.toLocaleString()} occupied</span>
                <span>{metrics.possibleRoomPeriods.toLocaleString()} available periods</span>
              </div>
            </>
          )}
          {canAccessAnalytics && (
            <button type="button" className="dashboard-text-link" onClick={() => onNavigate("Analytics")}>
              Explore room analytics <ArrowUpRight size={15} />
            </button>
          )}
        </section>

        <section className="panel dashboard-schedule-panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">TODAY’S TIMETABLE</span>
              <h2>{DAY_LABELS[todayCode] || "Today"}</h2>
              <p>Recurring weekday schedule · local date</p>
            </div>
            <Clock3 size={19} />
          </div>
          {currentPeriod && (
            <div className="dashboard-current-period">
              <span className="dashboard-live-dot" />
              Period {currentPeriod.id} · {currentPeriod.start}–{currentPeriod.end}
              {currentOccupiedRooms !== null && (
                <span>{currentOccupiedRooms} rooms in use · {availableRooms} available</span>
              )}
            </div>
          )}
          {!currentPeriod && (
            <div className="dashboard-current-period muted">
              <Clock3 size={14} /> No teaching period is active at this time.
            </div>
          )}
          <ScheduleList
            sessions={todaySessions.slice(0, 4)}
            periods={timetable?.periods || []}
            emptyMessage={`No classes are listed for ${DAY_LABELS[todayCode] || "today"} in the weekly timetable.`}
          />
          <button type="button" className="dashboard-text-link" onClick={() => onNavigate("Timetable")}>
            Open full timetable <ArrowUpRight size={15} />
          </button>
        </section>

        <CalendarWidget
          timetable={timetable}
          selectedDate={selectedDate}
          onSelectDate={date => setSelectedDateValue(date.getTime())}
        />
      </div>

      <div className="dashboard-bottom-grid">
        <section className="panel dashboard-faculty-panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">FACULTY WORKLOAD</span>
              <h2>Most scheduled this week</h2>
            </div>
            <GraduationCap size={19} />
          </div>
          {loading ? (
            <div className="dashboard-skeleton dashboard-skeleton-short" />
          ) : topFaculty.length ? (
            <div className="dashboard-faculty-list">
              {topFaculty.map((faculty, index) => (
                <div className="dashboard-faculty-row" key={faculty.id}>
                  <span className="dashboard-faculty-rank">{index + 1}</span>
                  <span className="dashboard-faculty-name">
                    <strong>{faculty.name || faculty.id}</strong>
                    <small>{faculty.department || faculty.id}</small>
                  </span>
                  <span className="dashboard-faculty-count">
                    {faculty.count} <small>classes</small>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="dashboard-empty">Faculty workload data is unavailable.</p>
          )}
        </section>

        <section className="panel dashboard-signals-panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">CAMPUS SNAPSHOT</span>
              <h2>Schedule signals</h2>
            </div>
            <Zap size={19} />
          </div>
          <div className="dashboard-signal-row">
            <span className="dashboard-signal-icon"><Users size={16} /></span>
            <span><strong>{metrics.groups.length}</strong><small>student groups in the timetable</small></span>
          </div>
          <div className="dashboard-signal-row">
            <span className="dashboard-signal-icon"><MapPin size={16} /></span>
            <span>
              <strong>{availableFaculty === null ? "—" : availableFaculty}</strong>
              <small>
                {currentPeriod
                  ? "faculty without a class in the active period"
                  : "faculty availability requires an active teaching period"}
              </small>
            </span>
          </div>
          <div className="dashboard-data-note">
            Upcoming events, notifications, and system activity are not supplied by the available APIs.
          </div>
          <button type="button" className="dashboard-text-link" onClick={() => onNavigate("Spectra Events")}>
            Open Spectra Events <ArrowUpRight size={15} />
          </button>
        </section>
      </div>

      {selectedSessions.length > 0 && selectedDate.toDateString() !== currentDate.toDateString() && (
        <div className="dashboard-selected-day panel">
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-overline">SELECTED DAY</span>
              <h2>{DAY_LABELS[selectedDay] || selectedDay}</h2>
            </div>
            <CalendarDays size={18} />
          </div>
          <ScheduleList
            sessions={selectedSessions}
            periods={timetable?.periods || []}
            emptyMessage="No scheduled classes for this weekday."
          />
        </div>
      )}
    </section>
  );
}

export default DashboardPage;
