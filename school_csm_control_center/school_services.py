from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


EXTERNAL_SECTION_ID = "external"
INTERNAL_SECTION_ID = "internal"


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    """One stable school transaction shared by desktop, web, CSV, and MRS."""

    id: str
    label: str
    section: str
    channel: str = "any"
    mrs_code: str = ""
    short_label: str = ""
    description: str = ""
    aliases: tuple[str, ...] = ()
    translations: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        translations = {
            language: {"label": values[0], "description": values[1]}
            for language, values in self.translations.items()
        }
        return {
            "id": self.id,
            "label": self.label,
            "short_label": self.short_label or self.label,
            "description": self.description,
            "section": self.section,
            "channel": self.channel,
            "mrs_code": self.mrs_code or None,
            "translations": translations,
        }


SERVICE_CATALOG = (
    ServiceDefinition(
        "teacher_i_application.walk_in",
        "Acceptance of Employment Application for Teacher I Position (Walk-in)",
        EXTERNAL_SECTION_ID,
        "walk_in",
        description="Submission of a Teacher I employment application at the school.",
    ),
    ServiceDefinition(
        "teacher_i_application.online",
        "Acceptance of Employment Application for Teacher I Position (Online)",
        EXTERNAL_SECTION_ID,
        "online",
        description="Online submission of a Teacher I employment application.",
    ),
    ServiceDefinition(
        "learning_materials.borrow",
        "Borrowing of Learning Materials from the School Library/Learning Resource Center",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S06",
        "Borrow Learning Materials",
        "Borrowing books or learning resources from the library or Learning Resource Center.",
    ),
    ServiceDefinition(
        "learning_modules.distribute",
        "Distribution of Printed Self-Learning Modules in Distance Learning Modality",
        EXTERNAL_SECTION_ID,
        "walk_in",
        description="Distribution of printed self-learning modules for distance learning.",
    ),
    ServiceDefinition(
        "enrollment.walk_in",
        "Enrollment (Walk-in)",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S01",
        "Walk-in Enrollment",
        "Onsite learner enrollment at the school.",
        ("Walk in enrollment",),
    ),
    ServiceDefinition(
        "enrollment.online",
        "Enrollment (Online)",
        EXTERNAL_SECTION_ID,
        "online",
        "S02",
        "Online Enrollment",
        "Enrollment submitted or processed through the school's online channel.",
    ),
    ServiceDefinition(
        "certified_copies.walk_in",
        "Issuance of Requested Documents in Certified True Copy (CTC) and Photocopy (Walk-in)",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S04",
        "Certified True Copies",
        "Issuance of certified true copies or photocopies of school documents onsite.",
        ("Certification",),
    ),
    ServiceDefinition(
        "certified_copies.online",
        "Issuance of Requested Documents in Certified True Copy (CTC) and Photocopy (Online)",
        EXTERNAL_SECTION_ID,
        "online",
        description="Online request for certified true copies or photocopies of school documents.",
    ),
    ServiceDefinition(
        "school_clearance.issue",
        "Issuance of School Clearance for different purposes",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S05",
        "School Clearance",
        "Issuance of a school clearance for transfer, completion, employment, or another purpose.",
        ("School Clearance",),
    ),
    ServiceDefinition(
        "school_records.issue",
        "Issuance of School Forms, Certifications, and other School Permanent Records",
        EXTERNAL_SECTION_ID,
        "any",
        "S03",
        "School Records & Certifications",
        "Issuance of school forms, certifications, or permanent student records.",
        ("official records issuance", "School Records and Certifications"),
    ),
    ServiceDefinition(
        "public_assistance.walk_in_phone",
        "Public assistance (walk-in/phone call)",
        EXTERNAL_SECTION_ID,
        "walk_in_or_phone",
        "S07",
        "Public Assistance — Walk-in/Phone",
        "Information or assistance provided in person or through a phone call.",
    ),
    ServiceDefinition(
        "public_assistance.digital",
        "Public assistance (email/social media)",
        EXTERNAL_SECTION_ID,
        "digital",
        "S08",
        "Public Assistance — Email/Social Media",
        "Information or assistance provided through email or social media.",
    ),
    ServiceDefinition(
        "documents.receive_release",
        "Receiving and releasing of communications and other documents",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S09",
        "Receive or Release Documents",
        "Receiving or releasing official communications and other documents.",
        ("Receive/release documents", "Receiving and releasing documents"),
    ),
    ServiceDefinition(
        "school_facilities.reserve",
        "Reservation Process for the Use of School Facilities",
        EXTERNAL_SECTION_ID,
        "walk_in",
        "S10",
        "Reserve School Facilities",
        "Reservation of school rooms, grounds, or other facilities.",
    ),
    ServiceDefinition(
        "personnel_records.request",
        "Request for Personnel Records for Teaching/Non-Teaching Personnel",
        EXTERNAL_SECTION_ID,
        "any",
        description="Request for teaching or non-teaching personnel records.",
    ),
    ServiceDefinition(
        "service_credits.issue",
        "Issuance of Special Order for Service Credits and Certification of Compensatory Time Credits",
        INTERNAL_SECTION_ID,
        "internal",
        "S11",
        "Service Credits & CTC",
        "Issuance of a service-credit special order or compensatory time credit certification.",
    ),
    ServiceDefinition(
        "school_inventory.process",
        "Laboratory and School Inventory",
        INTERNAL_SECTION_ID,
        "internal",
        "S12",
        "Laboratory & School Inventory",
        "Laboratory or school inventory records and transactions.",
    ),
    ServiceDefinition(
        "learning_development.process",
        "School Learning and Development",
        INTERNAL_SECTION_ID,
        "internal",
        "S13",
        "School Learning & Development",
        "School-based learning and development activities or records.",
    ),
)

SERVICE_BY_ID = {definition.id: definition for definition in SERVICE_CATALOG}
SERVICE_BY_MRS_CODE = {
    definition.mrs_code: definition
    for definition in SERVICE_CATALOG
    if definition.mrs_code
}


def _service_key(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


SERVICE_ALIASES: dict[str, ServiceDefinition] = {}
for _definition in SERVICE_CATALOG:
    for _alias in (
        _definition.id,
        _definition.label,
        _definition.short_label,
        _definition.mrs_code,
        *_definition.aliases,
    ):
        if _alias:
            SERVICE_ALIASES[_service_key(_alias)] = _definition


SCHOOL_EXTERNAL_SERVICES = tuple(
    definition.label
    for definition in SERVICE_CATALOG
    if definition.section == EXTERNAL_SECTION_ID
)
SCHOOL_INTERNAL_SERVICES = tuple(
    definition.label
    for definition in SERVICE_CATALOG
    if definition.section == INTERNAL_SECTION_ID
)

SCHOOL_SERVICE_SECTIONS = (
    ("External school transactions", SCHOOL_EXTERNAL_SERVICES),
    ("Internal school transactions", SCHOOL_INTERNAL_SERVICES),
)
SCHOOL_TRANSACTIONS = SCHOOL_EXTERNAL_SERVICES + SCHOOL_INTERNAL_SERVICES


def canonical_service_definition(value: Any) -> ServiceDefinition | None:
    return SERVICE_ALIASES.get(_service_key(value))


def canonical_service_id(value: Any) -> str:
    definition = canonical_service_definition(value)
    return definition.id if definition is not None else ""


def service_catalog_payload() -> list[dict[str, Any]]:
    return [definition.as_dict() for definition in SERVICE_CATALOG]


def normalize_service_values(value: Any, *, strict: bool = False) -> list[str]:
    """Return canonical labels while retaining unknown legacy transactions."""

    if value is None:
        return []
    if isinstance(value, str):
        candidates: Iterable[Any] = (value,)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        candidates = value
    else:
        if strict:
            raise ValueError("Service availed must be text or a list of text values.")
        candidates = (str(value),)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            if strict:
                raise ValueError("Every selected school transaction must be text.")
            continue
        label = " ".join(candidate.split())
        if not label:
            continue
        definition = canonical_service_definition(label)
        if definition is not None:
            label = definition.label
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(label)
    return normalized


def service_display(value: Any, *, fallback: str = "Unspecified service") -> str:
    services = normalize_service_values(value)
    return "; ".join(services) if services else fallback


def service_value_from_record(record: Mapping[str, Any]) -> Any:
    """Read current and legacy service fields without mutating the record."""

    if not isinstance(record, Mapping):
        return None
    meta = record.get("meta")
    metadata = meta if isinstance(meta, Mapping) else {}
    for container, key in (
        (record, "service_availed"),
        (metadata, "service_availed"),
        (record, "service"),
        (metadata, "service"),
    ):
        if key in container:
            return container[key]
    return None
