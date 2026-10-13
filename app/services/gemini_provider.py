import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from app.schemas.analysis import GeminiComplaintAnalysis

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
logger = logging.getLogger(__name__)


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiCallError(RuntimeError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__(kind)


def get_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key or key == "PASTE_YOUR_API_KEY_HERE":
        raise GeminiConfigurationError("Gemini API key is missing or still the placeholder. Copy .env.example to .env and set GEMINI_API_KEY locally.")
    return key


def get_model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"


def _safe_provider_message(exc: Exception) -> str:
    """Use only the SDK's provider message and redact common credential forms."""
    message = getattr(exc, "message", None)
    if not isinstance(message, str) or not message.strip():
        return "No provider message available"
    message = " ".join(message.split())
    message = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED]", message)
    message = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", message)
    message = re.sub(r"(?i)((?:api[_ -]?)?key\s*[:=]\s*)\S+", r"\1[REDACTED]", message)
    return message[:1000]


def _http_status(exc: Exception):
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def analyze_with_gemini(prompt: str) -> GeminiComplaintAnalysis:
    """Call Gemini with provider-specific code isolated here; never expose SDK errors."""
    key = get_api_key()
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key, http_options={"timeout": 30000})
        response = client.models.generate_content(
            model=get_model_name(),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=GeminiComplaintAnalysis.model_json_schema(),
            ),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return GeminiComplaintAnalysis.model_validate(parsed)
        response_text = getattr(response, "text", None)
        if not response_text or not response_text.strip():
            raise GeminiCallError("empty_response")
        try:
            return GeminiComplaintAnalysis.model_validate_json(response_text)
        except (ValidationError, TypeError, ValueError) as exc:
            raise GeminiCallError("invalid_structured_response") from exc
    except GeminiCallError:
        raise
    except ValidationError as exc:
        raise GeminiCallError("invalid_structured_response") from exc
    except Exception as exc:
        name = type(exc).__name__.lower()
        safe_message = _safe_provider_message(exc)
        message = safe_message.lower()
        details = str(getattr(exc, "details", "")).lower()
        combined_error = f"{message} {details}"
        status = _http_status(exc)
        if status == 401 or any(phrase in combined_error for phrase in ("api key not valid", "invalid api key", "api_key_invalid")):
            kind = "invalid_api_key"
        elif status == 403 or "permissiondenied" in name or "permission denied" in combined_error:
            kind = "permission_denied"
        elif status == 404 or "not found" in combined_error and "model" in combined_error:
            kind = "model_not_found"
        elif status == 429 or "resourceexhausted" in name or "quota" in combined_error or "rate limit" in combined_error:
            kind = "rate_limited"
        elif status == 400 or "invalid argument" in combined_error or "bad request" in combined_error:
            kind = "invalid_request"
        elif "timeout" in name or "timeout" in message:
            kind = "timeout"
        elif status is not None and status >= 500:
            kind = "server_error"
        elif any(term in name for term in ("connection", "network", "transport", "socket", "ssl")):
            kind = "network_error"
        else:
            kind = "provider_error"
        logger.warning(
            "Gemini SDK call failed: exception_class=%s http_status=%s model=%s google_message=%s",
            type(exc).__name__, status if status is not None else "unavailable", get_model_name(), safe_message,
        )
        raise GeminiCallError(kind) from None
