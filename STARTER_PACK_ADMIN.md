# Crunch Week Starter Pack administration

The ADMIN-only **Dataset Management** workspace operates on the supplied
Crunch Week schema. It does not modify
`starter-pack/CrunchWeek_StarterPack/data/cit_public.json` or its CSV sources.
On first use, the API copies the source into
`backend/storage/starter_pack_management/active_dataset.json` and records
versioned snapshots alongside it.

## Admin workflow

1. Open **Dataset Management** and load the starter pack, or upload a
   `cit_public.json` working dataset / a supported CSV from
   `starter-pack/CrunchWeek_StarterPack/data/csv/`.
2. Explore records, select one in Dataset Editor, and save validated changes.
   Each save creates a new working-copy version; older versions can be
   restored without changing the source files.
3. Generate the timetable, exams, and Spectra booking decisions. The
   timetable and exam operations reuse the project's OR-Tools solvers;
   booking decisions reuse the starter-pack priority and cancellation rules.
4. Export `schedule.json`. The export is published only when the supplied
   `validator.py` reports zero hard violations.
5. To apply a disruption, upload a JSON file using the official disruption
   schema, then repair against the approved baseline. The repaired output is
   published as `exports/schedule_after.json` only if the official
   disruption validator reports zero hard violations.

The workspace provides Dataset Upload, Dataset Explorer, Dataset Editor,
Timetable Generator, Exam Scheduler, Spectra Booking Decisions, Disruption
Management, Schedule Validation, and Export Center pages. All API routes are
protected by the existing verified Keycloak principal and ADMIN role check.

## API surface

- `GET /api/admin/dataset` — active dataset metadata and version history
- `POST /api/admin/dataset/load-starter` — create a working copy from source
- `GET /api/admin/dataset/records/{entity}` and
  `PUT /api/admin/dataset/records/{entity}/{record_id}` — inspect/edit records
- `POST /api/admin/dataset/upload`, `POST /api/admin/dataset/import-csv`,
  and `POST /api/admin/dataset/validate` — import and validate working data
- `POST /api/admin/schedule/generate-timetable`,
  `POST /api/admin/schedule/generate-exams`, and
  `POST /api/admin/schedule/process-bookings` — generate real previews
- `POST /api/admin/schedule/export` and
  `POST /api/admin/schedule/validate` — create/check the official output
- `POST /api/admin/schedule/export-after-disruption` — revalidate and publish
  `schedule_after.json`
- `POST /api/admin/dataset/disruptions` and
  `POST /api/admin/schedule/disruption/repair` — apply and repair disruption
- `GET /api/admin/schedule/download/{filename}` — download a validated
  schedule export

The backend dependencies already include FastAPI and OR-Tools. Run the
backend from `backend` using the existing environment and run the frontend
with `npm run dev` from `frontend`.
