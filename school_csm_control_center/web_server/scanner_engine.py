"""Server-side machine-readable CSM form recognition.

The scanner remote only captures and reviews images. This module runs on the
Control Center laptop and performs marker detection, perspective correction,
and fixed-choice mark interpretation against the bundled coordinate map.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence


ENGINE_VERSION = "0.3.0-dev11"
COORDINATE_MAP_VERSION = "0.4.2"


class ScannerRecognitionError(ValueError):
    """Raised when an uploaded image cannot be interpreted as a supported MRS form."""


@dataclass(frozen=True)
class ScannerProcessingOptions:
    rotation_degrees: int = 0
    contrast: float = 1.0
    manual_corners: tuple[tuple[float, float], ...] | None = None
    manual_language: str | None = None


def coordinate_map_path(project_root: str | Path) -> Path:
    root = Path(project_root)
    canonical = (
        root
        / "assets"
        / "mrs_v0.4"
        / "CSM_MRS_Coordinate_Map_v0.4.json"
    )
    if canonical.is_file():
        return canonical

    # Embedded/test servers can provide the browser-facing copy below the
    # static root. It is accepted only when it is byte-for-byte identical to
    # the canonical map shipped beside this scanner engine. This keeps one
    # authoritative coordinate contract while allowing an isolated server
    # data root to process jobs safely.
    static_copy = (
        root
        / "school_csm_control_center"
        / "web_server"
        / "static"
        / "CSM_MRS_Coordinate_Map_v0.4.json"
    )
    packaged_canonical = (
        Path(__file__).resolve().parents[2]
        / "assets"
        / "mrs_v0.4"
        / "CSM_MRS_Coordinate_Map_v0.4.json"
    )
    if (
        static_copy.is_file()
        and packaged_canonical.is_file()
        and _sha256_file(static_copy) == _sha256_file(packaged_canonical)
    ):
        return static_copy
    raise ScannerRecognitionError("The MRS coordinate map is missing from the Control Center package.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_coordinate_map(project_root: str | Path) -> dict[str, Any]:
    path = coordinate_map_path(project_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScannerRecognitionError("The MRS coordinate map could not be read.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("templates"), dict):
        raise ScannerRecognitionError("The MRS coordinate map is invalid.")
    if str(data.get("version") or "") != COORDINATE_MAP_VERSION:
        raise ScannerRecognitionError(
            f"The MRS coordinate map must be version {COORDINATE_MAP_VERSION}."
        )
    return data


def process_mrs_image(
    source_path: str | Path,
    output_directory: str | Path,
    project_root: str | Path,
    *,
    options: ScannerProcessingOptions | None = None,
) -> dict[str, Any]:
    """Recognize a photographed MRS form and create canonical review images."""

    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as exc:  # pragma: no cover - exercised on user machine when dependency is absent.
        raise ScannerRecognitionError(
            "The scanner recognition engine is unavailable. Install the requirements, including OpenCV."
        ) from exc

    opts = options or ScannerProcessingOptions()
    source = Path(source_path)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(source) as opened:
            pil_image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise ScannerRecognitionError("The uploaded image could not be opened.") from exc

    rotation = int(opts.rotation_degrees or 0) % 360
    if rotation not in {0, 90, 180, 270}:
        raise ScannerRecognitionError("Scanner image rotation must be 0, 90, 180, or 270 degrees.")
    if rotation:
        # PIL rotates counter-clockwise; negative produces a clockwise operator command.
        pil_image = pil_image.rotate(-rotation, expand=True)

    # Geometry detection must always use the unmodified photographed sheet.
    # Applying operator contrast before ArUco detection can merge marker cells,
    # clip marker borders, and make previously detected corners disappear.
    max_dimension = 4200
    if max(pil_image.size) > max_dimension:
        pil_image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    source_preview_path = output / "source.jpg"
    pil_image.save(source_preview_path, "JPEG", quality=90, optimize=True)

    geometry_rgb = np.asarray(pil_image)
    geometry_bgr = cv2.cvtColor(geometry_rgb, cv2.COLOR_RGB2BGR)
    geometry_gray = cv2.cvtColor(geometry_bgr, cv2.COLOR_BGR2GRAY)
    blur_score = float(cv2.Laplacian(geometry_gray, cv2.CV_64F).var())
    mean_brightness = float(geometry_gray.mean())
    image_quality = {
        "blur_score": round(blur_score, 3),
        "mean_brightness": round(mean_brightness, 3),
        "width_px": int(geometry_gray.shape[1]),
        "height_px": int(geometry_gray.shape[0]),
        "blur_warning": blur_score < 70.0,
        "resolution_warning": min(geometry_gray.shape[:2]) < 1200,
        "lighting_warning": mean_brightness < 75.0 or mean_brightness > 238.0,
    }
    coordinate_map = load_coordinate_map(project_root)
    templates = coordinate_map["templates"]

    detected, marker_detection_pass = _detect_aruco_markers_robust(geometry_gray, cv2, np)
    language, template, source_points, destination_points, marker_ids = _resolve_template_and_points(
        detected,
        templates,
        opts,
        geometry_gray.shape[1],
        geometry_gray.shape[0],
        np,
    )

    canonical_size = template.get("canonical_size_px") or [1240, 1754]
    canonical_width, canonical_height = int(canonical_size[0]), int(canonical_size[1])
    transform = cv2.getPerspectiveTransform(source_points.astype("float32"), destination_points.astype("float32"))
    canonical_geometry_bgr = cv2.warpPerspective(
        geometry_bgr,
        transform,
        (canonical_width, canonical_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )

    # Contrast is an answer-recognition and review-preview adjustment only.
    # It is deliberately applied after the page geometry has been locked.
    contrast = max(0.6, min(2.0, float(opts.contrast or 1.0)))
    canonical_rgb_image = Image.fromarray(cv2.cvtColor(canonical_geometry_bgr, cv2.COLOR_BGR2RGB))
    if abs(contrast - 1.0) > 0.001:
        canonical_rgb_image = ImageEnhance.Contrast(canonical_rgb_image).enhance(contrast)
    canonical_bgr = cv2.cvtColor(np.asarray(canonical_rgb_image), cv2.COLOR_RGB2BGR)
    canonical_gray = cv2.cvtColor(canonical_bgr, cv2.COLOR_BGR2GRAY)
    canonical_gray = cv2.normalize(canonical_gray, None, 0, 255, cv2.NORM_MINMAX)

    fields = _recognize_fields(template, canonical_gray, np)
    barcode = _recognize_barcode(
        template, canonical_geometry_bgr, canonical_bgr, canonical_gray, output, cv2, np
    )
    trusted_control = (
        str(barcode.get("value") or "")
        if str(barcode.get("status") or "") == "Normal"
        and str(barcode.get("value_source") or "") == "barcode"
        and float(barcode.get("confidence") or 0.0) >= 0.90
        else ""
    )
    date_recognition = _recognize_date_boxes(
        template,
        canonical_gray,
        output,
        project_root,
        cv2,
        np,
        control_number=trusted_control,
    )
    field_values = list(fields.values())
    detected_count = sum(1 for field in field_values if field["status"] == "detected")
    ambiguous_count = sum(1 for field in field_values if field["status"] == "ambiguous")
    blank_count = sum(1 for field in field_values if field["status"] == "blank")
    field_confidence = sum(float(field["confidence"]) for field in field_values) / max(1, len(field_values))
    marker_confidence = 0.98 if len(marker_ids) == 4 else 0.74
    overall_confidence = round(marker_confidence * 0.35 + field_confidence * 0.65, 6)

    canonical_path = output / "canonical.jpg"
    corrected_path = output / "corrected.jpg"
    cv2.imwrite(str(canonical_path), canonical_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    annotated = canonical_bgr.copy()
    _draw_recognition_overlay(annotated, template, fields, cv2)
    cv2.imwrite(str(corrected_path), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90])

    warnings: list[str] = []
    if ambiguous_count:
        warnings.append(f"{ambiguous_count} response field(s) require operator confirmation.")
    if blank_count:
        warnings.append(f"{blank_count} response field(s) were interpreted as blank.")
    if barcode.get("status") in {"Crossed Out", "Uncertain", "Unreadable", "Damaged"}:
        warnings.append(f"Barcode status requires operator review: {barcode.get('status')}.")
        if barcode.get("ocr_runtime") == "unavailable":
            warnings.append("Printed control-number OCR is not installed on this laptop. Read the printed number from the review crop and type it manually.")
    if date_recognition.get("requires_operator_review", True):
        warnings.append("The date boxes require operator review before finalization.")
    if date_recognition.get("resolved_by_date_constraints"):
        warnings.append("The date was reconstructed from multiple digit candidates and calendar rules. Confirm every digit against the hardcopy.")
    if date_recognition.get("resolved_by_control_issue_constraint"):
        warnings.append(
            "A date candidate earlier than the printed form's control-number month "
            "was rejected. Confirm the reconstructed date against the hardcopy."
        )
    if image_quality["blur_warning"]:
        warnings.append("The photograph appears blurry. Rescan closer and hold the phone steady for better barcode and handwriting recognition.")
    if image_quality["resolution_warning"]:
        warnings.append("The photograph resolution is low. Fill more of the camera frame with the form before rescanning.")
    if image_quality["lighting_warning"]:
        warnings.append("The photograph lighting is unusually dark or bright. Use even lighting without glare or deep shadows.")
    if opts.manual_corners:
        warnings.append("Perspective correction used operator-supplied page corners.")

    return {
        "engine_version": ENGINE_VERSION,
        "coordinate_map_version": str(coordinate_map.get("version") or ""),
        "language_code": language,
        "language": str(template.get("language") or language),
        "template_id": str(template.get("template_id") or ""),
        "canonical_size_px": [canonical_width, canonical_height],
        "source_size_px": [int(geometry_gray.shape[1]), int(geometry_gray.shape[0])],
        "image_quality": image_quality,
        "source_corners": [[round(float(x), 3), round(float(y), 3)] for x, y in source_points],
        "marker_ids": marker_ids,
        "marker_count": len(marker_ids),
        "marker_detection_source": "original_geometry",
        "marker_detection_pass": marker_detection_pass,
        "contrast_stage": "post_perspective_recognition",
        "fields": fields,
        "barcode": barcode,
        "date_recognition": date_recognition,
        "detected_fields": detected_count,
        "ambiguous_fields": ambiguous_count,
        "blank_fields": blank_count,
        "overall_confidence": overall_confidence,
        "warnings": warnings,
        "rotation_degrees": rotation,
        "contrast": contrast,
        "source_filename": source_preview_path.name,
        "canonical_filename": canonical_path.name,
        "corrected_filename": corrected_path.name,
    }



def _detect_aruco_markers_robust(gray: Any, cv2: Any, np: Any) -> tuple[dict[int, dict[str, Any]], str]:
    """Detect markers without using the operator's recognition contrast.

    The original grayscale image is authoritative. A mild local-equalization
    pass is used only as a fallback for uneven lighting; detections from the
    fallback are merged only for marker IDs missing from the original pass.
    """

    detected = _detect_aruco_markers(gray, cv2, np)
    if len(detected) >= 4:
        return detected, "original"

    try:
        clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
        locally_equalized = clahe.apply(gray)
        fallback = _detect_aruco_markers(locally_equalized, cv2, np)
    except Exception:
        fallback = {}

    merged = dict(detected)
    for marker_id, metadata in fallback.items():
        merged.setdefault(marker_id, metadata)
    if len(merged) > len(detected):
        return merged, "original_plus_local_equalization"
    return detected, "original"

def _detect_aruco_markers(gray: Any, cv2: Any, np: Any) -> dict[int, dict[str, Any]]:
    if not hasattr(cv2, "aruco"):
        raise ScannerRecognitionError("The installed OpenCV package does not include ArUco marker support.")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    try:
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        corners, ids, _rejected = detector.detectMarkers(gray)
    except AttributeError:  # OpenCV 4.6 compatibility.
        parameters = cv2.aruco.DetectorParameters_create()
        corners, ids, _rejected = cv2.aruco.detectMarkers(gray, dictionary, parameters=parameters)
    result: dict[int, dict[str, Any]] = {}
    if ids is None:
        return result
    for marker_corners, marker_id in zip(corners, ids.flatten()):
        points = np.asarray(marker_corners, dtype="float32").reshape(4, 2)
        center = points.mean(axis=0)
        result[int(marker_id)] = {
            "id": int(marker_id),
            "center": [float(center[0]), float(center[1])],
            "corners": [[float(x), float(y)] for x, y in points],
        }
    return result


def _resolve_template_and_points(
    detected: Mapping[int, Mapping[str, Any]],
    templates: Mapping[str, Mapping[str, Any]],
    options: ScannerProcessingOptions,
    image_width: int,
    image_height: int,
    np: Any,
) -> tuple[str, Mapping[str, Any], Any, Any, list[int]]:
    manual_language = str(options.manual_language or "").strip().casefold()
    language_order = [manual_language] if manual_language in templates else []
    language_order.extend(key for key in ("en", "fil", "war") if key in templates and key not in language_order)
    language_order.extend(key for key in templates if key not in language_order)

    if options.manual_corners:
        if len(options.manual_corners) != 4:
            raise ScannerRecognitionError("Exactly four manual page corners are required.")
        language = language_order[0]
        template = templates[language]
        source_points = _order_quad_points(np.asarray(options.manual_corners, dtype="float32"), np)
        if not _valid_quad(source_points, image_width, image_height, np):
            raise ScannerRecognitionError("The selected page corners do not form a valid quadrilateral.")
        width, height = template.get("canonical_size_px") or [1240, 1754]
        destination_points = np.asarray(
            [[0.0, 0.0], [float(width - 1), 0.0], [float(width - 1), float(height - 1)], [0.0, float(height - 1)]],
            dtype="float32",
        )
        return language, template, source_points, destination_points, sorted(detected)

    for language in language_order:
        template = templates[language]
        marker_map = template.get("markers") if isinstance(template.get("markers"), Mapping) else {}
        ordered = ("tl", "tr", "br", "bl")
        expected_ids = [int(marker_map[position]["id"]) for position in ordered]
        if all(marker_id in detected for marker_id in expected_ids):
            source_points = np.asarray([detected[marker_id]["center"] for marker_id in expected_ids], dtype="float32")
            destination_points = np.asarray(
                [marker_map[position]["center_px"] for position in ordered],
                dtype="float32",
            )
            return language, template, source_points, destination_points, expected_ids

    decoded = ", ".join(str(value) for value in sorted(detected)) or "none"
    raise ScannerRecognitionError(
        "All four machine-readable corner markers were not detected. Keep the complete A4 sheet visible, "
        f"avoid glare, and retake the photograph. Decoded marker IDs: {decoded}."
    )


def _order_quad_points(points: Any, np: Any) -> Any:
    """Return four page points in top-left, top-right, bottom-right, bottom-left order."""

    array = np.asarray(points, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    sums = array.sum(axis=1)
    differences = np.diff(array, axis=1).reshape(-1)
    ordered[0] = array[int(np.argmin(sums))]
    ordered[2] = array[int(np.argmax(sums))]
    ordered[1] = array[int(np.argmin(differences))]
    ordered[3] = array[int(np.argmax(differences))]
    return ordered


def _valid_quad(points: Any, width: int, height: int, np: Any) -> bool:
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return False
    if (points[:, 0] < 0).any() or (points[:, 0] >= width).any() or (points[:, 1] < 0).any() or (points[:, 1] >= height).any():
        return False
    # Reject repeated points, tiny selections, crossed quadrilaterals, and extremely thin page shapes.
    distances = [
        float(np.linalg.norm(points[index] - points[(index + 1) % 4]))
        for index in range(4)
    ]
    if min(distances) < min(width, height) * 0.08:
        return False
    cross_products = []
    for index in range(4):
        a = points[(index + 1) % 4] - points[index]
        b = points[(index + 2) % 4] - points[(index + 1) % 4]
        cross_products.append(float(a[0] * b[1] - a[1] * b[0]))
    if not (all(value > 0 for value in cross_products) or all(value < 0 for value in cross_products)):
        return False
    x, y = points[:, 0], points[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    return area >= width * height * 0.15


def _recognize_fields(template: Mapping[str, Any], gray: Any, np: Any) -> dict[str, dict[str, Any]]:
    raw: dict[str, dict[str, float]] = {}
    all_scores: list[float] = []
    fields = template.get("fields") if isinstance(template.get("fields"), Mapping) else {}
    for field_name, options in fields.items():
        if not isinstance(options, Mapping):
            continue
        field_scores: dict[str, float] = {}
        for code, metadata in options.items():
            if not isinstance(metadata, Mapping):
                continue
            roi = metadata.get("roi_px")
            if not isinstance(roi, Sequence) or len(roi) != 4:
                continue
            score = _sample_roi(gray, [float(value) for value in roi], np)
            field_scores[str(code)] = score
            all_scores.append(score)
        if field_scores:
            raw[str(field_name)] = field_scores

    global_base = float(median(all_scores)) if all_scores else 0.0
    results: dict[str, dict[str, Any]] = {}
    for field_name, scores in raw.items():
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        maximum = ordered[0]
        second = ordered[1] if len(ordered) > 1 else ("", 0.0)
        local_base = float(median(scores.values()))
        threshold = max(0.13, global_base + 0.10, local_base + 0.09)
        value: str | None = None
        status = "blank"
        confidence = max(0.35, min(0.92, 0.90 - (maximum[1] / max(threshold, 0.001)) * 0.38))
        marked = [item for item in ordered if item[1] >= threshold]
        if len(marked) == 1 and maximum[1] - second[1] >= 0.045:
            value = maximum[0]
            status = "detected"
            confidence = max(
                0.58,
                min(0.99, 0.58 + (maximum[1] - threshold) * 1.05 + (maximum[1] - second[1]) * 0.9),
            )
        elif marked:
            value = maximum[0]
            status = "ambiguous"
            confidence = max(0.25, min(0.69, 0.48 + (maximum[1] - second[1]) * 0.9))
        elif maximum[1] >= threshold * 0.88:
            value = maximum[0]
            status = "ambiguous"
            confidence = 0.42
        results[field_name] = {
            "value": value,
            "status": status,
            "confidence": round(float(confidence), 6),
            "threshold": round(float(threshold), 6),
            "scores": {code: round(float(score), 6) for code, score in scores.items()},
        }
    return results


def _sample_roi(gray: Any, roi: Sequence[float], np: Any) -> float:
    x, y, width, height = roi
    center_x, center_y = x + width / 2.0, y + height / 2.0
    radius = min(width, height) * 0.27
    samples: list[float] = []
    for gy in range(-6, 7):
        for gx in range(-6, 7):
            nx, ny = gx / 6.0, gy / 6.0
            if nx * nx + ny * ny > 1.0:
                continue
            px = int(round(center_x + nx * radius))
            py = int(round(center_y + ny * radius))
            px = max(0, min(gray.shape[1] - 1, px))
            py = max(0, min(gray.shape[0] - 1, py))
            samples.append(float(gray[py, px]))
    if not samples:
        return 0.0
    array = np.asarray(samples, dtype="float32")
    darkness = float((1.0 - array / 255.0).mean())
    dark_fraction = float((array < 175.0).mean())
    return 0.65 * darkness + 0.35 * dark_fraction



def _decode_code128_scanline(crop_gray: Any, np: Any) -> tuple[str, float]:
    """Decode Code 128 across many scanlines and threshold interpretations.

    Phone photographs often contain gray bars, JPEG ringing, and uneven light.
    The decoder therefore evaluates Otsu and percentile-derived thresholds
    rather than relying on one fixed darkness value.
    """

    try:
        from school_csm_control_center.mrs_printing import _CODE128_PATTERNS
    except Exception:
        return "", 0.0
    patterns = [np.asarray([int(character) for character in pattern], dtype="float32") for pattern in _CODE128_PATTERNS]
    height, width = crop_gray.shape[:2]
    if height < 8 or width < 80:
        return "", 0.0
    finite = crop_gray.astype("float32")
    try:
        histogram, _ = np.histogram(finite, bins=256, range=(0, 256))
        total = float(histogram.sum())
        weighted = float(np.dot(np.arange(256), histogram))
        background_weight = 0.0
        background_sum = 0.0
        best_variance = -1.0
        otsu = 150
        for threshold in range(1, 255):
            background_weight += float(histogram[threshold - 1])
            if background_weight <= 0:
                continue
            foreground_weight = total - background_weight
            if foreground_weight <= 0:
                break
            background_sum += float((threshold - 1) * histogram[threshold - 1])
            mean_background = background_sum / background_weight
            mean_foreground = (weighted - background_sum) / foreground_weight
            variance = background_weight * foreground_weight * (mean_background - mean_foreground) ** 2
            if variance > best_variance:
                best_variance = variance
                otsu = threshold
    except Exception:
        otsu = 150
    percentiles = [int(value) for value in np.percentile(finite, [24, 36, 48])]
    thresholds = sorted({max(55, min(215, value)) for value in [105, 135, 165, otsu, *percentiles]})
    candidates: list[tuple[str, float]] = []
    scanlines = np.linspace(max(0, height * 0.14), max(0, height * 0.86), 11)
    for threshold in thresholds:
        for y in scanlines:
            row_index = min(height - 1, max(0, int(round(float(y)))))
            # Median of three neighboring rows suppresses isolated JPEG noise.
            y0, y1 = max(0, row_index - 1), min(height, row_index + 2)
            row = np.median(crop_gray[y0:y1], axis=0)
            binary = row < threshold
            dark = np.flatnonzero(binary)
            if dark.size < 20:
                continue
            values = binary[int(dark[0]): int(dark[-1]) + 1]
            runs: list[int] = []
            current = bool(values[0])
            length = 0
            for value in values:
                bit = bool(value)
                if bit == current:
                    length += 1
                else:
                    runs.append(length)
                    current = bit
                    length = 1
            runs.append(length)
            if len(runs) < 19 or (len(runs) - 7) % 6:
                continue
            symbol_count = (len(runs) - 7) // 6
            codes: list[int] = []
            errors: list[float] = []
            alternatives: list[list[tuple[int, float]]] = []
            valid = True
            for index in range(symbol_count):
                group = np.asarray(runs[index * 6:index * 6 + 6], dtype="float32")
                normalized = group / max(1.0, float(group.sum())) * 11.0
                distances = [
                    float(np.mean((normalized - pattern) ** 2)) if len(pattern) == 6 else 999.0
                    for pattern in patterns
                ]
                ranked = sorted(range(106), key=lambda candidate: distances[candidate])
                code = int(ranked[0])
                error = distances[code]
                if error > 1.05:
                    valid = False
                    break
                codes.append(code)
                errors.append(error)
                alternatives.append([(int(candidate), float(distances[candidate])) for candidate in ranked[:9] if distances[candidate] <= 1.55])
            if not valid or len(codes) < 3:
                continue
            stop_group = np.asarray(runs[-7:], dtype="float32")
            stop_normalized = stop_group / max(1.0, float(stop_group.sum())) * 13.0
            stop_error = float(np.mean((stop_normalized - patterns[106]) ** 2))
            if stop_error > 1.05:
                continue
            start_code = codes[0]
            mode = {103: "A", 104: "B", 105: "C"}.get(start_code)
            if mode is None:
                continue

            def checksum_for(sequence: Sequence[int]) -> int:
                return (sequence[0] + sum(position * code for position, code in enumerate(sequence[1:-1], start=1))) % 103

            checksum = checksum_for(codes)
            if checksum != codes[-1]:
                best_codes: list[int] | None = None
                best_errors: list[float] | None = None
                best_delta = float("inf")
                checksum_candidates = alternatives[-1] or [(codes[-1], errors[-1])]
                for checksum_code, checksum_error in checksum_candidates:
                    for index in range(1, len(codes) - 1):
                        for candidate, candidate_error in alternatives[index]:
                            if candidate == codes[index]:
                                continue
                            trial = list(codes)
                            trial[index] = candidate
                            trial[-1] = checksum_code
                            if checksum_for(trial) != checksum_code:
                                continue
                            delta = candidate_error - errors[index] + checksum_error - errors[-1]
                            if delta < best_delta:
                                trial_errors = list(errors)
                                trial_errors[index] = candidate_error
                                trial_errors[-1] = checksum_error
                                best_codes, best_errors, best_delta = trial, trial_errors, delta
                    if best_codes is not None:
                        continue
                    for first in range(1, len(codes) - 2):
                        for first_code, first_error in alternatives[first][:5]:
                            if first_code == codes[first]:
                                continue
                            for second in range(first + 1, len(codes) - 1):
                                for second_code, second_error in alternatives[second][:5]:
                                    if second_code == codes[second]:
                                        continue
                                    trial = list(codes)
                                    trial[first] = first_code
                                    trial[second] = second_code
                                    trial[-1] = checksum_code
                                    if checksum_for(trial) != checksum_code:
                                        continue
                                    delta = (first_error - errors[first] + second_error - errors[second] + checksum_error - errors[-1])
                                    if delta < best_delta:
                                        trial_errors = list(errors)
                                        trial_errors[first] = first_error
                                        trial_errors[second] = second_error
                                        trial_errors[-1] = checksum_error
                                        best_codes, best_errors, best_delta = trial, trial_errors, delta
                if best_codes is None:
                    continue
                codes = best_codes
                errors = best_errors or errors
            output: list[str] = []
            shift_mode = ""
            for code in codes[1:-1]:
                if code == 99:
                    mode = "C"
                    continue
                if code == 100 and mode != "B":
                    mode = "B"
                    continue
                if code == 101 and mode != "A":
                    mode = "A"
                    continue
                if code == 98 and mode in {"A", "B"}:
                    shift_mode = "B" if mode == "A" else "A"
                    continue
                active_mode = shift_mode or mode
                shift_mode = ""
                if active_mode == "C":
                    if 0 <= code <= 99:
                        output.append(f"{code:02d}")
                    else:
                        valid = False
                        break
                elif active_mode == "B":
                    if 0 <= code <= 95:
                        output.append(chr(code + 32))
                    elif code not in {96, 97, 102}:
                        valid = False
                        break
                else:
                    if 0 <= code <= 63:
                        output.append(chr(code + 32))
                    elif 64 <= code <= 95:
                        output.append(chr(code - 64))
                    elif code not in {96, 97, 102}:
                        valid = False
                        break
            value = "".join(output).strip()
            if valid and _is_valid_mrs_control_number(value):
                mean_error = (sum(errors) + stop_error) / max(1, len(errors) + 1)
                confidence = max(0.52, min(0.985, 0.985 - mean_error * 0.58))
                candidates.append((value, confidence))
    if not candidates:
        return "", 0.0
    counts: dict[str, list[float]] = {}
    for value, confidence in candidates:
        counts.setdefault(value, []).append(confidence)
    value, confidences = max(counts.items(), key=lambda item: (len(item[1]), sum(item[1]) / len(item[1]), max(item[1])))
    agreement_bonus = min(0.05, max(0, len(confidences) - 1) * 0.008)
    return value, min(0.99, sum(confidences) / len(confidences) + agreement_bonus)


def _is_valid_mrs_control_number(value: str) -> bool:
    return bool(re.fullmatch(r"CSM-MRS-[0-9]{4,12}-[0-9]{4}-(?:0[1-9]|1[0-2])-[0-9]{4}", str(value or "").strip().upper()))


def _control_number_issue_month(value: str) -> date | None:
    """Return the earliest possible response date encoded by a trusted form ID."""

    match = re.fullmatch(
        r"CSM-MRS-[0-9]{4,12}-(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])-[0-9]{4}",
        str(value or "").strip().upper(),
    )
    if match is None:
        return None
    try:
        return date(int(match.group("year")), int(match.group("month")), 1)
    except ValueError:
        return None


def _normalize_control_number_ocr(text: str) -> str:
    cleaned = str(text or "").upper().replace("—", "-").replace("–", "-").replace("_", "-")
    cleaned = re.sub(r"[^A-Z0-9-]+", "", cleaned)
    start = cleaned.find("CSM")
    if start >= 0:
        cleaned = cleaned[start:]
    cleaned = cleaned.replace("CSMMRS", "CSM-MRS").replace("CSM-MRS", "CSM-MRS")
    match = re.search(r"CSM-?MRS-?([A-Z0-9]{4,12})-?([A-Z0-9]{4})-?([A-Z0-9]{2})-?([A-Z0-9]{4})", cleaned)
    if not match:
        return ""
    groups = []
    for group in match.groups():
        groups.append(group.translate(str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})))
    candidate = "CSM-MRS-" + "-".join(groups)
    return candidate if _is_valid_mrs_control_number(candidate) else ""

def _candidate_line_coverage(binary: Any, line: Sequence[int], np: Any) -> float:
    x0, y0, x1, y1 = [int(value) for value in line]
    samples = max(80, min(400, abs(x1 - x0)))
    covered = 0
    height, width = binary.shape[:2]
    for fraction in np.linspace(0.0, 1.0, samples):
        x = int(round(x0 + (x1 - x0) * float(fraction)))
        y = int(round(y0 + (y1 - y0) * float(fraction)))
        ya, yb = max(0, y - 1), min(height, y + 2)
        if 0 <= x < width and ya < yb and bool((binary[ya:yb, x] > 0).any()):
            covered += 1
    return covered / max(1, samples)


def _barcode_region_candidates(template: Mapping[str, Any]) -> list[list[float]]:
    """Return primary and legacy barcode regions in preference order."""

    candidates: list[list[float]] = []

    def add(region: Any) -> None:
        if not isinstance(region, Sequence) or isinstance(region, (str, bytes)) or len(region) != 4:
            return
        cleaned = [float(value) for value in region]
        key = tuple(round(value, 3) for value in cleaned)
        if key not in {tuple(round(value, 3) for value in item) for item in candidates}:
            candidates.append(cleaned)

    regions = template.get("barcode_regions_px")
    if isinstance(regions, Sequence) and not isinstance(regions, (str, bytes)):
        for region in regions:
            add(region)
    add(template.get("barcode_region_px"))
    legacy = template.get("legacy_barcode_regions_px")
    if isinstance(legacy, Sequence) and not isinstance(legacy, (str, bytes)):
        for region in legacy:
            add(region)
    return candidates


def _rotate_barcode_variant(image: Any, angle: float, cv2: Any) -> Any:
    if abs(float(angle)) < 0.01:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), float(angle), 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def _barcode_variants(crop_gray: Any, cv2: Any, np: Any) -> list[tuple[str, Any]]:
    variants: list[tuple[str, Any]] = [("original", crop_gray)]
    try:
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 4)).apply(crop_gray)
    except Exception:
        clahe = crop_gray
    blurred = cv2.GaussianBlur(crop_gray, (0, 0), 1.1)
    sharpened = cv2.addWeighted(crop_gray, 1.85, blurred, -0.85, 0)
    otsu = cv2.threshold(crop_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(crop_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9)
    variants.extend([
        ("original_x2", cv2.resize(crop_gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)),
        ("clahe", clahe),
        ("clahe_x2", cv2.resize(clahe, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)),
        ("unsharp", sharpened),
        ("unsharp_x2", cv2.resize(sharpened, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)),
        ("otsu", otsu),
        ("otsu_x2", cv2.resize(otsu, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)),
        ("adaptive", adaptive),
        ("rotate_-1.5", _rotate_barcode_variant(crop_gray, -1.5, cv2)),
        ("rotate_1.5", _rotate_barcode_variant(crop_gray, 1.5, cv2)),
    ])
    return variants


def _opencv_barcode_decode(image: Any, cv2: Any) -> str:
    try:
        detector = cv2.barcode_BarcodeDetector()
        bgr = image if len(image.shape) == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        result = detector.detectAndDecode(bgr)
        value = ""
        if isinstance(result, tuple):
            if len(result) == 2 and isinstance(result[0], str):
                value = result[0].strip()
            elif len(result) >= 2:
                candidate = result[1]
                if isinstance(candidate, (list, tuple)) and candidate:
                    value = str(candidate[0] or "").strip()
                elif isinstance(candidate, str):
                    value = candidate.strip()
        value = value.upper()
        return value if _is_valid_mrs_control_number(value) else ""
    except Exception:
        return ""


def _recognize_barcode_region(
    canonical_bgr: Any,
    canonical_gray: Any,
    region: Sequence[float],
    cv2: Any,
    np: Any,
) -> tuple[dict[str, Any], Any]:
    x, y, width, height = [int(round(float(value))) for value in region]
    pad_x = max(8, int(round(width * 0.018)))
    pad_y = max(6, int(round(height * 0.12)))
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1 = min(canonical_gray.shape[1], x + max(1, width) + pad_x)
    y1 = min(canonical_gray.shape[0], y + max(1, height) + pad_y)
    crop_bgr = canonical_bgr[y0:y1, x0:x1]
    crop_gray = canonical_gray[y0:y1, x0:x1]
    decoded_candidates: dict[str, list[tuple[float, str]]] = {}
    variants = _barcode_variants(crop_gray, cv2, np)
    scanline_variants = {"original", "original_x2", "clahe_x2", "unsharp_x2", "otsu_x2"}
    for variant_name, variant in variants:
        value = _opencv_barcode_decode(variant, cv2)
        if value:
            decoded_candidates.setdefault(value, []).append((0.965, f"opencv:{variant_name}"))
        if variant_name in scanline_variants:
            fallback_value, fallback_confidence = _decode_code128_scanline(variant, np)
            if fallback_value:
                decoded_candidates.setdefault(fallback_value, []).append((fallback_confidence, f"scanline:{variant_name}"))
    value = ""
    confidence = 0.0
    decoder_source = ""
    if decoded_candidates:
        value, evidence = max(decoded_candidates.items(), key=lambda item: (len(item[1]), sum(score for score, _ in item[1]), max(score for score, _ in item[1])))
        confidence = min(0.995, sum(score for score, _ in evidence) / len(evidence) + min(0.035, 0.006 * max(0, len(evidence) - 1)))
        decoder_source = max(evidence, key=lambda item: item[0])[1]

    blurred = cv2.GaussianBlur(crop_gray, (3, 3), 0)
    binary = cv2.threshold(blurred, 130, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.HoughLinesP(
        binary, 1, np.pi / 180.0,
        threshold=max(40, int(crop_gray.shape[1] * 0.18)),
        minLineLength=max(60, int(crop_gray.shape[1] * 0.62)),
        maxLineGap=max(8, int(crop_gray.shape[1] * 0.035)),
    )
    crossing_line = None
    crossing_coverage = 0.0
    if lines is not None:
        for line in lines.reshape(-1, 4):
            x_start, y_start, x_end, y_end = [int(value) for value in line]
            dx, dy = x_end - x_start, y_end - y_start
            length = float((dx * dx + dy * dy) ** 0.5)
            angle = abs(float(np.degrees(np.arctan2(dy, dx))))
            angle = min(angle, abs(180.0 - angle))
            midpoint_y = (y_start + y_end) / 2.0
            candidate = [x_start, y_start, x_end, y_end]
            coverage = _candidate_line_coverage(binary, candidate, np)
            if length >= crop_gray.shape[1] * 0.62 and angle <= 22.0 and crop_gray.shape[0] * 0.12 <= midpoint_y <= crop_gray.shape[0] * 0.88 and coverage >= 0.80:
                crossing_line = candidate
                crossing_coverage = coverage
                break
    if crossing_line is not None:
        status = "Crossed Out"
        confidence = min(0.98, max(confidence, 0.72 + crossing_coverage * 0.27))
    elif value:
        status = "Normal"
        confidence = max(0.90, confidence)
    else:
        darkness = float((crop_gray < 150).mean()) if crop_gray.size else 0.0
        status = "Unreadable" if darkness < 0.08 else "Uncertain"
        confidence = 0.35 if status == "Uncertain" else 0.2
    return {
        "value": value,
        "status": status,
        "confidence": round(confidence, 6),
        "decoder_source": decoder_source,
        "decoder_evidence_count": sum(len(items) for items in decoded_candidates.values()),
        "variants_checked": len(variants),
        "crossout_line": crossing_line,
        "crossout_coverage": round(crossing_coverage, 6),
        "region_px": [float(value) for value in region],
    }, crop_bgr


def _control_number_text_regions(template: Mapping[str, Any]) -> list[list[float]]:
    regions = template.get("control_number_text_regions_px") or template.get("control_number_text_region_px") or []
    if isinstance(regions, Sequence) and regions and isinstance(regions[0], (int, float)):
        regions = [regions]
    valid: list[list[float]] = []
    if isinstance(regions, Sequence):
        for region in regions:
            if isinstance(region, Sequence) and not isinstance(region, (str, bytes)) and len(region) == 4:
                valid.append([float(value) for value in region])
    return valid


def _tesseract_executable() -> str:
    candidates = [os.environ.get("TESSERACT_CMD", ""), shutil.which("tesseract") or ""]
    for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), os.environ.get("LOCALAPPDATA", "")):
        if root:
            candidates.extend([str(Path(root) / "Tesseract-OCR" / "tesseract.exe"), str(Path(root) / "Programs" / "Tesseract-OCR" / "tesseract.exe")])
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), "")


def _tesseract_control_number(image: Any, cv2: Any) -> tuple[str, str]:
    executable = _tesseract_executable()
    if not executable:
        return "", "unavailable"
    try:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            return "", "failed"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [executable, "stdin", "stdout", "--psm", "7", "-l", "eng", "-c", "tessedit_char_whitelist=CSMRS-0123456789"],
            input=encoded.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=8, check=False, creationflags=creationflags,
        )
        candidate = _normalize_control_number_ocr(completed.stdout.decode("utf-8", errors="ignore"))
        return candidate, "available" if completed.returncode == 0 else "failed"
    except Exception:
        return "", "failed"


def _recognize_control_number_text(template: Mapping[str, Any], canonical_gray: Any, output: Path, cv2: Any, np: Any, *, perform_ocr: bool = True) -> dict[str, Any]:
    regions = _control_number_text_regions(template)
    best_value = ""
    best_confidence = 0.0
    best_crop = None
    runtime = "unavailable"
    checked = 0
    for region in regions:
        x, y, width, height = [int(round(float(value))) for value in region]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(canonical_gray.shape[1], x + width), min(canonical_gray.shape[0], y + height)
        crop = canonical_gray[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        lower_start = max(0, int(round(crop.shape[0] * 0.40)))
        text_only = crop[lower_start:, :] if lower_start < crop.shape[0] else crop
        # Some damaged forms have no bars left, making the complete crop the
        # cleanest OCR source. Intact forms benefit from the lower text-only
        # crop because the barcode bars otherwise confuse line OCR.
        if not perform_ocr:
            best_crop = crop
            break
        variants = [crop, text_only]
        variants.append(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4)).apply(crop))
        variants.append(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4)).apply(text_only))
        variants.append(cv2.threshold(text_only, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])
        for variant in variants[:4]:
            checked += 1
            enlarged = cv2.resize(variant, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            value, state = _tesseract_control_number(enlarged, cv2)
            runtime = state if state != "unavailable" else runtime
            if value:
                best_value, best_confidence, best_crop = value, (0.80 if state == "available" else 0.65), crop
                crop_path = output / "control_number.jpg"
                cv2.imwrite(str(crop_path), best_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                return {"value": best_value, "confidence": round(best_confidence, 6), "ocr_runtime": runtime, "crop_filename": crop_path.name, "variants_checked": checked}
    if best_crop is None and regions:
        x, y, width, height = [int(round(float(value))) for value in regions[0]]
        best_crop = canonical_gray[max(0, y):min(canonical_gray.shape[0], y + height), max(0, x):min(canonical_gray.shape[1], x + width)]
    crop_path = output / "control_number.jpg"
    if best_crop is not None and getattr(best_crop, "size", 0):
        cv2.imwrite(str(crop_path), best_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    return {"value": best_value, "confidence": round(best_confidence, 6), "ocr_runtime": (runtime if perform_ocr else "not_needed"), "crop_filename": crop_path.name if crop_path.is_file() else "", "variants_checked": checked}


def _recognize_barcode(
    template: Mapping[str, Any],
    canonical_geometry_bgr: Any,
    canonical_bgr: Any,
    canonical_gray: Any,
    output: Path,
    cv2: Any,
    np: Any,
) -> dict[str, Any]:
    regions = _barcode_region_candidates(template)
    if not regions:
        return {"value": "", "status": "Unreadable", "confidence": 0.0, "crop_filename": ""}
    geometry_gray = cv2.cvtColor(canonical_geometry_bgr, cv2.COLOR_BGR2GRAY)
    candidates: list[tuple[dict[str, Any], Any]] = []
    for region in regions:
        geometry_candidate = _recognize_barcode_region(canonical_geometry_bgr, geometry_gray, region, cv2, np)
        candidates.append(geometry_candidate)
        geometry_result = geometry_candidate[0]
        if not geometry_result.get("value") and geometry_result.get("status") != "Crossed Out":
            candidates.append(_recognize_barcode_region(canonical_bgr, canonical_gray, region, cv2, np))

    def rank(item: tuple[dict[str, Any], Any]) -> tuple[int, int, float, int]:
        result = item[0]
        status = str(result.get("status") or "")
        has_value = bool(str(result.get("value") or "").strip())
        status_rank = {"Crossed Out": 4, "Normal": 3, "Uncertain": 2, "Unreadable": 1}.get(status, 0)
        return status_rank, int(has_value), float(result.get("confidence") or 0.0), int(result.get("decoder_evidence_count") or 0)

    selected, selected_crop = max(candidates, key=rank)
    selected = dict(selected)
    decoded_value = str(selected.get("value") or "")
    need_text_ocr = not decoded_value or str(selected.get("status") or "") != "Normal"
    text_result = _recognize_control_number_text(template, geometry_gray, output, cv2, np, perform_ocr=need_text_ocr)
    text_value = str(text_result.get("value") or "")
    selected["human_readable_value"] = text_value
    selected["human_readable_confidence"] = text_result.get("confidence", 0.0)
    selected["ocr_runtime"] = text_result.get("ocr_runtime", "unavailable")
    selected["control_number_crop_filename"] = text_result.get("crop_filename", "")
    if not decoded_value and text_value:
        selected["value"] = text_value
        selected["value_source"] = "human_readable_ocr"
        selected["status"] = "Uncertain" if selected.get("status") != "Crossed Out" else "Crossed Out"
        selected["confidence"] = max(float(selected.get("confidence") or 0.0), float(text_result.get("confidence") or 0.0))
    elif decoded_value:
        selected["value_source"] = "barcode"
        if text_value:
            selected["human_readable_match"] = text_value == decoded_value
            if text_value == decoded_value:
                selected["confidence"] = min(0.995, float(selected.get("confidence") or 0.0) + 0.015)
    crop_path = output / "barcode.jpg"
    cv2.imwrite(str(crop_path), selected_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    selected["crop_filename"] = crop_path.name
    selected["region_candidates_checked"] = len(candidates)
    return selected

def _digit_templates(cv2: Any, np: Any) -> dict[str, list[Any]]:
    """Build small synthetic block-digit references for tentative date OCR."""
    templates: dict[str, list[Any]] = {str(number): [] for number in range(10)}
    fonts = [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX]
    for digit in templates:
        for font in fonts:
            for scale in (0.65, 0.8, 0.95, 1.1):
                for thickness in (1, 2, 3):
                    canvas = np.full((40, 28), 255, dtype="uint8")
                    (width, height), baseline = cv2.getTextSize(digit, font, scale, thickness)
                    origin = (max(0, (28 - width) // 2), max(height + 1, (40 + height) // 2 - baseline))
                    cv2.putText(canvas, digit, origin, font, scale, 0, thickness, cv2.LINE_AA)
                    templates[digit].append(_normalize_digit_image(canvas, cv2, np))
    return templates


def _normalize_digit_image(image: Any, cv2: Any, np: Any) -> Any:
    binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    # Printed box borders or adjacent separator lines may enter a crop after
    # perspective correction. Remove components touching the crop edge and
    # retain the central digit component(s).
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    cleaned = np.zeros_like(binary)
    rows, columns = binary.shape[:2]
    for label in range(1, component_count):
        x0, y0, width0, height0, area = [int(value) for value in stats[label]]
        touches_edge = x0 <= 1 or y0 <= 1 or x0 + width0 >= columns - 1 or y0 + height0 >= rows - 1
        center_x = x0 + width0 / 2.0
        if touches_edge or area < 3 or not (columns * 0.08 <= center_x <= columns * 0.92):
            continue
        cleaned[labels == label] = 255
    if bool(cleaned.any()):
        binary = cleaned
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((30, 20), dtype="float32")
    x, y, width, height = cv2.boundingRect(points)
    digit = binary[y:y + height, x:x + width]
    scale = min(16 / max(1, width), 26 / max(1, height))
    resized = cv2.resize(digit, (max(1, int(round(width * scale))), max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((30, 20), dtype="uint8")
    oy = max(0, (30 - resized.shape[0]) // 2)
    ox = max(0, (20 - resized.shape[1]) // 2)
    canvas[oy:oy + resized.shape[0], ox:ox + resized.shape[1]] = resized[:30 - oy, :20 - ox]
    return (canvas.astype("float32") / 255.0)


def _enclosed_hole_count(normalized: Any) -> int:
    """Count enclosed background regions in a normalized digit bitmap.

    This intentionally uses a small Python flood fill instead of a library
    contour routine.  The 30x20 bitmap is tiny, and fixed connectivity keeps
    topology evidence stable across OpenCV/NumPy wheel builds.
    """

    rows, columns = (int(value) for value in normalized.shape[:2])
    ink = [
        [float(normalized[row, column]) > 0.15 for column in range(columns)]
        for row in range(rows)
    ]
    visited = [[False] * columns for _ in range(rows)]

    def flood(start_row: int, start_column: int) -> int:
        stack = [(start_row, start_column)]
        visited[start_row][start_column] = True
        area = 0
        while stack:
            row, column = stack.pop()
            area += 1
            for next_row, next_column in (
                (row - 1, column),
                (row + 1, column),
                (row, column - 1),
                (row, column + 1),
            ):
                if (
                    0 <= next_row < rows
                    and 0 <= next_column < columns
                    and not ink[next_row][next_column]
                    and not visited[next_row][next_column]
                ):
                    visited[next_row][next_column] = True
                    stack.append((next_row, next_column))
        return area

    for row in range(rows):
        for column in (0, columns - 1):
            if not ink[row][column] and not visited[row][column]:
                flood(row, column)
    for column in range(columns):
        for row in (0, rows - 1):
            if not ink[row][column] and not visited[row][column]:
                flood(row, column)

    holes = 0
    for row in range(1, rows - 1):
        for column in range(1, columns - 1):
            if not ink[row][column] and not visited[row][column]:
                if flood(row, column) >= 3:
                    holes += 1
    return min(2, holes)


def _load_handwritten_digit_model(project_root: str | Path, np: Any) -> tuple[Any, Any] | None:
    path = Path(project_root) / "assets" / "recognition" / "handwritten_digits_v1.npz"
    if not path.is_file():
        return None
    try:
        data = np.load(path, allow_pickle=False)
        return data["images"].astype("float32"), data["labels"].astype("uint8")
    except Exception:
        return None


def _handwritten_digit_prediction(normalized: Any, model: tuple[Any, Any] | None, cv2: Any, np: Any) -> tuple[str, float, dict[str, float]]:
    if model is None:
        return "", 0.0, {}
    images, labels = model
    feature = cv2.resize(normalized.astype("float32"), (8, 8), interpolation=cv2.INTER_AREA)
    feature = cv2.GaussianBlur(feature, (3, 3), 0)
    distances = np.mean((images - feature[None, :, :]) ** 2, axis=(1, 2))
    nearest = np.argsort(distances)[:17]
    weights = 1.0 / (distances[nearest] + 0.012)
    votes: dict[int, float] = {}
    minimums: dict[int, float] = {}
    for label, weight, distance in zip(labels[nearest], weights, distances[nearest]):
        key = int(label)
        votes[key] = votes.get(key, 0.0) + float(weight)
        minimums[key] = min(minimums.get(key, float("inf")), float(distance))
    total = max(1e-6, sum(votes.values()))
    scores: dict[str, float] = {}
    for label, vote in votes.items():
        ratio = vote / total
        distance_bonus = max(0.0, 0.18 - minimums[label] * 0.9)
        scores[str(label)] = max(0.06, min(0.94, ratio * 0.82 + distance_bonus))
    winner, confidence = max(scores.items(), key=lambda item: item[1])
    return winner, confidence, scores


def _recognize_date_boxes(
    template: Mapping[str, Any],
    canonical_gray: Any,
    output: Path,
    project_root: str | Path,
    cv2: Any,
    np: Any,
    *,
    control_number: str = "",
) -> dict[str, Any]:
    boxes = template.get("date_digit_boxes_px")
    if not isinstance(boxes, Sequence) or len(boxes) != 8:
        return {"digits": [], "value": "", "status": "Unavailable", "requires_operator_review": True, "crops": []}
    references = _digit_templates(cv2, np)
    reference_topology = {
        digit: [(_enclosed_hole_count(variant), variant) for variant in variants]
        for digit, variants in references.items()
    }
    handwritten_model = _load_handwritten_digit_model(project_root, np)
    results: list[dict[str, Any]] = []
    crops: list[str] = []
    for index, box in enumerate(boxes):
        x, y, width, height = [int(round(float(value))) for value in box]
        x0, y0 = max(0, x), max(0, y)
        x1 = min(canonical_gray.shape[1], x + width)
        y1 = min(canonical_gray.shape[0], y + height)
        crop = canonical_gray[y0:y1, x0:x1]
        crop_path = output / f"date_digit_{index + 1}.jpg"
        cv2.imwrite(str(crop_path), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
        crops.append(crop_path.name)
        # Remove the printed box outline before digit matching.
        inset_x = max(3, int(round(crop.shape[1] * 0.14)))
        inset_y = max(3, int(round(crop.shape[0] * 0.12)))
        inner = crop[inset_y:max(inset_y + 1, crop.shape[0] - inset_y), inset_x:max(inset_x + 1, crop.shape[1] - inset_x)]
        normalized = _normalize_digit_image(inner, cv2, np)
        topology_holes = _enclosed_hole_count(normalized)
        ink = float((normalized > 0.15).mean())
        if ink < 0.015:
            results.append({"digit": "", "confidence": 0.0, "status": "blank"})
            continue
        distances: list[tuple[float, str]] = []
        for digit, variants in reference_topology.items():
            distance = min(
                float(np.mean((normalized - variant) ** 2))
                + abs(topology_holes - variant_holes) * 0.10
                for variant_holes, variant in variants
            )
            distances.append((distance, digit))
        distances.sort()
        best, second = distances[0], distances[1]
        margin = max(0.0, second[0] - best[0])
        template_confidence = max(0.12, min(0.88, 0.72 - best[0] * 1.6 + margin * 3.0))
        handwritten_digit, handwritten_confidence, handwritten_scores = _handwritten_digit_prediction(normalized, handwritten_model, cv2, np)
        if handwritten_digit and handwritten_digit == best[1]:
            digit = best[1]
            confidence = min(0.96, max(template_confidence, handwritten_confidence) + 0.08)
            method = "synthetic+handwritten_knn"
        elif template_confidence >= 0.38:
            # The form's printed-box digits and many neat handwritten digits are
            # represented well by the synthetic references. A disagreeing KNN
            # result must not override a reasonably strong template match.
            digit = best[1]
            confidence = max(template_confidence, min(0.72, handwritten_confidence * 0.72))
            method = "synthetic_template_disagreement"
        elif handwritten_confidence > template_confidence + 0.07:
            digit = handwritten_digit
            confidence = handwritten_confidence
            method = "handwritten_knn"
        else:
            digit = best[1]
            confidence = template_confidence
            method = "synthetic_template"
        template_scores = {candidate_digit: max(0.04, min(0.90, 0.82 - candidate_distance * 1.45)) for candidate_distance, candidate_digit in distances[:4]}
        alternative_scores: dict[str, float] = {}
        for candidate_digit, candidate_confidence in [*template_scores.items(), *handwritten_scores.items()]:
            alternative_scores[candidate_digit] = max(alternative_scores.get(candidate_digit, 0.0), float(candidate_confidence))
        alternative_scores[digit] = max(alternative_scores.get(digit, 0.0), float(confidence))
        alternatives = [
            {"digit": candidate_digit, "confidence": round(candidate_confidence, 6)}
            for candidate_digit, candidate_confidence in sorted(alternative_scores.items(), key=lambda item: item[1], reverse=True)[:4]
        ]
        results.append({"digit": digit, "confidence": round(confidence, 6), "status": "tentative" if confidence < 0.76 else "recognized", "method": method, "template_candidate": best[1], "handwritten_candidate": handwritten_digit, "topology_holes": topology_holes, "alternatives": alternatives})
    digits = [entry["digit"] for entry in results]
    raw = "".join(digits)
    value = ""
    status = "Incomplete"
    resolved_by_constraints = False
    current_year = date.today().year
    issue_month = _control_number_issue_month(control_number)

    def parsed_date(raw_digits: str, *, enforce_issue_month: bool = True) -> date | None:
        if len(raw_digits) != 8 or not raw_digits.isdigit():
            return None
        month, day, year = int(raw_digits[:2]), int(raw_digits[2:4]), int(raw_digits[4:])
        if year < 2000 or year > current_year + 1:
            return None
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if enforce_issue_month and issue_month is not None and candidate < issue_month:
            return None
        return candidate

    def valid_date(raw_digits: str) -> str:
        candidate = parsed_date(raw_digits)
        return candidate.isoformat() if candidate is not None else ""

    raw_calendar_date = parsed_date(raw, enforce_issue_month=False)
    raw_rejected_by_issue_month = bool(
        raw_calendar_date is not None
        and issue_month is not None
        and raw_calendar_date < issue_month
    )
    value = valid_date(raw)
    if not value and len(results) == 8:
        from itertools import product
        candidate_lists = []
        for entry in results:
            alternatives = entry.get("alternatives") or [{"digit": entry.get("digit", ""), "confidence": entry.get("confidence", 0.0)}]
            candidate_lists.append(alternatives[:4])
        best_choice = None
        best_score = float("-inf")
        for combination in product(*candidate_lists):
            candidate_raw = "".join(str(item.get("digit") or "") for item in combination)
            candidate_value = valid_date(candidate_raw)
            if not candidate_value:
                continue
            year = int(candidate_raw[4:])
            score = sum(float(np.log(max(0.01, float(item.get("confidence") or 0.0)))) for item in combination)
            score -= abs(year - current_year) * 0.08
            if score > best_score:
                best_score = score
                best_choice = (candidate_raw, candidate_value, combination)
        if best_choice is not None:
            raw, value, combination = best_choice
            resolved_by_constraints = True
            for entry, chosen in zip(results, combination):
                if entry.get("digit") != chosen.get("digit"):
                    entry["digit"] = str(chosen.get("digit") or "")
                    entry["confidence"] = round(float(chosen.get("confidence") or 0.0), 6)
                    entry["status"] = "tentative"
                    entry["method"] = "date_constraint_resolution"
    if value:
        status = "Recognized"
    elif len(raw) == 8 and raw.isdigit():
        status = "Invalid Date"
    minimum = min((float(entry["confidence"]) for entry in results), default=0.0)
    return {
        "digits": results,
        "raw_digits": raw,
        "value": value,
        "status": status,
        "confidence": round(minimum, 6),
        "requires_operator_review": status != "Recognized" or minimum < 0.86 or resolved_by_constraints,
        "resolved_by_date_constraints": resolved_by_constraints,
        "control_issue_month": issue_month.isoformat() if issue_month is not None else "",
        "resolved_by_control_issue_constraint": bool(
            raw_rejected_by_issue_month and resolved_by_constraints
        ),
        "crops": crops,
    }


def _draw_recognition_overlay(image: Any, template: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]], cv2: Any) -> None:
    template_fields = template.get("fields") if isinstance(template.get("fields"), Mapping) else {}
    for field_name, result in fields.items():
        value = result.get("value")
        if value is None:
            continue
        field_options = template_fields.get(field_name) if isinstance(template_fields.get(field_name), Mapping) else {}
        metadata = field_options.get(str(value)) if isinstance(field_options.get(str(value)), Mapping) else {}
        center = metadata.get("center_px")
        roi = metadata.get("roi_px")
        if not isinstance(center, Sequence) or len(center) != 2:
            continue
        radius = 15
        if isinstance(roi, Sequence) and len(roi) == 4:
            radius = max(10, int(float(roi[2]) * 0.55))
        status = str(result.get("status") or "")
        color = (74, 190, 71) if status == "detected" else (0, 174, 255)
        cv2.circle(image, (int(round(float(center[0]))), int(round(float(center[1])))), radius, color, 3, cv2.LINE_AA)
