import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path


DATABASE_PATH = (
    Path(__file__).resolve().parents[2]
    / "storage"
    / "spectra_events.sqlite3"
)


@contextmanager
def connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS spectra_events (
            id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            category TEXT NOT NULL,
            room_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            start_period INTEGER NOT NULL,
            end_period INTEGER NOT NULL,
            duration_periods INTEGER NOT NULL,
            expected_participants INTEGER NOT NULL CHECK (expected_participants > 0),
            required_features TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'approved', 'rejected', 'cancelled')
            ),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    event_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(spectra_events)")
    }
    if "required_features" not in event_columns:
        connection.execute(
            "ALTER TABLE spectra_events "
            "ADD COLUMN required_features TEXT NOT NULL DEFAULT '[]'"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS spectra_event_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL REFERENCES spectra_events(id),
            action TEXT NOT NULL CHECK (
                action IN ('created', 'approved', 'rejected', 'cancelled')
            ),
            actor TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            reason TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS spectra_event_modification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL REFERENCES spectra_events(id),
            action TEXT NOT NULL CHECK (action = 'rescheduled'),
            actor TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            reason TEXT NOT NULL,
            previous_event_json TEXT NOT NULL,
            updated_event_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS booking_action_proposals (
            id TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            action TEXT NOT NULL CHECK (
                action IN ('create', 'reschedule', 'cancel')
            ),
            booking_id TEXT,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'confirmed', 'dismissed', 'expired')
            ),
            result_event_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS booking_action_proposals_owner_status
        ON booking_action_proposals (created_by, status, expires_at)
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS spectra_event_history_no_update
        BEFORE UPDATE ON spectra_event_history
        BEGIN
            SELECT RAISE(ABORT, 'event history is append-only');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS spectra_event_history_no_delete
        BEFORE DELETE ON spectra_event_history
        BEGIN
            SELECT RAISE(ABORT, 'event history is append-only');
        END
        """
    )
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def insert_history(connection, event_id, action, actor, timestamp, reason):
    connection.execute(
        """
        INSERT INTO spectra_event_history
            (event_id, action, actor, timestamp, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, action, actor, timestamp, reason),
    )


def insert_modification_history(
    connection,
    event_id,
    actor,
    timestamp,
    reason,
    previous_event,
    updated_event,
):
    connection.execute(
        """
        INSERT INTO spectra_event_modification_history (
            event_id, action, actor, timestamp, reason,
            previous_event_json, updated_event_json
        ) VALUES (?, 'rescheduled', ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            actor,
            timestamp,
            reason,
            json.dumps(previous_event, separators=(",", ":"), sort_keys=True),
            json.dumps(updated_event, separators=(",", ":"), sort_keys=True),
        ),
    )


def event_record(row):
    if row is None:
        return None
    record = dict(row)
    features = record.get("required_features", "[]")
    if isinstance(features, str):
        record["required_features"] = json.loads(features)
    return record
