import csv
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as api
import app.api.analysis as analysis_api
import app.services.extraction as extraction
from app.schemas.analysis import ComplaintAnalysis, GeminiComplaintAnalysis, IssueCategory
from app.services.analysis_csv import process_csv
from app.services.gemini_provider import GeminiCallError, GeminiConfigurationError, get_api_key
from app.services.safety import apply_safety_checks
from app.services.routing import DEPARTMENT_MAPPING, map_department


def sample_analysis(**overrides):
    value = {
        "detected_language": "Kannada (Latin transliteration)",
        "normalized_summary": "Water has been unavailable for three days.",
        "issue_category": "water_supply",
        "issue_description": "The resident reports no water for three days.",
        "location_mentioned": None,
        "duration_mentioned": "3 dina",
        "urgency_signals": [],
        "safety_critical": False,
        "missing_information": [],
        "requires_human_review": False,
    }
    value.update(overrides)
    return value


def test_schema_accepts_allowed_category_and_rejects_unknown():
    result = ComplaintAnalysis.model_validate(sample_analysis())
    assert result.issue_category.value == "water_supply"
    with pytest.raises(ValidationError):
        ComplaintAnalysis.model_validate(sample_analysis(issue_category="department_routing"))


@pytest.mark.parametrize("value", ["", "PASTE_YOUR_API_KEY_HERE"])
def test_missing_and_placeholder_key(value, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", value)
    with pytest.raises(GeminiConfigurationError, match="missing or still the placeholder"):
        get_api_key()


@pytest.mark.parametrize("text", [
    "There is a live wire outside the school",
    "A person trapped in the lift",
    "The gas leak is near the entrance",
    "An open manhole is on the footpath",
    "The building may collapse",
])
def test_obvious_safety_phrases_flag_review(text):
    result = apply_safety_checks(text, ComplaintAnalysis.model_validate(sample_analysis()))
    assert result.safety_critical and result.requires_human_review
    assert result.urgency_signals


def test_safety_never_downgrades_model_flag_and_documents_negation_limit():
    flagged = ComplaintAnalysis.model_validate(sample_analysis(safety_critical=True, requires_human_review=True))
    assert apply_safety_checks("Routine water complaint", flagged).safety_critical
    # This known false-positive demonstrates that the starter keyword rule does not parse negation.
    negated = apply_safety_checks("There is no live wire here", ComplaintAnalysis.model_validate(sample_analysis()))
    assert negated.safety_critical


@pytest.mark.parametrize("category", list(IssueCategory))
def test_every_issue_category_has_deterministic_department_mapping(category):
    analysis = ComplaintAnalysis.model_validate(sample_analysis(issue_category=category.value))
    mapped = map_department(analysis)
    if category is IssueCategory.OTHER:
        assert mapped.assigned_department == "Municipal General Services"
        assert mapped.requires_human_review
    else:
        assert mapped.assigned_department == DEPARTMENT_MAPPING[category.value][0]
        assert mapped.routing_reason


def test_department_mapping_can_be_configured_and_unknown_uses_manual_fallback(monkeypatch):
    monkeypatch.setenv("CIVIC_DEPARTMENT_WATER_SUPPLY", "City Water Board")
    monkeypatch.setenv("CIVIC_MANUAL_REVIEW_DEPARTMENT", "Ward Review Desk")
    analysis = ComplaintAnalysis.model_validate(sample_analysis())
    assert map_department(analysis).assigned_department == "City Water Board"

    unknown = ComplaintAnalysis.model_construct(**sample_analysis(issue_category="unmapped"))
    mapped = map_department(unknown)
    assert mapped.assigned_department == "Ward Review Desk"
    assert mapped.requires_human_review
    assert "unknown or unmapped" in mapped.routing_reason


def test_safety_critical_always_requires_human_review_even_if_model_disagrees():
    analysis = ComplaintAnalysis.model_validate(sample_analysis(safety_critical=True, requires_human_review=False))
    mapped = map_department(apply_safety_checks("routine issue", analysis))
    assert mapped.safety_critical and mapped.requires_human_review


def test_multiple_issues_require_review_and_are_recorded():
    result = apply_safety_checks("No water and a large pothole", ComplaintAnalysis.model_validate(sample_analysis()))
    assert result.requires_human_review
    assert any("Multiple distinct issues" in item for item in result.missing_information)


def test_prompt_keeps_multilingual_original_and_marks_untrusted_data():
    original = "Namma street alli 3 dina inda neeru barthilla"
    prompt = extraction.build_prompt(original)
    assert original in prompt
    assert "Kannada" in prompt and "untrusted data" in prompt


def test_extraction_rejects_empty_text_without_calling_provider():
    def provider(_):
        raise AssertionError("provider must not be called")
    with pytest.raises(ValueError, match="must not be empty"):
        extraction.extract_complaint("   ", provider)


def test_extraction_converts_gemini_model_before_validation_and_routes_safety(monkeypatch):
    gemini_result = GeminiComplaintAnalysis.model_validate(sample_analysis(
        issue_category="electricity",
        safety_critical=True,
        requires_human_review=False,
    ))
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda _prompt: gemini_result)

    result = extraction.extract_complaint("Routine text")

    assert isinstance(result, ComplaintAnalysis)
    assert result.detected_language == gemini_result.detected_language
    assert result.normalized_summary == gemini_result.normalized_summary
    assert result.issue_category == gemini_result.issue_category
    assert result.issue_description == gemini_result.issue_description
    assert result.location_mentioned == gemini_result.location_mentioned
    assert result.duration_mentioned == gemini_result.duration_mentioned
    assert result.urgency_signals == gemini_result.urgency_signals
    assert result.safety_critical is True
    assert result.missing_information == gemini_result.missing_information
    assert result.requires_human_review is True
    assert result.assigned_department == "Electrical Services Department"
    assert result.routing_reason


def _write_csv(path: Path, rows, columns=None):
    columns = columns or ["complaint_id", "original_text", "custom_field"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_missing_csv_text_column_and_same_path_are_rejected(tmp_path):
    source = tmp_path / "source.csv"
    _write_csv(source, [{"complaint_id": "x", "description": "issue"}], ["complaint_id", "description"])
    with pytest.raises(ValueError, match="original_text"):
        process_csv(source, tmp_path / "out.csv", analyzer=lambda _: sample_analysis())
    with pytest.raises(ValueError, match="different"):
        process_csv(source, source)


def test_successful_csv_processing_preserves_input_and_original_fields(tmp_path):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    rows = [{"complaint_id": "c-1", "original_text": "ನೀರು ಮೂರು ದಿನಗಳಿಂದ ಬರುತ್ತಿಲ್ಲ", "custom_field": "preserve me"}]
    _write_csv(source, rows)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    result = process_csv(source, target, max_rows=5, analyzer=lambda _: sample_analysis())
    assert result["successful_analyses"] == 1 and result["failures"] == result["skipped_rows"] == 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with target.open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["original_text"] == rows[0]["original_text"] and row["custom_field"] == "preserve me"
    assert row["issue_category"] == "water_supply" and row["analysis_status"] == "ok"


def test_failed_model_and_empty_csv_rows_preserved(tmp_path):
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    _write_csv(source, [
        {"complaint_id": "c-1", "original_text": "complaint", "custom_field": "one"},
        {"complaint_id": "c-2", "original_text": "", "custom_field": "two"},
    ])
    def fails(_):
        raise GeminiCallError("rate_limited")
    result = process_csv(source, target, analyzer=fails)
    assert result["failures"] == 1 and result["skipped_rows"] == 1
    with target.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["analysis_status"] == "error" and "rate limit" in rows[0]["analysis_error"]
    assert rows[0]["original_text"] == "complaint" and rows[1]["analysis_status"] == "skipped_empty_text"


def test_api_single_analysis_and_csv_path_restrictions(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis_api, "DATA_DIR", tmp_path)
    client = TestClient(api.app)
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda prompt: GeminiComplaintAnalysis.model_validate(sample_analysis()))
    response = client.post("/analyze", json={"complaint_id": "demo-001", "original_text": "Namma street alli 3 dina inda neeru barthilla"})
    assert response.status_code == 200 and response.json()["analysis"]["issue_category"] == "water_supply"
    assert response.json()["analysis"]["assigned_department"] == "Water Supply Department"
    assert response.json()["analysis"]["routing_reason"]
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda prompt: GeminiComplaintAnalysis.model_validate(sample_analysis(safety_critical=True)))
    critical = client.post("/analyze", json={"complaint_id": "demo-critical", "original_text": "Routine text"})
    assert critical.status_code == 200 and critical.json()["analysis"]["requires_human_review"] is True
    _write_csv(tmp_path / "sample_complaints.csv", [{"complaint_id": "c-1", "original_text": "Water issue", "custom_field": "keep"}])
    batch = client.post("/analyze-csv", json={"input_path": "sample_complaints.csv", "output_path": "results.csv", "max_rows": 1})
    assert batch.status_code == 200 and batch.json()["successful_analyses"] == 1
    empty = client.post("/analyze", json={"complaint_id": "x", "original_text": " "})
    assert empty.status_code == 422
    denied = client.post("/analyze-csv", json={"input_path": "../../outside.csv", "output_path": "result.csv"})
    assert denied.status_code == 422


def test_api_sanitizes_provider_failure(monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda _: (_ for _ in ()).throw(GeminiCallError("rate_limited")))
    response = client.post("/analyze", json={"complaint_id": "x", "original_text": "water problem"})
    assert response.status_code == 429
    assert "key" not in response.text.lower() and "authorization" not in response.text.lower()


def test_health_does_not_call_gemini(monkeypatch):
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda _: (_ for _ in ()).throw(AssertionError("health called Gemini")))
    response = TestClient(api.app).get("/health")
    assert response.status_code == 200


def test_provider_failure_without_key_is_safe(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "PASTE_YOUR_API_KEY_HERE")
    from app.services.gemini_provider import analyze_with_gemini
    with pytest.raises(GeminiConfigurationError) as error:
        analyze_with_gemini("test")
    assert "PASTE_YOUR_API_KEY_HERE" not in str(error.value)


def test_rejected_gemini_key_is_classified_without_returning_provider_text(monkeypatch):
    from google import genai

    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")

    class FakeClientError(Exception):
        code = 400
        details = {"error": {"message": "API key not valid. Test-only sensitive detail."}}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        class models:
            @staticmethod
            def generate_content(**_kwargs):
                raise FakeClientError("provider response")

    monkeypatch.setattr(genai, "Client", FakeClient)
    from app.services.gemini_provider import analyze_with_gemini

    with pytest.raises(GeminiCallError) as error:
        analyze_with_gemini("test complaint")
    assert error.value.kind == "invalid_api_key"
    assert "sensitive" not in str(error.value).lower()


def test_invalid_key_api_response_is_safe(monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda _: (_ for _ in ()).throw(GeminiCallError("invalid_api_key")))
    response = client.post("/analyze", json={"complaint_id": "x", "original_text": "water problem"})
    assert response.status_code == 401
    assert "check GEMINI_API_KEY" in response.json()["detail"]
    assert "sensitive" not in response.text.lower()


def test_google_genai_sdk_client_is_mocked(monkeypatch):
    from google import genai
    from google.genai import types
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")
    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"]
            assert isinstance(kwargs["config"], types.GenerateContentConfig)
            assert kwargs["config"].response_mime_type == "application/json"
            assert kwargs["config"].response_json_schema == GeminiComplaintAnalysis.model_json_schema()
            assert "assigned_department" not in kwargs["config"].response_json_schema["properties"]
            return type("Response", (), {"parsed": None, "text": json.dumps(sample_analysis())})()
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "local-test-only"
            self.models = FakeModels()
    monkeypatch.setattr(genai, "Client", FakeClient)
    from app.services.gemini_provider import analyze_with_gemini
    assert analyze_with_gemini("test").issue_category.value == "water_supply"


def test_malformed_parsed_gemini_response_is_rejected(monkeypatch):
    from google import genai
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")

    class FakeModels:
        def generate_content(self, **_kwargs):
            return type("Response", (), {"parsed": sample_analysis(issue_category="unknown"), "text": None})()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    from app.services.gemini_provider import analyze_with_gemini

    with pytest.raises(GeminiCallError) as error:
        analyze_with_gemini("test complaint")
    assert error.value.kind == "invalid_structured_response"


@pytest.mark.parametrize(("code", "kind"), [
    (401, "invalid_api_key"),
    (403, "permission_denied"),
    (404, "model_not_found"),
    (429, "rate_limited"),
    (400, "invalid_request"),
    (503, "server_error"),
])
def test_gemini_sdk_failures_log_safe_diagnostics(monkeypatch, caplog, code, kind):
    import logging
    from google import genai
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")

    class FakeSDKError(Exception):
        message = "Provider said no; key=local-secret-value Bearer hidden-token"

        def __init__(self):
            self.code = code

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise FakeSDKError()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    from app.services.gemini_provider import analyze_with_gemini

    with caplog.at_level(logging.WARNING, logger="app.services.gemini_provider"):
        with pytest.raises(GeminiCallError) as error:
            analyze_with_gemini("test complaint")
    assert error.value.kind == kind
    assert "FakeSDKError" in caplog.text
    assert f"http_status={code}" in caplog.text
    assert "model=test-model" in caplog.text
    assert "Provider said no" in caplog.text
    assert "local-secret-value" not in caplog.text
    assert "hidden-token" not in caplog.text


def test_gemini_network_failure_is_classified_and_api_response_stays_safe(monkeypatch, caplog):
    import logging
    from google import genai
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")

    class FakeConnectionError(Exception):
        message = "Connection refused"

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise FakeConnectionError()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)
    from app.services.gemini_provider import analyze_with_gemini

    with caplog.at_level(logging.WARNING, logger="app.services.gemini_provider"):
        with pytest.raises(GeminiCallError) as error:
            analyze_with_gemini("test complaint")
    assert error.value.kind == "network_error"
    assert "Connection refused" in caplog.text

    monkeypatch.setattr(extraction, "analyze_with_gemini", lambda _: (_ for _ in ()).throw(GeminiCallError("permission_denied")))
    response = TestClient(api.app).post("/analyze", json={"complaint_id": "x", "original_text": "water problem"})
    assert response.status_code == 403
    assert "Connection refused" not in response.text
