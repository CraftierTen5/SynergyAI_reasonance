from pathlib import Path

from pydantic import BaseModel

from app.schemas.analysis import ComplaintAnalysis, IssueCategory
from app.services.gemini_provider import analyze_with_gemini
from app.services.routing import map_department
from app.services.safety import apply_safety_checks

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "complaint_extraction.txt"


def build_prompt(original_text: str) -> str:
    instructions = PROMPT_PATH.read_text(encoding="utf-8").replace(
        "{permitted_categories}", ", ".join(category.value for category in IssueCategory)
    )
    return f"{instructions}\n\n<citizen_complaint_untrusted_data>\n{original_text}\n</citizen_complaint_untrusted_data>"


def extract_complaint(original_text: str, provider=None) -> ComplaintAnalysis:
    if not original_text or not original_text.strip():
        raise ValueError("Complaint text must not be empty")
    provider = provider or analyze_with_gemini
    provider_result = provider(build_prompt(original_text))
    analysis_data = provider_result.model_dump() if isinstance(provider_result, BaseModel) else provider_result
    analysis = ComplaintAnalysis.model_validate(analysis_data)
    return map_department(apply_safety_checks(original_text, analysis))
