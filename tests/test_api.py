import csv
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as api
from app.services.csv_storage import CSVStorage, CSVStorageError, FIELDS
from scripts.generate_sample_data import generate_records


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "sample_complaints.csv"
    storage = CSVStorage(path)
    storage.ensure_file()
    monkeypatch.setattr(api, "storage", storage)
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api, "ROOT", tmp_path)
    return TestClient(api.app), storage


def payload(**overrides):
    value = {"source_channel": "Web portal", "original_text": "नमूना शिकायत / sample issue", "language_code": "hi", "location_text": "Sector Q", "ward_id": "W-04", "submitted_at": datetime.now(timezone.utc).isoformat(), "idempotency_key": "test-1"}
    value.update(overrides)
    return value


def test_generator_has_500_records_and_reproducible_text():
    first, second = generate_records(), generate_records()
    assert len(first) == 500
    assert first == second
    assert {r["language_code"] for r in first} == {"en", "hi", "hinglish"}
    assert any("\u0900" <= c <= "\u097f" for r in first for c in r["original_text"])
    assert any(r["language_code"] == "hinglish" for r in first)


def test_headers_and_required_fields(tmp_path):
    rows = generate_records()
    path = tmp_path / "out.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, FIELDS); writer.writeheader(); writer.writerows(rows)
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == FIELDS
        row = next(reader)
    assert row["complaint_id"] and row["original_text"] and row["submitted_at"].endswith("+00:00")


def test_create_idempotency_retrieval_pagination_and_filters(client):
    c, _ = client
    first = c.post("/complaints", json=payload())
    assert first.status_code == 201
    assert first.json()["processing_status"] == "RECEIVED"
    repeated = c.post("/complaints", json=payload())
    assert repeated.json()["inserted"] is False
    cid = first.json()["complaint_id"]
    assert c.get(f"/complaints/{cid}").json()["original_text"].startswith("नमूना")
    c.post("/complaints", json=payload(idempotency_key="test-2", ward_id="W-05", source_channel="Phone helpline"))
    page = c.get("/complaints", params={"page": 1, "page_size": 1})
    assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
    filtered = c.get("/complaints", params={"ward_id": "W-05", "source_channel": "Phone helpline"})
    assert filtered.json()["total"] == 1


def test_bulk_invalid_records_and_dedupe(client):
    c, _ = client
    result = c.post("/complaints/bulk", json={"complaints": [payload(), {"source_channel": "bogus"}, payload()]})
    assert result.status_code == 200
    assert result.json()["inserted"] == 1 and result.json()["skipped"] == 1 and result.json()["failed"] == 1


def test_attachment_validation(client):
    c, _ = client
    accepted = c.post("/complaints", data={"payload": json.dumps(payload())}, files={"files": ("proof.pdf", b"%PDF fictional", "application/pdf")})
    assert accepted.status_code == 201
    bad = c.post("/complaints", data={"payload": json.dumps(payload(idempotency_key="bad-file"))}, files={"files": ("../evil.exe", b"x", "application/octet-stream")})
    assert bad.status_code == 422


def test_missing_and_malformed_csv(tmp_path):
    missing = CSVStorage(tmp_path / "missing.csv")
    with pytest.raises(FileNotFoundError): missing.read_all()
    bad_path = tmp_path / "bad.csv"
    bad_path.write_text("wrong,header\n", encoding="utf-8")
    with pytest.raises(CSVStorageError): CSVStorage(bad_path).read_all()
