from __future__ import annotations

from pathlib import Path

from school_csm_control_center.mrs_diagnostics import (
    format_mrs_self_check_report,
    run_mrs_software_self_check,
    write_mrs_self_check_report,
)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    result = run_mrs_software_self_check(root)
    report = write_mrs_self_check_report(root, result)
    print(format_mrs_self_check_report(result))
    print(f"Saved: {report}")
