from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IssueCategory(str, Enum):
    WATER_SUPPLY = "water_supply"
    ROADS = "roads"
    DRAINAGE = "drainage"
    STREET_LIGHTING = "street_lighting"
    WASTE_MANAGEMENT = "waste_management"
    SANITATION = "sanitation"
    PUBLIC_SAFETY = "public_safety"
    ELECTRICITY = "electricity"
    OTHER = "other"


class ComplaintAnalysisFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detected_language: str = Field(min_length=1, max_length=100)
    normalized_summary: str = Field(min_length=1, max_length=1000)
    issue_category: IssueCategory
    issue_description: str = Field(min_length=1, max_length=2000)
    location_mentioned: str | None = Field(max_length=1000)
    duration_mentioned: str | None = Field(max_length=500)
    urgency_signals: list[str] = Field(default_factory=list, max_length=30)
    safety_critical: bool
    missing_information: list[str] = Field(default_factory=list, max_length=30)


class GeminiComplaintAnalysis(ComplaintAnalysisFields):
    requires_human_review: bool


class ComplaintAnalysis(GeminiComplaintAnalysis):
    assigned_department: str = ""
    routing_reason: str = ""


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complaint_id: str = Field(min_length=1, max_length=100)
    original_text: str = Field(min_length=1, max_length=10000)


class AnalyzeResponse(BaseModel):
    complaint_id: str
    original_text: str
    analysis: ComplaintAnalysis


class AnalyzeCSVRequest(BaseModel):
    input_path: str = "sample_complaints.csv"
    output_path: str = "analysis_results.csv"
    max_rows: int | None = Field(default=5, ge=1, le=500)

