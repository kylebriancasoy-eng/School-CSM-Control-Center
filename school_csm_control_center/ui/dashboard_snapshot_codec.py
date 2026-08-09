"""Qt-side encoding and restoration for immutable Dashboard print snapshots."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF
from PySide6.QtGui import QImage

from school_csm_control_center.storage.dashboard_snapshot_store import (
    DashboardSnapshotStore,
    SnapshotIntegrityError,
)
from school_csm_control_center.ui.dashboard_printing import (
    DashboardSnapshot,
    PrintReportMetadata,
)


_ASSET_FIELDS = {
    "school_logo": "school_logo_path",
    "deped_logo": "deped_logo_path",
    "application_logo": "app_logo_path",
    "mosslab_logo": "mosslab_logo_path",
    "mosslab_seal": "mosslab_seal_path",
}


def encode_dashboard_snapshot(
    snapshot: DashboardSnapshot,
) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, bytes]]:
    """Return PNG, image metadata, report metadata, and frozen branding bytes."""

    if snapshot.image.isNull():
        raise ValueError("The Dashboard snapshot image is empty.")
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("The Dashboard snapshot image buffer could not be opened.")
    try:
        if not snapshot.image.save(buffer, "PNG"):
            raise RuntimeError("The Dashboard snapshot image could not be encoded as PNG.")
    finally:
        buffer.close()
    image_png = bytes(payload)

    image_meta: dict[str, Any] = {
        "logical_width": int(snapshot.logical_width),
        "logical_height": int(snapshot.logical_height),
        "render_scale": float(snapshot.render_scale),
        "pixel_width": int(snapshot.image.width()),
        "pixel_height": int(snapshot.image.height()),
        "component_bounds": {
            str(name): [
                float(bounds.x()),
                float(bounds.y()),
                float(bounds.width()),
                float(bounds.height()),
            ]
            for name, bounds in snapshot.component_bounds.items()
        },
    }
    metadata = snapshot.report_metadata
    if metadata is None:
        raise ValueError("The Dashboard snapshot has no print report metadata.")
    assets: dict[str, bytes] = {}
    asset_keys: dict[str, str] = {}
    for key, field_name in _ASSET_FIELDS.items():
        path = Path(str(getattr(metadata, field_name, "") or ""))
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if content:
            assets[key] = content
            asset_keys[field_name] = key
    report = {
        "control_number": metadata.control_number,
        "generated_at": metadata.generated_at.isoformat(),
        "system_name": metadata.system_name,
        "school_name": metadata.school_name,
        "filter_scope": metadata.filter_scope,
        "asset_keys": asset_keys,
    }
    return image_png, image_meta, report, assets


def decode_dashboard_snapshot(
    store: DashboardSnapshotStore,
    reference: Mapping[str, Any],
) -> tuple[DashboardSnapshot, dict[str, Any]]:
    """Verify and reconstruct the exact historical print image and metadata."""

    manifest = store.load(reference)
    image_bytes = store.read_blob(reference, DashboardSnapshotStore.MAIN_IMAGE_KEY)
    image = QImage.fromData(image_bytes, "PNG")
    if image.isNull():
        raise SnapshotIntegrityError("The stored Dashboard PNG cannot be decoded.")
    blobs = manifest.get("blobs")
    image_entry = (
        blobs.get(DashboardSnapshotStore.MAIN_IMAGE_KEY)
        if isinstance(blobs, Mapping)
        else None
    )
    image_meta = (
        image_entry.get("metadata")
        if isinstance(image_entry, Mapping)
        and isinstance(image_entry.get("metadata"), Mapping)
        else {}
    )
    report = manifest.get("report") if isinstance(manifest.get("report"), Mapping) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), Mapping) else {}
    control_number = str(
        report.get("control_number")
        or source.get("dashboard_control_number")
        or ""
    )
    generated_text = str(report.get("generated_at") or manifest.get("captured_at") or "")
    try:
        generated_at = datetime.fromisoformat(generated_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotIntegrityError(
            "The stored Dashboard print timestamp is invalid."
        ) from exc
    asset_paths: dict[str, str] = {}
    asset_keys = report.get("asset_keys") if isinstance(report.get("asset_keys"), Mapping) else {}
    snapshot_dir = store.path / str(manifest.get("snapshot_id") or "")
    for field_name, blob_key in asset_keys.items():
        blob = blobs.get(blob_key) if isinstance(blobs, Mapping) else None
        if isinstance(blob, Mapping):
            path = (snapshot_dir / str(blob.get("path") or "")).resolve()
            try:
                path.relative_to(snapshot_dir.resolve())
            except ValueError as exc:
                raise SnapshotIntegrityError(
                    "A stored Dashboard branding asset has an unsafe path."
                ) from exc
            asset_paths[str(field_name)] = str(path)
    metadata = PrintReportMetadata(
        control_number=control_number,
        generated_at=generated_at,
        system_name=str(report.get("system_name") or ""),
        school_name=str(report.get("school_name") or "School"),
        filter_scope=str(report.get("filter_scope") or "Complete Dashboard"),
        school_logo_path=asset_paths.get("school_logo_path", ""),
        deped_logo_path=asset_paths.get("deped_logo_path", ""),
        app_logo_path=asset_paths.get("app_logo_path", ""),
        mosslab_logo_path=asset_paths.get("mosslab_logo_path", ""),
        mosslab_seal_path=asset_paths.get("mosslab_seal_path", ""),
    )
    bounds: dict[str, QRectF] = {}
    raw_bounds = image_meta.get("component_bounds")
    if isinstance(raw_bounds, Mapping):
        for name, values in raw_bounds.items():
            if not isinstance(values, list) or len(values) != 4:
                raise SnapshotIntegrityError(
                    "The stored Dashboard component geometry is invalid."
                )
            try:
                bounds[str(name)] = QRectF(*(float(value) for value in values))
            except (TypeError, ValueError) as exc:
                raise SnapshotIntegrityError(
                    "The stored Dashboard component geometry is invalid."
                ) from exc
    snapshot = DashboardSnapshot(
        image=image,
        logical_width=int(image_meta.get("logical_width") or image.width()),
        logical_height=int(image_meta.get("logical_height") or image.height()),
        render_scale=float(image_meta.get("render_scale") or 1.0),
        component_bounds=bounds,
        report_metadata=metadata,
    )
    return snapshot, manifest
