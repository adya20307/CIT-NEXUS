import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileUp,
  Loader2,
  RefreshCw,
  Save,
  Upload,
} from "lucide-react";
import { apiFetch } from "./auth/keycloakClient.js";
import "./StarterPackAdmin.css";

const PAGES = [
  ["upload", "Dataset Upload"],
  ["explorer", "Dataset Explorer"],
  ["editor", "Dataset Editor"],
  ["timetable", "Timetable Generator"],
  ["exams", "Exam Scheduler"],
  ["bookings", "Spectra Booking Decisions"],
  ["disruption", "Disruption Management"],
  ["validation", "Schedule Validation"],
  ["exports", "Export Center"],
];

const ENTITIES = [
  "rooms",
  "room_unavailability",
  "faculty",
  "student_groups",
  "courses",
  "sessions",
  "booking_requests",
  "cancellations",
  "exams",
];

async function requestJSON(path, options = {}) {
  const response = await apiFetch(path, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The API returned an invalid response (HTTP ${response.status}).`);
  }
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || payload.message;
    const errors = Array.isArray(detail?.errors) ? ` ${detail.errors.join(" ")}` : "";
    throw new Error(`${message || `Request failed (HTTP ${response.status}).`}${errors}`);
  }
  return payload;
}

function ActionButton({ children, onClick, disabled, secondary = false, type = "button" }) {
  return (
    <button
      type={type}
      className={`starter-action ${secondary ? "secondary" : ""}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

function ResultSummary({ result }) {
  if (!result) return null;
  if (typeof result.valid === "boolean") {
    return (
      <div className={`starter-result ${result.valid ? "success" : "failure"}`} role="status">
        <strong>{result.valid ? "Dataset validation passed." : "Dataset validation failed."}</strong>
        {!!result.errors?.length && <ul>{result.errors.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>}
      </div>
    );
  }
  if (result.validation) {
    const report = result.validation;
    const bookingStats = report.bookings || {};
    return (
      <div className={`starter-result ${report.valid ? "success" : "failure"}`} role="status">
        <strong>{report.valid ? "Official validator: valid" : "Official validator: violations found"}</strong>
        <div className="starter-result-grid">
          <span>Hard violations <b>{report.hard_violation_count ?? report.hard_violations?.length ?? 0}</b></span>
          <span>Soft penalty <b>{report.soft_total ?? "—"}</b></span>
          <span>Booking score <b>{report.booking_score ?? "—"}</b></span>
          <span>Priority inversions <b>{report.priority_inversions?.length ?? 0}</b></span>
          <span>Unscheduled sessions <b>{report.unscheduled_sessions ?? "—"}</b></span>
          <span>Unscheduled exams <b>{report.unscheduled_exams ?? "—"}</b></span>
          <span>Approved bookings <b>{bookingStats.approved ?? "—"}</b></span>
          <span>Alternatives <b>{bookingStats.alternatives ?? "—"}</b></span>
          <span>Rejected bookings <b>{bookingStats.rejected ?? "—"}</b></span>
        </div>
        {!!report.hard_violations?.length && (
          <details>
            <summary>Scheduling errors ({report.hard_violations.length})</summary>
            <ul>{report.hard_violations.slice(0, 100).map((item, index) => (
              <li key={`${item.rule}-${index}`}>[{item.rule}] {item.detail}</li>
            ))}</ul>
          </details>
        )}
        {result.export_path && <small>Export: {result.export_path}</small>}
      </div>
    );
  }
  return (
    <div className="starter-result success" role="status">
      <strong>{result.message || "Operation completed."}</strong>
      {result.version && (
        <p>Version {result.version.version_id} · Dataset {result.version.dataset_id}</p>
      )}
      {result.session_count != null && <p>Generated sessions: {result.session_count} · Solver: {result.solver_status}</p>}
      {result.exam_count != null && <p>Generated exams: {result.exam_count} · Solver: {result.solver_status}</p>}
    </div>
  );
}

function RecordsTable({ records, recordKeys = [], onSelect }) {
  const selectable = typeof onSelect === "function";
  const columns = useMemo(() => {
    const keys = new Set();
    records.slice(0, 8).forEach(record =>
      Object.keys(record).slice(0, 6).forEach(key => keys.add(key))
    );
    return [...keys].slice(0, 6);
  }, [records]);
  if (!records.length) return <p className="starter-empty">No records match this view.</p>;
  return (
    <div className="starter-table-wrap">
      <table className="starter-table">
        <thead><tr>{columns.map(key => <th key={key}>{key}</th>)}{selectable && <th>Action</th>}</tr></thead>
        <tbody>
          {records.map((record, index) => (
            <tr key={record.id ?? `${record.request_id}-${index}`}>
              {columns.map(key => (
                <td key={key}>{typeof record[key] === "object" ? JSON.stringify(record[key]) : String(record[key] ?? "")}</td>
              ))}
              {selectable && <td><button type="button" className="starter-link-button" onClick={() => onSelect(record, recordKeys[index])}>Select</button></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StarterPackAdmin() {
  const [page, setPage] = useState("upload");
  const [metadata, setMetadata] = useState(null);
  const [entity, setEntity] = useState("rooms");
  const [records, setRecords] = useState([]);
  const [selectedRecord, setSelectedRecord] = useState(null);
  const [recordKeys, setRecordKeys] = useState([]);
  const [selectedRecordKey, setSelectedRecordKey] = useState(null);
  const [recordDraft, setRecordDraft] = useState({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function refreshMetadata() {
    const data = await requestJSON("/api/admin/dataset");
    setMetadata(data);
    return data;
  }

  async function refreshRecords() {
    const data = await requestJSON(
      `/api/admin/dataset/records/${entity}?limit=500&search=${encodeURIComponent(search)}`
    );
    setRecords(data.records);
    setRecordKeys(data.record_keys || []);
  }

  useEffect(() => {
    let active = true;
    requestJSON("/api/admin/dataset")
      .then(data => { if (active) setMetadata(data); })
      .catch(loadError => { if (active) setError(loadError.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (page !== "explorer" && page !== "editor" && page !== "bookings") return;
    let active = true;
    requestJSON(`/api/admin/dataset/records/${entity}?limit=500&search=${encodeURIComponent(search)}`)
      .then(data => {
        if (active) {
          setRecords(data.records);
          setRecordKeys(data.record_keys || []);
        }
      })
      .catch(loadError => { if (active) setError(loadError.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, entity, search]);

  async function runAction(action, operation) {
    setBusyAction(action);
    setError("");
    setResult(null);
    try {
      const payload = await operation();
      setResult(payload);
      await refreshMetadata();
      if (page === "explorer" || page === "editor" || page === "bookings") {
        await refreshRecords();
      }
      return payload;
    } catch (actionError) {
      setError(actionError.message);
      return null;
    } finally {
      setBusyAction("");
    }
  }

  async function post(path, body) {
    return requestJSON(path, {
      method: "POST",
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  }

  async function uploadDataset(file) {
    if (!file) return;
    await runAction("upload", async () => post("/api/admin/dataset/upload", {
      filename: file.name,
      dataset: JSON.parse(await file.text()),
    }));
  }

  async function uploadCsv(file) {
    if (!file) return;
    await runAction("csv", async () => post("/api/admin/dataset/import-csv", {
      entity,
      filename: file.name,
      content: await file.text(),
    }));
  }

  async function uploadDisruption(file) {
    if (!file) return;
    const applied = await runAction("disruption", async () => post("/api/admin/dataset/disruptions", {
      filename: file.name,
      disruption: JSON.parse(await file.text()),
    }));
    if (applied) {
      setResult(applied);
    }
  }

  function chooseRecord(record, recordKey) {
    setSelectedRecord(record);
    setSelectedRecordKey(recordKey);
    setRecordDraft({ ...record });
  }

  async function saveRecord() {
    if (selectedRecordKey == null) {
      setError("Select a record before saving.");
      return;
    }
    const record = {};
    try {
      for (const [key, originalValue] of Object.entries(selectedRecord)) {
        const value = recordDraft[key];
        if (typeof originalValue === "boolean") {
          record[key] = Boolean(value);
        } else if (typeof originalValue === "number") {
          record[key] = Number(value);
        } else if (originalValue === null || typeof originalValue === "object") {
          record[key] = typeof value === "string" ? JSON.parse(value) : value;
        } else {
          record[key] = value;
        }
      }
    } catch {
      setError("Array and object fields must contain valid JSON.");
      return;
    }
    await runAction("save", async () => requestJSON(
      `/api/admin/dataset/records/${entity}/${encodeURIComponent(selectedRecordKey)}`,
      { method: "PUT", body: JSON.stringify(record) },
    ));
  }

  async function download(path, filename) {
    setBusyAction("download");
    setError("");
    try {
      const response = await apiFetch(path);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Download failed (HTTP ${response.status}).`);
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (downloadError) {
      setError(downloadError.message);
    } finally {
      setBusyAction("");
    }
  }

  const activePage = PAGES.find(([id]) => id === page)?.[1];
  const isBusy = Boolean(busyAction);

  return (
    <section className="starter-admin">
      <header className="starter-heading">
        <div>
          <span className="admin-eyebrow">CRUNCH WEEK STARTER PACK</span>
          <h1>Dataset Management</h1>
          <p>Versioned working data, existing OR-Tools scheduling, and the official validator.</p>
        </div>
        <ActionButton secondary disabled={loading || isBusy} onClick={() => runAction("refresh", refreshMetadata)}>
          {loading ? <Loader2 className="admin-spin" size={16} /> : <RefreshCw size={16} />} Refresh
        </ActionButton>
      </header>

      <div className="starter-meta">
        <span><b>Dataset ID</b>{metadata?.dataset_id || "Loading…"}</span>
        <span><b>Working version</b>{metadata?.version_id || "—"}</span>
        <span><b>Version history</b>{metadata?.history?.length ?? "—"}</span>
        <span><b>Source</b>{metadata?.original_is_read_only ? "Original kept read-only" : "—"}</span>
      </div>

      <nav className="starter-page-nav" aria-label="Starter-pack administration pages">
        {PAGES.map(([id, label]) => (
          <button
            type="button"
            key={id}
            className={page === id ? "active" : ""}
            aria-current={page === id ? "page" : undefined}
            onClick={() => {
              if (["explorer", "editor", "bookings"].includes(id)) setLoading(true);
              setPage(id);
              setError("");
              setResult(null);
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && <div className="starter-alert error" role="alert"><AlertCircle size={17} />{error}</div>}
      {result && <ResultSummary result={result} />}
      {loading && <div className="starter-loading" role="status"><Loader2 className="admin-spin" size={17} /> Loading dataset records…</div>}

      <div className="starter-page">
        <span className="admin-eyebrow">{activePage?.toUpperCase()}</span>
        {page === "upload" && (
          <>
            <h2>Load or import a dataset</h2>
            <p>Imports are validated and saved as a new working-copy version. The supplied source files are never modified.</p>
            <div className="starter-action-row">
              <ActionButton disabled={isBusy} onClick={() => runAction("load", () => post("/api/admin/dataset/load-starter"))}>
                {busyAction === "load" ? <Loader2 className="admin-spin" size={16} /> : <Upload size={16} />} Load Starter Pack
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => runAction("validate-dataset", () => post("/api/admin/dataset/validate"))}>
                {busyAction === "validate-dataset" ? <Loader2 className="admin-spin" size={16} /> : <CheckCircle2 size={16} />} Validate Dataset
              </ActionButton>
              <label className="starter-action secondary">
                <FileUp size={16} /> Upload cit_public.json
                <input type="file" accept=".json,application/json" onChange={event => uploadDataset(event.target.files?.[0])} />
              </label>
            </div>
            <div className="starter-field">
              <label htmlFor="starter-entity-upload">CSV entity</label>
              <select id="starter-entity-upload" value={entity} onChange={event => setEntity(event.target.value)}>
                {ENTITIES.map(item => <option key={item}>{item}</option>)}
              </select>
              <label className="starter-action secondary">
                <FileUp size={16} /> Upload supported CSV
                <input type="file" accept=".csv,text/csv" onChange={event => uploadCsv(event.target.files?.[0])} />
              </label>
            </div>
            <div className="starter-count-grid">
              {ENTITIES.map(item => <span key={item}><b>{metadata?.record_counts?.[item] ?? "—"}</b>{item.replaceAll("_", " ")}</span>)}
            </div>
            {!!metadata?.history?.length && (
              <section className="starter-history">
                <h3>Dataset version history</h3>
                <div className="starter-table-wrap"><table className="starter-table">
                  <thead><tr><th>Version</th><th>Dataset</th><th>Created</th><th>Change</th><th /></tr></thead>
                  <tbody>{metadata.history.slice(0, 20).map(item => (
                    <tr key={item.version_id}>
                      <td>{item.version_id}</td><td>{item.dataset_id}</td>
                      <td>{new Date(item.created_at).toLocaleString()}</td><td>{item.reason}</td>
                      <td><button type="button" className="starter-link-button" onClick={() => runAction("restore", () => post(`/api/admin/dataset/versions/${item.version_id}/restore`))}>Restore</button></td>
                    </tr>
                  ))}</tbody>
                </table></div>
              </section>
            )}
          </>
        )}

        {(page === "explorer" || page === "editor" || page === "bookings") && (
          <>
            <h2>{page === "explorer" ? "Explore dataset records" : page === "bookings" ? "Starter-pack booking decisions" : "Edit a dataset record"}</h2>
            {page === "bookings" && <p>Decisions use the source dataset priority, cancellation, room and period rules; no decisions are hardcoded.</p>}
            <div className="starter-field horizontal">
              <label htmlFor="starter-entity">Dataset entity</label>
              <select id="starter-entity" value={entity} onChange={event => { setLoading(true); setEntity(event.target.value); setSelectedRecord(null); setSelectedRecordKey(null); }}>
                {ENTITIES.map(item => <option key={item}>{item}</option>)}
              </select>
              <input aria-label="Search dataset records" placeholder="Search records" value={search} onChange={event => { setLoading(true); setSearch(event.target.value); }} />
              {page === "bookings" && (
                <ActionButton disabled={isBusy} onClick={() => runAction("bookings", () => post("/api/admin/schedule/process-bookings"))}>
                  {busyAction === "bookings" ? <Loader2 className="admin-spin" size={16} /> : <RefreshCw size={16} />} Process Bookings
                </ActionButton>
              )}
            </div>
            <RecordsTable records={records} recordKeys={recordKeys} onSelect={page === "editor" ? chooseRecord : undefined} />
            {page === "editor" && (
              <div className="starter-editor">
                <h3>{selectedRecord ? `Editing ${selectedRecord.id ?? "selected record"}` : "Select a record to edit"}</h3>
                {selectedRecord && (
                  <div className="starter-record-form">
                    {Object.entries(selectedRecord).map(([key, value]) => (
                      <label key={key} className={typeof value === "object" || value === null ? "wide" : ""}>
                        <span>{key}</span>
                        {typeof value === "boolean" ? (
                          <input
                            type="checkbox"
                            checked={Boolean(recordDraft[key])}
                            onChange={event => setRecordDraft(current => ({ ...current, [key]: event.target.checked }))}
                          />
                        ) : typeof value === "number" ? (
                          <input
                            type="number"
                            step="any"
                            value={recordDraft[key] ?? ""}
                            onChange={event => setRecordDraft(current => ({ ...current, [key]: event.target.value }))}
                          />
                        ) : value === null || typeof value === "object" ? (
                          <textarea
                            value={typeof recordDraft[key] === "string"
                              ? recordDraft[key]
                              : JSON.stringify(recordDraft[key], null, 2)}
                            onChange={event => setRecordDraft(current => ({ ...current, [key]: event.target.value }))}
                            spellCheck="false"
                          />
                        ) : (
                          <input
                            value={recordDraft[key] ?? ""}
                            onChange={event => setRecordDraft(current => ({ ...current, [key]: event.target.value }))}
                          />
                        )}
                      </label>
                    ))}
                  </div>
                )}
                <ActionButton disabled={isBusy || !selectedRecord} onClick={saveRecord}>
                  {busyAction === "save" ? <Loader2 className="admin-spin" size={16} /> : <Save size={16} />} Save Changes
                </ActionButton>
              </div>
            )}
            {page === "bookings" && result?.decisions && <RecordsTable records={result.decisions} />}
          </>
        )}

        {page === "timetable" && (
          <>
            <h2>Generate the weekly timetable</h2>
            <p>Runs the existing OR-Tools model on the active dataset, including teaching periods, lunch, labs, faculty limits and availability, rooms, accessibility, fixed slots, and group conflicts.</p>
            <ActionButton disabled={isBusy} onClick={() => runAction("timetable", () => post("/api/admin/schedule/generate-timetable"))}>
              {busyAction === "timetable" ? <Loader2 className="admin-spin" size={16} /> : <CheckCircle2 size={16} />} Generate Timetable
            </ActionButton>
            {result?.preview && <RecordsTable records={result.preview} />}
          </>
        )}

        {page === "exams" && (
          <>
            <h2>Generate exam allocations</h2>
            <p>Uses the existing exam scheduler with exam-week sessions, half-capacity room allocations and student-group conflict constraints.</p>
            <ActionButton disabled={isBusy} onClick={() => runAction("exams", () => post("/api/admin/schedule/generate-exams"))}>
              {busyAction === "exams" ? <Loader2 className="admin-spin" size={16} /> : <CheckCircle2 size={16} />} Generate Exams
            </ActionButton>
            {result?.validation?.hard_violations?.length > 0 && (
              <div className="starter-alert error">{result.validation.hard_violations.join("; ")}</div>
            )}
          </>
        )}

        {page === "disruption" && (
          <>
            <h2>Apply and repair a disruption</h2>
            <p>Upload an official disruption JSON using the starter-pack schema. The disruption is versioned against the working copy; repair requires an exported, validator-approved baseline schedule.</p>
            <label className="starter-action secondary">
              <FileUp size={16} /> Apply Disruption JSON
              <input type="file" accept=".json,application/json" onChange={event => uploadDisruption(event.target.files?.[0])} />
            </label>
            <ActionButton disabled={isBusy} onClick={() => runAction("repair", () => post("/api/admin/schedule/disruption/repair"))}>
              {busyAction === "repair" ? <Loader2 className="admin-spin" size={16} /> : <RefreshCw size={16} />} Repair Schedule
            </ActionButton>
          </>
        )}

        {page === "validation" && (
          <>
            <h2>Run the official validator</h2>
            <p>Validity is reported only from the supplied validator.py output. A generated schedule with hard violations is not marked valid.</p>
            <div className="starter-action-row">
              <ActionButton disabled={isBusy} onClick={() => runAction("validate", () => post("/api/admin/schedule/validate"))}>
                {busyAction === "validate" ? <Loader2 className="admin-spin" size={16} /> : <CheckCircle2 size={16} />} Validate Schedule
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => runAction("validate-after", () => post("/api/admin/schedule/validate?after_disruption=true"))}>
                Validate schedule_after.json
              </ActionButton>
            </div>
            {result?.validator_output && <pre className="starter-validator-output">{result.validator_output}</pre>}
          </>
        )}

        {page === "exports" && (
          <>
            <h2>Official schedule exports</h2>
            <p>Export requires timetable, booking and exam previews. Official files are written only after the supplied validator reports zero hard violations.</p>
            <div className="starter-action-row">
              <ActionButton disabled={isBusy} onClick={() => runAction("export", () => post("/api/admin/schedule/export"))}>
                {busyAction === "export" ? <Loader2 className="admin-spin" size={16} /> : <Save size={16} />} Export schedule.json
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => download("/api/admin/schedule/download/schedule.json", "schedule.json")}>
                <Download size={16} /> Download schedule.json
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => download("/api/admin/schedule/download/schedule_after.json", "schedule_after.json")}>
                <Download size={16} /> Download schedule_after.json
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => runAction("export-after", () => post("/api/admin/schedule/export-after-disruption"))}>
                {busyAction === "export-after" ? <Loader2 className="admin-spin" size={16} /> : <Save size={16} />} Export schedule_after.json
              </ActionButton>
              <ActionButton secondary disabled={isBusy} onClick={() => download("/api/admin/dataset/export/json", "cit_public-working.json")}>
                <Download size={16} /> Export working dataset
              </ActionButton>
            </div>
            <div className="starter-field horizontal">
              <label htmlFor="starter-csv-export">Export CSV entity</label>
              <select id="starter-csv-export" value={entity} onChange={event => setEntity(event.target.value)}>
                {ENTITIES.map(item => <option key={item}>{item}</option>)}
              </select>
              <ActionButton secondary disabled={isBusy} onClick={() => download(`/api/admin/dataset/export/csv/${entity}`, `${entity}.csv`)}>
                <Download size={16} /> Download {entity}.csv
              </ActionButton>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
