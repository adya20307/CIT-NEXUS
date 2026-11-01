# Persistent workflow services

The workflow API stores timetable proposals/versions, profile mappings,
faculty requests, workflow notifications, and append-only workflow audit
events in PostgreSQL. It does not derive application roles from profile
fields: the verified Keycloak access token remains the authorization source.
Changing realm roles or activating accounts requires Keycloak Admin API
permissions, which are not enabled for the current frontend/API clients.

## Configure PostgreSQL

1. Copy the `DATABASE_URL` example from `backend/.env.example` into
   `backend/.env` and replace the placeholder with a PostgreSQL connection
   URL for an application database. Keep credentials local; do not put this
   setting in Vite environment variables or commit it.
2. Install backend requirements if they have not been restored:

   ```powershell
   Set-Location 'C:\Users\ANWESHA\OneDrive\Desktop\CIT-Nexus\backend'
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Apply additive migrations:

   ```powershell
   .\.venv\Scripts\python.exe migrate.py
   ```

The migration runner uses a PostgreSQL advisory lock and a schema-version
table. Migration `001_workflows.sql` only creates new tables, indexes, and an
append-only audit trigger; it does not drop or rewrite existing tables. The
application applies pending migrations lazily on the first workflow API call.

## Workflow APIs

- `GET/PUT /api/workflows/profile` — view and edit one's own profile fields.
- `GET /api/workflows/admin/profiles` and
  `PUT /api/workflows/admin/profiles/{subject_id}` — ADMIN profile search and mappings.
- `POST /api/workflows/timetable/proposals` — run OR-Tools, assemble the
  official schedule and validate it before storing a proposal.
- `GET /api/workflows/timetable/proposals` and
  `POST /api/workflows/timetable/proposals/{id}/decision` — compare and decide.
- `GET /api/workflows/timetable/current` and `/versions` — approved version.
- `GET /api/workflows/audit` — ADMIN-only search and pagination.
- `POST/GET /api/workflows/faculty-requests` — faculty request interpretation,
  solver alternatives, and owner-scoped status.
- `POST /api/workflows/faculty-requests/{id}/decision` — ADMIN decision and,
  on approval, a transactionally published timetable version.
- `GET /api/workflows/notifications` and
  `POST /api/workflows/notifications/{id}/read` — subject-scoped notifications.

The common `GET /api/timetable` endpoint reads the PostgreSQL-approved
timetable once a current workflow version has been established. Prior to the
first workflow proposal, it continues using the existing validated export.
After a faculty request or proposal is approved, all three timetable views
consume the same approved dataset snapshot and schedule.

## Identity and operational limitations

Profiles are created when a Keycloak-authenticated user calls the profile API.
ADMINs can map already registered FACULTY and STUDENT subjects to records in
the current dataset. The application cannot enumerate Keycloak users or change
their realm roles/account status with its present client configuration.
Student roster membership is not represented in the supplied dataset, so an
ADMIN mapping is administrative metadata, not proof of institutional
enrollment.

Spectra event records and their existing history remain in the current SQLite
store; this migration preserves that data and does not silently copy or delete
it. The new PostgreSQL audit log covers the new workflow operations. Extending
the same audit transaction to the legacy SQLite event store requires an
explicit cross-database/outbox migration and is not represented as atomic.
