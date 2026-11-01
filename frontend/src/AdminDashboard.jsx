import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  BarChart3,
  Bell,
  BookOpenCheck,
  Building2,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  Database,
  GraduationCap,
  Loader2,
  LogOut,
  RefreshCw,
  Settings,
  ShieldCheck,
  Users,
  XCircle,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import AnalyticsPage from "./AnalyticsPage.jsx";
import CampusAssistant from "./CampusAssistant.jsx";
import SpectraEventsPage from "./SpectraEventsPage.jsx";
import StarterPackAdmin from "./StarterPackAdmin.jsx";
import TimetablePage from "./TimetablePage.jsx";
import {
  AdminProfilesPage,
  AuditHistoryPage,
  TimetableApprovalPage,
} from "./WorkflowPages.jsx";
import "./AdminDashboard.css";

const ADMIN_SECTIONS = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "dataset-management", label: "Dataset Management", icon: Database },
  { id: "approvals", label: "Timetable approvals", icon: CheckCircle2 },
  { id: "bookings", label: "Booking requests", icon: BookOpenCheck },
  { id: "timetable", label: "Timetable", icon: CalendarDays },
  { id: "analytics", label: "Room analytics", icon: BarChart3 },
  { id: "assistant", label: "AI Assistant", icon: ShieldCheck },
  { id: "users", label: "Profiles & users", icon: Users },
  { id: "student-faculty", label: "Students & faculty", icon: GraduationCap },
  { id: "complaints", label: "Complaints", icon: ClipboardList },
  { id: "notices", label: "Notices", icon: Bell },
  { id: "audit", label: "Audit history", icon: Settings },
  { id: "settings", label: "Settings", icon: Settings },
];

const NOT_IMPLEMENTED = {
  users: {
    title: "Users & roles",
    description: "The backend verifies Keycloak roles, but does not expose a user directory or Keycloak Admin API integration. No user records or role changes are available here.",
  },
  "student-faculty": {
    title: "Students & faculty",
    description: "The campus dataset contains academic groups and faculty scheduling records, not registered user profiles or student roster records. Profile management and attendance are not available.",
  },
  complaints: {
    title: "Complaints",
    description: "There is no complaint persistence, assignment workflow, or complaint API in the current backend.",
  },
  notices: {
    title: "Notices",
    description: "There is no notice storage, publishing workflow, or audience delivery API in the current backend.",
  },
  settings: {
    title: "Settings",
    description: "No application settings API is configured. Authentication and realm roles remain managed by Keycloak.",
  },
};

async function fetchJSON(path) {
  const response = await apiFetch(path, {
    headers: { Accept: "application/json" },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The API returned an invalid response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : payload.detail?.message;
    throw new Error(detail || `The request failed (HTTP ${response.status}).`);
  }
  return payload;
}

function Metric({ label, value, note, icon: Icon, tone }) {
  return (
    <article className={`admin-metric-card ${tone || ""}`}>
      <span className="admin-metric-icon"><Icon size={18} /></span>
      <span className="admin-metric-label">{label}</span>
      <strong>{value ?? "—"}</strong>
      <small>{note}</small>
    </article>
  );
}

function DatasetNotice() {
  return (
    <aside className="admin-data-notice">
      <CircleHelp size={17} />
      <span>
        Counts describe the supplied campus dataset and booking records, not
        registered accounts. The project currently has no student roster,
        complaint, attendance, or notice database.
      </span>
    </aside>
  );
}

function AdminOverview({ data, errors, loading, onRefresh, onSelect }) {
  const eventCounts = useMemo(() => {
    const events = data.events || [];
    return {
      total: events.length,
      pending: events.filter(event => event.status === "pending").length,
      approved: events.filter(event => event.status === "approved").length,
      cancelled: events.filter(event => event.status === "cancelled").length,
    };
  }, [data.events]);
  const roomTypes = data.analytics?.by_room_type || [];
  const recentEvents = (data.events || [])
    .slice()
    .sort((left, right) => (right.created_at || "").localeCompare(left.created_at || ""))
    .slice(0, 6);

  return (
    <section className="admin-overview">
      <header className="admin-page-heading">
        <div>
          <span className="admin-eyebrow">CAMPUS ADMINISTRATION</span>
          <h1>Overview</h1>
          <p>Current timetable, resource, and venue-request data.</p>
        </div>
        <button
          type="button"
          className="admin-refresh-button"
          onClick={onRefresh}
          disabled={loading}
        >
          {loading
            ? <Loader2 className="admin-spin" size={16} />
            : <RefreshCw size={16} />}
          Refresh
        </button>
      </header>

      <DatasetNotice />

      {loading && (
        <div className="admin-state" role="status">
          <Loader2 className="admin-spin" size={18} />
          Loading current campus records…
        </div>
      )}

      {Object.keys(errors).length > 0 && (
        <div className="admin-error" role="alert">
          <AlertCircle size={18} />
          <div>
            <strong>Some dashboard data is unavailable.</strong>
            {Object.entries(errors).map(([source, message]) => (
              <p key={source}><b>{source}:</b> {message}</p>
            ))}
          </div>
        </div>
      )}

      <div className="admin-metrics-grid">
        <Metric
          label="Campus dataset faculty"
          value={data.timetable?.faculty?.length ?? data.summary?.faculty}
          note="Scheduling records; not user accounts"
          icon={Users}
          tone="blue"
        />
        <Metric
          label="Student groups"
          value={data.timetable?.student_groups?.length ?? data.summary?.student_groups}
          note="Academic groups in the timetable dataset"
          icon={GraduationCap}
          tone="violet"
        />
        <Metric
          label="Campus rooms"
          value={data.timetable?.rooms?.length ?? data.summary?.rooms}
          note="Rooms defined in the current dataset"
          icon={Building2}
          tone="teal"
        />
        <Metric
          label="Scheduled sessions"
          value={data.timetable?.baseline_sessions?.length ?? data.summary?.sessions}
          note="Official recurring weekly timetable"
          icon={CalendarDays}
          tone="amber"
        />
        <Metric
          label="Event requests"
          value={data.events ? eventCounts.total : null}
          note="Persisted Spectra event records"
          icon={ClipboardList}
          tone="blue"
        />
        <Metric
          label="Pending requests"
          value={data.events ? eventCounts.pending : null}
          note="Awaiting administrator review"
          icon={Activity}
          tone="amber"
        />
        <Metric
          label="Approved requests"
          value={data.events ? eventCounts.approved : null}
          note="Approved in the event store"
          icon={CheckCircle2}
          tone="teal"
        />
        <Metric
          label="Cancelled requests"
          value={data.events ? eventCounts.cancelled : null}
          note="Cancelled in the event store"
          icon={XCircle}
          tone="rose"
        />
      </div>

      <div className="admin-overview-grid">
        <section className="admin-card">
          <div className="admin-card-heading">
            <div>
              <span className="admin-eyebrow">REVIEW QUEUE</span>
              <h2>Recent event requests</h2>
            </div>
            <button type="button" className="admin-text-button" onClick={() => onSelect("bookings")}>
              Manage requests <ChevronRight size={15} />
            </button>
          </div>
          {!data.events?.length ? (
            <p className="admin-empty">
              {data.events ? "No event requests have been recorded." : "Event records could not be loaded."}
            </p>
          ) : (
            <div className="admin-table-scroll">
              <table className="admin-table">
                <thead>
                  <tr><th>Request</th><th>Venue</th><th>Date</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {recentEvents.map(event => (
                    <tr key={event.id}>
                      <td>
                        <strong>{event.event_name}</strong>
                        <small>{event.category.replaceAll("_", " ")}</small>
                      </td>
                      <td>{event.room_id}</td>
                      <td>{event.event_date}</td>
                      <td><span className={`admin-status status-${event.status}`}>{event.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="admin-card">
          <div className="admin-card-heading">
            <div>
              <span className="admin-eyebrow">ROOM INVENTORY</span>
              <h2>Rooms by dataset type</h2>
            </div>
            <button type="button" className="admin-text-button" onClick={() => onSelect("analytics")}>
              View analytics <ChevronRight size={15} />
            </button>
          </div>
          {roomTypes.length === 0 ? (
            <p className="admin-empty">
              {data.analytics ? "No room-type inventory was returned." : "Room analytics could not be loaded."}
            </p>
          ) : (
            <div className="admin-room-types">
              {roomTypes.map(type => (
                <div className="admin-room-type" key={type.room_type}>
                  <span>{type.room_type.replaceAll("_", " ")}</span>
                  <strong>{type.room_count}</strong>
                  <small>{type.utilization_percent}% teaching utilization</small>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="admin-card admin-unavailable-card">
        <div className="admin-card-heading">
          <div>
            <span className="admin-eyebrow">DATA AVAILABILITY</span>
            <h2>Modules without backing services</h2>
          </div>
        </div>
        <p>
          User directory and role changes, complaints, attendance, notices,
          notifications, and general audit logs require data stores or APIs
          that are not present in this project. They are intentionally not
          represented as live admin features.
        </p>
        <button type="button" className="admin-text-button" onClick={() => onSelect("users")}>
          See module status <ChevronRight size={15} />
        </button>
      </section>
    </section>
  );
}

function UnsupportedModule({ section }) {
  const item = NOT_IMPLEMENTED[section];
  return (
    <section className="admin-module-state">
      <span className="admin-module-icon"><CircleHelp size={22} /></span>
      <span className="admin-eyebrow">MODULE NOT AVAILABLE</span>
      <h1>{item.title}</h1>
      <p>{item.description}</p>
      <p className="admin-module-footnote">
        No mock records, controls, or unsupported database operations are shown.
      </p>
    </section>
  );
}

function AdminDashboard({ auth }) {
  const [section, setSection] = useState("overview");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState({});
  const [data, setData] = useState({
    summary: null,
    timetable: null,
    events: null,
    analytics: null,
  });

  useEffect(() => {
    let active = true;
    async function loadSource(key, path, predicate = () => true) {
      try {
        const payload = await fetchJSON(path);
        if (!predicate(payload)) {
          throw new Error("The API returned an unexpected data shape.");
        }
        return { key, value: payload };
      } catch (error) {
        return { key, error: error.message };
      }
    }

    Promise.all([
      loadSource("summary", "/api/dataset/summary", payload =>
        payload.status === "success" && payload.data),
      loadSource("timetable", "/api/timetable", payload =>
        payload.status === "success" && payload.data),
      loadSource("events", "/api/spectra/events", payload =>
        Array.isArray(payload.events)),
      loadSource("analytics", "/api/analytics/room-utilization", payload =>
        payload.status === "success" && payload.data),
    ]).then(results => {
      if (!active) return;
      const nextData = { summary: null, timetable: null, events: null, analytics: null };
      const nextErrors = {};
      for (const result of results) {
        if (result.error) {
          nextErrors[result.key] = result.error;
        } else if (result.key === "summary") {
          nextData.summary = result.value.data;
        } else if (result.key === "timetable") {
          nextData.timetable = result.value.data;
        } else if (result.key === "events") {
          nextData.events = result.value.events;
        } else if (result.key === "analytics") {
          nextData.analytics = result.value.data;
        }
      }
      setData(nextData);
      setErrors(nextErrors);
    }).finally(() => {
      if (active) setLoading(false);
    });

    return () => {
      active = false;
    };
  }, [reload]);

  function refreshDashboard() {
    setLoading(true);
    setReload(value => value + 1);
  }

  const sectionInfo = ADMIN_SECTIONS.find(item => item.id === section);
  const userName = auth.identity?.identity?.username || "Administrator";

  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <a className="admin-brand" href="/" aria-label="Return to CIT Nexus">
          <img className="admin-brand-logo" src="/images/cit-nexus-logo.png" alt="" />
          <span><strong>CIT Nexus</strong><small>ADMIN WORKSPACE</small></span>
        </a>
        <nav className="admin-navigation" aria-label="Administrator sections">
          <span className="admin-navigation-label">WORKSPACE</span>
          {ADMIN_SECTIONS.map(item => {
            const Icon = item.icon;
            const unsupported = Boolean(NOT_IMPLEMENTED[item.id]);
            return (
              <button
                type="button"
                key={item.id}
                className={`admin-nav-item ${section === item.id ? "active" : ""} ${unsupported ? "unsupported" : ""}`}
                onClick={() => setSection(item.id)}
                aria-current={section === item.id ? "page" : undefined}
                title={unsupported ? "Backing service is not available" : undefined}
              >
                <Icon size={16} />
                <span>{item.label}</span>
                {unsupported && <span className="admin-nav-dot" aria-label="Unavailable" />}
              </button>
            );
          })}
        </nav>
        <div className="admin-sidebar-footer">
          <span className="admin-verified-mark"><ShieldCheck size={16} /></span>
          <span><strong>{userName}</strong><small>Verified ADMIN role</small></span>
        </div>
      </aside>

      <main className="admin-main">
        <header className="admin-topbar">
          <div className="admin-breadcrumb">
            <span>Admin</span><ChevronRight size={14} /><strong>{sectionInfo?.label}</strong>
          </div>
          <div className="admin-topbar-actions">
            <span className="admin-auth-badge"><ShieldCheck size={14} /> ADMIN</span>
            <a className="admin-back-link" href="/">CIT Nexus</a>
            <button type="button" className="admin-logout" onClick={() => auth.logout()}>
              <LogOut size={15} /><span>Sign out</span>
            </button>
          </div>
        </header>

        <div className="admin-content">
          {section === "overview" && (
            <AdminOverview
              data={data}
              errors={errors}
              loading={loading}
              onRefresh={refreshDashboard}
              onSelect={setSection}
            />
          )}
          {section === "dataset-management" && <StarterPackAdmin />}
          {section === "approvals" && <TimetableApprovalPage />}
          {section === "audit" && <AuditHistoryPage />}
          {section === "users" && <AdminProfilesPage />}
          {section === "bookings" && <SpectraEventsPage auth={auth} />}
          {section === "timetable" && (
            <section className="admin-embedded-module">
              <div className="admin-module-intro">
                <span className="admin-eyebrow">OFFICIAL SCHEDULE</span>
                <h1>Timetable</h1>
                <p>Current published schedule and availability. Editing and publishing APIs are not available.</p>
              </div>
              <TimetablePage />
            </section>
          )}
          {section === "analytics" && <AnalyticsPage />}
          {section === "assistant" && (
            <section className="admin-embedded-module">
              <div className="admin-module-intro">
                <span className="admin-eyebrow">GEMINI BOOKING AGENT</span>
                <h1>AI Assistant</h1>
                <p>Use the existing authenticated assistant. There is no tool-activity log, service-health API, or admin toggle in the current backend.</p>
              </div>
              <CampusAssistant />
            </section>
          )}
          {NOT_IMPLEMENTED[section] && <UnsupportedModule section={section} />}
          {loading && section !== "overview" && (
            <span className="admin-global-loading" role="status">
              <Loader2 className="admin-spin" size={14} /> Refreshing workspace data
            </span>
          )}
        </div>
      </main>
    </div>
  );
}

export default AdminDashboard;
