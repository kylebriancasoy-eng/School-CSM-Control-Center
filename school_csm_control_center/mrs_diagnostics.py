"""Software-side MRS template, barcode, recognition, and printer diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from school_csm_control_center.mrs_printing import (
    available_mrs_templates,
    discover_physical_printers,
    inspect_printer_queues,
    render_official_mrs_form,
    template_path,
)


def run_mrs_software_self_check(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": str(detail)})

    templates = available_mrs_templates(root)
    add(
        "Installed v0.4 language templates",
        len(templates) == 3,
        ", ".join(spec["language"] for spec in templates) or "None",
    )

    try:
        from PIL import Image
        from school_csm_control_center.web_server.scanner_engine import _tesseract_executable, process_mrs_image
    except ImportError as exc:
        add("Recognition dependencies", False, str(exc))
        return _finish(root, checks, [])


    ocr_executable = _tesseract_executable()
    add(
        "Optional printed-control OCR fallback",
        True,
        ocr_executable if ocr_executable else "Not installed; Code 128 recognition and manual control-number review remain available.",
    )

    expected_control = "CSM-MRS-123627-2026-07-0001"
    for spec in templates:
        language = spec["language"]
        try:
            path = template_path(root, spec["code"])
            with Image.open(path) as image:
                size = image.size
            add(f"{language} template geometry", size == (2480, 3508), f"{size[0]} x {size[1]} px")
            with TemporaryDirectory(prefix="mrs_check_") as temporary:
                temporary_path = Path(temporary)
                blank = process_mrs_image(path, temporary_path / "blank", root)
                expected_template = spec["template_id"]
                add(
                    f"{language} marker/template recognition",
                    blank.get("template_id") == expected_template and int(blank.get("marker_count") or 0) == 4,
                    f"{blank.get('template_id')} · markers {blank.get('marker_ids')}",
                )
                official = render_official_mrs_form(
                    root,
                    expected_control,
                    school_name="Sample School",
                    language=language,
                )
                official_path = temporary_path / "official.png"
                official.save(official_path)
                result = process_mrs_image(official_path, temporary_path / "official_result", root)
                barcode = dict(result.get("barcode") or {})
                add(
                    f"{language} Code 128 round trip",
                    barcode.get("value") == expected_control and barcode.get("status") == "Normal",
                    f"{barcode.get('value') or 'not decoded'} · {barcode.get('status') or 'unknown'}",
                )
        except Exception as exc:  # diagnostic should report all failures instead of stopping.
            add(f"{language} template check", False, f"{type(exc).__name__}: {exc}")

    inventory = inspect_printer_queues()
    printers = list(inventory.get("eligible") or [])
    excluded_printers = list(inventory.get("excluded") or [])
    if printers:
        add(
            "Connected physical printers",
            True,
            "; ".join(f"{row.get('name')} ({row.get('connection_type')}, {row.get('status')})" for row in printers),
        )
    else:
        add(
            "Connected physical printers",
            False,
            "No eligible physical printer was detected in this environment. This is expected outside the Windows deployment laptop.",
        )
    if excluded_printers:
        add(
            "Excluded printer queues",
            True,
            "; ".join(
                f"{row.get('name')} — {row.get('exclusion_reason') or 'excluded'}"
                for row in excluded_printers
            ),
        )
    else:
        add(
            "Excluded printer queues",
            True,
            "No virtual, offline, paused, error-state, or non-A4 queues were reported.",
        )
    try:
        from school_csm_control_center.storage.mrs_field_tests import MRSFieldTestStore
        summary = MRSFieldTestStore(root).summary()
        add(
            "Field-test record store",
            True,
            f"{summary.get('total', 0)} calibration-only scan(s) recorded; official analysis remains isolated.",
        )
    except Exception as exc:
        add("Field-test record store", False, f"{type(exc).__name__}: {exc}")
    return _finish(root, checks, printers)


def _finish(root: Path, checks: list[dict[str, Any]], printers: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in checks if item["passed"])
    failed = len(checks) - passed
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_root": str(root),
        "passed": passed,
        "failed": failed,
        "checks": checks,
        "printers": printers,
        "software_ready": failed == 0,
    }


def format_mrs_self_check_report(result: dict[str, Any]) -> str:
    lines = [
        "School CSM Control Center - MRS Software Self-Check",
        f"Generated: {result.get('generated_at', '')}",
        f"Summary: {result.get('passed', 0)} passed; {result.get('failed', 0)} requires attention",
        "",
    ]
    for item in result.get("checks") or []:
        status = "PASS" if item.get("passed") else "CHECK"
        lines.append(f"[{status}] {item.get('name')}: {item.get('detail')}")
    lines.extend(
        [
            "",
            "This report verifies software assets and current printer discovery only.",
            "Actual A4 scale, paper feed, jams, ink/shading, phone camera conditions, and multi-device queues require field testing.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_mrs_self_check_report(project_root: str | Path, result: dict[str, Any]) -> Path:
    path = Path(project_root) / "MRS_FIELD_TEST_REPORT.txt"
    path.write_text(format_mrs_self_check_report(result), encoding="utf-8")
    return path
