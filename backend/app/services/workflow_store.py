import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_ROOT = BACKEND_ROOT / "migrations"
MIGRATIONS = ((1, "001_workflows.sql"),)
MIGRATION_LOCK_ID = 743281905


class WorkflowStoreUnavailable(RuntimeError):
    pass


def _database_url():
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise WorkflowStoreUnavailable("PostgreSQL DATABASE_URL is not configured.")
    if not value.startswith(("postgresql://", "postgres://")):
        raise WorkflowStoreUnavailable("DATABASE_URL must use PostgreSQL.")
    return value


@contextmanager
def connection():
    try:
        conn = psycopg.connect(_database_url(), row_factory=dict_row, connect_timeout=5)
    except WorkflowStoreUnavailable:
        raise
    except psycopg.Error as error:
        raise WorkflowStoreUnavailable("PostgreSQL workflow storage is unavailable.") from error
    try:
        with conn:
            yield conn
    except psycopg.Error as error:
        raise WorkflowStoreUnavailable("PostgreSQL workflow storage operation failed.") from error
    finally:
        conn.close()


def migrate():
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workflow_schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        applied = {
            row["version"]
            for row in conn.execute(
                "SELECT version FROM workflow_schema_migrations"
            ).fetchall()
        }
        for version, filename in MIGRATIONS:
            if version in applied:
                continue
            sql = (MIGRATIONS_ROOT / filename).read_text(encoding="utf-8")
            conn.execute(sql, prepare=False)
            conn.execute(
                "INSERT INTO workflow_schema_migrations (version) VALUES (%s)",
                (version,),
            )


def audit(
    conn,
    actor_subject,
    actor_roles,
    action,
    entity_type,
    entity_id,
    result="success",
    before=None,
    after=None,
    details=None,
):
    conn.execute(
        """
        INSERT INTO workflow_audit_log (
            actor_subject, actor_roles, action, entity_type, entity_id,
            before_state, after_state, result, details
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
        """,
        (
            actor_subject,
            list(actor_roles),
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
            result,
            json.dumps(details or {}),
        ),
    )


def create_baseline(conn, schedule, dataset_id, dataset_version_id, actor):
    current = conn.execute(
        """
        SELECT v.* FROM timetable_current c
        JOIN timetable_versions v ON v.id = c.version_id
        WHERE c.singleton = TRUE
        FOR UPDATE OF c
        """
    ).fetchone()
    if current:
        return current
    version_id = uuid.uuid4()
    version = conn.execute(
        """
        INSERT INTO timetable_versions (
            id, version_number, dataset_id, dataset_version_id,
            schedule, created_by, decision
        ) VALUES (%s, 1, %s, %s, %s::jsonb, %s, 'baseline')
        RETURNING *
        """,
        (
            version_id,
            dataset_id,
            dataset_version_id,
            json.dumps(schedule),
            actor,
        ),
    ).fetchone()
    conn.execute(
        "INSERT INTO timetable_current (singleton, version_id) VALUES (TRUE, %s)",
        (version_id,),
    )
    return version


def get_current_timetable():
    migrate()
    with connection() as conn:
        return conn.execute(
            """
            SELECT v.* FROM timetable_current c
            JOIN timetable_versions v ON v.id = c.version_id
            WHERE c.singleton = TRUE
              AND (
                    (v.decision = 'baseline' AND v.source_proposal_id IS NULL)
                    OR (
                        v.decision = 'approved'
                        AND EXISTS (
                            SELECT 1 FROM timetable_proposals p
                            WHERE p.id = v.source_proposal_id
                              AND p.status = 'approved'
                        )
                    )
              )
            """
        ).fetchone()
