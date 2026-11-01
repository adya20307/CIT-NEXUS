import csv
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.main import app
from app.services import dataset_loader, starter_pack_manager as manager


@pytest.fixture
def isolated_manager(tmp_path):
    root = tmp_path / "working"
    active = root / "active_dataset.json"
    versions = root / "versions"
    paths = {
        manager: {
            "ACTIVE_DATASET_PATH": active,
            "MANAGER_ROOT": root,
            "VERSIONS_ROOT": versions,
            "HISTORY_PATH": root / "history.json",
            "DISRUPTION_STATE_PATH": root / "disruption_state.json",
        },
        dataset_loader: {"ACTIVE_DATASET_PATH": active},
    }
    with patch.multiple(manager, **paths[manager]), patch.multiple(
        dataset_loader, **paths[dataset_loader]
    ):
        yield root


def test_starter_dataset_is_copied_and_versioned_without_modifying_source(isolated_manager):
    source_bytes = manager.DATASET_PATH.read_bytes()
    metadata = manager.load_starter_pack("admin")
    active = json.loads(manager.ACTIVE_DATASET_PATH.read_text(encoding="utf-8"))

    assert metadata["dataset_id"] == active["dataset_id"] == "cit-public-v1"
    assert manager.validate_dataset(active) == []
    assert manager.DATASET_PATH.read_bytes() == source_bytes
    assert manager.ACTIVE_DATASET_PATH != manager.DATASET_PATH
    assert (manager.VERSIONS_ROOT / f"{metadata['version_id']}.json").is_file()


def test_csv_import_preserves_starter_pack_types_and_headers(isolated_manager):
    manager.load_starter_pack("admin")
    csv_path = manager.CSV_ROOT / "rooms.csv"
    metadata = manager.import_csv(
        "rooms",
        csv_path.name,
        csv_path.read_text(encoding="utf-8"),
        "admin",
    )
    exported = manager.export_entity_csv("rooms")
    rows = list(csv.DictReader(exported.splitlines()))
    active = manager.load_dataset()

    assert metadata["dataset_id"] == active["dataset_id"]
    assert len(rows) == len(active["rooms"])
    assert isinstance(active["rooms"][0]["capacity"], int)
    assert isinstance(active["rooms"][0]["features"], list)
    assert manager.validate_dataset(active) == []


def test_dataset_validation_rejects_weakened_calendar_constraints():
    dataset = manager.load_original_dataset()
    dataset["calendar"]["periods"][9]["teaching"] = True
    lab_session = next(session for session in dataset["sessions"] if session["kind"] == "lab")
    lab_session["duration"] = 2
    errors = manager.validate_dataset(dataset)

    assert any("P10 through P13" in error for error in errors)
    assert any("three-hour duration" in error for error in errors)


def test_disruption_creates_a_new_working_version(isolated_manager):
    baseline = manager.load_starter_pack("admin")
    disruption_path = manager.STARTER_ROOT / "examples" / "sample_disruption.json"
    disruption = json.loads(disruption_path.read_text(encoding="utf-8"))
    applied = manager.apply_disruption_version(disruption, "admin")
    current = manager.load_dataset()
    state = manager.get_disruption_state()

    assert applied["version_id"] != baseline["version_id"]
    assert state["base_version_id"] == baseline["version_id"]
    assert state["applied_version_id"] == applied["version_id"]
    assert manager.validate_dataset(current) == []


def test_dataset_admin_routes_require_admin_role():
    client = TestClient(app)
    student = AuthenticatedPrincipal(
        issuer="test-issuer",
        subject="student",
        roles=("STUDENT",),
        departments=(),
        username="student",
    )
    try:
        assert client.get("/api/admin/dataset").status_code == 401
        app.dependency_overrides[get_current_principal] = lambda: student
        assert client.get("/api/admin/dataset").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_can_load_inspect_edit_and_validate_working_copy(isolated_manager):
    client = TestClient(app)
    admin = AuthenticatedPrincipal(
        issuer="test-issuer",
        subject="admin",
        roles=("ADMIN",),
        departments=(),
        username="admin",
    )
    app.dependency_overrides[get_current_principal] = lambda: admin
    try:
        loaded = client.post("/api/admin/dataset/load-starter")
        assert loaded.status_code == 200
        metadata = client.get("/api/admin/dataset")
        assert metadata.status_code == 200
        assert metadata.json()["record_counts"]["sessions"] == 189

        records = client.get("/api/admin/dataset/records/rooms")
        assert records.status_code == 200
        room = records.json()["records"][0]
        room["name"] = f"{room['name']} updated"
        edited = client.put(
            f"/api/admin/dataset/records/rooms/{room['id']}",
            json=room,
        )
        assert edited.status_code == 200
        closure = client.get("/api/admin/dataset/records/room_unavailability").json()
        closure_record = closure["records"][0]
        closure_record["reason"] = f"{closure_record['reason']} updated"
        closure_edit = client.put(
            f"/api/admin/dataset/records/room_unavailability/{closure['record_keys'][0]}",
            json=closure_record,
        )
        assert closure_edit.status_code == 200
        cancellation = client.get("/api/admin/dataset/records/cancellations").json()
        cancellation_record = cancellation["records"][0]
        cancellation_record["note"] = f"{cancellation_record['note']} updated"
        cancellation_edit = client.put(
            "/api/admin/dataset/records/cancellations/"
            f"{cancellation_record['request_id']}",
            json=cancellation_record,
        )
        assert cancellation_edit.status_code == 200
        assert client.post("/api/admin/dataset/validate").json()["valid"] is True
        assert metadata.json()["version_id"] != edited.json()["version"]["version_id"]
    finally:
        app.dependency_overrides.clear()
