import re

from app.schemas.analysis import ComplaintAnalysis

# Prototype phrase checks only. These do not understand negation, surrounding context,
# Hindi/Kannada, transliteration, synonyms, or spelling variations comprehensively.
SAFETY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("live_wire", re.compile(r"\b(?:live wire|exposed (?:electrical )?wire)\b", re.I)),
    ("electric_shock", re.compile(r"\b(?:electric(?:al)? shock|got shocked)\b", re.I)),
    ("structural_danger", re.compile(r"\b(?:building collapse|building may collapse|structural danger|structure is unsafe)\b", re.I)),
    ("gas_leak", re.compile(r"\b(?:gas leak|leaking gas)\b", re.I)),
    ("person_trapped", re.compile(r"\b(?:person|someone|people) trapped\b", re.I)),
    ("open_manhole", re.compile(r"\b(?:open manhole|uncovered manhole)\b", re.I)),
)

# Coarse English starter vocabulary used only to catch obvious multi-issue reports.
ISSUE_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("water_supply", re.compile(r"\b(?:water|no water|water supply)\b", re.I)),
    ("roads", re.compile(r"\b(?:road|pothole|street surface)\b", re.I)),
    ("drainage", re.compile(r"\b(?:drain|sewer|drainage)\b", re.I)),
    ("street_lighting", re.compile(r"\b(?:street ?lights?|street lighting)\b", re.I)),
    ("waste_management", re.compile(r"\b(?:garbage|trash|rubbish|waste collection)\b", re.I)),
    ("sanitation", re.compile(r"\b(?:sanitation|public toilet|sewage smell)\b", re.I)),
    ("public_safety", re.compile(r"\b(?:unsafe|safety hazard|trapped|collapse)\b", re.I)),
    ("electricity", re.compile(r"\b(?:electricity|power outage|wire|electric shock)\b", re.I)),
)


def apply_safety_checks(text: str, analysis: ComplaintAnalysis) -> ComplaintAnalysis:
    matched = [name for name, pattern in SAFETY_RULES if pattern.search(text)]
    issue_groups = [name for name, pattern in ISSUE_TERMS if pattern.search(text)]
    multi_issue = len(issue_groups) > 1 and re.search(r"\b(?:and|also|plus|as well as)\b|&|और|तथा", text, re.I)
    needs_review = analysis.safety_critical or bool(matched) or bool(multi_issue)
    if not matched and not multi_issue and (not analysis.safety_critical or analysis.requires_human_review):
        return analysis
    signals = list(dict.fromkeys([*analysis.urgency_signals, *matched]))
    missing = list(analysis.missing_information)
    if multi_issue:
        missing.append("Multiple distinct issues may be present; review secondary issue(s).")
    return analysis.model_copy(update={
        "safety_critical": analysis.safety_critical or bool(matched),
        "requires_human_review": analysis.requires_human_review or needs_review,
        "urgency_signals": signals,
        "missing_information": list(dict.fromkeys(missing)),
    })
