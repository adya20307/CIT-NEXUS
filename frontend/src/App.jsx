import { useEffect, useMemo, useState } from "react";
import {
  LayoutDashboard, CalendarDays, GraduationCap,
  Building2, CalendarCheck, RefreshCw, BrainCircuit,
  BarChart3, Database, Users, Settings, Bell,
  Menu, Zap, ShieldCheck, Moon, Sun, LogOut,
  Play, CheckCircle2, AlertCircle, Loader2, UserRound,
  FileText, Activity, Download, Search, X, ChevronDown
} from "lucide-react";
import "./App.css";
import "./design-system.css";
import { filterExams } from "./examFilters.js";
import { useKeycloakAuth } from "./auth/KeycloakAuthContext.js";
import { apiFetch } from "./auth/keycloakClient.js";
import { getDashboardPath, getDashboardRole } from "./auth/roles.js";
import TimetablePage from "./TimetablePage.jsx";
import AnalyticsPage from "./AnalyticsPage.jsx";
import VenueBookingPage from "./VenueBookingPage.jsx";
import CampusAssistant from "./CampusAssistant.jsx";
import SpectraEventsPage from "./SpectraEventsPage.jsx";
import DashboardPage from "./DashboardPage.jsx";
import AdminDashboard from "./AdminDashboard.jsx";
import StarterPackAdmin from "./StarterPackAdmin.jsx";
import { AccountWorkflowPage } from "./WorkflowPages.jsx";

const navigation = [
  { name: "Dashboard", icon: LayoutDashboard },
  { name: "Timetable", icon: CalendarDays },
  { name: "Examinations", icon: GraduationCap },
  { name: "Venue Booking", icon: Building2 },
  { name: "Spectra Events", icon: CalendarCheck },
  { name: "Faculty Requests", icon: RefreshCw },
  { name: "My Profile", icon: UserRound },
  { name: "Campus Crisis Mode", icon: Zap },
  { name: "What-If Simulator", icon: Activity },
  { name: "AI Assistant", icon: BrainCircuit },
  { name: "Analytics", icon: BarChart3 },
  { name: "Dataset Management", icon: Database },
  { name: "Users & Roles", icon: Users },
  { name: "Settings", icon: Settings },
  { name: "Admin Console", icon: ShieldCheck },
];

const navigationSections = [
  { label: "Overview", names: ["Dashboard"] },
  { label: "Scheduling", names: ["Timetable"] },
  {
    label: "Campus operations",
    names: ["Examinations", "Venue Booking", "Spectra Events"],
  },
  {
    label: "Intelligence",
    names: [
      "Campus Crisis Mode",
      "What-If Simulator",
      "AI Assistant",
      "Analytics",
    ],
  },
  {
    label: "Administration",
    names: ["Faculty Requests", "My Profile", "Dataset Management", "Users & Roles", "Settings", "Admin Console"],
  },
];

const roleAccess = {
  Dashboard: ["ADMIN", "FACULTY", "STUDENT"],
  Timetable: ["ADMIN", "FACULTY", "STUDENT"],
  Examinations: ["ADMIN", "FACULTY", "STUDENT"],
  "Venue Booking": ["ADMIN", "FACULTY", "STUDENT"],
  "Spectra Events": ["ADMIN", "FACULTY", "STUDENT"],
  "Faculty Requests": ["FACULTY"],
  "My Profile": ["ADMIN", "FACULTY", "STUDENT"],
  "Campus Crisis Mode": ["ADMIN"],
  "What-If Simulator": ["ADMIN", "FACULTY"],
  "AI Assistant": ["ADMIN", "FACULTY", "STUDENT"],
  Analytics: ["ADMIN"],
  "Dataset Management": ["ADMIN"],
  "Users & Roles": ["ADMIN"],
  Settings: ["ADMIN"],
  "Admin Console": ["ADMIN"],
};

const EMPTY_ROLES = [];
const API_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const examDayNames = {
  MON: "Monday",
  TUE: "Tuesday",
  WED: "Wednesday",
  THU: "Thursday",
  FRI: "Friday",
  SAT: "Saturday",
};

function UnavailablePage({ title, description }) {
  return (
    <section className="unavailable-page panel" role="status">
      <span className="eyebrow">MODULE STATUS</span>
      <h1>{title}</h1>
      <p>{description}</p>
      <p className="unavailable-page-note">
        This module is not exposed as a working feature in the current application. No placeholder data or actions are shown.
      </p>
    </section>
  );
}

function ExaminationsPage() {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedDay, setSelectedDay] = useState("ALL");
  const [selectedSession, setSelectedSession] = useState("ALL");
  const [search, setSearch] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [exportError, setExportError] = useState("");

  async function downloadExamExport(path, filename) {
    setExportError("");
    try {
      const response = await apiFetch(`${API_URL}${path}`);
      if (!response.ok) {
        throw new Error(`Could not download the examination export (HTTP ${response.status}).`);
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (downloadError) {
      setExportError(downloadError.message);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function loadExams() {
      setLoading(true);
      setError("");

      try {
        const response = await apiFetch(`${API_URL}/api/exams`);
        const payload = await response.json();
        if (!response.ok || payload.status !== "success") {
          const detail = payload.detail;
          throw new Error(
            typeof detail === "string"
              ? detail
              : detail?.message || "Could not load the examination timetable."
          );
        }

        if (!cancelled) {
          setResult(payload.data);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadExams();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const visibleExams = result
    ? filterExams(result.exams, {
      day: selectedDay,
      session: selectedSession,
      search,
    })
    : [];

  return (
    <section className="examinations-page">
      <div className="welcome">
        <div>
          <span className="eyebrow">MIDSEM EXAMINATION PLANNER</span>
          <h1>Examination Timetable</h1>
          <p>
            Exam assignments generated from the supplied dataset for the
            scheduled examination week.
          </p>
        </div>

        {result && (
          <div className="exam-export-actions">
            <button
              type="button"
              className="exam-download-button"
              onClick={() => downloadExamExport(
                "/api/exams/export/full",
                "examination_timetable.json",
              )}
            >
              <Download size={17} />
              Export JSON
            </button>
            <button
              type="button"
              className="exam-download-button"
              onClick={() => downloadExamExport(
                "/api/exams/export/pdf",
                "examination_timetable.pdf",
              )}
            >
              <Download size={17} />
              Download PDF
            </button>
          </div>
        )}
      </div>

      {exportError && <p className="exam-error" role="alert">{exportError}</p>}
      {loading && (
        <div className="panel exam-state" role="status">
          <Loader2 className="exam-spinner" size={20} />
          Generating and validating the examination timetable...
        </div>
      )}

      {!loading && error && (
        <div className="exam-error" role="alert">
          <AlertCircle size={20} />
          <div>
            <strong>Examination timetable unavailable</strong>
            <p>{error}</p>
            <button
              type="button"
              onClick={() => setReloadKey(value => value + 1)}
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {!loading && result && (
        <>
          <div className="exam-summary-grid">
            <div className="exam-summary-card">
              <span>Scheduled exams</span>
              <strong>{result.exam_count}</strong>
            </div>
            <div className="exam-summary-card">
              <span>Solver status</span>
              <strong>{result.solver_status}</strong>
            </div>
            <div className="exam-summary-card">
              <span>Hard-constraint checks</span>
              <strong>
                {result.validation.hard_constraints_satisfied
                  ? "Passed"
                  : "Failed"}
              </strong>
            </div>
          </div>

          <div
            className={`exam-validation ${
              result.validation.hard_constraints_satisfied ? "success" : "error"
            }`}
            role="status"
          >
            {result.validation.hard_constraints_satisfied
              ? <CheckCircle2 size={20} />
              : <AlertCircle size={20} />}
            <div>
              <strong>
                {result.validation.hard_constraints_satisfied
                  ? "All checked hard constraints passed"
                  : "Hard-constraint validation failed"}
              </strong>
              {result.validation.hard_violations.length > 0 && (
                <ul>
                  {result.validation.hard_violations.map(violation => (
                    <li key={violation}>{violation}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {result.validation.unverified_constraints.map(constraint => (
            <div className="exam-validation warning" key={constraint}>
              <AlertCircle size={20} />
              <div>
                <strong>Validation limitation</strong>
                <p>{constraint}</p>
              </div>
            </div>
          ))}

          <div className="panel exam-calendar-panel">
            <div className="exam-calendar-heading">
              <div>
                <h2>Weekly examination calendar</h2>
                <p>
                  {visibleExams.length} of {result.exam_count} exams shown ·
                  Solver objective {result.objective}
                </p>
              </div>
              <div className="exam-filter-controls">
                <label className="exam-search">
                  <Search size={16} aria-hidden="true" />
                  <span className="visually-hidden">Search exams</span>
                  <input
                    type="search"
                    value={search}
                    onChange={event => setSearch(event.target.value)}
                    placeholder="Search ID, course or group"
                  />
                </label>
                <label>
                  Filter day
                  <select
                    value={selectedDay}
                    onChange={event => setSelectedDay(event.target.value)}
                  >
                    <option value="ALL">All days</option>
                    {result.days.map(day => (
                      <option key={day} value={day}>
                        {examDayNames[day] || day}
                      </option>
                    ))}
                  </select>
                </label>
                <fieldset className="exam-session-filter">
                  <legend>Session</legend>
                  {["ALL", "AM", "PM"].map(session => (
                    <button
                      type="button"
                      key={session}
                      className={selectedSession === session ? "selected" : ""}
                      aria-pressed={selectedSession === session}
                      onClick={() => setSelectedSession(session)}
                    >
                      {session === "ALL" ? "All" : session}
                    </button>
                  ))}
                </fieldset>
              </div>
            </div>

            <div className="exam-week-scroll">
              <div className="exam-week-grid">
                {result.days
                  .filter(day => selectedDay === "ALL" || day === selectedDay)
                  .map(day => {
                    const dayExams = visibleExams.filter(
                      exam => exam.day === day,
                    );
                    return (
                      <section
                        className="exam-day-column"
                        key={day}
                        aria-label={`${examDayNames[day] || day} examinations`}
                      >
                        <h3>{examDayNames[day] || day}</h3>
                        {["AM", "PM"].map(session => {
                          const sessionExams = dayExams.filter(
                            exam => exam.session === session,
                          );
                          return (
                            <div
                              className={`exam-session-slot ${session.toLowerCase()}`}
                              key={session}
                            >
                              <h4>
                                <span
                                  className={`exam-session-indicator ${session.toLowerCase()}`}
                                />
                                {session}{" "}
                                <span>
                                  {session === "AM" ? "Morning" : "Afternoon"}
                                </span>
                              </h4>
                              {sessionExams.map(exam => (
                                <details
                                  className={`exam-card ${session.toLowerCase()}`}
                                  key={exam.exam_id}
                                >
                                  <summary>
                                    <span
                                      className={`exam-session-badge ${session.toLowerCase()}`}
                                    >
                                      {session}
                                    </span>
                                    <strong>{exam.exam_id}</strong>
                                    <span>
                                      {exam.course_names.join(", ")}
                                    </span>
                                    <small>
                                      {exam.start_time}–{exam.end_time}
                                    </small>
                                  </summary>
                                  <div className="exam-card-details">
                                    <p>
                                      <strong>Student groups</strong>
                                      {exam.groups
                                        .map(group => group.name)
                                        .join(", ")}
                                    </p>
                                    <p>
                                      <strong>Students</strong>
                                      {exam.student_count}
                                    </p>
                                    <p>
                                      <strong>Duration</strong>
                                      {exam.duration_hours} hours
                                    </p>
                                    <p className="exam-capacity-total">
                                      <strong>Total allocated capacity</strong>
                                      {exam.total_allocated_capacity} seats for{" "}
                                      {exam.student_count} students
                                    </p>
                                    <div className="exam-assigned-rooms">
                                      <strong>Allocated rooms</strong>
                                      {exam.rooms.map(room => (
                                        <p key={room.id}>
                                          {room.name} ({room.id}) ·{" "}
                                          {room.exam_capacity} exam seats
                                        </p>
                                      ))}
                                    </div>
                                  </div>
                                </details>
                              ))}
                              {sessionExams.length === 0 && (
                                <p className="exam-slot-empty">No exams</p>
                              )}
                            </div>
                          );
                        })}
                      </section>
                    );
                  })}
              </div>
              {visibleExams.length === 0 && (
                <p className="exam-empty">No exams match the selected filters.</p>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function MainApplication() {
  const auth = useKeycloakAuth();
  const authRoles = auth.identity?.roles ?? EMPTY_ROLES;
  const accessibleNavigation = useMemo(
    () => navigation.filter(item =>
      roleAccess[item.name].some(role => authRoles.includes(role)),
    ),
    [authRoles],
  );
  const [backend, setBackend] = useState("Connecting...");
  const [dataset, setDataset] = useState(null);
  const [datasetLoading, setDatasetLoading] = useState(true);
  const [datasetError, setDatasetError] = useState("");
  const [officialSchedule, setOfficialSchedule] = useState({
    loading: true,
    sessionCount: 0,
    error: "",
  });
  const [timetableData, setTimetableData] = useState(null);
  const [timetableSearchRequest, setTimetableSearchRequest] = useState(null);

  const [requestedActive, setActive] = useState("Dashboard");
  const active = accessibleNavigation.some(
    item => item.name === requestedActive,
  )
    ? requestedActive
    : accessibleNavigation[0]?.name || "Dashboard";
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [clock, setClock] = useState(() => Date.now());
  const [searchText, setSearchText] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [theme, setTheme] = useState(() => {
    try {
      return window.localStorage.getItem("cit-nexus-theme") === "dark"
        ? "dark"
        : "light";
    } catch {
      return "light";
    }
  });
  const searchResults = useMemo(() => {
    const query = searchText.trim().toLocaleLowerCase();
    if (!query) return [];
    const modules = accessibleNavigation
      .filter(item => item.name.toLocaleLowerCase().includes(query))
      .map(item => ({ type: "module", id: item.name, label: item.name }));
    const entities = [
      ...(timetableData?.student_groups || []).map(item => ({
        type: "group",
        id: item.id,
        label: item.name || item.id,
        detail: `Student group · ${item.id}`,
      })),
      ...(timetableData?.faculty || []).map(item => ({
        type: "faculty",
        id: item.id,
        label: item.name || item.id,
        detail: `Faculty · ${item.department || item.id}`,
      })),
      ...(timetableData?.rooms || []).map(item => ({
        type: "room",
        id: item.id,
        label: item.name || item.id,
        detail: `Room · ${item.building || item.id}`,
      })),
    ].filter(item => `${item.label} ${item.id} ${item.detail}`
      .toLocaleLowerCase()
      .includes(query));
    return [...modules, ...entities].slice(0, 8);
  }, [accessibleNavigation, searchText, timetableData]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.body.dataset.theme = theme;
    try {
      window.localStorage.setItem("cit-nexus-theme", theme);
    } catch {
      // Theme selection remains active for the current page if storage is unavailable.
    }
  }, [theme]);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  // Crisis Mode states
  const [crisisLoading, setCrisisLoading] = useState(false);
  const [crisisResult, setCrisisResult] = useState(null);
  const [crisisError, setCrisisError] = useState("");
  const [reportLoading, setReportLoading] = useState(false);
  const [compareResult, setCompareResult] = useState(null);
  const [whatIfOptions, setWhatIfOptions] = useState(null);
  const [whatIfOptionsError, setWhatIfOptionsError] = useState("");
  const [whatIfType, setWhatIfType] = useState("room_unavailable");
  const [whatIfResourceId, setWhatIfResourceId] = useState("");
  const [whatIfDays, setWhatIfDays] = useState([]);
  const [whatIfPeriods, setWhatIfPeriods] = useState([]);
  const [whatIfReason, setWhatIfReason] = useState("");
  const [whatIfLoading, setWhatIfLoading] = useState(false);
  const [whatIfError, setWhatIfError] = useState("");
  const [whatIfResult, setWhatIfResult] = useState(null);

  useEffect(() => {
    if (authRoles.length === 0) return undefined;
    let cancelled = false;

    async function loadDashboardData() {
      try {
        const response = await apiFetch(`${API_URL}/api/health`);

        if (!response.ok) {
          throw new Error("Backend health check failed");
        }

        const healthData = await response.json();

        if (!cancelled) {
          setBackend(healthData.status || "Connected");
        }
      } catch {
        if (!cancelled) {
          setBackend("Disconnected");
        }
      }

      try {
        const response = await apiFetch(
          `${API_URL}/api/dataset/summary`
        );

        if (!response.ok) {
          throw new Error("Could not load dataset statistics");
        }

        const result = await response.json();

        if (result.status !== "success" || !result.data) {
          throw new Error(
            result.message || "Invalid dataset response"
          );
        }

        if (!cancelled) {
          setDataset(result.data);
          setDatasetError("");
        }
      } catch (error) {
        if (!cancelled) {
          setDatasetError(error.message);
        }
      } finally {
        if (!cancelled) {
          setDatasetLoading(false);
        }
      }

      try {
        const response = await apiFetch(`${API_URL}/api/timetable`);
        const result = await response.json();
        if (!response.ok || result.status !== "success") {
          throw new Error(
            result.detail?.message ||
            result.detail ||
            "Official timetable could not be loaded.",
          );
        }
        const sessions = result.data?.baseline_sessions;
        if (!Array.isArray(sessions)) {
          throw new Error("The timetable API returned no official baseline.");
        }
        if (!cancelled) {
          setTimetableData(result.data);
          setOfficialSchedule({
            loading: false,
            sessionCount: sessions.length,
            error: "",
          });
        }
      } catch (error) {
        if (!cancelled) {
          setOfficialSchedule({
            loading: false,
            sessionCount: 0,
            error: error.message,
          });
        }
      }
    }

    loadDashboardData();

    return () => {
      cancelled = true;
    };
  }, [authRoles]);

  useEffect(() => {
    if (
      !["Campus Crisis Mode", "What-If Simulator"].includes(active) ||
      whatIfOptions
    ) return undefined;

    let cancelled = false;
    async function loadWhatIfOptions() {
      try {
        const response = await apiFetch(`${API_URL}/api/crisis/options`);
        const result = await response.json();
        if (!response.ok || result.status !== "success") {
          throw new Error(result.detail || "Could not load simulation options.");
        }
        if (!cancelled) {
          setWhatIfOptions(result.data);
          setWhatIfResourceId(result.data.rooms[0]?.id || "");
        }
      } catch (error) {
        if (!cancelled) {
          setWhatIfOptionsError(error.message);
        }
      }
    }

    loadWhatIfOptions();
    return () => {
      cancelled = true;
    };
  }, [active, whatIfOptions]);

  useEffect(() => {
    if (active === "What-If Simulator") {
      document.getElementById("what-if-panel")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }, [active]);

  function clearWhatIfPreview() {
    setWhatIfResult(null);
    setWhatIfError("");
  }

  // Run Campus Crisis Mode
 // Run Campus Crisis Mode
async function runCrisisMode() {
  setCrisisLoading(true);
  setCrisisError("");
  setCrisisResult(null);
  setCompareResult(null);
  setWhatIfResult(null);

  try {
    // 1. Run the crisis scheduler
    const response = await apiFetch(`${API_URL}/api/crisis/run`, {
      method: "POST",
      headers: {
        Accept: "application/json"
      }
    });

    const result = await response.json();

    if (!response.ok || result.status !== "success") {
      throw new Error(result.detail || "Crisis simulation failed");
    }

    // 2. Fetch the official validation report
    const reportResponse = await apiFetch(
      `${API_URL}/api/crisis/report`
    );

    const reportResult = await reportResponse.json();

    if (!reportResponse.ok || reportResult.status !== "success") {
      throw new Error("Schedule generated, but validation report could not be loaded.");
    }

    // 3. Fetch timetable comparison
    const compareResponse = await apiFetch(
      `${API_URL}/api/crisis/compare`
    );

    const compareData = await compareResponse.json();

    if (!compareResponse.ok || compareData.status !== "success") {
      throw new Error("Timetable comparison could not be loaded.");
    }

    // 4. Save comparison data
    setCompareResult(compareData);

    // 5. Combine scheduler output and validation report
    setCrisisResult({
      ...result,
      data: result.data,
      validation: reportResult.report
    });

  } catch (error) {
    setCrisisError(error.message);
  } finally {
    setCrisisLoading(false);
  }
}
async function runWhatIfSimulation() {
  setWhatIfLoading(true);
  setWhatIfError("");
  setWhatIfResult(null);

  try {
    const response = await apiFetch(`${API_URL}/api/crisis/what-if`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        disruption_type: whatIfType,
        resource_id: whatIfResourceId,
        days: whatIfDays,
        periods: whatIfPeriods,
        reason: whatIfReason
      })
    });
    const result = await response.json();
    if (!response.ok || result.status !== "success") {
      throw new Error(result.detail || "What-if simulation failed.");
    }
    setWhatIfResult(result);
  } catch (error) {
    setWhatIfError(error.message);
  } finally {
    setWhatIfLoading(false);
  }
}
  // Fetch latest crisis report
// Fetch latest crisis report and timetable comparison
async function loadCrisisReport() {
  setReportLoading(true);
  setCrisisError("");

  try {
    // 1. Fetch validation report
    const response = await apiFetch(
      `${API_URL}/api/crisis/report`
    );

    const result = await response.json();

    if (!response.ok || result.status !== "success") {
      throw new Error(
        result.detail || "Could not load validation report"
      );
    }

    // 2. Fetch timetable comparison
    const compareResponse = await apiFetch(
      `${API_URL}/api/crisis/compare`
    );

    const compareData = await compareResponse.json();

    if (!compareResponse.ok || compareData.status !== "success") {
      throw new Error("Could not load timetable comparison.");
    }

    // 3. Save comparison
    setCompareResult(compareData);

    // 4. Save validation report
    setCrisisResult({
      status: "success",
      message: "Latest crisis validation report loaded.",
      validation:
        result.validation ??
        result.report ??
        result.data?.validation ??
        null
    });

  } catch (error) {
    setCrisisError(error.message);
  } finally {
    setReportLoading(false);
  }
}
  function selectSearchResult(result) {
    setSearchText("");
    setSearchOpen(false);
    if (result.type === "module") {
      setActive(result.id);
      return;
    }

    setTimetableSearchRequest({
      kind: result.type,
      id: result.id,
      nonce: Date.now(),
    });
    setActive("Timetable");
  }



  // Read hard violations from the official validator report.
const validation =
  crisisResult?.validation ??
  crisisResult?.report ??
  crisisResult?.data?.validation ??
  crisisResult?.data?.report ??
  null;

// Read hard violations from the official validator report.
const hardViolationCount = (() => {
  if (!validation) return null;

  const direct =
    validation.hard_violation_count ??
    validation.hard_violations_count ??
    validation.hardViolationCount;

  if (typeof direct === "number") {
    return direct;
  }

  const violations = validation.hard_violations;

  if (Array.isArray(violations)) {
    return violations.length;
  }

  if (violations && typeof violations === "object") {
    return Object.values(violations).reduce((total, value) => {
      if (typeof value === "number") return total + value;
      if (Array.isArray(value)) return total + value.length;
      return total;
    }, 0);
  }

  return null;
})();

// Official validator uses soft_total.
const softPenalty =
  validation?.soft_total ??
  validation?.soft_penalty ??
  "—";

  if (authRoles.length === 0) {
    return (
      <main className="auth-loading" role="alert">
        <h1>No CIT Nexus role assigned</h1>
        <p>Ask a CIT Nexus administrator to assign ADMIN, FACULTY, or STUDENT in the cit-nexus realm.</p>
        <button type="button" onClick={auth.logout}>Sign out</button>
      </main>
    );
  }

  return (
    <div className={`app-layout ${sidebarCollapsed ? "sidebar-is-collapsed" : ""}`}>
      <aside
        className={`sidebar ${menuOpen ? "open" : ""}`}
        aria-label="Main navigation"
      >
        <div className="brand">
          <img
            className="brand-logo"
            src="/images/cit-nexus-logo.png"
            alt=""
          />
          <div>
            <h2>CIT Nexus</h2>
            <span>Campus Intelligence</span>
          </div>
          <button
            type="button"
            className="sidebar-collapse-button"
            onClick={() => setSidebarCollapsed(value => !value)}
            aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
            title={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
          >
            <Menu size={16} />
          </button>
        </div>

        <nav aria-label="Application sections">
          {navigationSections.map(section => {
            const items = section.names
              .map(name => accessibleNavigation.find(item => item.name === name))
              .filter(Boolean);
            if (items.length === 0) return null;
            return (
              <div className="sidebar-group" key={section.label}>
                <div className="side-label">{section.label}</div>
                {items.map(item => {
                  const Icon = item.icon;
                  return (
                    <button
                      type="button"
                      key={item.name}
                      className={`nav-item ${active === item.name ? "active" : ""}`}
                      onClick={() => {
                        if (item.name === "Admin Console") {
                          window.location.assign("/admin");
                          return;
                        }
                        setActive(item.name);
                        setMenuOpen(false);
                      }}
                      title={sidebarCollapsed ? item.name : undefined}
                      aria-current={active === item.name ? "page" : undefined}
                    >
                      <Icon size={17} />
                      <span>{item.name}</span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <span
            className={`status-dot ${
              backend === "Disconnected" ? "offline" : ""
            }`}
          ></span>

          Backend {backend}
          <small>CIT Nexus v1.0</small>
        </div>
      </aside>
      {menuOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <main className="main-content">
        <header className="topbar">
          <button
            type="button"
            className="menu-button"
            onClick={() => setMenuOpen(value => !value)}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={18} /> : <Menu size={18} />}
          </button>

          <div className="topbar-heading">
            <h2>{active}</h2>
            <p>Chilika Institute of Technology · CIT Nexus</p>
          </div>

          <div className="topbar-search">
            <Search size={15} aria-hidden="true" />
            <input
              type="search"
              value={searchText}
              onChange={event => {
                setSearchText(event.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onKeyDown={event => {
                if (event.key === "Escape") setSearchOpen(false);
                if (event.key === "Enter" && searchResults[0]) {
                  selectSearchResult(searchResults[0]);
                }
              }}
              placeholder="Search modules, faculty, groups, rooms"
              aria-label="Search modules, faculty, groups, and rooms"
              aria-expanded={searchOpen && Boolean(searchText)}
              aria-controls="global-search-results"
            />
            <kbd>Enter</kbd>
            {searchOpen && searchText.trim() && (
              <div className="search-results" id="global-search-results" role="listbox">
                <div className="search-results-heading">Search results</div>
                {searchResults.length ? searchResults.map(result => (
                  <button
                    type="button"
                    className="search-result"
                    key={`${result.type}-${result.id}`}
                    role="option"
                    aria-selected="false"
                    onClick={() => selectSearchResult(result)}
                  >
                    <strong>{result.label}</strong>
                    <small>{result.detail || result.type}</small>
                  </button>
                )) : (
                  <p className="search-empty">
                    No matching module or timetable record. Try a faculty, group, or room name.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="top-actions">
            <span className="topbar-date">
              {new Date(clock).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </span>
            <button
              type="button"
              className="topbar-icon-button"
              onClick={() => setTheme(value => value === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <div className="popover-wrap">
              <button
                type="button"
                className="topbar-icon-button"
                onClick={() => {
                  setNotificationsOpen(value => !value);
                  setProfileOpen(false);
                }}
                aria-label="Open notifications"
                aria-expanded={notificationsOpen}
              >
                <Bell size={16} />
              </button>
              {notificationsOpen && (
                <section className="topbar-popover" aria-label="Notifications">
                  <div className="popover-heading">Notifications</div>
                  <p className="popover-empty">
                    No notification feed is connected to this application yet.
                  </p>
                </section>
              )}
            </div>
            <div className="popover-wrap">
              <button
                type="button"
                className="profile-trigger"
                onClick={() => {
                  setProfileOpen(value => !value);
                  setNotificationsOpen(false);
                }}
                aria-label="Open account menu"
                aria-expanded={profileOpen}
              >
                <span className="avatar">
                  {(auth.identity?.identity?.username || "U").slice(0, 1).toUpperCase()}
                </span>
                <span>{auth.identity?.identity?.username || "Campus user"}</span>
                <ChevronDown size={14} />
              </button>
              {profileOpen && (
                <section className="topbar-popover popover-profile" aria-label="Account">
                  <strong>{auth.identity?.identity?.username || "Signed in"}</strong>
                  <span className="popover-subtitle">Verified CIT Nexus account</span>
                  <div className="profile-role-list">
                    {(auth.identity?.roles || []).map(role => (
                      <span key={role}>{role}</span>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="popover-action"
                    onClick={() => auth.logout()}
                  >
                    <LogOut size={14} /> Sign out
                  </button>
                </section>
              )}
            </div>
          </div>
        </header>

        {auth.status === "error" && (
          <div className="auth-error" role="alert">
            {auth.error}
          </div>
        )}

        {/* CAMPUS CRISIS MODE PAGE */}
        {active === "Examinations" ? (
          <ExaminationsPage />
        ) : active === "Timetable" ? (
          <TimetablePage
            key={timetableSearchRequest?.nonce || "timetable"}
            searchRequest={timetableSearchRequest}
          />
        ) : active === "Spectra Events" ? (
          <SpectraEventsPage auth={auth} />
        ) : active === "Faculty Requests" || active === "My Profile" ? (
          <AccountWorkflowPage roles={authRoles} />
        ) : active === "Dataset Management" ? (
          <StarterPackAdmin />
        ) : active === "Users & Roles" ? (
          <UnavailablePage
            title="Users & Roles"
            description="User administration is not available here. Authentication and verified roles remain managed by the existing identity provider."
          />
        ) : active === "Settings" ? (
          <UnavailablePage
            title="Settings"
            description="No application settings API is currently available."
          />
        ) : active === "Analytics" ? (
          <AnalyticsPage />
        ) : active === "Venue Booking" ? (
          <VenueBookingPage />
        ) : active === "AI Assistant" ? (
          <CampusAssistant />
        ) : active === "Campus Crisis Mode" ||
          active === "What-If Simulator" ? (

          <section style={{ padding: "25px 5px" }}>

            <div className="welcome">
              <div>
                <span className="eyebrow">
                  SIGNATURE INTELLIGENCE FEATURE
                </span>

                <h1>Campus Crisis Mode ⚡</h1>

                <p>
                  Detect disruptions, automatically reschedule
                  sessions and validate the updated timetable.
                </p>
              </div>

              <div className="semester-tag">
                Self-Healing Scheduler
              </div>
            </div>

            {/* CRISIS INTRODUCTION */}
            <div className="panel" style={{ marginBottom: 22 }}>
              <div className="panel-heading">
                <div>
                  <span className="crisis-label">
                    DISRUPTION MANAGEMENT
                  </span>

                  <h3>What happens during a campus disruption?</h3>

                  <p>
                    CIT Nexus regenerates the timetable when campus
                    resources become unavailable.
                  </p>
                </div>

                <Zap color="#d97706" size={32} />
              </div>

              <div className="module-list">
                <div>
                  <AlertCircle />
                  Faculty or room unavailability
                </div>

                <div>
                  <RefreshCw />
                  Automatic timetable regeneration
                </div>

                <div>
                  <ShieldCheck />
                  Official constraint validation
                </div>

                <div>
                  <Activity />
                  Schedule stability analysis
                </div>
              </div>
            </div>

            {/* WHAT-IF SIMULATOR */}
            <div id="what-if-panel" className="panel what-if-panel" style={{ marginBottom: 22 }}>
              <div className="panel-heading">
                <div>
                  <span className="crisis-label">WHAT-IF SIMULATOR</span>
                  <h3>Test a disruption before applying it</h3>
                  <p>
                    Preview a temporary schedule for a room closure or faculty
                    absence. The official timetable and reports will not change.
                  </p>
                </div>
                <Activity color="#2563eb" size={30} />
              </div>

              {whatIfOptionsError ? (
                <div className="what-if-error" role="alert">
                  <AlertCircle size={18} />
                  {whatIfOptionsError}
                </div>
              ) : (
                <>
                  <div className="what-if-grid">
                    <label className="what-if-field">
                      Disruption
                      <select
                        value={whatIfType}
                        disabled={!whatIfOptions}
                        onChange={event => {
                          const type = event.target.value;
                          setWhatIfType(type);
                          const resources = type === "room_unavailable"
                            ? whatIfOptions?.rooms
                            : whatIfOptions?.faculty;
                          setWhatIfResourceId(resources?.[0]?.id || "");
                          clearWhatIfPreview();
                        }}
                      >
                        <option value="room_unavailable">Room unavailable</option>
                        <option value="faculty_unavailable">Faculty unavailable</option>
                      </select>
                    </label>

                    <label className="what-if-field">
                      {whatIfType === "room_unavailable" ? "Room" : "Faculty"}
                      <select
                        value={whatIfResourceId}
                        onChange={event => {
                          setWhatIfResourceId(event.target.value);
                          clearWhatIfPreview();
                        }}
                        disabled={!whatIfOptions}
                      >
                        {(whatIfType === "room_unavailable"
                          ? whatIfOptions?.rooms
                          : whatIfOptions?.faculty
                        )?.map(resource => (
                          <option key={resource.id} value={resource.id}>
                            {resource.name} ({resource.id})
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="what-if-field">
                      Reason (optional)
                      <input
                        type="text"
                        maxLength={200}
                        value={whatIfReason}
                        onChange={event => {
                          setWhatIfReason(event.target.value);
                          clearWhatIfPreview();
                        }}
                        placeholder="e.g. Building maintenance"
                      />
                    </label>
                  </div>

                  <fieldset className="what-if-choice-group">
                    <legend>Affected days</legend>
                    <div className="what-if-checkboxes">
                      {whatIfOptions?.days.map(day => (
                        <label key={day}>
                          <input
                            type="checkbox"
                            checked={whatIfDays.includes(day)}
                            onChange={event => {
                              setWhatIfDays(current =>
                                event.target.checked
                                  ? [...current, day]
                                  : current.filter(value => value !== day)
                              );
                              clearWhatIfPreview();
                            }}
                          />
                          {day}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  <fieldset className="what-if-choice-group">
                    <legend>Affected teaching periods</legend>
                    <div className="what-if-checkboxes">
                      {whatIfOptions?.periods.map(period => (
                        <label key={period}>
                          <input
                            type="checkbox"
                            checked={whatIfPeriods.includes(period)}
                            onChange={event => {
                              setWhatIfPeriods(current =>
                                event.target.checked
                                  ? [...current, period]
                                  : current.filter(value => value !== period)
                              );
                              clearWhatIfPreview();
                            }}
                          />
                          P{period}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  <button
                    className="what-if-run-button"
                    onClick={runWhatIfSimulation}
                    disabled={
                      !whatIfOptions ||
                      !whatIfResourceId ||
                      whatIfDays.length === 0 ||
                      whatIfPeriods.length === 0 ||
                      whatIfLoading ||
                      crisisLoading
                    }
                  >
                    {whatIfLoading ? (
                      <>
                        <Loader2 size={18} />
                        Simulating...
                      </>
                    ) : (
                      <>
                        <Play size={18} />
                        Run What-If Simulation
                      </>
                    )}
                  </button>

                  {whatIfError && (
                    <div className="what-if-error" role="alert">
                      <AlertCircle size={18} />
                      {whatIfError}
                    </div>
                  )}
                </>
              )}

              {whatIfResult && (
                <div className="what-if-results" aria-live="polite">
                  <div
                    className={`what-if-result-banner ${
                      whatIfResult.validation.hard_constraints_satisfied
                        ? "success"
                        : "warning"
                    }`}
                  >
                    {whatIfResult.validation.hard_constraints_satisfied
                      ? <CheckCircle2 size={20} />
                      : <AlertCircle size={20} />}
                    <div>
                      <strong>Preview only — official timetable unchanged</strong>
                      <p>
                        Solver: {whatIfResult.validation.solver_status} ·{" "}
                        {whatIfResult.validation.hard_constraints_satisfied
                          ? "Hard constraints satisfied"
                          : "Hard constraints were not satisfied"}
                      </p>
                    </div>
                  </div>

                  <div className="what-if-summary-grid">
                    {[
                      {
                        label: "Original sessions",
                        value: whatIfResult.summary.original_sessions
                      },
                      {
                        label: "Hypothetical sessions",
                        value: whatIfResult.summary.revised_sessions
                      },
                      {
                        label: "Moved",
                        value: whatIfResult.summary.moved_sessions
                      },
                      {
                        label: "Unchanged",
                        value: whatIfResult.summary.unchanged_sessions
                      },
                      {
                        label: "Added",
                        value: whatIfResult.summary.added_sessions
                      },
                      {
                        label: "Removed",
                        value: whatIfResult.summary.removed_sessions
                      }
                    ].map(item => (
                      <div className="what-if-summary-card" key={item.label}>
                        <strong>{item.value}</strong>
                        <span>{item.label}</span>
                      </div>
                    ))}
                  </div>

                  {whatIfResult.moved.length === 0 ? (
                    <p className="what-if-empty">
                      No sessions needed to move for this disruption.
                    </p>
                  ) : (
                    <>
                      <div className="what-if-comparison">
                        <h4>Official vs hypothetical placements</h4>
                        <p>
                          Highlighted rows identify sessions whose day, period,
                          or room changed in this preview.
                        </p>
                        <div className="what-if-table-scroll">
                          <table className="what-if-comparison-table">
                            <thead>
                              <tr>
                                <th rowSpan="2">Session ID</th>
                                <th rowSpan="2">Course</th>
                                <th className="what-if-original-heading" colSpan="3">
                                  Official timetable
                                </th>
                                <th className="what-if-hypothetical-heading" colSpan="3">
                                  Hypothetical timetable
                                </th>
                                <th rowSpan="2">Recorded explanation</th>
                              </tr>
                              <tr>
                                <th>Day</th>
                                <th>Period</th>
                                <th>Room</th>
                                <th>Day</th>
                                <th>Period</th>
                                <th>Room</th>
                              </tr>
                            </thead>
                            <tbody>
                              {whatIfResult.moved.map(item => {
                                const explanation = item.explanation || {};
                                const evidenceType =
                                  item.explanation_type ||
                                  explanation.explanation_type ||
                                  "INSUFFICIENT_EVIDENCE";
                                const recordedExplanation =
                                  evidenceType !== "INSUFFICIENT_EVIDENCE" &&
                                  explanation.explanation
                                    ? explanation.explanation
                                    : "Insufficient evidence";

                                return (
                                  <tr key={item.session_id}>
                                    <td>{item.session_id}</td>
                                    <td>
                                      {item.course?.name ||
                                        explanation.course?.name ||
                                        "—"}
                                    </td>
                                    <td className="what-if-original-cell">
                                      {item.original.day}
                                    </td>
                                    <td className="what-if-original-cell">
                                      P{item.original.start_period}
                                    </td>
                                    <td className="what-if-original-cell">
                                      {item.original.room_id}
                                    </td>
                                    <td className="what-if-hypothetical-cell">
                                      {item.revised.day}
                                    </td>
                                    <td className="what-if-hypothetical-cell">
                                      P{item.revised.start_period}
                                    </td>
                                    <td className="what-if-hypothetical-cell">
                                      {item.revised.room_id}
                                    </td>
                                    <td>{recordedExplanation}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </div>

                      <div className="what-if-moved-list">
                        <h4>Rescheduled sessions and recorded evidence</h4>
                        {whatIfResult.moved.map(item => {
                          const explanation = item.explanation || {};
                          const evidence = Array.isArray(
                            explanation.hard_constraint_evidence
                          )
                            ? explanation.hard_constraint_evidence
                            : [];
                          const effects = Array.isArray(
                            explanation.shared_objective_effects
                          )
                            ? explanation.shared_objective_effects
                            : [];
                          const evidenceType =
                            item.explanation_type ||
                            explanation.explanation_type ||
                            "INSUFFICIENT_EVIDENCE";
                          const confidence =
                            item.confidence || explanation.confidence;

                          return (
                            <details
                              className="crisis-explanation-card"
                              key={item.session_id}
                            >
                              <summary>
                                <span>
                                  {item.session_id}
                                  {item.course?.name || explanation.course?.name
                                    ? ` — ${item.course?.name || explanation.course.name}`
                                    : ""}
                                </span>
                                <span className={`crisis-explanation-status ${evidenceType.toLowerCase()}`}>
                                  {evidenceType.replaceAll("_", " ")}
                                  {confidence ? ` · ${confidence} confidence` : ""}
                                </span>
                              </summary>
                              <div className="crisis-explanation-content">
                                <div className="crisis-explanation-placements">
                                  <p className="what-if-original-cell">
                                    <strong>Official:</strong>{" "}
                                    {item.original.day}, P{item.original.start_period},{" "}
                                    {item.original.room_id}
                                  </p>
                                  <p className="what-if-hypothetical-cell">
                                    <strong>Hypothetical:</strong>{" "}
                                    {item.revised.day}, P{item.revised.start_period},{" "}
                                    {item.revised.room_id}
                                  </p>
                                </div>

                                <h4>Recorded hard-constraint evidence</h4>
                                {evidence.length > 0 ? (
                                  <ul>
                                    {evidence.map((entry, index) => (
                                      <li key={`${entry.constraint}-${index}`}>
                                        {entry.constraint && (
                                          <strong>
                                            {entry.constraint.replaceAll("_", " ")}:{" "}
                                          </strong>
                                        )}
                                        {entry.detail}
                                        {entry.reason ? ` Reason: ${entry.reason}` : ""}
                                      </li>
                                    ))}
                                  </ul>
                                ) : evidenceType === "OPTIMIZATION_TRADE_OFF" ? (
                                  <p>
                                    No hard-constraint exclusion was recorded.
                                    This is an optimization trade-off, not proof
                                    of a single cause.
                                  </p>
                                ) : (
                                  <p>
                                    Insufficient evidence to explain why this
                                    session moved.
                                  </p>
                                )}

                                {evidenceType !== "INSUFFICIENT_EVIDENCE" &&
                                  explanation.explanation && (
                                    <>
                                      <h4>Explanation</h4>
                                      <p>{explanation.explanation}</p>
                                    </>
                                  )}

                                {effects.length > 0 && (
                                  <>
                                    <h4>Recorded shared objective effects</h4>
                                    <p>
                                      These are shared totals for the listed
                                      arrangement, not causes attributed to one
                                      session.
                                    </p>
                                    <ul>
                                      {effects.map((effect, index) => (
                                        <li key={`${effect.objective}-${index}`}>
                                          {effect.objective}
                                          {effect.scope
                                            ? ` · ${effect.scope.replaceAll("_", " ")}`
                                            : ""}
                                          {effect.group_id
                                            ? ` · ${effect.group_id}`
                                            : ""}
                                          {effect.day ? ` · ${effect.day}` : ""}
                                          {typeof effect.penalty_points === "number"
                                            ? `: ${effect.penalty_points} points`
                                            : ""}
                                        </li>
                                      ))}
                                    </ul>
                                  </>
                                )}
                              </div>
                            </details>
                          );
                        })}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            {/* RUN SIMULATION */}
            <div
              className="panel"
              style={{
                marginBottom: 22,
                border: "1px solid #d97706"
              }}
            >
              <div className="panel-heading">
                <div>
                  <h3>Run Campus Crisis Simulation</h3>

                  <p>
                    Execute the configured sample campus disruption
                    and generate a revised schedule.
                  </p>
                </div>

                <Zap color="#d97706" size={30} />
              </div>

              <div style={{ marginTop: 20 }}>
                {authRoles.includes("ADMIN") && <button
                  onClick={runCrisisMode}
                  disabled={crisisLoading || whatIfLoading}
                  style={{
                    background: crisisLoading ? "#64748b" : "#d97706",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: 8,
                    padding: "13px 22px",
                    fontWeight: 700,
                    fontSize: 15,
                    cursor: crisisLoading ? "not-allowed" : "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 10
                  }}
                >
                  {crisisLoading ? (
                    <>
                      <Loader2 size={18} />
                      Regenerating Schedule...
                    </>
                  ) : (
                    <>
                      <Play size={18} />
                      Run Crisis Simulation
                    </>
                  )}
                </button>}

                <button
                  onClick={loadCrisisReport}
                  disabled={reportLoading || crisisLoading}
                  style={{
                    marginLeft: 12,
                    background: "#334155",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: 8,
                    padding: "13px 22px",
                    fontWeight: 600,
                    cursor: "pointer"
                  }}
                >
                  {reportLoading
                    ? "Loading Report..."
                    : "Load Latest Report"}
                </button>
              </div>

              {crisisLoading && (
                <div
                  style={{
                    marginTop: 20,
                    padding: 15,
                    background: "#fff7ed",
                    borderRadius: 8,
                    color: "#9a3412"
                  }}
                >
                  <Loader2
                    size={18}
                    style={{
                      display: "inline",
                      verticalAlign: "middle",
                      marginRight: 8
                    }}
                  />
                  The scheduler is processing the disruption.
                  Please wait. Do not refresh the page.
                </div>
              )}

              {crisisError && (
                <div
                  style={{
                    marginTop: 20,
                    padding: 15,
                    background: "#fef2f2",
                    color: "#b91c1c",
                    borderRadius: 8
                  }}
                >
                  <AlertCircle
                    size={18}
                    style={{
                      display: "inline",
                      verticalAlign: "middle",
                      marginRight: 8
                    }}
                  />
                  <strong>Simulation Error:</strong> {crisisError}
                </div>
              )}
            </div>

            {/* VALIDATION RESULTS */}
            {crisisResult && (
              <div className="panel" style={{ marginBottom: 22 }}>

                <div className="panel-heading">
                  <div>
                    <span className="crisis-label">
                      SIMULATION RESULTS
                    </span>

                    <h3>Validation Report</h3>

                    <p>
                      {crisisResult.message ||
                        "Latest validation results"}
                    </p>
                  </div>

                  {hardViolationCount === 0 ? (
                    <CheckCircle2 color="#16a34a" size={30} />
                  ) : (
                    <AlertCircle color="#d97706" size={30} />
                  )}
                </div>

                {/* RESULT CARDS */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(150px, 1fr))",
                    gap: 15,
                    marginTop: 22
                  }}
                >
                  <div
                    style={{
                      background: "#f0fdf4",
                      padding: 18,
                      borderRadius: 10
                    }}
                  >
                    <h2 style={{ color: "#15803d" }}>
                      {hardViolationCount ?? "—"}
                    </h2>
                    <p>Hard Violations</p>
                  </div>

                  <div
                    style={{
                      background: "#eff6ff",
                      padding: 18,
                      borderRadius: 10
                    }}
                  >
                    <h2 style={{ color: "#1d4ed8" }}>
                      {crisisResult.data?.sessions_scheduled ?? "—"}
                    </h2>
                    <p>Sessions Scheduled</p>
                  </div>

                  <div
                    style={{
                      background: "#fff7ed",
                      padding: 18,
                      borderRadius: 10
                    }}
                  >
                    <h2 style={{ color: "#c2410c" }}>
                      {crisisResult.data?.bookings_processed ?? "—"}
                    </h2>
                    <p>Bookings Processed</p>
                  </div>

                  <div
                    style={{
                      background: "#f5f3ff",
                      padding: 18,
                      borderRadius: 10
                    }}
                  >
                    <h2 style={{ color: "#6d28d9" }}>
                      {softPenalty}
                    </h2>
                    <p>Soft Penalty</p>
                  </div>
                </div>

                {/* VALIDATION STATUS */}
                <div
                  style={{
                    marginTop: 22,
                    padding: 16,
                    borderRadius: 8,
                    background:
                      hardViolationCount === 0
                        ? "#f0fdf4"
                        : "#fff7ed",
                    color:
                      hardViolationCount === 0
                        ? "#166534"
                        : "#9a3412"
                  }}
                >
                  {hardViolationCount === 0 ? (
                    <>
                      <CheckCircle2
                        size={20}
                        style={{
                          display: "inline",
                          verticalAlign: "middle",
                          marginRight: 8
                        }}
                      />
                      <strong>
                        All hard constraints passed!
                      </strong>
                    </>
                  ) : hardViolationCount === null ? (
                    <>
                      <AlertCircle
                        size={20}
                        style={{
                          display: "inline",
                          verticalAlign: "middle",
                          marginRight: 8
                        }}
                      />
                      <strong>
                        Validation data is not available. Check the report below.
                      </strong>
                    </>
                  ) : (
                    <>
                      <AlertCircle
                        size={20}
                        style={{
                          display: "inline",
                          verticalAlign: "middle",
                          marginRight: 8
                        }}
                      />
                      <strong>
                        Check the validation report for violations.
                      </strong>
                    </>
                  )}
                </div>
{/* ORIGINAL VS REVISED TIMETABLE */}
{compareResult && (
  <div style={{ marginTop: 30, marginBottom: 30 }}>

    <div className="panel-heading">
      <div>
        <span className="crisis-label">
          SCHEDULE STABILITY ANALYSIS
        </span>

        <h3>Original vs Revised Timetable</h3>

        <p>
          See how CIT Nexus adapted the timetable after the disruption.
        </p>
      </div>

      <RefreshCw color="#d97706" size={28} />
    </div>

    {/* SUMMARY CARDS */}
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
        gap: 14,
        marginTop: 20
      }}
    >
      {[
        {
          label: "Original Sessions",
          value: compareResult.summary.original_sessions
        },
        {
          label: "Revised Sessions",
          value: compareResult.summary.revised_sessions
        },
        {
          label: "Rescheduled",
          value: compareResult.summary.moved_sessions
        },
        {
          label: "Unchanged",
          value: compareResult.summary.unchanged_sessions
        },
        {
          label: "Added",
          value: compareResult.summary.added_sessions
        },
        {
          label: "Removed",
          value: compareResult.summary.removed_sessions
        }
      ].map(item => (
        <div
          key={item.label}
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            padding: 16,
            borderRadius: 10
          }}
        >
          <h2
  style={{
    color: "#1e40af",
    fontSize: "26px",
    fontWeight: 800,
    opacity: 1,
    visibility: "visible",
    WebkitTextFillColor: "#1e40af",
    textAlign: "center",
    margin: "0 0 10px 0"
  }}
>
  {item.value}
</h2>
          <p>{item.label}</p>
        </div>
      ))}
    </div>

    {/* RESCHEDULED SESSIONS */}
    <div className="crisis-comparison-table" style={{ marginTop: 28 }}>
      <h3>Rescheduled Sessions</h3>

      <p style={{ marginBottom: 15, color: "#64748b" }}>
        {compareResult.moved.length} sessions received new timetable placements.
      </p>

      <div style={{ overflowX: "auto" }}>
       <table
  className="crisis-session-table"
  style={{
            width: "100%",
            minWidth: 700,
            borderCollapse: "collapse",
            fontSize: 13
          }}
        >
          <thead>
            <tr style={{ background: "#f1f5f9" }}>
              {[
                "Session ID",
                "Old Day",
                "Old Period",
                "Old Room",
                "New Day",
                "New Period",
                "New Room"
              ].map(heading => (
                <th
                  key={heading}
                  style={{
                    padding: 12,
                    textAlign: "left",
                    borderBottom: "1px solid #e2e8f0"
                  }}
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {compareResult.moved.map(item => (
              <tr key={item.session_id}>
                <td style={{ padding: 12, fontWeight: 700 }}>
                  {item.session_id}
                </td>

                <td style={{ padding: 12 }}>
                  {item.original.day}
                </td>

                <td style={{ padding: 12 }}>
                  P{item.original.start_period}
                </td>

                <td style={{ padding: 12 }}>
                  {item.original.room_id}
                </td>

                <td style={{ padding: 12, color: "#15803d", fontWeight: 600 }}>
                  {item.revised.day}
                </td>

                <td style={{ padding: 12, color: "#15803d", fontWeight: 600 }}>
                  P{item.revised.start_period}
                </td>

                <td style={{ padding: 12, color: "#15803d", fontWeight: 600 }}>
                  {item.revised.room_id}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>

    {/* PER-SESSION EXPLANATIONS */}
    <div className="crisis-explanations">
      <h3>Why These Sessions Were Rescheduled</h3>
      <p className="crisis-explanation-disclaimer">
        Explanations summarize recorded constraints and objective values; they
        are not proof that any one factor caused a solver decision.
      </p>

      {compareResult.moved.map(item => {
        const explanation = item.explanation || {};
        const hardEvidence = explanation.hard_constraint_evidence || [];
        const sharedEffects = explanation.shared_objective_effects || [];
        const contributions = explanation.objective_contributions || {};
        const originalRoom = explanation.rooms?.original;
        const revisedRoom = explanation.rooms?.revised;
        const evidenceStatus = explanation.explanation_type ||
          "INSUFFICIENT_EVIDENCE";
        const statusLabel = evidenceStatus.replaceAll("_", " ");

        return (
          <details
            className="crisis-explanation-card"
            key={item.session_id}
          >
            <summary>
              <span>
                {item.session_id}
                {" — "}
                {explanation.course?.name || "Course unavailable"}
              </span>
              <span className={`crisis-explanation-status ${evidenceStatus.toLowerCase()}`}>
                {statusLabel} · {explanation.confidence || "LOW"} confidence
              </span>
            </summary>

            <div className="crisis-explanation-content">
              <div className="crisis-explanation-placements">
                <p>
                  <strong>Original:</strong>{" "}
                  {item.original.day}, P{item.original.start_period},{" "}
                  {originalRoom?.name || item.original.room_id}
                  {" ("}{item.original.room_id}{")"}
                </p>
                <p>
                  <strong>New:</strong>{" "}
                  {item.revised.day}, P{item.revised.start_period},{" "}
                  {revisedRoom?.name || item.revised.room_id}
                  {" ("}{item.revised.room_id}{")"}
                </p>
                <p>
                  <strong>Change:</strong>{" "}
                  {item.change_type?.replaceAll("_", " ") || "Unavailable"}
                </p>
              </div>

              <p className="crisis-explanation-summary">
                {explanation.explanation || "Precise cause unavailable."}
              </p>

              <div className="crisis-explanation-impact">
                <h4>Impacts supported by schedule data</h4>
                <p>
                  <strong>Faculty:</strong>{" "}
                  {explanation.faculty?.name ||
                    explanation.faculty?.id ||
                    "Unavailable"}
                </p>
                <p>
                  <strong>Student groups:</strong>{" "}
                  {explanation.groups?.length
                    ? explanation.groups
                        .map(group => group.name || group.id)
                        .join(", ")
                    : "Unavailable"}
                  {explanation.student_impact?.session_size
                    ? ` (${explanation.student_impact.session_size} scheduled students)`
                    : ""}
                </p>
                <p>
                  <strong>Rooms:</strong>{" "}
                  {originalRoom?.name || item.original.room_id}
                  {" → "}
                  {revisedRoom?.name || item.revised.room_id}
                </p>
              </div>

              <div className="crisis-explanation-evidence">
                <h4>Hard-constraint evidence</h4>
                {hardEvidence.length > 0 ? (
                  <ul>
                    {hardEvidence.map((evidence, index) => (
                      <li key={`${evidence.constraint}-${index}`}>
                        <strong>{evidence.constraint.replaceAll("_", " ")}:</strong>{" "}
                        {evidence.detail}
                        {evidence.reason ? ` Reason: ${evidence.reason}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>
                    {evidenceStatus === "OPTIMIZATION_TRADE_OFF"
                      ? "No hard constraint excluded the original placement in the recorded checks."
                      : "Precise cause unavailable."}
                  </p>
                )}
              </div>

              <div className="crisis-explanation-objective">
                <h4>Measured per-session objective contributions</h4>
                {Object.entries(contributions).some(([, value]) => value) ? (
                  <ul>
                    {Object.entries(contributions)
                      .filter(([, value]) => value)
                      .map(([objective, value]) => (
                        <li key={objective}>
                          {objective === "stability_move_penalty"
                            ? "Stability move penalty"
                            : objective}
                          {": "}
                          {value} objective points
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p>No non-zero per-session contribution was recorded.</p>
                )}
              </div>

              <div className="crisis-explanation-shared">
                <h4>Shared group/day or course/day effects</h4>
                {sharedEffects.length > 0 ? (
                  <>
                    <p>
                      These are shared totals for the listed arrangement; they
                      are not attributed to this individual session.
                    </p>
                    <ul>
                      {sharedEffects.map((effect, index) => (
                        <li key={`${effect.objective}-${index}`}>
                          {effect.objective} · {effect.scope.replaceAll("_", " ")}
                          {effect.group_id ? ` · ${effect.group_id}` : ""}
                          {effect.course_id ? ` · ${effect.course_id}` : ""}
                          {` · ${effect.day}: ${effect.penalty_points} points`}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p>No non-zero shared S2, S3, or S4 effect was recorded for this placement.</p>
                )}
              </div>
            </div>
          </details>
        );
      })}
    </div>

    {/* ADDED SESSIONS */}
    {compareResult.added.length > 0 && (
      <div style={{ marginTop: 25 }}>
        <h3>Newly Added Sessions</h3>

        {compareResult.added.map(item => (
          <p
            key={item.session_id}
className="crisis-added-session"
style={{
  background: "#f0fdfa",
  padding: 14,
  borderRadius: 8
}}
          >
            <strong>{item.session_id}</strong>
            {" — "}
            {item.day}, Period {item.start_period}, Room {item.room_id}
          </p>
        ))}
      </div>
    )}

  </div>
)}
                {/* RAW REPORT */}
                <div style={{ marginTop: 25 }}>
                  <div className="panel-heading">
                    <div>
                      <h3>
                        <FileText
                          size={18}
                          style={{
                            display: "inline",
                            verticalAlign: "middle",
                            marginRight: 8
                          }}
                        />
                        Complete Validation Data
                      </h3>

                      <p>Official validator JSON response</p>
                    </div>
                  </div>

                  <pre
                    style={{
                      background: "#111827",
                      color: "#d1fae5",
                      padding: 18,
                      borderRadius: 8,
                      overflowX: "auto",
                      maxHeight: 450,
                      fontSize: 12,
                      lineHeight: 1.6,
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere"
                    }}
                  >
                    {JSON.stringify(crisisResult, null, 2)}
                  </pre>
                </div>
              </div>
            )}

          </section>

        ) : (
          <DashboardPage
            dataset={dataset}
            timetable={timetableData}
            loading={officialSchedule.loading || datasetLoading}
            error={officialSchedule.error || datasetError}
            backend={backend}
            username={auth.identity?.identity?.username}
            userRole={getDashboardRole(authRoles)}
            canAccessAnalytics={authRoles.includes("ADMIN")}
            onNavigate={setActive}
          />
        )}

        <footer>
          CIT Nexus © 2026 · Intelligent Campus Management
        </footer>

      </main>
    </div>
  );
}

function App() {
  const auth = useKeycloakAuth();
  const dashboardPath = getDashboardPath(auth.identity?.roles);
  const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";

  useEffect(() => {
    if (
      auth.status === "authenticated" &&
      dashboardPath &&
      currentPath !== dashboardPath
    ) {
      window.location.replace(dashboardPath);
    }
  }, [auth.status, currentPath, dashboardPath]);

  if (auth.status !== "authenticated" || !dashboardPath) return null;
  if (currentPath !== dashboardPath) {
    return (
      <main className="auth-loading" aria-live="polite">
        <h1>Opening your CIT Nexus dashboard</h1>
        <p>Redirecting based on your verified application role…</p>
      </main>
    );
  }

  return dashboardPath === "/admin"
    ? <AdminDashboard auth={auth} />
    : <MainApplication />;
}

export default App;