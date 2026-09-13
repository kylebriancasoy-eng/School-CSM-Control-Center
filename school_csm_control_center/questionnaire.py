"""ARTA Annex A Client Satisfaction Measurement questionnaire specification.

The source document contains distinct onsite and online forms.  The onsite
form includes SQD0 (general service satisfaction), while the online form starts
at SQD1.  The active school questionnaire omits SQD5 (fees/costs).  Historical
SQD5 values remain compatible with storage and legacy scanner imports, but the
question is not presented to new respondents or included in current scoring.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from school_csm_control_center.demographics import REGION_OPTIONS
from school_csm_control_center.school_services import SCHOOL_TRANSACTIONS


SURVEY_MODES = ("onsite", "online")
MODE_LABELS = {
    "onsite": "Onsite",
    "online": "Online",
}

CLIENT_TYPES = ("Citizen", "Business", "Government")
SEX_OPTIONS = ("Male", "Female")

RATING_LABELS = {
    1: "Strongly Disagree",
    2: "Disagree",
    3: "Neither Agree nor Disagree",
    4: "Agree",
    5: "Strongly Agree",
}
RATING_SHORT_LABELS = {
    1: "SD",
    2: "D",
    3: "NAD",
    4: "A",
    5: "SA",
}
RATING_OPTIONS = [
    {
        "value": score,
        "label": label,
        "short_label": RATING_SHORT_LABELS[score],
    }
    for score, label in RATING_LABELS.items()
] + [
    {
        "value": 0,
        "label": "Not Applicable",
        "short_label": "N/A",
    }
]
ONLINE_RATING_OPTIONS = [
    deepcopy(option) for option in RATING_OPTIONS if option["value"] != 0
]
POSITIVE_RATINGS = (4, 5)


SQD_DIMENSIONS = {
    "sqd1": "Responsiveness",
    "sqd2": "Reliability",
    "sqd3": "Access and Facilities",
    "sqd4": "Communication",
    "sqd6": "Integrity",
    "sqd7": "Assurance",
    "sqd8": "Outcome",
}

ACTIVE_SQD_KEYS = tuple(SQD_DIMENSIONS)
"""SQD dimension keys presented and scored by the current questionnaire."""

LEGACY_SQD_KEYS = ("sqd5",)
"""Retained storage-only keys from earlier forms; never shown or scored."""


def _option(value: int, label: str) -> dict[str, Any]:
    return {"value": value, "label": label}


CC_QUESTIONS = {
    "onsite": {
        "cc1": {
            "code": "CC1",
            "prompt": "Which of the following best describes your awareness of a Citizen's Charter?",
            "options": [
                _option(1, "I know what a Citizen's Charter is and I saw this office's Citizen's Charter."),
                _option(2, "I know what a Citizen's Charter is but I did not see this office's Citizen's Charter."),
                _option(3, "I learned of the Citizen's Charter only when I saw this office's Citizen's Charter."),
                _option(4, "I do not know what a Citizen's Charter is and I did not see one in this office."),
            ],
            "branching": {
                4: {
                    "set": {"cc2": 5, "cc3": 4},
                    "reason": "A respondent who is not aware of the Citizen's Charter answers N/A to CC2 and CC3.",
                }
            },
        },
        "cc2": {
            "code": "CC2",
            "prompt": "If aware of the Citizen's Charter, would you say that this office's Citizen's Charter was...?",
            "options": [
                _option(1, "Easy to see"),
                _option(2, "Somewhat easy to see"),
                _option(3, "Difficult to see"),
                _option(4, "Not visible at all"),
                _option(5, "Not Applicable"),
            ],
        },
        "cc3": {
            "code": "CC3",
            "prompt": "If aware of the Citizen's Charter, how much did it help you in your transaction?",
            "options": [
                _option(1, "Helped very much"),
                _option(2, "Somewhat helped"),
                _option(3, "Did not help"),
                _option(4, "Not Applicable"),
            ],
        },
    },
    "online": {
        "cc1": {
            "code": "CC1",
            "prompt": "Do you know about the Citizen's Charter (document of an agency's services and requirements)?",
            "options": [
                _option(1, "Yes, aware before my transaction with this office"),
                _option(2, "Yes, but aware only when I saw the Citizen's Charter of this office"),
                _option(3, "No, not aware of the Citizen's Charter"),
            ],
            "branching": {
                3: {
                    "skip": ["cc2", "cc3"],
                    "reason": "A respondent who is not aware of the Citizen's Charter skips CC2 and CC3.",
                }
            },
        },
        "cc2": {
            "code": "CC2",
            "prompt": "If yes to CC1, did you see this office's Citizen's Charter?",
            "options": [
                _option(1, "Yes, the Citizen's Charter was easy to find"),
                _option(2, "Yes, but the Citizen's Charter was hard to find"),
                _option(3, "No, I did not see this office's Citizen's Charter"),
            ],
            "branching": {
                3: {
                    "skip": ["cc3"],
                    "reason": "A respondent who did not see the Citizen's Charter skips CC3.",
                }
            },
        },
        "cc3": {
            "code": "CC3",
            "prompt": "If yes to CC2, did you use the Citizen's Charter as a guide for the service you availed?",
            "options": [
                _option(1, "Yes, I was able to use the Citizen's Charter"),
                _option(2, "No, I was not able to use the Citizen's Charter"),
            ],
            "allows_reason": True,
        },
    },
}


SQD_QUESTIONS = {
    "onsite": {
        "sqd0": {
            "code": "SQD0",
            "dimension": "Overall Satisfaction",
            "prompt": "I am satisfied with the service that I availed.",
            "onsite_only": True,
        },
        "sqd1": {
            "code": "SQD1",
            "dimension": SQD_DIMENSIONS["sqd1"],
            "prompt": "I spent a reasonable amount of time for my transaction.",
            "onsite_only": False,
        },
        "sqd2": {
            "code": "SQD2",
            "dimension": SQD_DIMENSIONS["sqd2"],
            "prompt": "The office followed the transaction's requirements and steps based on the information provided.",
            "onsite_only": False,
        },
        "sqd3": {
            "code": "SQD3",
            "dimension": SQD_DIMENSIONS["sqd3"],
            "prompt": "The steps (including payment) I needed to do for my transaction were easy and simple.",
            "onsite_only": False,
        },
        "sqd4": {
            "code": "SQD4",
            "dimension": SQD_DIMENSIONS["sqd4"],
            "prompt": "I easily found information about my transaction from the office or its website.",
            "onsite_only": False,
        },
        "sqd6": {
            "code": "SQD6",
            "dimension": SQD_DIMENSIONS["sqd6"],
            "prompt": "I feel the office was fair to everyone, or 'walang palakasan', during my transaction.",
            "onsite_only": False,
        },
        "sqd7": {
            "code": "SQD7",
            "dimension": SQD_DIMENSIONS["sqd7"],
            "prompt": "I was treated courteously by the staff, and (if asked for help) the staff was helpful.",
            "onsite_only": False,
        },
        "sqd8": {
            "code": "SQD8",
            "dimension": SQD_DIMENSIONS["sqd8"],
            "prompt": "I got what I needed from the government office, or (if denied) denial of request was sufficiently explained to me.",
            "onsite_only": False,
        },
    },
    "online": {
        "sqd1": {
            "code": "SQD1",
            "dimension": SQD_DIMENSIONS["sqd1"],
            "prompt": "I spent an acceptable amount of time to complete my transaction.",
            "onsite_only": False,
        },
        "sqd2": {
            "code": "SQD2",
            "dimension": SQD_DIMENSIONS["sqd2"],
            "prompt": "The office accurately informed and followed the transaction's requirements and steps.",
            "onsite_only": False,
        },
        "sqd3": {
            "code": "SQD3",
            "dimension": SQD_DIMENSIONS["sqd3"],
            "prompt": "My online transaction (including steps and payment) was simple and convenient.",
            "onsite_only": False,
        },
        "sqd4": {
            "code": "SQD4",
            "dimension": SQD_DIMENSIONS["sqd4"],
            "prompt": "I easily found information about my transaction from the office or its website.",
            "onsite_only": False,
        },
        "sqd6": {
            "code": "SQD6",
            "dimension": SQD_DIMENSIONS["sqd6"],
            "prompt": "I am confident my online transaction was secure.",
            "onsite_only": False,
        },
        "sqd7": {
            "code": "SQD7",
            "dimension": SQD_DIMENSIONS["sqd7"],
            "prompt": "The office's online support was available, or (if asked questions) online support was quick to respond.",
            "onsite_only": False,
        },
        "sqd8": {
            "code": "SQD8",
            "dimension": SQD_DIMENSIONS["sqd8"],
            "prompt": "I got what I needed from the government office.",
            "onsite_only": False,
        },
    },
}


ONSITE_META_FIELDS = [
    {"key": "control_number", "label": "Control No.", "type": "text", "optional": True},
    {"key": "client_type", "label": "Client Type", "type": "choice", "options": list(CLIENT_TYPES), "optional": True},
    {"key": "survey_date", "label": "Date", "type": "date", "optional": True},
    {"key": "sex", "label": "Sex", "type": "choice", "options": list(SEX_OPTIONS), "optional": True},
    {"key": "age", "label": "Age", "type": "integer", "optional": True},
    {
        "key": "region",
        "label": "Region of Residence",
        "type": "select",
        "options": list(REGION_OPTIONS),
        "optional": True,
    },
    {
        "key": "service_availed",
        "label": "Service Availed",
        "type": "multi_choice",
        "options": SCHOOL_TRANSACTIONS,
        "optional": True,
    },
    {"key": "email", "label": "Email Address", "type": "email", "optional": True},
]

ONLINE_META_FIELDS = [
    {"key": "age", "label": "Age", "type": "integer", "optional": True},
    {"key": "sex", "label": "Sex", "type": "choice", "options": list(SEX_OPTIONS), "optional": True},
    {
        "key": "region",
        "label": "Region",
        "type": "select",
        "options": list(REGION_OPTIONS),
        "optional": True,
    },
    {"key": "agency_visited", "label": "Agency Visited", "type": "text", "optional": True},
    {
        "key": "service_availed",
        "label": "Service Availed",
        "type": "multi_choice",
        "options": SCHOOL_TRANSACTIONS,
        "optional": True,
    },
    {"key": "client_type", "label": "Customer Type", "type": "choice", "options": list(CLIENT_TYPES), "optional": True},
]


QUESTIONNAIRE_SPEC = {
    "onsite": {
        "mode": "onsite",
        "label": MODE_LABELS["onsite"],
        "meta_fields": ONSITE_META_FIELDS,
        "cc_questions": CC_QUESTIONS["onsite"],
        "sqd_questions": SQD_QUESTIONS["onsite"],
        "rating_options": RATING_OPTIONS,
        "feedback_label": "Suggestions on how we can further improve our services",
    },
    "online": {
        "mode": "online",
        "label": MODE_LABELS["online"],
        "meta_fields": ONLINE_META_FIELDS,
        "cc_questions": CC_QUESTIONS["online"],
        "sqd_questions": SQD_QUESTIONS["online"],
        "rating_options": ONLINE_RATING_OPTIONS,
        "feedback_label": "Remarks",
    },
}


def questionnaire_for(mode: str) -> dict[str, Any]:
    """Return an isolated questionnaire definition for *mode*."""

    key = str(mode or "").strip().casefold()
    if key not in QUESTIONNAIRE_SPEC:
        raise ValueError(f"Unsupported survey mode: {mode!r}")
    return deepcopy(QUESTIONNAIRE_SPEC[key])


def apply_cc_branching(mode: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of CC answers with Annex A skip rules applied.

    Skipped online questions are removed instead of being treated as explicit
    N/A answers.  The onsite form has printed N/A codes, so its forced values
    remain explicit numeric answers.
    """

    key = str(mode or "").strip().casefold()
    if key not in CC_QUESTIONS:
        raise ValueError(f"Unsupported survey mode: {mode!r}")
    result = {str(name).casefold(): value for name, value in dict(answers).items()}
    cc1 = result.get("cc1")
    if key == "onsite" and cc1 == 4:
        result["cc2"] = 5
        result["cc3"] = 4
        return result
    if key == "online" and cc1 == 3:
        result.pop("cc2", None)
        result.pop("cc3", None)
        result.pop("cc3_reason", None)
        return result
    if key == "online" and result.get("cc2") == 3:
        result.pop("cc3", None)
        result.pop("cc3_reason", None)
    return result
