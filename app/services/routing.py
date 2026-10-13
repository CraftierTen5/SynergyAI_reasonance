"""Deterministic mapping from extracted issue categories to municipal departments."""

import os
from pathlib import Path

from dotenv import load_dotenv

from app.schemas.analysis import ComplaintAnalysis, IssueCategory

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Defaults are generic examples; set CIVIC_DEPARTMENT_<CATEGORY> in .env to
# adapt each destination to a city's department names.
DEPARTMENT_MAPPING: dict[str, tuple[str, str]] = {
    IssueCategory.WATER_SUPPLY.value: ("Water Supply Department", "Water supply complaints are handled by the water utility."),
    IssueCategory.ROADS.value: ("Roads and Public Works Department", "Road and pothole repairs are handled by public works."),
    IssueCategory.DRAINAGE.value: ("Stormwater and Drainage Department", "Drainage issues are handled by the municipal drainage service."),
    IssueCategory.STREET_LIGHTING.value: ("Street Lighting Department", "Street lighting maintenance is handled by the lighting service."),
    IssueCategory.WASTE_MANAGEMENT.value: ("Solid Waste Management Department", "Waste collection and disposal are handled by solid waste management."),
    IssueCategory.SANITATION.value: ("Public Health and Sanitation Department", "Sanitation and public toilet issues are handled by public health and sanitation."),
    IssueCategory.PUBLIC_SAFETY.value: ("Municipal Public Safety Office", "Public safety complaints are routed to the municipal safety office."),
    IssueCategory.ELECTRICITY.value: ("Electrical Services Department", "Electrical infrastructure complaints are handled by electrical services."),
    IssueCategory.OTHER.value: ("", "This issue needs a general municipal review to determine the right department."),
}


def map_department(analysis: ComplaintAnalysis) -> ComplaintAnalysis:
    """Attach a configured department and enforce review for uncertain routing."""
    category = getattr(analysis.issue_category, "value", analysis.issue_category)
    general = os.getenv("CIVIC_GENERAL_DEPARTMENT", "Municipal General Services").strip()
    if not general:
        general = "Municipal General Services"
    manual = os.getenv("CIVIC_MANUAL_REVIEW_DEPARTMENT", general).strip() or general

    if category == IssueCategory.OTHER.value:
        department = os.getenv("CIVIC_DEPARTMENT_OTHER", manual).strip() or manual
        reason = DEPARTMENT_MAPPING[category][1]
        needs_review = True
    elif category in DEPARTMENT_MAPPING:
        default_department, reason = DEPARTMENT_MAPPING[category]
        setting_name = f"CIVIC_DEPARTMENT_{str(category).upper()}"
        department = os.getenv(setting_name, default_department).strip() or default_department
        needs_review = False
    else:
        department = manual
        reason = "The issue category is unknown or unmapped; manual municipal review is required."
        needs_review = True

    return analysis.model_copy(update={
        "assigned_department": department,
        "routing_reason": reason,
        "requires_human_review": analysis.requires_human_review or analysis.safety_critical or needs_review,
    })
