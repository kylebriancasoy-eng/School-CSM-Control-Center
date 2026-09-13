"""Controlled machine-readable form rendering and physical-printer support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import socket
import subprocess
from typing import Any, Mapping, Sequence


TEMPLATE_SPECS: dict[str, dict[str, str]] = {
    "en": {
        "code": "en", "language": "English", "suffix": "EN",
        "template_id": "CSM-MRS-A4-2026-04-EN",
        "template_filename": "CSM_MRS_Form_Template_EN_v0.4.png",
        "coordinate_map_filename": "CSM_MRS_Form_Coordinate_Map_EN_v0.4.json",
        "title": "CLIENT SATISFACTION MEASUREMENT (CSM)",
        "form_title": "MACHINE-READABLE SURVEY FORM",
        "instruction": "Shade ONE circle completely using black or blue ballpen. Keep all four corner markers visible and undamaged.",
    },
    "fil": {
        "code": "fil", "language": "Filipino", "suffix": "FIL",
        "template_id": "CSM-MRS-A4-2026-04-FIL",
        "template_filename": "CSM_MRS_Form_Template_FIL_v0.4.png",
        "coordinate_map_filename": "CSM_MRS_Form_Coordinate_Map_FIL_v0.4.json",
        "title": "PAGSUKAT NG KASIYAHAN NG KLIYENTE (CSM)",
        "form_title": "MACHINE-READABLE NA SURVEY FORM",
        "instruction": "Kulayan nang buo ang ISANG bilog gamit ang itim o asul na ballpen. Panatilihing nakikita at buo ang apat na corner marker.",
    },
    "war": {
        "code": "war", "language": "Waray-Waray", "suffix": "WAR",
        "template_id": "CSM-MRS-A4-2026-04-WAR",
        "template_filename": "CSM_MRS_Form_Template_WAR_v0.4.png",
        "coordinate_map_filename": "CSM_MRS_Form_Coordinate_Map_WAR_v0.4.json",
        "title": "PAGSUKOL HAN KATAGBAW HAN KLIYENTE (CSM)",
        "form_title": "MACHINE-READABLE NGA SURVEY FORM",
        "instruction": "Pun-a hin bug-os an USA nga lingin gamit an itom o asul nga ballpen. Pabilina nga nakikita ngan diri guba an upat nga corner marker.",
    },
}
TEMPLATE_ID = TEMPLATE_SPECS["en"]["template_id"]
TEMPLATE_FILENAME = TEMPLATE_SPECS["en"]["template_filename"]
COORDINATE_MAP_FILENAME = TEMPLATE_SPECS["en"]["coordinate_map_filename"]
VIRTUAL_PRINTER_MARKERS = (
    "print to pdf", "microsoft pdf", "xps document writer", "fax", "onenote",
    "adobe pdf", "pdfcreator", "pdf creator", "cute pdf", "dopdf", "bullzip",
    "foxit pdf", "nitro pdf", "pdf-xchange", "send to onenote", "file output",
)

# Dynamic barcode placement within the reserved MRS barcode cell. The primary
# barcode is raised to keep the readable control number clear of the printed
# spoiled-form instruction below it. The legacy region remains scanner-readable.
BARCODE_PRINT_REGION_PX = (297, 582, 1098, 108)
BARCODE_LEGACY_REGION_PX = (297, 623, 1098, 108)
BARCODE_CONTROL_TEXT_Y_PX = 704

# Keep the calibrated v0.4 geometry readable for previously issued sheets, but
# remove SQD5 from every newly rendered school form.  The row remains the same
# height so all later bubbles and all scanner coordinates stay stable.
SQD5_OMISSION_REGION_PX = (230, 2736, 2250, 2816)
SQD5_OMISSION_LABELS = {
    "en": "SQD5 OMITTED - NO RESPONSE REQUIRED",
    "fil": "HINDI KASAMA ANG SQD5 - HUWAG SAGUTAN",
    "war": "GIN-OMIT AN SQD5 - DIRI KINAHANGLAN BATONAN",
}


# Code 128 symbol widths, values 0..106. Value 106 is the seven-module stop symbol.
_CODE128_PATTERNS = (
    "212222","222122","222221","121223","121322","131222","122213","122312","132212","221213",
    "221312","231212","112232","122132","122231","113222","123122","123221","223211","221132",
    "221231","213212","223112","312131","311222","321122","321221","312212","322112","322211",
    "212123","212321","232121","111323","131123","131321","112313","132113","132311","211313",
    "231113","231311","112133","112331","132131","113123","113321","133121","313121","211331",
    "231131","213113","213311","213131","311123","311321","331121","312113","312311","332111",
    "314111","221411","431111","111224","111422","121124","121421","141122","141221","112214",
    "112412","122114","122411","142112","142211","241211","221114","413111","241112","134111",
    "111242","121142","121241","114212","124112","124211","411212","421112","421211","212141",
    "214121","412121","111143","111341","131141","114113","114311","411113","411311","113141",
    "114131","311141","411131","211412","211214","211232","2331112",
)


class MRSPrintingError(RuntimeError):
    """Raised when rendering or direct printing cannot continue."""


@dataclass(frozen=True)
class PrinterDescriptor:
    name: str
    status: str = "Ready"
    connection_type: str = "Unknown"
    driver: str = ""
    port: str = ""
    computer_name: str = ""
    a4_capable: bool = True
    color_capable: bool | None = None
    duplex_capable: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "connection_type": self.connection_type,
            "driver": self.driver,
            "port": self.port,
            "computer_name": self.computer_name,
            "a4_capable": self.a4_capable,
            "color_capable": self.color_capable,
            "duplex_capable": self.duplex_capable,
        }


def is_virtual_printer_name(name: Any) -> bool:
    normalized = " ".join(str(name or "").casefold().split())
    return not normalized or any(marker in normalized for marker in VIRTUAL_PRINTER_MARKERS)


def is_virtual_printer_descriptor(name: Any, driver: Any = "", port: Any = "") -> bool:
    combined = " ".join(str(value or "") for value in (name, driver, port))
    normalized_port = " ".join(str(port or "").casefold().split())
    if is_virtual_printer_name(combined):
        return True
    return normalized_port in {"file:", "portprompt:", "nul:", "null:"}


def _windows_printer_metadata() -> dict[str, dict[str, Any]]:
    """Read current Windows queue metadata without requiring pywin32."""

    if os.name != "nt":
        return {}
    script = r'''Get-Printer | ForEach-Object {
        [PSCustomObject]@{
            Name = $_.Name
            DriverName = $_.DriverName
            PortName = $_.PortName
            PrinterStatus = $_.PrinterStatus.ToString()
            WorkOffline = [bool]$_.WorkOffline
            Shared = [bool]$_.Shared
            Type = $_.Type.ToString()
        }
    } | ConvertTo-Json -Compress'''
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        loaded = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    rows = loaded if isinstance(loaded, list) else [loaded]
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("Name") or "").strip()
        if not name:
            continue
        result[name.casefold()] = {
            "name": name,
            "driver": str(row.get("DriverName") or "").strip(),
            "port": str(row.get("PortName") or "").strip(),
            "windows_status": str(row.get("PrinterStatus") or "").strip(),
            "work_offline": bool(row.get("WorkOffline")),
            "shared": bool(row.get("Shared")),
            "windows_type": str(row.get("Type") or "").strip(),
        }
    return result


def _windows_queue_usable(metadata: Mapping[str, Any]) -> bool:
    if bool(metadata.get("work_offline")):
        return False
    status = str(metadata.get("windows_status") or "").casefold().replace(" ", "")
    blocked = ("offline", "paused", "error", "paperjam", "notavailable", "stopped")
    return not any(token in status for token in blocked)


def infer_connection_type(name: Any, port: Any = "") -> str:
    text = f"{name} {port}".casefold()
    if any(token in text for token in ("usb", "dot4", "lpt")):
        return "USB"
    if any(token in text for token in ("\\\\", "ipp", "wsa", "wifi", "wi-fi", "network", "tcp", "ip_")):
        return "Network"
    if "com" in text:
        return "Serial"
    return "Local/Shared"


def inspect_printer_queues() -> dict[str, list[dict[str, Any]]]:
    """Return eligible and excluded printer queues with explicit reasons."""

    try:
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    except ImportError:
        return {"eligible": [], "excluded": []}
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    windows_metadata = _windows_printer_metadata()
    for info in QPrinterInfo.availablePrinters():
        name = str(info.printerName() or "").strip()
        metadata = windows_metadata.get(name.casefold(), {})
        driver = str(metadata.get("driver") or "")
        port = str(metadata.get("port") or "")
        state = info.state()
        state_name = {
            QPrinter.PrinterState.Idle: "Ready",
            QPrinter.PrinterState.Active: "Active",
            QPrinter.PrinterState.Aborted: "Aborted",
            QPrinter.PrinterState.Error: "Error",
        }.get(state, "Unknown")
        windows_status = str(metadata.get("windows_status") or "").strip()
        if windows_status and windows_status.casefold() not in {"normal", "idle", "unknown"}:
            state_name = windows_status
        supported = info.supportedPageSizes()
        a4 = any("A4" in str(page.name()) for page in supported) if supported else True
        descriptor = PrinterDescriptor(
            name=name,
            status=state_name,
            connection_type=infer_connection_type(name, port),
            driver=driver,
            port=port,
            computer_name=socket.gethostname(),
            a4_capable=bool(a4),
        ).as_dict()
        reason = ""
        if is_virtual_printer_descriptor(name, driver, port):
            reason = "Virtual or file-output printer"
        elif state_name in {"Aborted", "Error"}:
            reason = f"Queue state is {state_name}"
        elif metadata and not _windows_queue_usable(metadata):
            reason = f"Windows queue is unavailable ({windows_status or 'offline/paused/error'})"
        elif not a4:
            reason = "A4 paper size is not reported"
        if reason:
            descriptor["exclusion_reason"] = reason
            excluded.append(descriptor)
        else:
            eligible.append(descriptor)
    eligible.sort(key=lambda row: (0 if row["status"] == "Ready" else 1, row["name"].casefold()))
    excluded.sort(key=lambda row: row["name"].casefold())
    return {"eligible": eligible, "excluded": excluded}


def discover_physical_printers() -> list[dict[str, Any]]:
    """Return currently usable, A4-capable physical printer queues."""

    return inspect_printer_queues()["eligible"]


def verify_physical_printer(printer_name: str) -> dict[str, Any]:
    wanted = " ".join(str(printer_name or "").split()).casefold()
    for printer in discover_physical_printers():
        if " ".join(str(printer.get("name") or "").split()).casefold() == wanted:
            return printer
    raise MRSPrintingError("The selected physical printer is no longer connected or usable.")


def normalize_template_language(language: Any) -> str:
    value = " ".join(str(language or "English").strip().casefold().replace("_", "-").split())
    aliases = {
        "en": "en", "english": "en",
        "fil": "fil", "filipino": "fil", "tagalog": "fil", "tagalog / filipino": "fil",
        "war": "war", "waray": "war", "waray-waray": "war", "waray waray": "war",
    }
    code = aliases.get(value)
    if code is None:
        raise MRSPrintingError(f"Unsupported MRS language: {language or 'blank'}")
    return code


def template_spec(language: Any = "English") -> dict[str, str]:
    return dict(TEMPLATE_SPECS[normalize_template_language(language)])


def available_mrs_templates(project_root: str | Path) -> list[dict[str, str]]:
    root = Path(project_root)
    available: list[dict[str, str]] = []
    for code in ("en", "fil", "war"):
        spec = dict(TEMPLATE_SPECS[code])
        path = root / "assets" / "mrs_v0.4" / spec["template_filename"]
        if not path.is_file():
            path = None
        if path is not None:
            spec["path"] = str(path)
            available.append(spec)
    return available


def template_path(project_root: str | Path, language: str = "English") -> Path:
    root = Path(project_root)
    spec = template_spec(language)
    candidate = root / "assets" / "mrs_v0.4" / spec["template_filename"]
    if candidate.is_file():
        return candidate
    raise MRSPrintingError(f"The {spec['language']} v0.4 MRS template image is missing.")


def encode_code128b(value: str) -> list[int]:
    text = str(value or "")
    if not text:
        raise MRSPrintingError("A control number is required for the Code 128 barcode.")
    codes: list[int] = []
    for character in text:
        codepoint = ord(character)
        if codepoint < 32 or codepoint > 126:
            raise MRSPrintingError("Code 128B supports printable ASCII characters only.")
        codes.append(codepoint - 32)
    checksum = (104 + sum((index + 1) * code for index, code in enumerate(codes))) % 103
    return [104, *codes, checksum, 106]


def code128_modules(value: str, *, quiet_modules: int = 12) -> list[int]:
    modules: list[int] = [0] * max(10, int(quiet_modules))
    black = True
    for code in encode_code128b(value):
        pattern = _CODE128_PATTERNS[code]
        for width in pattern:
            modules.extend(([1] if black else [0]) * int(width))
            black = not black
    modules.extend([0] * max(10, int(quiet_modules)))
    return modules


def render_code128_image(value: str, width: int, height: int):
    from PIL import Image, ImageDraw

    modules = code128_modules(value)
    target_width = max(len(modules), int(width))
    target_height = max(24, int(height))
    image = Image.new("L", (target_width, target_height), 255)
    draw = ImageDraw.Draw(image)
    module_width = target_width / len(modules)
    for index, bit in enumerate(modules):
        if not bit:
            continue
        x0 = int(round(index * module_width))
        x1 = max(x0 + 1, int(round((index + 1) * module_width)))
        draw.rectangle((x0, 0, x1 - 1, target_height - 1), fill=0)
    return image


def render_official_mrs_form(
    project_root: str | Path,
    control_number: str,
    *,
    school_name: str,
    language: str = "English",
) -> Any:
    """Create one official page image in memory; no printable file is saved."""

    from PIL import Image, ImageDraw, ImageFont

    spec = template_spec(language)
    base = Image.open(template_path(project_root, spec["code"])).convert("RGB")
    draw = ImageDraw.Draw(base)
    control = str(control_number or "").strip().upper()
    if not control:
        raise MRSPrintingError("An MRS control number is required.")

    def load_font(size: int, *, bold_font: bool = False):
        names = (
            ("arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
            if bold_font else
            ("arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        )
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()
    bold = load_font(54, bold_font=True)
    regular = load_font(27, bold_font=True)
    small = load_font(22)
    control_font = load_font(43, bold_font=True)

    # New official sheets visibly omit the fees/costs item.  Covering only the
    # row interior preserves the calibrated table borders and the locations of
    # SQD6-SQD8.  The scanner retains its old SQD5 map solely so historical
    # sheets remain importable; current analysis ignores that legacy value.
    omission_left, omission_top, omission_right, omission_bottom = SQD5_OMISSION_REGION_PX
    draw.rectangle(
        (omission_left, omission_top, omission_right, omission_bottom),
        fill="white",
    )
    omission_font = load_font(30, bold_font=True)
    omission_label = SQD5_OMISSION_LABELS[spec["code"]]
    omission_box = draw.textbbox((0, 0), omission_label, font=omission_font)
    omission_width = omission_box[2] - omission_box[0]
    omission_height = omission_box[3] - omission_box[1]
    draw.text(
        (
            (omission_left + omission_right - omission_width) / 2,
            omission_top
            + ((omission_bottom - omission_top) - omission_height) / 2
            - omission_box[1],
        ),
        omission_label,
        font=omission_font,
        fill=(75, 75, 75),
    )
    # The supplied English template is branded for Calapi ES. Other schools
    # receive a clean dynamic heading from School Information.
    normalized_school = " ".join(str(school_name or "").upper().split())
    if normalized_school and normalized_school != "CALAPI ELEMENTARY SCHOOL":
        # Keep the replacement header fully inside the marker-safe area. The
        # previous right edge (2310 px) erased part of the top-right ArUco
        # marker centered at x=2322.5, which made some marker patterns (notably
        # Filipino marker 21) unreadable after inserting a different school
        # name.
        heading_box = (250, 225, 2205, 527)
        draw.rectangle(heading_box, fill="white")
        center_x = base.width // 2

        def centered_fit(text: str, y0: int, y1: int, max_size: int, min_size: int, *, bold_font: bool = False) -> None:
            chosen = load_font(min_size, bold_font=bold_font)
            for size in range(max_size, min_size - 1, -1):
                candidate = load_font(size, bold_font=bold_font)
                box = draw.textbbox((0, 0), text, font=candidate)
                if box[2] - box[0] <= heading_box[2] - heading_box[0] - 60 and box[3] - box[1] <= y1 - y0:
                    chosen = candidate
                    break
            box = draw.textbbox((0, 0), text, font=chosen)
            draw.text(
                (center_x - (box[2] - box[0]) / 2, y0 + ((y1 - y0) - (box[3] - box[1])) / 2),
                text,
                font=chosen,
                fill="black",
            )

        centered_fit("Republic of the Philippines - Department of Education", 238, 282, 32, 20)
        centered_fit(normalized_school, 282, 360, 54, 30, bold_font=True)
        centered_fit(spec["title"], 358, 418, 42, 24, bold_font=True)
        centered_fit(spec["form_title"], 416, 463, 36, 22, bold_font=True)
        centered_fit(f"Template: {spec['template_id']}  |  Language: {spec['language']}  |  Page 1 of 1", 462, 497, 26, 16)
        centered_fit(spec["instruction"], 495, 526, 23, 14, bold_font=True)

    # Barcode reserved region from the supplied coordinate map.  Both the bars
    # and the readable control number are raised within the cell so neither
    # covers the spoiled-form instruction below.
    x, y, w, h = BARCODE_PRINT_REGION_PX
    draw.rectangle((292, 570, 1401, 699), fill="white")
    barcode = render_code128_image(control, w - 80, h - 12).convert("RGB")
    base.paste(barcode.resize((w - 80, h - 12), Image.Resampling.NEAREST), (x + 40, y + 6))

    # Human-readable value beneath the raised barcode; preserve invalidation
    # dots at the sides and the printed instruction below the value.
    text_box = draw.textbbox((0, 0), control, font=control_font)
    text_width = text_box[2] - text_box[0]
    draw.rectangle((240, 698, 1455, 795), fill="white")
    draw.text((877 - text_width / 2, BARCODE_CONTROL_TEXT_Y_PX), control, font=control_font, fill="black")
    return base


def print_official_mrs_pages(
    project_root: str | Path,
    printer_name: str,
    pages: Sequence[Mapping[str, Any]],
    *,
    school_name: str,
) -> int:
    """Render and submit official MRS pages directly to one physical printer."""

    if not pages:
        raise MRSPrintingError("No MRS pages were supplied for printing.")
    verify_physical_printer(printer_name)
    try:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
        from PySide6.QtGui import QPageLayout, QPageSize
    except ImportError as exc:
        raise MRSPrintingError("PySide6 printing support is unavailable.") from exc

    info = next((item for item in QPrinterInfo.availablePrinters() if item.printerName() == printer_name), None)
    if info is None:
        raise MRSPrintingError("The selected printer disappeared before the print job was submitted.")
    printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    printer.setDocName("Official CSM Machine-Readable Survey Forms")
    printer.setOutputFormat(QPrinter.OutputFormat.NativeFormat)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    printer.setFullPage(True)
    painter = QPainter()
    if not painter.begin(printer):
        raise MRSPrintingError("Windows did not accept the MRS print job.")
    printed = 0
    try:
        for index, page in enumerate(pages):
            image = render_official_mrs_form(
                project_root,
                str(page.get("control_number") or ""),
                school_name=school_name,
                language=str(page.get("language") or "English"),
            )
            qimage = QImage(image.tobytes("raw", "RGB"), image.width, image.height, image.width * 3, QImage.Format.Format_RGB888).copy()
            target = QRectF(0, 0, printer.width(), printer.height())
            painter.drawImage(target, qimage)
            printed += 1
            if index < len(pages) - 1 and not printer.newPage():
                raise MRSPrintingError("The printer rejected a subsequent MRS page.")
    finally:
        painter.end()
    if printed != len(pages):
        raise MRSPrintingError("Not all MRS pages were submitted to the printer.")
    return printed
