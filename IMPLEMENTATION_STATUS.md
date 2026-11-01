# CIT Nexus Implementation Status

**Audit date:** 2026-11-01
**Overall result:** Starter Pack scheduling and exports remain implemented and validator-confirmed. The five requested persistent-workflow features now have implementation work in place, but database-backed and authenticated end-to-end behavior is **not verified** because PostgreSQL is not configured in this environment. Do not treat these features as fully accepted.

## 0. Role-based authentication update

The existing CIT Nexus realm defines `ADMIN`, `FACULTY`, and `STUDENT` as
**realm roles** in the signed `realm_access.roles` claim; the frontend client
has no application client roles. The application now redirects verified
accounts to `/admin`, `/faculty`, or `/student`, respectively. Multiple-role
accounts use Admin, then Faculty, then Student precedence. Accounts without a
recognized role receive Access Denied. The existing Keycloak login theme and
single sign-in flow are unchanged. FastAPI continues to validate the access
token and enforce role dependencies independently of frontend routing.

Automated role routing, token validation, and unauthorized API tests are
covered by the frontend and backend suites. **Manual login and redirect tests
with actual STUDENT, FACULTY, and ADMIN accounts were not run**: no test user
credentials were provided, and the realm export intentionally provisions no
users. Do not interpret automated token tests as real-account end-to-end
verification.

## 1. Initial repository audit

Inspected the application entry points, authentication setup, API routers, dataset manager, schedulers, crisis rescheduler, Gemini services, frontend navigation and dashboards, test inventory, dependency manifests, Starter Pack files, and current exports.

The project is an existing React/Vite frontend and FastAPI backend. It uses Keycloak for authentication, OR-Tools for scheduling, the supplied JSON/CSV Starter Pack, and Google Gemini. Dataset management uses versioned files under backend storage. Spectra event bookings use SQLite; there is no configured PostgreSQL application layer or migration system in the inspected code/dependencies. The Docker Compose file starts Keycloak, not the application database.

The supplied source files are under `starter-pack/CrunchWeek_StarterPack/`. The source dataset and CSV files are kept separate from the versioned administrator working copy.

## 2. Requested persistent workflow features

| Feature | Status | Evidence and remaining verification |
|---|---|---|
| Timetable approval/rejection workflow | IMPLEMENTED BUT NOT VERIFIED | PostgreSQL proposal/version APIs, diffs, reason-required rejection, notifications, stale-version protection, and Admin UI are implemented. Requires live database and real authenticated ADMIN verification. |
| Persistent audit history | PARTIALLY IMPLEMENTED | Append-only PostgreSQL audit table and admin search UI cover the new workflow operations. Legacy dataset, booking, exam, and disruption writes are not yet all captured. |
| Faculty scheduling request workflow | IMPLEMENTED BUT NOT VERIFIED | Gemini interpretation is advisory; OR-Tools generates candidate alternatives and official validation runs before persistence. Submission, decision, notifications, and status APIs are implemented; live DB/auth flow is unverified. |
| Student/faculty/admin profile management | PARTIALLY IMPLEMENTED | Own-profile updates and ADMIN profile search/mapping are implemented for users who have signed in. Keycloak directory enumeration, role changes, account activation, and institutional enrollment verification are unsupported. |
| PostgreSQL persistence | IMPLEMENTED BUT NOT VERIFIED | Additive schema and transactional store cover new workflows; migration, restart persistence, and concurrency were not tested because `DATABASE_URL` is absent. Existing event storage remains SQLite and dataset working copies remain file-backed. |

## 2. Feature status

| Requirement | Status | Evidence / limitation |
|---|---|---|
| Keycloak login integration and protected React application | **IMPLEMENTED BUT NOT TESTED** | React provider initializes Keycloak with PKCE and login-required behavior; local access-token refresh is performed before API requests. Local frontend and Keycloak endpoints responded HTTP 200, and the sign-in page rendered. No real user credentials were entered, so end-to-end login and role authorization were not verified in a browser. |
| Backend JWT verification and role checks | **COMPLETED AND TESTED** | Backend validates asymmetric signatures, issuer, audience, authorized party, expiry, and uppercase realm roles. Existing tests cover authentication and authorization, including unauthenticated and insufficient-role responses. |
| Student and faculty personal dashboards | **PARTIALLY IMPLEMENTED** | Added persisted self-profile APIs, admin profile mappings, faculty scheduling requests, and notification views. Student profiles can be mapped to dataset groups, but the starter pack contains no verified enrollment roster; personalized timetable filtering is not implemented. |
| Admin overview and existing admin modules | **PARTIALLY IMPLEMENTED** | Overview, dataset management, timetable, analytics, booking/event views, and assistant are present. The admin UI explicitly marks some modules unavailable. |
| Dataset upload, validation, editing, version history, and CSV support | **COMPLETED AND TESTED** | Uses the Starter Pack schema and versioned working copies; source files are not changed. JSON/CSV validation and record editing have backend tests. |
| Timetable generation | **COMPLETED AND TESTED** | Reuses the existing OR-Tools solver with actual dataset input. Current baseline export contains all 189 sessions and passes the official validator with zero hard violations. |
| Examination scheduling | **COMPLETED AND TESTED** | Reuses the existing scheduler. Current baseline export contains all 41 exams and has zero exam soft penalty in validator output. |
| Starter Pack booking decisions | **COMPLETED AND TESTED** | Existing priority/cancellation engine processes the dataset requests. Current export has 25 decisions, no priority inversions, and validator booking score 63. |
| Spectra venue request lifecycle | **COMPLETED AND TESTED** | Existing SQLite-backed API supports authenticated submissions, ownership checks, availability checks, rescheduling, administrator approval/rejection, and event history. This does not constitute a general PostgreSQL campus booking database. |
| Disruption solver and comparison | **COMPLETED AND TESTED** | Existing OR-Tools repair uses a stability objective and emits a comparison/explanation report. Current disruption export passes the supplied disruption validator and moves 11 sessions. |
| Disruption proposal approval before applying | **NOT IMPLEMENTED** | The current disruption workflow can generate/publish a validated disruption export; it does not provide the required proposal-review/approve/reject lifecycle for timetable changes. |
| Group, faculty, and room timetable views | **IMPLEMENTED BUT NOT TESTED** | Frontend provides selectors and filters for all three dataset-backed views. They share the published timetable data; user-specific filtering is not implemented. Build/tests passed, but an authenticated browser session was not exercised. |
| Gemini campus assistant and booking agent | **COMPLETED AND TESTED** | Authenticated assistant endpoints and confirmation-backed booking proposals exist. A live provider test returned a real assistant response through the configured fallback model after the primary model returned temporary 503 errors and exhausted its retries. This validates the fallback path only, not primary-model availability. |
| Gemini faculty scheduling assistant | **IMPLEMENTED BUT NOT VERIFIED** | Authenticated faculty requests are interpreted by Gemini; the existing crisis solver generates alternatives and the official validator checks the candidate. PostgreSQL-backed submission/approval and real authenticated provider flows could not be exercised. |
| General explainability | **PARTIALLY IMPLEMENTED** | Crisis repair emits evidence and objective-related explanations; the UI labels insufficient evidence. A general queryable explanation API for all timetable and booking decisions is not present. |
| General approval center, audit logs, timetable history | **IMPLEMENTED BUT NOT VERIFIED** | Added versioned proposals, original/proposed diffs, admin approval/rejection with required reasons, stale-version checks, notifications, and an append-only PostgreSQL audit log. Audit coverage is currently limited to the new workflow operations; existing dataset, Spectra booking, exam, and disruption writes are not all integrated. |
| User/profile management and notifications | **PARTIALLY IMPLEMENTED** | Self-service profiles, admin search/mapping for users who have signed in, and private notifications are implemented. Keycloak user-directory enumeration, realm-role changes, account activation, complaints, notices, and system settings are not implemented; no Admin API credentials are configured. |
| PostgreSQL persistence and migrations | **IMPLEMENTED BUT NOT VERIFIED** | Added psycopg-backed storage, additive migration `001_workflows.sql`, migration runner, foreign keys/indexes, and transactional workflow writes. `DATABASE_URL` is absent, so migration execution, persistence across restart, and concurrent approval behavior remain unverified. Legacy event bookings remain in SQLite and dataset working copies remain file-backed. |
| Official exports and validator | **COMPLETED AND TESTED** | Existing `exports/schedule.json` and `exports/schedule_after.json` were validated read-only with the supplied validator. Both have zero hard violations. |

## 3. Implemented APIs

The current API surface includes:

- Keycloak identity verification: `GET /api/auth/me`.
- Dataset administration, editing, upload/import, version restore, schedule generation, export, and validation under `/api/admin/*` (ADMIN-only).
- Authenticated assistant chat, booking agent, confirmation, and proposal listing under `/api/assistant/*`.
- Crisis rescheduling and what-if simulation under `/api/crisis/*`.
- Timetable and room utilization under `/api/timetable` and `/api/analytics/room-utilization`.
- Examination retrieval and exports under `/api/exams*`.
- Spectra event availability, requests, ownership-scoped reads, history, rescheduling, and admin decisions under `/api/spectra/events/*`.

Additional persistent workflow APIs are registered under `/api/workflows/*`: profile read/update and admin profile mapping; timetable proposal creation, listing, decisions, current version and history; admin audit search; faculty request creation/listing/admin decision; and subject-scoped notifications. All new routes use existing verified Keycloak principal and role dependencies. The full Keycloak user directory and role-management APIs are intentionally absent because the current client configuration does not provide Admin API access.

## 4. Files created or modified

### Previously implemented Starter Pack work

Created:

- `backend/app/services/starter_pack_manager.py`
- `backend/app/api/admin_dataset.py`
- `backend/tests/test_starter_pack_admin.py`
- `frontend/src/StarterPackAdmin.jsx`
- `frontend/src/StarterPackAdmin.css`
- `STARTER_PACK_ADMIN.md`

Modified:

- `.gitignore`
- `backend/app/services/dataset_loader.py`
- `backend/app/main.py`
- `backend/app/scheduler/timetable.py`
- `backend/app/scheduler/soft_objectives.py`
- `backend/app/rescheduler/crisis_engine.py`
- `backend/app/api/timetable.py`
- `frontend/src/AdminDashboard.jsx`
- `frontend/src/App.jsx`

### This audit

- Created this `IMPLEMENTATION_STATUS.md`.
- Sanitized the `GEMINI_API_KEY` sample in `backend/.env.example` to a placeholder. If the previous sample was an active credential, rotate it with the provider. No credential value is recorded here.

### Persistent workflow implementation

Created:

- `backend/migrations/001_workflows.sql`
- `backend/app/services/workflow_store.py`
- `backend/app/api/workflows.py`
- `backend/migrate.py`
- `backend/tests/test_workflows.py`
- `frontend/src/WorkflowPages.jsx`
- `frontend/src/WorkflowPages.css`
- `frontend/src/workflowApi.js`
- `WORKFLOW_PERSISTENCE.md`

Modified:

- `backend/requirements.txt` (added psycopg 3)
- `backend/.env.example` (PostgreSQL URL placeholder only)
- `backend/app/main.py`
- `backend/app/api/timetable.py`
- `frontend/src/AdminDashboard.jsx`
- `frontend/src/App.jsx`

The migration is additive and does not modify existing schedule exports or delete existing data. The migration itself has **not** been run against PostgreSQL.

## 5. Gemini and authentication verification

- The backend key and model settings were present; their values were not printed.
- One live request using the required example campus-assistant message received repeated temporary 503 responses from the primary model. The configured retry policy ran, then the fallback model returned a non-empty response. The successful response used `gemini-3.5-flash-lite`.
- A live Keycloak sign-in page and discovery endpoint were reachable. No credentials were submitted, so real token claims, authenticated browser requests, and ADMIN/FACULTY/STUDENT live role permissions remain unverified.
- Do not infer Gemini primary-model health from the successful fallback response.

## 6. Test and validator results

Automated suite results from the current project:

- Backend: `pytest` — **115 passed**, 4 Starlette deprecation warnings, 15 subtests passed.
- Frontend: `npm run lint` — passed.
- Frontend: `npm test` — **9 passed**.
- Frontend: `npm run build` — passed.
- Pylance diagnostics for `workflows.py`, `workflow_store.py`, and `WorkflowPages.jsx` — no errors.
- PostgreSQL integration/migration/restart/concurrency tests — **not run** (`DATABASE_URL` is not configured).
- Browser tests with real STUDENT/FACULTY/ADMIN Keycloak sessions — **not run** (no live test identities).

Current official validator results:

- Baseline `schedule.json`: **0 hard violations**, soft penalty **98**, booking score **63.0**, **0 priority inversions**, 189 sessions and 41 exams.
- Disruption `schedule_after.json`: **0 hard violations**, soft penalty **89**, booking score **63.0**, **0 priority inversions**, 190 session assignments and 41 exams; validator reports **11 sessions moved**.

These results validate the current exports and the existing automated tests. They do not prove that every end-to-end workflow in the master prompt exists.

## 7. Remaining work / blockers

The project is **not ready to claim full acceptance** until the following are addressed and tested:

1. Configure a valid local `DATABASE_URL`, apply `backend/migrate.py`, and verify proposals, requests, profiles, notifications, and audit rows survive backend/database restarts.
2. Run database-backed tests for approval/rejection, concurrent approvals, stale dataset/timetable versions, and transactional rollback.
3. Exercise real ADMIN, FACULTY, and STUDENT Keycloak accounts through browser/API workflows and verify unauthorized requests fail.
4. Wire persistent audit events into legacy dataset upload/edit, Spectra booking, examination, and disruption operations. These currently retain their existing file/SQLite stores; the new PostgreSQL audit records do not cover every such mutation.
5. Add institutional roster provisioning before relying on student-group mappings or exposing personalized student schedules.
6. Add Keycloak Admin API integration only after securely provisioning least-privilege service credentials; role changes and account activation are currently unsupported.
7. Test a real Gemini primary-model success when that provider/model is available; the last documented successful live answer used the fallback.

No institutional roster, role-management service account, notice policy, or complaint workflow was invented. See `WORKFLOW_PERSISTENCE.md` for local database configuration and the implemented endpoint list.

## 8. Local run commands

Start the backend from the project root:

```powershell
Set-Location 'C:\Users\ANWESHA\OneDrive\Desktop\CIT-Nexus\backend'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the frontend in a separate terminal:

```powershell
Set-Location 'C:\Users\ANWESHA\OneDrive\Desktop\CIT-Nexus\frontend'
npm run dev
```

Keycloak is configured through the repository Docker Compose setup and requires the local deployment variables documented by that compose file. Keep Gemini credentials in `backend/.env`; never place them in Vite environment variables or commit them.
