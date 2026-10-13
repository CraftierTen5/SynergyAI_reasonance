from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.schemas.analysis import AnalyzeCSVRequest, AnalyzeRequest, AnalyzeResponse
from app.services.analysis_csv import process_csv
from app.services.extraction import extract_complaint
from app.services.gemini_provider import GeminiCallError, GeminiConfigurationError

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
router = APIRouter()


def _data_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (DATA_DIR / candidate).resolve()
    try:
        resolved.relative_to(DATA_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(422, "CSV paths must be inside the project's data directory") from exc
    if resolved.suffix.lower() != ".csv":
        raise HTTPException(422, "CSV paths must use the .csv extension")
    return resolved


def _provider_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GeminiConfigurationError):
        return HTTPException(503, str(exc))
    if isinstance(exc, GeminiCallError):
        status = {"timeout": 504, "network_error": 502, "rate_limited": 429, "invalid_api_key": 401, "permission_denied": 403, "model_not_found": 404, "invalid_request": 400, "server_error": 502, "provider_error": 502, "empty_response": 502, "invalid_structured_response": 502}.get(exc.kind, 502)
        messages = {"timeout": "Gemini request timed out", "network_error": "Could not connect to Gemini", "rate_limited": "Gemini quota or rate limit reached; retry later", "invalid_api_key": "Gemini rejected the API key; check GEMINI_API_KEY in the local .env file", "permission_denied": "Gemini denied permission for this request", "model_not_found": "Configured Gemini model was not found or is unavailable", "invalid_request": "Gemini rejected the request; check the configured model and request settings", "server_error": "Gemini service returned a server error", "provider_error": "Gemini request failed", "empty_response": "Gemini returned an empty response", "invalid_structured_response": "Gemini returned invalid structured data"}
        return HTTPException(status, messages.get(exc.kind, "Analysis failed"))
    if isinstance(exc, ValidationError):
        return HTTPException(502, "Analysis result did not match the required schema")
    return HTTPException(502, "Analysis failed")


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_one(request: AnalyzeRequest):
    try:
        analysis = extract_complaint(request.original_text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise _provider_http_error(exc) from None
    return AnalyzeResponse(complaint_id=request.complaint_id, original_text=request.original_text, analysis=analysis)


@router.post("/analyze-csv")
def analyze_csv(request: AnalyzeCSVRequest):
    source = _data_path(request.input_path)
    target = _data_path(request.output_path)
    if source == target:
        raise HTTPException(422, "Input and output CSV paths must be different")
    try:
        return process_csv(source, target, request.max_rows)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError:
        raise HTTPException(500, "Could not safely write the analysis output CSV") from None
