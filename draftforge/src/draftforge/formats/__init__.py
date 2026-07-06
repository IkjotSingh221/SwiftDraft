"""Format specs: JSON-Schema-validated spec loading + programmatic compliance
checking (no LLM -- see spec.md constraint #4).
"""

from draftforge.formats.schema import (
    CaptionRule,
    CaptionRules,
    CitationStyle,
    FormatSpec,
    FormatSpecError,
    FrontMatterSpec,
    HeadingNumbering,
    SectionSpec,
    SpecSummary,
    WordRange,
    list_available_specs,
    load_spec,
    load_spec_from_dict,
)
from draftforge.formats.validator import (
    Severity,
    ValidationContext,
    Violation,
    ViolationCode,
    validate_document,
    validate_front_matter,
    validate_section,
)

__all__ = [
    "CaptionRule",
    "CaptionRules",
    "CitationStyle",
    "FormatSpec",
    "FormatSpecError",
    "FrontMatterSpec",
    "HeadingNumbering",
    "SectionSpec",
    "SpecSummary",
    "WordRange",
    "list_available_specs",
    "load_spec",
    "load_spec_from_dict",
    "Severity",
    "ValidationContext",
    "Violation",
    "ViolationCode",
    "validate_document",
    "validate_front_matter",
    "validate_section",
]
