"""Pure live-analysis functions for CSM survey records.

The Annex A form defines the questions and response scale, but does not print a
scoring formula.  This module follows the ARTA CSM reporting method requested
for the application: positive response rate is Agree plus Strongly Agree,
divided by valid non-N/A answers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from school_csm_control_center.demographics import (
    age_bracket_label,
    normalize_age,
    normalize_age_bracket,
    normalize_region_code,
    region_label,
)
from school_csm_control_center.questionnaire import (
    ACTIVE_SQD_KEYS,
    CC_QUESTIONS,
    POSITIVE_RATINGS,
    RATING_LABELS,
    SQD_DIMENSIONS,
    SQD_QUESTIONS,
)
from school_csm_control_center.school_services import normalize_service_values, service_display


SQD_KEYS = ACTIVE_SQD_KEYS
NO_DATA_LABEL = "No Data"


def rating_label(score: float | int | None) -> str:
    """Return the required performance band for a positive-response rate."""

    if score is None or isinstance(score, bool):
        return NO_DATA_LABEL
    try:
        value = float(score)
    except (TypeError, ValueError):
        return NO_DATA_LABEL
    if value < 60:
        return "Poor"
    if value < 80:
        return "Fair"
    if value < 90:
        return "Satisfactory"
    if value < 95:
        return "Very Satisfactory"
    return "Outstanding"


def _next_performance_band(score: float | int | None) -> dict[str, Any] | None:
    if score is None or isinstance(score, bool):
        return None
    value = float(score)
    for threshold, label in (
        (60.0, "Fair"),
        (80.0, "Satisfactory"),
        (90.0, "Very Satisfactory"),
        (95.0, "Outstanding"),
    ):
        if value < threshold:
            return {
                "label": label,
                "threshold": threshold,
                "points_needed": round(threshold - value, 2),
            }
    return {
        "label": "Outstanding",
        "threshold": 95.0,
        "points_needed": 0.0,
    }


class AnalysisService:
    """Build dashboard-ready analysis from in-memory survey records.

    The service does not read or write files.  ``analyze`` and
    ``filter_records`` accept dictionaries so the UI can immediately analyze a
    newly saved record without reconstructing domain objects.
    """

    rating_label = staticmethod(rating_label)

    @classmethod
    def filter_records(
        cls,
        records: Iterable[Mapping[str, Any]],
        filters: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return independent record copies matching the supplied filters.

        Supported filters include ``mode``, ``source_code``, ``scanner_operator``,
        ``service``, ``client_type``, ``sex``, ``region``, ``agency_visited``, ``age_group``, ``age_min``,
        ``age_max``, ``date_from``, ``date_to``, and ``search``.  Categorical
        filters may be a scalar or an iterable of accepted values.  Common
        aliases such as ``modes``, ``service_availed``, ``from_date`` and
        ``to_date`` are also accepted.
        """

        clean_records = [
            deepcopy(dict(record))
            for record in records
            if isinstance(record, Mapping)
        ]
        if not filters:
            return clean_records

        query = dict(filters)
        demographic_filters = query.get("demographics")
        if isinstance(demographic_filters, Mapping):
            for key, value in demographic_filters.items():
                query.setdefault(str(key), value)

        date_from = _parse_date(
            query.get("date_from")
            or query.get("from_date")
            or query.get("start_date")
        )
        date_to = _parse_date(
            query.get("date_to")
            or query.get("to_date")
            or query.get("end_date")
        )
        age_min = _optional_int(query.get("age_min"))
        age_max = _optional_int(query.get("age_max"))

        filtered: list[dict[str, Any]] = []
        for record in clean_records:
            record_date = _record_date(record)
            if date_from is not None and (
                record_date is None or record_date < date_from
            ):
                continue
            if date_to is not None and (
                record_date is None or record_date > date_to
            ):
                continue

            if not _matches_filter(
                _record_value(record, "mode"),
                query.get("mode", query.get("modes")),
            ):
                continue
            meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
            source_value = str(record.get("source_code") or "").strip().upper()
            if not source_value:
                source_value = {
                    "local_web_form": "WBS",
                    "scanned_hardcopy": "MRS",
                    "manual_entry": "SCC",
                }.get(str(meta.get("submission_source") or "").strip().casefold(), "SCC")
            if not _matches_filter(
                source_value,
                query.get("source_code", query.get("source")),
            ):
                continue
            if not _matches_filter(
                meta.get("scanner_operator_user_id") or meta.get("scanner_operator_display_name"),
                query.get("scanner_operator", query.get("scanner_operator_user_id")),
            ):
                continue
            wanted_service = query.get(
                "service",
                query.get("services", query.get("service_availed")),
            )
            if not _matches_service_filter(_record_services(record), wanted_service):
                continue
            if not _matches_filter(
                _record_value(record, "client_type"),
                query.get(
                    "client_type",
                    query.get("client_types", query.get("customer_type")),
                ),
            ):
                continue
            for field in ("sex", "agency_visited"):
                if not _matches_filter(
                    _record_value(record, field), query.get(field)
                ):
                    break
            else:
                wanted_region = query.get("region")
                actual_region = _record_value(record, "region")
                if wanted_region not in (None, "", "all", "any"):
                    actual_code = normalize_region_code(actual_region)
                    wanted_code = normalize_region_code(wanted_region)
                    if wanted_code:
                        if actual_code != wanted_code:
                            continue
                    elif not _matches_filter(actual_region, wanted_region):
                        continue
                age = normalize_age(_record_value(record, "age"))
                if age_min is not None and (age is None or age < age_min):
                    continue
                if age_max is not None and (age is None or age > age_max):
                    continue
                wanted_age_group = query.get("age_group")
                actual_age_group = _record_age_bracket_label(record)
                wanted_age_code = normalize_age_bracket(wanted_age_group)
                if (
                    wanted_age_group not in (None, "", "all", "any")
                    and normalize_age_bracket(
                        _record_value(record, "age_bracket"),
                        age=age,
                    )
                    != wanted_age_code
                ):
                    continue
                if not _matches_search(record, query.get("search")):
                    continue
                filtered.append(record)

        return filtered

    @classmethod
    def analyze(
        cls,
        records: Iterable[Mapping[str, Any]],
        filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return complete dashboard analysis for the filtered record set."""

        filtered = cls.filter_records(records, filters)
        dimensions = [_dimension_metric(filtered, key) for key in SQD_KEYS]
        ranked = sorted(
            (item for item in dimensions if item["positive_rate"] is not None),
            key=lambda item: (
                -float(item["positive_rate"]),
                item["code"],
            ),
        )
        ranks: dict[str, int] = {}
        previous_rate: float | None = None
        current_rank = 0
        for index, item in enumerate(ranked, 1):
            rate = float(item["positive_rate"])
            if previous_rate is None or rate != previous_rate:
                current_rank = index
                previous_rate = rate
            ranks[item["key"]] = current_rank
        for item in dimensions:
            item["rank"] = ranks.get(item["key"])

        overall = _ratings_metric(filtered, SQD_KEYS)
        sqd0 = _dimension_metric(
            [record for record in filtered if _mode(record) == "onsite"],
            "sqd0",
        )
        response_mix = _response_mix(filtered)
        trend = _trend(
            filtered,
            str((filters or {}).get("trend_granularity") or "month"),
        )
        citizen_charter = _citizen_charter(filtered)
        demographics = _demographic_breakdowns(filtered)
        services = _service_breakdown(filtered)
        feedback = _feedback_items(
            filtered,
            limit=_positive_limit((filters or {}).get("feedback_limit"), 50),
        )
        feedback_count = sum(bool(_feedback_text(record)) for record in filtered)
        recent = _recent_items(
            filtered,
            limit=_positive_limit((filters or {}).get("recent_limit"), 10),
        )

        dates = sorted(
            record_date
            for record in filtered
            if (record_date := _record_date(record)) is not None
        )
        total = len(filtered)
        next_band = _next_performance_band(overall["positive_rate"])
        overview = {
            "total_responses": total,
            "response_count": total,
            "onsite_responses": sum(
                1 for record in filtered if _mode(record) == "onsite"
            ),
            "online_responses": sum(
                1 for record in filtered if _mode(record) == "online"
            ),
            "valid_sqd_answers": overall["valid_responses"],
            "positive_sqd_answers": overall["positive_responses"],
            "positive_rate": overall["positive_rate"],
            "average_rating": overall["average_rating"],
            "rating": overall["rating"],
            "band": overall["rating"],
            "completion_rate": response_mix["completion_rate"],
            "feedback_count": feedback_count,
            "feedback_rate": _percent(feedback_count, total),
            "answer_coverage_rate": response_mix["answer_coverage_rate"],
            "scorable_rate": response_mix["scorable_rate"],
            "answered_sqd_answers": response_mix["answered_count"],
            "total_possible_sqd_answers": response_mix["total_possible_answers"],
            "next_band": next_band,
            "period": {
                "from": dates[0].isoformat() if dates else None,
                "to": dates[-1].isoformat() if dates else None,
            },
            "sqd0": sqd0,
        }
        insights = _insights(
            overview,
            dimensions,
            trend,
            citizen_charter,
            services,
            feedback_count,
        )

        return {
            "overview": overview,
            "dimensions": dimensions,
            "response_mix": response_mix,
            "trend": trend,
            "citizen_charter": citizen_charter,
            "demographics": demographics,
            "services": services,
            "insights": insights,
            "feedback": feedback,
            "recent": recent,
            "sqd0": sqd0,
            "applied_filters": deepcopy(dict(filters or {})),
        }


def _dimension_metric(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    metric = _ratings_metric(records, (key,))
    code = key.upper()
    dimension = (
        "Overall Satisfaction"
        if key == "sqd0"
        else SQD_DIMENSIONS.get(key, code)
    )
    questions = {
        mode: questions[key]["prompt"]
        for mode, questions in SQD_QUESTIONS.items()
        if key in questions
    }
    return {
        "key": key,
        "code": code,
        "dimension": dimension,
        "name": dimension,
        "questions": questions,
        **metric,
    }


def _ratings_metric(
    records: list[dict[str, Any]], keys: tuple[str, ...]
) -> dict[str, Any]:
    counts = {score: 0 for score in RATING_LABELS}
    not_applicable = 0
    unanswered = 0
    for record in records:
        for key in keys:
            raw, present = _raw_rating(record, key)
            score = _normalize_rating(raw)
            if score is None:
                if present and _is_not_applicable(raw):
                    not_applicable += 1
                else:
                    unanswered += 1
                continue
            counts[score] += 1

    valid = sum(counts.values())
    positive = sum(counts[score] for score in POSITIVE_RATINGS)
    average = (
        round(sum(score * count for score, count in counts.items()) / valid, 2)
        if valid
        else None
    )
    positive_rate = _percent(positive, valid)
    return {
        "valid_responses": valid,
        "positive_responses": positive,
        "positive_rate": positive_rate,
        "average_rating": average,
        "rating": rating_label(positive_rate),
        "band": rating_label(positive_rate),
        "distribution": [
            {
                "score": score,
                "label": RATING_LABELS[score],
                "count": counts[score],
                "percent": _percent(counts[score], valid),
            }
            for score in RATING_LABELS
        ],
        "not_applicable": not_applicable,
        "unanswered": unanswered,
    }


def _response_mix(records: list[dict[str, Any]]) -> dict[str, Any]:
    metric = _ratings_metric(records, SQD_KEYS)
    distribution = metric["distribution"]
    by_score = {item["score"]: item["count"] for item in distribution}
    valid = metric["valid_responses"]
    negative = by_score[1] + by_score[2]
    neutral = by_score[3]
    positive = by_score[4] + by_score[5]
    possible = len(records) * len(SQD_KEYS)
    answered = valid + metric["not_applicable"]
    return {
        "ratings": distribution,
        "valid_responses": valid,
        "positive_count": positive,
        "neutral_count": neutral,
        "negative_count": negative,
        "positive_percent": _percent(positive, valid),
        "neutral_percent": _percent(neutral, valid),
        "negative_percent": _percent(negative, valid),
        "not_applicable": metric["not_applicable"],
        "unanswered": metric["unanswered"],
        "answered_count": answered,
        "answer_coverage_rate": _percent(answered, possible),
        "scorable_rate": _percent(valid, possible),
        "completion_rate": _percent(answered, possible),
        "unanswered_rate": _percent(metric["unanswered"], possible),
        "total_possible_answers": possible,
    }


def _trend(records: list[dict[str, Any]], granularity: str) -> list[dict[str, Any]]:
    daily = granularity.strip().casefold() in {"day", "daily"}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        survey_date = _record_date(record)
        if survey_date is None:
            continue
        period = survey_date.isoformat() if daily else survey_date.strftime("%Y-%m")
        buckets[period].append(record)

    rows: list[dict[str, Any]] = []
    previous_rate: float | None = None
    for period in sorted(buckets):
        metric = _ratings_metric(buckets[period], SQD_KEYS)
        current_rate = metric["positive_rate"]
        change = (
            round(float(current_rate) - previous_rate, 2)
            if current_rate is not None and previous_rate is not None
            else None
        )
        rows.append(
            {
                "period": period,
                "label": _period_label(period, daily),
                "total_responses": len(buckets[period]),
                "positive_rate": current_rate,
                "average_rating": metric["average_rating"],
                "rating": metric["rating"],
                "change": change,
                "valid_responses": metric["valid_responses"],
                "positive_responses": metric["positive_responses"],
                "not_applicable": metric["not_applicable"],
                "unanswered": metric["unanswered"],
            }
        )
        if current_rate is not None:
            previous_rate = float(current_rate)
    return rows


def _citizen_charter(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode: dict[str, Any] = {}
    awareness_numerator = awareness_denominator = 0
    saw_numerator = saw_denominator = 0
    easy_numerator = easy_denominator = 0
    helpful_numerator = helpful_denominator = 0

    for mode in ("onsite", "online"):
        mode_records = [record for record in records if _mode(record) == mode]
        question_rows: dict[str, Any] = {}
        for key, question in CC_QUESTIONS[mode].items():
            valid_options = {option["value"] for option in question["options"]}
            counts = Counter()
            unanswered = 0
            for record in mode_records:
                raw, present = _raw_cc(record, key)
                value = _optional_int(raw)
                if not present or value not in valid_options:
                    unanswered += 1
                    continue
                counts[value] += 1
            answered = sum(counts.values())
            question_rows[key] = {
                "code": question["code"],
                "prompt": question["prompt"],
                "answered": answered,
                "unanswered": unanswered,
                "distribution": [
                    {
                        "value": option["value"],
                        "label": option["label"],
                        "count": counts[option["value"]],
                        "percent": _percent(counts[option["value"]], answered),
                    }
                    for option in question["options"]
                ],
            }
        by_mode[mode] = {
            "responses": len(mode_records),
            "questions": question_rows,
        }

        for record in mode_records:
            cc1 = _optional_int(_raw_cc(record, "cc1")[0])
            if mode == "onsite" and cc1 in {1, 2, 3, 4}:
                awareness_denominator += 1
                awareness_numerator += int(cc1 in {1, 2, 3})
            elif mode == "online" and cc1 in {1, 2, 3}:
                awareness_denominator += 1
                awareness_numerator += int(cc1 in {1, 2})

            cc2 = _optional_int(_raw_cc(record, "cc2")[0])
            if mode == "onsite" and cc2 in {1, 2, 3, 4}:
                saw_denominator += 1
                saw_numerator += int(cc2 in {1, 2, 3})
                easy_denominator += 1
                easy_numerator += int(cc2 in {1, 2})
            elif mode == "online" and cc2 in {1, 2, 3}:
                saw_denominator += 1
                saw_numerator += int(cc2 in {1, 2})
                easy_denominator += 1
                easy_numerator += int(cc2 == 1)

            cc3 = _optional_int(_raw_cc(record, "cc3")[0])
            if mode == "onsite" and cc3 in {1, 2, 3}:
                helpful_denominator += 1
                helpful_numerator += int(cc3 in {1, 2})
            elif mode == "online" and cc3 in {1, 2}:
                helpful_denominator += 1
                helpful_numerator += int(cc3 == 1)

    return {
        "responses": len(records),
        "awareness_rate": _percent(awareness_numerator, awareness_denominator),
        "awareness_positive": awareness_numerator,
        "awareness_valid": awareness_denominator,
        "saw_charter_rate": _percent(saw_numerator, saw_denominator),
        "saw_charter_positive": saw_numerator,
        "saw_charter_valid": saw_denominator,
        "easy_to_see_rate": _percent(easy_numerator, easy_denominator),
        "easy_to_see_positive": easy_numerator,
        "easy_to_see_valid": easy_denominator,
        "helpfulness_rate": _percent(helpful_numerator, helpful_denominator),
        "helpfulness_positive": helpful_numerator,
        "helpfulness_valid": helpful_denominator,
        "by_mode": by_mode,
    }


def _demographic_breakdowns(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "mode": _breakdown(records, lambda record: _mode(record).title()),
        "client_type": _breakdown(
            records, lambda record: _record_value(record, "client_type")
        ),
        "sex": _breakdown(
            records, lambda record: _record_value(record, "sex")
        ),
        "age_group": _breakdown(
            records,
            _record_age_bracket_label,
        ),
        "region": _breakdown(
            records, lambda record: region_label(_record_value(record, "region"))
        ),
    }


def _service_breakdown(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display_labels: dict[str, str] = {}
    for record in records:
        services = _record_services(record) or ["Unspecified service"]
        for service in services:
            normalized = service.casefold()
            display_labels.setdefault(normalized, service)
            grouped[normalized].append(record)

    rows: list[dict[str, Any]] = []
    for normalized, group in grouped.items():
        metric = _ratings_metric(group, SQD_KEYS)
        rows.append(
            {
                "service": display_labels[normalized],
                "count": len(group),
                "percent": _percent(len(group), len(records)),
                "positive_rate": metric["positive_rate"],
                "valid_responses": metric["valid_responses"],
                "positive_responses": metric["positive_responses"],
                "not_applicable": metric["not_applicable"],
                "unanswered": metric["unanswered"],
                "average_rating": metric["average_rating"],
                "rating": metric["rating"],
            }
        )
    rows.sort(
        key=lambda row: (
            row["service"] == "Unspecified service",
            -row["count"],
            row["service"].casefold(),
        )
    )
    return rows


def _breakdown(records: list[dict[str, Any]], label_getter) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display_labels: dict[str, str] = {}
    for record in records:
        raw_label = label_getter(record)
        label = _display_value(raw_label)
        normalized = label.casefold()
        display_labels.setdefault(normalized, label)
        grouped[normalized].append(record)

    rows = []
    for normalized, group in grouped.items():
        metric = _ratings_metric(group, SQD_KEYS)
        rows.append(
            {
                "label": display_labels[normalized],
                "count": len(group),
                "percent": _percent(len(group), len(records)),
                "positive_rate": metric["positive_rate"],
                "valid_responses": metric["valid_responses"],
                "positive_responses": metric["positive_responses"],
                "not_applicable": metric["not_applicable"],
                "unanswered": metric["unanswered"],
                "average_rating": metric["average_rating"],
                "rating": metric["rating"],
            }
        )
    rows.sort(
        key=lambda row: (
            row["label"] == "Not Specified",
            -row["count"],
            row["label"].casefold(),
        )
    )
    return rows


def _feedback_items(
    records: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    result = []
    for record in _sorted_recent(records):
        text = _feedback_text(record)
        if not text:
            continue
        result.append(
            {
                "id": str(record.get("id") or ""),
                "control_number": str(record.get("control_number") or ""),
                "date": _record_date(record).isoformat()
                if _record_date(record)
                else None,
                "mode": _mode(record),
                "service": service_display(
                    _record_value(record, "service_availed")
                ),
                "client_type": _display_value(
                    _record_value(record, "client_type")
                ),
                "text": text,
            }
        )
        if len(result) >= limit:
            break
    return result


def _recent_items(
    records: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    result = []
    for record in _sorted_recent(records)[:limit]:
        metric = _ratings_metric([record], SQD_KEYS)
        result.append(
            {
                "id": str(record.get("id") or ""),
                "control_number": str(record.get("control_number") or ""),
                "date": _record_date(record).isoformat()
                if _record_date(record)
                else None,
                "mode": _mode(record),
                "service": service_display(
                    _record_value(record, "service_availed")
                ),
                "client_type": _display_value(
                    _record_value(record, "client_type")
                ),
                "positive_rate": metric["positive_rate"],
                "average_rating": metric["average_rating"],
                "rating": metric["rating"],
                "has_feedback": bool(_feedback_text(record)),
            }
        )
    return result


def _insights(
    overview: dict[str, Any],
    dimensions: list[dict[str, Any]],
    trend: list[dict[str, Any]],
    citizen_charter: dict[str, Any],
    services: list[dict[str, Any]],
    feedback_count: int,
) -> list[dict[str, Any]]:
    if not overview["total_responses"]:
        return [
            {
                "severity": "info",
                "title": "No survey data yet",
                "message": "Add a survey result to begin the live analysis.",
                "metric": None,
            }
        ]

    insights = []
    overall_rate = overview["positive_rate"]
    insights.append(
        {
            "severity": "positive" if overall_rate is not None and overall_rate >= 90 else "warning",
            "title": f"Overall rating: {overview['rating']}",
            "message": (
                f"{overview['positive_sqd_answers']}/{overview['valid_sqd_answers']} valid active SQD answers "
                f"were positive ({overall_rate:.2f}%); {overview['answered_sqd_answers']}/"
                f"{overview['total_possible_sqd_answers']} items were answered."
                if overall_rate is not None
                else "No valid active SQD answers were available."
            ),
            "metric": overall_rate,
        }
    )

    scored = [item for item in dimensions if item["positive_rate"] is not None]
    if scored:
        strongest = max(scored, key=lambda item: item["positive_rate"])
        weakest = min(scored, key=lambda item: item["positive_rate"])
        if strongest["positive_rate"] == weakest["positive_rate"]:
            insights.append(
                {
                    "severity": "positive" if strongest["positive_rate"] >= 90 else "info",
                    "title": "Dimensions are consistent",
                    "message": (
                        f"All {len(scored)} scored service quality dimensions are tied at "
                        f"{strongest['positive_rate']:.2f}% positive; use the dimension table's exact "
                        "counts and averages to monitor future separation."
                    ),
                    "metric": strongest["positive_rate"],
                }
            )
        else:
            insights.extend(
                [
                    {
                        "severity": "positive",
                        "title": f"Strongest: {strongest['dimension']}",
                        "message": (
                            f"{strongest['code']} has {strongest['positive_responses']}/"
                            f"{strongest['valid_responses']} positive answers ({strongest['positive_rate']:.2f}%) "
                            f"with a {strongest['average_rating']:.2f}/5 average."
                        ),
                        "metric": strongest["positive_rate"],
                    },
                    {
                        "severity": "warning" if weakest["positive_rate"] < 90 else "info",
                        "title": f"Focus area: {weakest['dimension']}",
                        "message": (
                            f"{weakest['code']} is lowest at {weakest['positive_responses']}/"
                            f"{weakest['valid_responses']} positive answers ({weakest['positive_rate']:.2f}%) "
                            f"with a {weakest['average_rating']:.2f}/5 average."
                        ),
                        "metric": weakest["positive_rate"],
                    },
                ]
            )

    if len(trend) >= 2 and trend[-1]["change"] is not None:
        change = float(trend[-1]["change"])
        direction = "increased" if change > 0 else "decreased" if change < 0 else "held steady"
        insights.append(
            {
                "severity": "positive" if change > 0 else "warning" if change < 0 else "info",
                "title": "Latest trend",
                "message": (
                    f"The positive-response rate {direction} by {abs(change):.2f} percentage points versus the "
                    f"previous reported period; the latest period includes {trend[-1]['total_responses']} response(s)."
                ),
                "metric": change,
            }
        )

    awareness = citizen_charter.get("awareness_rate")
    if awareness is not None:
        insights.append(
            {
                "severity": "warning" if awareness < 80 else "info",
                "title": "Citizen's Charter awareness",
                "message": (
                    f"{citizen_charter.get('awareness_positive', 0)}/"
                    f"{citizen_charter.get('awareness_valid', 0)} valid CC1 respondents were aware "
                    f"({awareness:.2f}%)."
                ),
                "metric": awareness,
            }
        )

    scored_services = [row for row in services if row["positive_rate"] is not None]
    if len(scored_services) > 1:
        lowest = min(scored_services, key=lambda row: row["positive_rate"])
        highest = max(scored_services, key=lambda row: row["positive_rate"])
        tied = lowest["positive_rate"] == highest["positive_rate"]
        if tied:
            title = "Service scores are consistent"
            severity = "positive" if lowest["positive_rate"] >= 90 else "info"
            message = (
                f"All {len(scored_services)} scored services are tied at {lowest['positive_rate']:.2f}% positive; "
                "compare their response volumes before drawing service-level conclusions."
            )
        elif lowest["positive_rate"] < 90:
            title = f"Service to review: {lowest['service']}"
            severity = "warning"
            message = (
                f"This service has {lowest['positive_responses']}/{lowest['valid_responses']} positive SQD answers "
                f"({lowest['positive_rate']:.2f}%) across {lowest['count']} response(s)."
            )
        else:
            title = f"Lowest service remains strong: {lowest['service']}"
            severity = "positive" if lowest["positive_rate"] >= 95 else "info"
            message = (
                f"The lowest service score is still {lowest['positive_responses']}/"
                f"{lowest['valid_responses']} positive SQD answers ({lowest['positive_rate']:.2f}%) "
                f"across {lowest['count']} response(s)."
            )
        insights.append(
            {
                "severity": severity,
                "title": title,
                "message": message,
                "metric": lowest["positive_rate"],
            }
        )

    if feedback_count:
        insights.append(
            {
                "severity": "info",
                "title": "Written feedback available",
                "message": f"{feedback_count} filtered response(s) include suggestions or remarks.",
                "metric": feedback_count,
            }
        )
    return insights


def _raw_rating(record: Mapping[str, Any], key: str) -> tuple[Any, bool]:
    sqd = record.get("sqd")
    if isinstance(sqd, Mapping):
        if key in sqd:
            return sqd[key], True
        upper = key.upper()
        if upper in sqd:
            return sqd[upper], True
    if key in record:
        return record[key], True
    upper = key.upper()
    if upper in record:
        return record[upper], True
    return None, False


def _raw_cc(record: Mapping[str, Any], key: str) -> tuple[Any, bool]:
    cc = record.get("cc")
    if isinstance(cc, Mapping):
        if key in cc:
            return cc[key], True
        upper = key.upper()
        if upper in cc:
            return cc[upper], True
    if key in record:
        return record[key], True
    upper = key.upper()
    if upper in record:
        return record[upper], True
    return None, False


def _normalize_rating(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        normalized = " ".join(value.split()).casefold()
        if normalized in {"", "n/a", "na", "not applicable", "null", "none"}:
            return None
        for score, label in RATING_LABELS.items():
            if normalized == label.casefold():
                return score
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer() and 1 <= number <= 5:
        return int(number)
    return None


def _is_not_applicable(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if value == 0 or value == 0.0:
        return True
    return _normalized_text(value) in {"n/a", "na", "not applicable"}


def _record_value(record: Mapping[str, Any], key: str) -> Any:
    if key in record:
        return record[key]
    meta = record.get("meta")
    if isinstance(meta, Mapping) and key in meta:
        return meta[key]
    aliases = {
        "client_type": ("customer_type",),
        "service_availed": ("service",),
        "region": ("region_of_residence",),
    }
    for alias in aliases.get(key, ()):
        if alias in record:
            return record[alias]
        if isinstance(meta, Mapping) and alias in meta:
            return meta[alias]
    return None


def _record_services(record: Mapping[str, Any]) -> list[str]:
    return normalize_service_values(_record_value(record, "service_availed"))


def _mode(record: Mapping[str, Any]) -> str:
    value = str(_record_value(record, "mode") or "onsite").strip().casefold()
    return value if value in {"onsite", "online"} else "onsite"


def _record_date(record: Mapping[str, Any]) -> date | None:
    meta = record.get("meta")
    candidates = [
        record.get("survey_date"),
        record.get("date"),
        meta.get("survey_date") if isinstance(meta, Mapping) else None,
        record.get("submitted_at"),
        record.get("created_at"),
        record.get("updated_at"),
    ]
    for candidate in candidates:
        parsed = _parse_date(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    for pattern in ("%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _matches_filter(actual: Any, wanted: Any) -> bool:
    if wanted is None:
        return True
    if isinstance(wanted, str) and wanted.strip().casefold() in {"", "all", "any"}:
        return True
    if isinstance(wanted, Iterable) and not isinstance(wanted, (str, bytes, Mapping)):
        values = [value for value in wanted if str(value or "").strip()]
        if not values:
            return True
        return any(_matches_filter(actual, value) for value in values)
    return _normalized_text(actual) == _normalized_text(wanted)


def _matches_service_filter(actual: list[str], wanted: Any) -> bool:
    if _matches_filter("", wanted):
        return True
    services = actual or ["Unspecified service"]
    return any(_matches_filter(service, wanted) for service in services)


def _matches_search(record: Mapping[str, Any], search: Any) -> bool:
    wanted = _normalized_text(search)
    if not wanted:
        return True
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    candidates = [
        record.get("control_number"),
        record.get("id"),
        _feedback_text(record),
        *meta.values(),
    ]
    return any(wanted in _normalized_text(value) for value in candidates)


def _feedback_text(record: Mapping[str, Any]) -> str:
    feedback = record.get("feedback")
    if isinstance(feedback, Mapping):
        for key in ("text", "suggestions", "remarks", "comment", "comments"):
            value = str(feedback.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(feedback or record.get("remarks") or record.get("suggestions") or "").strip()


def _sorted_recent(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda record: (
            _record_date(record) or date.min,
            str(record.get("updated_at") or record.get("created_at") or ""),
            str(record.get("id") or ""),
        ),
        reverse=True,
    )


def _record_age_bracket_label(record: Mapping[str, Any]) -> str:
    return age_bracket_label(
        _record_value(record, "age_bracket"),
        age=_record_value(record, "age"),
    )


def _age_group(age: int | None) -> str:
    """Compatibility wrapper for callers that supplied an exact age."""

    return age_bracket_label(None, age=age)


def _display_value(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text or "Not Specified"


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def _positive_limit(value: Any, default: int) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None and parsed > 0 else default


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100, 2)


def _period_label(period: str, daily: bool) -> str:
    try:
        parsed = datetime.strptime(period, "%Y-%m-%d" if daily else "%Y-%m")
    except ValueError:
        return period
    return parsed.strftime("%d %b %Y" if daily else "%b %Y")
