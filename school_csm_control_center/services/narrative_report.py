"""Deterministic and validated narrative reports from Dashboard snapshots."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping


NARRATIVE_SCHEMA_VERSION = "1.0"
VALIDITY_STATEMENT_TEMPLATE = (
    "This Narrative Report is valid only when attached to or accompanied by "
    "the successful Dashboard Printout bearing Control Number "
    "{DASHBOARD_CONTROL_NUMBER}. Without the referenced Dashboard Printout, "
    "this Narrative Report shall be considered invalid."
)
SIGNATORY_ROLES = ("CSM Coordinator", "School Head / Head of Office")
SECTION_HEADINGS = {
    "executive_summary": "Executive Summary",
    "scope_and_methodology": "Scope and Methodology",
    "key_findings": "Key Findings",
    "strengths": "Strengths",
    "areas_for_improvement": "Areas for Improvement",
    "recommended_actions": "Recommended Actions",
    "conclusion": "Conclusion",
}
SECTION_ORDER = tuple(SECTION_HEADINGS)


class NarrativeValidationError(ValueError):
    """Raised when narrative content cannot be tied to its source snapshot."""


class UnknownNumericalClaimError(NarrativeValidationError):
    """Raised when prose contains a number absent from the source evidence."""


NARRATIVE_AI_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "executive_summary": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 3,
        },
        "scope_and_methodology": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 3,
        },
        "key_findings": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 6,
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 6,
        },
        "areas_for_improvement": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 6,
        },
        "recommended_actions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 8,
        },
        "conclusion": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 3,
        },
    },
    "required": list(SECTION_ORDER),
    "additionalProperties": False,
}


def validity_statement(control_number: str) -> str:
    control = " ".join(str(control_number or "").split())
    if not control:
        raise ValueError("Dashboard control number is required.")
    return VALIDITY_STATEMENT_TEMPLATE.format(DASHBOARD_CONTROL_NUMBER=control)


def build_narrative_source(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return aggregate-only evidence safe for local or optional AI use.

    Free-text feedback and recent individual-response rows are intentionally
    excluded. The narrative engine receives only the school/report identity,
    active filters, and aggregate Dashboard values.
    """

    if not isinstance(snapshot, Mapping):
        raise NarrativeValidationError("A verified Dashboard snapshot is required.")
    source = snapshot.get("source")
    analysis = snapshot.get("analysis")
    if not isinstance(source, Mapping) or not isinstance(analysis, Mapping):
        raise NarrativeValidationError("The Dashboard snapshot lacks source analysis.")
    overview = analysis.get("overview")
    if not isinstance(overview, Mapping) or not isinstance(
        overview.get("total_responses"), int
    ) or isinstance(overview.get("total_responses"), bool) or int(
        overview.get("total_responses", -1)
    ) < 0:
        raise NarrativeValidationError(
            "The Dashboard snapshot lacks a valid response total."
        )
    snapshot_id = _required_text(snapshot.get("snapshot_id"), "Snapshot ID")
    snapshot_sha = _required_digest(snapshot.get("digest_sha256"), "Snapshot digest")
    print_id = _required_text(source.get("print_record_id"), "Print record ID")
    control = _required_text(
        source.get("dashboard_control_number"),
        "Dashboard control number",
    )

    aggregate_analysis = {
        key: deepcopy(analysis[key])
        for key in (
            "overview",
            "dimensions",
            "response_mix",
            "trend",
            "citizen_charter",
            "demographics",
            "services",
            "sqd0",
        )
        if key in analysis
    }
    return {
        "source": {
            "print_record_id": print_id,
            "dashboard_control_number": control,
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_sha,
        },
        "school": _safe_school(snapshot.get("school")),
        "report": _safe_report(snapshot.get("report")),
        "filters": _safe_filters(snapshot.get("filters")),
        "analysis": aggregate_analysis,
    }


def compose_narrative_document(
    snapshot: Mapping[str, Any],
    section_content: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    """Add mandatory source, validity, and signature fields to section prose."""

    source = build_narrative_source(snapshot)
    sections = []
    for key in SECTION_ORDER:
        raw_items = section_content.get(key) if isinstance(section_content, Mapping) else None
        items = _clean_items(raw_items, key)
        sections.append(
            {
                "key": key,
                "heading": SECTION_HEADINGS[key],
                "paragraphs": items if key in _PARAGRAPH_SECTIONS else [],
                "bullets": items if key not in _PARAGRAPH_SECTIONS else [],
            }
        )
    control = source["source"]["dashboard_control_number"]
    document = {
        "schema_version": NARRATIVE_SCHEMA_VERSION,
        "title": "Client Satisfaction Measurement Narrative Report",
        "dashboard_control_number": control,
        "source": deepcopy(source["source"]),
        "validity_statement": validity_statement(control),
        "sections": sections,
        "signatories": [
            {"role": role, "name": "", "signature_date": ""}
            for role in SIGNATORY_ROLES
        ],
    }
    validate_narrative_document(document, snapshot)
    return document


class LocalNarrativeGenerator:
    """Generate the same narrative every time for the same source snapshot."""

    method = "local"

    def generate(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        source = build_narrative_source(snapshot)
        analysis = source["analysis"]
        overview = _mapping(analysis.get("overview"))
        response_mix = _mapping(analysis.get("response_mix"))
        dimensions = _mapping_rows(analysis.get("dimensions"))
        trend = _mapping_rows(analysis.get("trend"))
        services = _mapping_rows(analysis.get("services"))
        charter = _mapping(analysis.get("citizen_charter"))

        total = _number(overview.get("total_responses"), default=0)
        onsite = _number(overview.get("onsite_responses"), default=0)
        online = _number(overview.get("online_responses"), default=0)
        valid = _number(overview.get("valid_sqd_answers"), default=0)
        positive = _number(overview.get("positive_sqd_answers"), default=0)
        rate = overview.get("positive_rate")
        rating = _text(overview.get("rating")) or "No Data"

        executive = [
            (
                f"The Dashboard summarizes {_fmt(total)} survey responses: "
                f"{_fmt(onsite)} onsite and {_fmt(online)} online."
            )
        ]
        if rate is None:
            executive.append(
                "No overall positive-response rate is available because the "
                "snapshot contains no valid SQD answers."
            )
        else:
            executive.append(
                f"Of {_fmt(valid)} valid SQD answers, {_fmt(positive)} were "
                f"positive, resulting in a {_fmt(rate)}% positive-response rate "
                f"and a performance rating of {rating}."
            )

        scope = [
            "This report interprets the aggregate values preserved with the "
            "referenced successful Dashboard print; it does not recalculate "
            "results from current live records.",
            "Positive response rate is based on Agree and Strongly Agree "
            "responses divided by valid non-N/A answers.",
        ]
        period = _mapping(overview.get("period"))
        if period.get("from") and period.get("to"):
            scope.append(
                f"The preserved response period runs from {period['from']} to {period['to']}."
            )

        findings: list[str] = []
        coverage = response_mix.get("answer_coverage_rate")
        if coverage is not None:
            findings.append(
                f"SQD answer coverage in the preserved dataset is {_fmt(coverage)}%."
            )
        scored_dimensions = [row for row in dimensions if row.get("positive_rate") is not None]
        ranked_dimensions = sorted(
            scored_dimensions,
            key=lambda row: (-float(row["positive_rate"]), _dimension_name(row).casefold()),
        )
        if ranked_dimensions:
            highest = ranked_dimensions[0]
            lowest = min(
                ranked_dimensions,
                key=lambda row: (float(row["positive_rate"]), _dimension_name(row).casefold()),
            )
            findings.append(
                f"{_dimension_name(highest)} has the highest positive-response "
                f"rate at {_fmt(highest['positive_rate'])}%."
            )
            findings.append(
                f"{_dimension_name(lowest)} has the lowest positive-response "
                f"rate at {_fmt(lowest['positive_rate'])}%."
            )
        if trend:
            latest = trend[-1]
            if latest.get("positive_rate") is not None:
                label = _text(latest.get("label") or latest.get("period"))
                findings.append(
                    f"The latest preserved trend period, {label}, records a "
                    f"positive-response rate of {_fmt(latest['positive_rate'])}%."
                )
        awareness = charter.get("awareness_rate")
        if awareness is not None:
            findings.append(
                f"Citizen's Charter awareness is {_fmt(awareness)}% among valid responses."
            )

        strengths: list[str] = []
        improvements: list[str] = []
        recommendations: list[str] = []
        if ranked_dimensions:
            highest = ranked_dimensions[0]
            lowest = min(
                ranked_dimensions,
                key=lambda row: (float(row["positive_rate"]), _dimension_name(row).casefold()),
            )
            strengths.append(
                f"Sustain the practices supporting {_dimension_name(highest)}, "
                f"which recorded {_fmt(highest['positive_rate'])}% positive responses."
            )
            improvements.append(
                f"Prioritize review of {_dimension_name(lowest)}, which recorded "
                f"{_fmt(lowest['positive_rate'])}% positive responses."
            )
            recommendations.append(
                f"Assign a responsible team to review processes affecting {_dimension_name(lowest)}."
            )
        else:
            strengths.append("The preserved snapshot provides a traceable baseline for future review.")
            improvements.append("Collect enough valid SQD answers to support dimension-level findings.")
            recommendations.append("Continue collecting complete and valid survey responses.")

        scored_services = [row for row in services if row.get("positive_rate") is not None]
        if scored_services:
            strongest_service = max(
                scored_services,
                key=lambda row: (float(row["positive_rate"]), _service_name(row).casefold()),
            )
            weakest_service = min(
                scored_services,
                key=lambda row: (float(row["positive_rate"]), _service_name(row).casefold()),
            )
            strengths.append(
                f"{_service_name(strongest_service)} is the strongest scored service "
                f"at {_fmt(strongest_service['positive_rate'])}% positive responses."
            )
            improvements.append(
                f"{_service_name(weakest_service)} is the lowest scored service "
                f"at {_fmt(weakest_service['positive_rate'])}% positive responses."
            )
            recommendations.append(
                f"Review service-delivery evidence for {_service_name(weakest_service)} "
                "and document corrective actions."
            )
        recommendations.append(
            "Monitor the same preserved indicators in subsequent successful Dashboard prints."
        )

        conclusion = [
            "The findings and recommended actions in this narrative are limited "
            "to the immutable aggregate evidence identified by the referenced "
            "Dashboard control number."
        ]
        if rate is not None:
            conclusion.append(
                f"The preserved overall result is {_fmt(rate)}% positive with a "
                f"performance rating of {rating}."
            )

        return compose_narrative_document(
            snapshot,
            {
                "executive_summary": executive,
                "scope_and_methodology": scope,
                "key_findings": findings or ["No scored aggregate findings are available."],
                "strengths": strengths,
                "areas_for_improvement": improvements,
                "recommended_actions": recommendations,
                "conclusion": conclusion,
            },
        )


class NarrativeNumericalValidator:
    """Reject prose figures that are absent from the immutable source values."""

    def validate(
        self,
        document: Mapping[str, Any],
        snapshot: Mapping[str, Any],
    ) -> None:
        source = build_narrative_source(snapshot)
        # Identifiers (control numbers, snapshot hashes, and school codes) are
        # intentionally excluded. A coincidental digit in an identifier must
        # never authorize a quantitative claim in generated prose.
        known = _known_numeric_values(
            {
                "analysis": source["analysis"],
                "report": source["report"],
                "filters": source["filters"],
            }
        )
        unknown: list[str] = []
        for location, text in _generated_text(document):
            for match in _NUMBER_PATTERN.finditer(text):
                token = match.group(0)
                value = _decimal_token(token)
                if value is not None and value not in known:
                    unknown.append(f"{token.strip()} in {location}")
        if unknown:
            details = "; ".join(unknown[:5])
            raise UnknownNumericalClaimError(
                "Narrative text contains figure(s) not found in the source "
                f"Dashboard snapshot: {details}."
            )


def validate_narrative_document(
    document: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> None:
    """Validate structure, source identity, required statement, and figures."""

    if not isinstance(document, Mapping):
        raise NarrativeValidationError("Narrative document must be an object.")
    if _contains_api_secret(document):
        raise NarrativeValidationError(
            "Narrative content cannot contain API credentials."
        )
    required_document_keys = {
        "schema_version",
        "title",
        "dashboard_control_number",
        "source",
        "validity_statement",
        "sections",
        "signatories",
    }
    if set(document) != required_document_keys:
        raise NarrativeValidationError("Narrative document fields are incomplete or unsupported.")
    source = build_narrative_source(snapshot)
    expected_source = source["source"]
    if str(document.get("schema_version") or "") != NARRATIVE_SCHEMA_VERSION:
        raise NarrativeValidationError("Narrative schema version is unsupported.")
    if _text(document.get("title")) != "Client Satisfaction Measurement Narrative Report":
        raise NarrativeValidationError("Narrative report title is invalid.")
    if _text(document.get("dashboard_control_number")) != expected_source["dashboard_control_number"]:
        raise NarrativeValidationError("Narrative Dashboard control number does not match its source.")
    actual_source = document.get("source")
    if not isinstance(actual_source, Mapping) or dict(actual_source) != expected_source:
        raise NarrativeValidationError("Narrative source identity does not match the snapshot.")
    expected_statement = validity_statement(expected_source["dashboard_control_number"])
    if document.get("validity_statement") != expected_statement:
        raise NarrativeValidationError("The required narrative validity statement was changed.")

    sections = document.get("sections")
    if not isinstance(sections, list) or len(sections) != len(SECTION_ORDER):
        raise NarrativeValidationError("Narrative sections are incomplete.")
    for expected_key, section in zip(SECTION_ORDER, sections):
        if not isinstance(section, Mapping):
            raise NarrativeValidationError(f"Narrative section {expected_key!r} is invalid.")
        if set(section) != {"key", "heading", "paragraphs", "bullets"}:
            raise NarrativeValidationError(
                f"Narrative section {expected_key!r} has unsupported fields."
            )
        if section.get("key") != expected_key or section.get("heading") != SECTION_HEADINGS[expected_key]:
            raise NarrativeValidationError(f"Narrative section {expected_key!r} was renamed or reordered.")
        paragraphs = section.get("paragraphs")
        bullets = section.get("bullets")
        if not isinstance(paragraphs, list) or not isinstance(bullets, list):
            raise NarrativeValidationError(f"Narrative section {expected_key!r} has invalid content.")
        active = paragraphs if expected_key in _PARAGRAPH_SECTIONS else bullets
        inactive = bullets if expected_key in _PARAGRAPH_SECTIONS else paragraphs
        if inactive or not active or any(not _text(item) for item in active):
            raise NarrativeValidationError(f"Narrative section {expected_key!r} is incomplete.")

    signatories = document.get("signatories")
    if not isinstance(signatories, list) or [item.get("role") for item in signatories if isinstance(item, Mapping)] != list(SIGNATORY_ROLES):
        raise NarrativeValidationError("Required narrative signatories are incomplete.")
    for signatory in signatories:
        if not isinstance(signatory, Mapping):
            raise NarrativeValidationError("Narrative signatory entry is invalid.")
        if set(signatory) != {"role", "name", "signature_date"}:
            raise NarrativeValidationError("Narrative signatory fields are unsupported.")
        if not isinstance(signatory.get("name"), str) or not isinstance(signatory.get("signature_date"), str):
            raise NarrativeValidationError("Narrative signatory fields must be text.")

    NarrativeNumericalValidator().validate(document, snapshot)


_PARAGRAPH_SECTIONS = frozenset(
    {"executive_summary", "scope_and_methodology", "key_findings", "conclusion"}
)
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*%?"
)
_API_SECRET_PATTERN = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}\b|\bBearer\s+[A-Za-z0-9._~-]{8,})",
    re.IGNORECASE,
)


def _clean_items(value: Iterable[str] | None, section: str) -> list[str]:
    if isinstance(value, str) or value is None:
        raw = [value] if isinstance(value, str) else []
    else:
        try:
            raw = list(value)
        except TypeError as exc:
            raise NarrativeValidationError(f"Narrative section {section!r} must be a list.") from exc
    if any(not isinstance(item, str) for item in raw):
        raise NarrativeValidationError(
            f"Narrative section {section!r} must contain text only."
        )
    result = [_text(item) for item in raw if _text(item)]
    if not result:
        raise NarrativeValidationError(f"Narrative section {section!r} cannot be empty.")
    return result


def _generated_text(document: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    sections = document.get("sections")
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        key = str(section.get("key") or "section")
        for field in ("paragraphs", "bullets"):
            values = section.get(field)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, str):
                        yield f"{key}.{field}[{index}]", value


def _known_numeric_values(value: Any) -> set[Decimal]:
    result: set[Decimal] = set()

    def visit(item: Any) -> None:
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, (int, float, Decimal)):
            try:
                result.add(Decimal(str(item)).normalize())
            except InvalidOperation:
                return
        elif isinstance(item, str):
            for match in _NUMBER_PATTERN.finditer(item):
                parsed = _decimal_token(match.group(0))
                if parsed is not None:
                    result.add(parsed)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return result


def _contains_api_secret(value: Any) -> bool:
    if isinstance(value, str):
        return _API_SECRET_PATTERN.search(value) is not None
    if isinstance(value, Mapping):
        return any(_contains_api_secret(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_api_secret(item) for item in value)
    return False


def _decimal_token(token: str) -> Decimal | None:
    cleaned = token.strip().removesuffix("%").strip().replace(",", "")
    try:
        return Decimal(cleaned).normalize()
    except InvalidOperation:
        return None


def _safe_school(value: Any) -> dict[str, Any]:
    school = _mapping(value)
    allowed = (
        "school_name",
        "school_id",
        "office_name",
        "district",
        "division",
        "region",
        "address",
    )
    return {key: deepcopy(school[key]) for key in allowed if key in school}


def _safe_report(value: Any) -> dict[str, Any]:
    report = _mapping(value)
    allowed = (
        "scope",
        "scope_text",
        "filter_scope",
        "period",
        "generated_at",
        "printed_at",
        "system_name",
        "survey_count",
        "page_count",
        "selected_pages",
        "paper_size",
        "orientation",
    )
    return {key: deepcopy(report[key]) for key in allowed if key in report}


def _safe_filters(value: Any) -> dict[str, Any]:
    filters = _mapping(value)
    # Free-text searches may contain respondent-entered content. Categorical
    # and date filters are sufficient to describe the report scope.
    blocked_fragments = (
        "search",
        "query",
        "feedback",
        "comment",
        "suggestion",
        "free_text",
    )

    def safe_key(value: Any) -> bool:
        key = str(value).casefold()
        return not any(fragment in key for fragment in blocked_fragments)

    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): clean(nested)
                for key, nested in item.items()
                if safe_key(key)
            }
        if isinstance(item, list):
            return [clean(nested) for nested in item]
        return deepcopy(item)

    return clean(filters)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _required_text(value: Any, label: str) -> str:
    text = _text(value)
    if not text:
        raise NarrativeValidationError(f"{label} is required.")
    return text


def _required_digest(value: Any, label: str) -> str:
    digest = _required_text(value, label).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise NarrativeValidationError(f"{label} is invalid.")
    return digest


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _number(value: Any, *, default: int | float) -> int | float:
    if isinstance(value, bool):
        return default
    return value if isinstance(value, (int, float)) else default


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return _text(value)


def _dimension_name(row: Mapping[str, Any]) -> str:
    return _text(row.get("dimension") or row.get("name") or row.get("code")) or "Unnamed dimension"


def _service_name(row: Mapping[str, Any]) -> str:
    return _text(row.get("service") or row.get("label")) or "Unspecified service"
