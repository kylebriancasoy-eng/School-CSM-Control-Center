"""Canonical demographic values shared by every CSM intake channel.

The web form and the manually encoded survey can record an exact age.  The
printed MRS form intentionally records only an age bracket.  Both routes use
the same official bracket codes so analysis never has to invent an exact age
for a paper response.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


MINIMUM_AGE = 0
MAXIMUM_AGE = 130

AGE_BRACKETS = (
    ("19_or_lower", "19 or lower"),
    ("20_34", "20–34"),
    ("35_49", "35–49"),
    ("50_64", "50–64"),
    ("65_or_higher", "65 or higher"),
    ("did_not_specify", "Not Specified"),
)
AGE_BRACKET_LABELS = dict(AGE_BRACKETS)
AGE_BRACKET_CODES = frozenset(AGE_BRACKET_LABELS)


@dataclass(frozen=True, slots=True)
class RegionDefinition:
    code: str
    label: str
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "label": self.label}


REGION_DEFINITIONS = (
    RegionDefinition("ncr", "National Capital Region (NCR)", ("national capital region",)),
    RegionDefinition("car", "Cordillera Administrative Region (CAR)", ("cordillera administrative region",)),
    RegionDefinition("region_01", "Region I – Ilocos Region", ("region i", "region 1", "ilocos region")),
    RegionDefinition("region_02", "Region II – Cagayan Valley", ("region ii", "region 2", "cagayan valley")),
    RegionDefinition("region_03", "Region III – Central Luzon", ("region iii", "region 3", "central luzon")),
    RegionDefinition(
        "region_04a",
        "Region IV-A – CALABARZON",
        ("region iv-a", "region 4a", "region 4-a", "calabarzon"),
    ),
    RegionDefinition(
        "region_04b",
        "Region IV-B – MIMAROPA Region",
        ("region iv-b", "region 4b", "region 4-b", "mimaropa", "mimaropa region"),
    ),
    RegionDefinition("region_05", "Region V – Bicol Region", ("region v", "region 5", "bicol", "bicol region")),
    RegionDefinition(
        "region_06",
        "Region VI – Western Visayas",
        ("region vi", "region 6", "western visayas"),
    ),
    RegionDefinition(
        "region_07",
        "Region VII – Central Visayas",
        ("region vii", "region 7", "central visayas"),
    ),
    RegionDefinition(
        "region_08",
        "Region VIII – Eastern Visayas",
        ("region viii", "region 8", "eastern visayas"),
    ),
    RegionDefinition(
        "region_09",
        "Region IX – Zamboanga Peninsula",
        ("region ix", "region 9", "zamboanga peninsula"),
    ),
    RegionDefinition(
        "region_10",
        "Region X – Northern Mindanao",
        ("region x", "region 10", "northern mindanao"),
    ),
    RegionDefinition(
        "region_11",
        "Region XI – Davao Region",
        ("region xi", "region 11", "davao", "davao region"),
    ),
    RegionDefinition(
        "region_12",
        "Region XII – SOCCSKSARGEN",
        ("region xii", "region 12", "soccsksargen"),
    ),
    RegionDefinition(
        "region_13",
        "Region XIII – Caraga",
        ("region xiii", "region 13", "caraga"),
    ),
    RegionDefinition(
        "nir",
        "Negros Island Region (NIR)",
        ("negros island region",),
    ),
    RegionDefinition(
        "barmm",
        "Bangsamoro Autonomous Region in Muslim Mindanao (BARMM)",
        (
            "bangsamoro autonomous region in muslim mindanao",
            "bangsamoro",
            "armm",
        ),
    ),
    RegionDefinition(
        "outside_philippines",
        "Outside the Philippines",
        ("outside philippines", "overseas", "foreign"),
    ),
    RegionDefinition(
        "did_not_specify",
        "Not Specified",
        ("not specified", "did not specify", "not provided", "n/a", "na"),
    ),
)
REGION_LABELS = {definition.code: definition.label for definition in REGION_DEFINITIONS}
REGION_CODES = frozenset(REGION_LABELS)
REGION_OPTIONS = tuple(definition.as_dict() for definition in REGION_DEFINITIONS)


def _key(value: Any) -> str:
    text = " ".join(str(value or "").strip().split()).casefold()
    text = text.replace("—", "-").replace("–", "-").replace("_", " ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_REGION_ALIASES: dict[str, str] = {}
for _definition in REGION_DEFINITIONS:
    for _alias in (_definition.code, _definition.label, *_definition.aliases):
        _REGION_ALIASES[_key(_alias)] = _definition.code
# v0.4 MRS used ``caraga`` while newer data uses the official Region XIII code.
_REGION_ALIASES[_key("caraga")] = "region_13"
_REGION_ALIASES[_key("region 13")] = "region_13"


def normalize_age(value: Any, *, strict: bool = False) -> int | None:
    """Return a whole-number age from 0 through 130, or ``None``.

    ``strict`` is intended for intake validation.  Analysis uses the forgiving
    default so a malformed legacy value is grouped as not specified.
    """

    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        if strict:
            raise ValueError("Age must be a whole number from 0 to 130.") from exc
        return None
    if not number.is_integer() or not MINIMUM_AGE <= number <= MAXIMUM_AGE:
        if strict:
            raise ValueError("Age must be a whole number from 0 to 130.")
        return None
    return int(number)


def age_bracket_for(value: Any) -> str:
    age = normalize_age(value)
    if age is None:
        return "did_not_specify"
    if age <= 19:
        return "19_or_lower"
    if age <= 34:
        return "20_34"
    if age <= 49:
        return "35_49"
    if age <= 64:
        return "50_64"
    return "65_or_higher"


_AGE_ALIASES = {
    _key(code): code for code, _label in AGE_BRACKETS
}
_AGE_ALIASES.update({_key(label): code for code, label in AGE_BRACKETS})
_AGE_ALIASES.update(
    {
        _key("19 and below"): "19_or_lower",
        _key("below 20"): "19_or_lower",
        _key("20-34"): "20_34",
        _key("35-49"): "35_49",
        _key("50-64"): "50_64",
        _key("65+"): "65_or_higher",
        _key("65 and above"): "65_or_higher",
        _key("did not specify"): "did_not_specify",
        _key("not provided"): "did_not_specify",
    }
)


def normalize_age_bracket(value: Any, *, age: Any = None, strict: bool = False) -> str:
    """Return a canonical MRS-compatible age-bracket code."""

    if value not in (None, ""):
        code = _AGE_ALIASES.get(_key(value))
        if code is not None:
            return code
        if strict:
            raise ValueError("A valid age bracket is required.")
    if age not in (None, ""):
        return age_bracket_for(age)
    return "did_not_specify"


def age_bracket_label(value: Any, *, age: Any = None) -> str:
    return AGE_BRACKET_LABELS[normalize_age_bracket(value, age=age)]


def normalize_region_code(value: Any, *, strict: bool = False) -> str:
    """Return a canonical region code.

    Blank values become ``did_not_specify``.  Unknown legacy text is returned
    as an empty code unless strict validation is requested; callers that need
    to preserve that text can use :func:`normalize_region_value`.
    """

    if value in (None, ""):
        return "did_not_specify"
    code = _REGION_ALIASES.get(_key(value))
    if code is not None:
        return code
    if strict:
        raise ValueError("A valid region of residence is required.")
    return ""


def region_label(value: Any) -> str:
    code = normalize_region_code(value)
    if code:
        return REGION_LABELS[code]
    text = " ".join(str(value or "").split())
    return text or REGION_LABELS["did_not_specify"]


def normalize_region_value(value: Any, *, strict: bool = False) -> str:
    """Normalize recognized regions to codes while preserving unknown legacy text."""

    code = normalize_region_code(value, strict=strict)
    if code:
        return code
    return " ".join(str(value or "").split())


def normalize_demographics(
    metadata: Mapping[str, Any] | None,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Return a copy with canonical age, bracket, and region values."""

    result = deepcopy(dict(metadata or {}))
    age = normalize_age(result.get("age"), strict=strict)
    if age is None:
        result.pop("age", None)
    else:
        result["age"] = age
    result["age_bracket"] = normalize_age_bracket(
        result.get("age_bracket"),
        age=age,
        strict=strict and result.get("age_bracket") not in (None, ""),
    )
    result["region"] = normalize_region_value(result.get("region"), strict=strict)
    return result
