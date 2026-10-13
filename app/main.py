import json
import os
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.schemas.complaints import BulkInput, ComplaintInput
from app.api.analysis import router as analysis_router
from app.services.csv_storage import CSVStorage, CSVStorageError

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CIVICRESOLVE_DATA_DIR", ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
storage = CSVStorage(DATA_DIR / "sample_complaints.csv")
storage.ensure_file()
app = FastAPI(title="CivicResolve AI CSV Ingestion", version="1.0.0")
app.include_router(analysis_router)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED = {".png": {"image/png"}, ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"}, ".webp": {"image/webp"}, ".wav": {"audio/wav", "audio/x-wav"}, ".mp3": {"audio/mpeg"}, ".m4a": {"audio/mp4", "audio/x-m4a"}, ".pdf": {"application/pdf"}}


def _input(payload: dict) -> dict:
    model = ComplaintInput.model_validate(payload)
    data = model.model_dump(mode="json")
    data["submitted_at"] = model.submitted_at.isoformat()
    return data


async def _save_uploads(files: list[UploadFile]) -> list[str]:
    refs = []
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for upload in files:
        ext = Path(upload.filename or "").suffix.lower()
        if ext not in ALLOWED or upload.content_type not in ALLOWED[ext]:
            raise HTTPException(422, f"Unsupported attachment extension or MIME type: {ext}")
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Attachments must be non-empty and at most 10 MiB")
        name = f"{UUID(bytes=os.urandom(16))}{ext}"
        target = UPLOAD_DIR / name
        target.write_bytes(data)
        refs.append(target.relative_to(ROOT).as_posix())
    return refs


@app.post("/complaints", status_code=201)
async def create_complaint(request: Request):
    files = []
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            payload = form.get("payload")
            if not isinstance(payload, str):
                raise HTTPException(422, "Multipart requests require a JSON 'payload' field")
            parsed = json.loads(payload)
            files = [v for k, v in form.multi_items() if k == "files" and isinstance(v, StarletteUploadFile)]
        else:
            parsed = await request.json()
        valid = _input(parsed)
    except HTTPException:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    row, inserted = storage.add(valid, await _save_uploads(files))
    return {"complaint_id": row["complaint_id"], "processing_status": row["processing_status"], "inserted": inserted}


@app.post("/complaints/bulk")
def bulk_complaints(body: BulkInput):
    inserted = skipped = failed = 0
    errors = []
    for index, raw in enumerate(body.complaints):
        try:
            valid = _input(raw)
            _, was_inserted = storage.add(valid)
            inserted += int(was_inserted)
            skipped += int(not was_inserted)
        except (ValidationError, TypeError, ValueError) as exc:
            failed += 1
            errors.append({"index": index, "error": str(exc)})
    return {"inserted": inserted, "skipped": skipped, "failed": failed, "errors": errors}


@app.get("/complaints/{complaint_id}")
def get_complaint(complaint_id: UUID):
    rows = storage.read_all()
    row = next((r for r in rows if r["complaint_id"] == str(complaint_id)), None)
    if row is None:
        raise HTTPException(404, "Complaint not found")
    return row


@app.get("/complaints")
def list_complaints(source_channel: str | None = None, processing_status: str | None = None, ward_id: str | None = None,
                    submitted_from: date | None = None, submitted_to: date | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)):
    rows = storage.read_all()
    def match(row):
        if source_channel and row["source_channel"] != source_channel: return False
        if processing_status and row["processing_status"] != processing_status: return False
        if ward_id and row["ward_id"] != ward_id: return False
        try: submitted = datetime.fromisoformat(row["submitted_at"]).date()
        except ValueError: return False
        return not ((submitted_from and submitted < submitted_from) or (submitted_to and submitted > submitted_to))
    matched = [r for r in rows if match(r)]
    start = (page - 1) * page_size
    return {"items": matched[start:start + page_size], "page": page, "page_size": page_size, "total": len(matched)}


@app.get("/health")
def health():
    csv_ok = storage.path.is_file() and os.access(storage.path, os.R_OK | os.W_OK)
    uploads_ok = UPLOAD_DIR.exists() or os.access(DATA_DIR, os.W_OK)
    return {"status": "ok" if csv_ok and uploads_ok else "degraded", "data_files_accessible": csv_ok and uploads_ok}


@app.exception_handler(CSVStorageError)
async def storage_error_handler(_, exc: CSVStorageError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": str(exc)})
