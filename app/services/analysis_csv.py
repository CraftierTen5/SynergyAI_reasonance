import csv
import json
import os
import tempfile
from pathlib import Path

from app.schemas.analysis import ComplaintAnalysis
from app.services.extraction import extract_complaint
from app.services.gemini_provider import GeminiCallError, GeminiConfigurationError

ANALYSIS_FIELDS = [
    "detected_language", "normalized_summary", "issue_category", "issue_description",
    "location_mentioned", "duration_mentioned", "urgency_signals", "safety_critical",
    "missing_information", "requires_human_review", "assigned_department", "routing_reason",
    "analysis_status", "analysis_error",
]
TEXT_COLUMN = "original_text"


def _error_label(exc: Exception) -> str:
    if isinstance(exc, GeminiConfigurationError):
        return "configuration: Gemini API key missing or placeholder"
    if isinstance(exc, GeminiCallError):
        labels = {
            "invalid_api_key": "configuration: Gemini rejected the API key; check local .env",
            "timeout": "provider timeout; retry later",
            "rate_limited": "provider rate limit; retry later",
            "empty_response": "provider returned an empty response",
            "invalid_structured_response": "provider returned invalid structured data",
            "provider_error": "provider request failed",
        }
        return labels.get(exc.kind, "analysis failed")
    return "analysis failed validation"


def process_csv(input_path: str | Path, output_path: str | Path, max_rows: int | None = None, text_column: str = TEXT_COLUMN, analyzer=None) -> dict:
    source = Path(input_path).resolve()
    target = Path(output_path).resolve()
    if source == target:
        raise ValueError("Input and output CSV paths must be different")
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            if not fields or text_column not in fields:
                raise ValueError(f"Input CSV is missing required text column '{text_column}'")
            rows = list(reader)
    except FileNotFoundError as exc:
        raise ValueError("Input CSV file was not found") from exc
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError("Input CSV could not be read") from exc
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Input CSV contains malformed rows")

    selected = rows[:max_rows] if max_rows is not None else rows
    analyzer = analyzer or extract_complaint
    output_fields = list(fields)
    for field in ANALYSIS_FIELDS:
        if field not in output_fields:
            output_fields.append(field)
    counts = {"rows_read": len(selected), "successful_analyses": 0, "failures": 0, "skipped_rows": 0}
    output_rows = []
    for original in selected:
        row = dict(original)
        text = row.get(text_column) or ""
        if not text.strip():
            row.update({key: "" for key in ANALYSIS_FIELDS})
            row.update(analysis_status="skipped_empty_text", analysis_error="")
            counts["skipped_rows"] += 1
        else:
            try:
                analysis = ComplaintAnalysis.model_validate(analyzer(text))
                values = analysis.model_dump(mode="json")
                values["issue_category"] = analysis.issue_category.value
                values["urgency_signals"] = json.dumps(values["urgency_signals"], ensure_ascii=False)
                values["missing_information"] = json.dumps(values["missing_information"], ensure_ascii=False)
                values.update(analysis_status="ok", analysis_error="")
                row.update(values)
                counts["successful_analyses"] += 1
            except Exception as exc:
                row.update({key: "" for key in ANALYSIS_FIELDS})
                row.update(analysis_status="error", analysis_error=_error_label(exc))
                counts["failures"] += 1
        output_rows.append(row)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(output_rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {**counts, "output_path": str(target)}
