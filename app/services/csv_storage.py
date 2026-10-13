import csv
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

FIELDS = ["complaint_id", "source_channel", "original_text", "language_code", "location_text", "ward_id", "latitude", "longitude", "submitted_at", "processing_status", "idempotency_key", "attachment_refs", "created_at", "updated_at"]
_thread_lock = threading.RLock()


class CSVStorageError(RuntimeError):
    pass


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _thread_lock, lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt
            if os.fstat(lock_file.fileno()).st_size == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class CSVStorage:
    def __init__(self, csv_path: str | Path):
        self.path = Path(csv_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _validate_header(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Complaint CSV does not exist: {self.path}")
        try:
            with self.path.open("r", encoding="utf-8", newline="") as f:
                header = next(csv.reader(f), None)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CSVStorageError(f"Unable to read complaint CSV: {exc}") from exc
        if header != FIELDS:
            raise CSVStorageError("Complaint CSV has a missing or malformed header")

    def read_all(self) -> list[dict[str, str]]:
        with _locked(self.path):
            self._validate_header()
            try:
                with self.path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if any(None in row or any(value is None for value in row.values()) for row in rows):
                    raise CSVStorageError("Complaint CSV contains malformed rows")
                return rows
            except (OSError, UnicodeError, csv.Error) as exc:
                raise CSVStorageError(f"Unable to parse complaint CSV: {exc}") from exc

    def append_many(self, records: list[dict]) -> None:
        with _locked(self.path):
            self._validate_header()
            try:
                with self.path.open("a", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="raise")
                    for row in records:
                        writer.writerow({key: row.get(key, "") if row.get(key) is not None else "" for key in FIELDS})
                    f.flush()
                    os.fsync(f.fileno())
            except (OSError, csv.Error) as exc:
                raise CSVStorageError(f"Unable to append complaint: {exc}") from exc

    def replace_all(self, records: list[dict]) -> None:
        with _locked(self.path):
            fd, tmp_name = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows({key: row.get(key, "") if row.get(key) is not None else "" for key in FIELDS} for row in records)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, self.path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)

    def ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _locked(self.path):
            if not self.path.exists():
                with self.path.open("w", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerow(FIELDS)

    def add(self, payload: dict, attachment_refs: list[str] | None = None) -> tuple[dict, bool]:
        with _locked(self.path):
            self._validate_header()
            try:
                with self.path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if any(None in row or any(value is None for value in row.values()) for row in rows):
                    raise CSVStorageError("Complaint CSV contains malformed rows")
            except (OSError, UnicodeError, csv.Error) as exc:
                raise CSVStorageError(f"Unable to parse complaint CSV: {exc}") from exc
            key = payload.get("idempotency_key")
            if key and (found := next((r for r in rows if r["idempotency_key"] == key), None)):
                return found, False
            now = datetime.now(timezone.utc).isoformat()
            row = {field: "" for field in FIELDS}
            row.update(payload)
            row.update(complaint_id=str(uuid4()), processing_status="RECEIVED", attachment_refs=json.dumps(attachment_refs or [], ensure_ascii=False), created_at=now, updated_at=now)
            with self.path.open("a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
                f.flush()
                os.fsync(f.fileno())
            return row, True
