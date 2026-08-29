"""Queued scanner-image jobs executed by the Control Center laptop."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections import deque
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
import re
import shutil
from threading import Condition, RLock, Thread
import time
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.file_safety import atomic_write_json
from school_csm_control_center.web_server.scanner_engine import (
    ENGINE_VERSION,
    ScannerProcessingOptions,
    ScannerRecognitionError,
    process_mrs_image,
)


class ScannerJobError(ValueError):
    pass


class ScannerJobNotFoundError(KeyError):
    pass


class ScannerJobManager:
    """Maintain multiple uploads while limiting simultaneous image processing."""

    MAX_UPLOAD_BYTES = 12 * 1024 * 1024
    MAX_UNFINISHED_JOBS = 100
    MINIMUM_FREE_BYTES = 256 * 1024 * 1024
    COMPLETED_ARTIFACT_RETENTION_DAYS = 90
    FAILED_ARTIFACT_RETENTION_DAYS = 30

    def __init__(
        self,
        project_root: str | Path,
        *,
        processing_limit_provider: Callable[[], int],
        worker_count: int = 10,
        data_root: str | Path | None = None,
    ) -> None:
        self.install_root = Path(project_root).expanduser().resolve()
        self.project_root = storage_root_for(project_root, data_root)
        self.jobs_root = self.project_root / "data" / "csm_survey" / "scanner_jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.processing_limit_provider = processing_limit_provider
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queue: deque[str] = deque()
        self._active = 0
        self._closed = False
        self._workers: list[Thread] = []
        self._load_existing_jobs()
        self._prune_retained_artifacts()
        for index in range(max(1, min(10, int(worker_count or 10)))):
            worker = Thread(
                target=self._worker_loop,
                name=f"SchoolCSMScannerWorker-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def create_job(
        self,
        *,
        image_data_url: str,
        operator: Mapping[str, Any],
        session_id: str,
        test_mode: bool = False,
        access_transport: str = "local",
    ) -> dict[str, Any]:
        mime_type, extension, data = _decode_image_data_url(image_data_url, self.MAX_UPLOAD_BYTES)
        with self._condition:
            unfinished = sum(
                1
                for job in self._jobs.values()
                if str(job.get("status") or "")
                not in {"completed", "cancelled", "failed"}
            )
            if unfinished >= self.MAX_UNFINISHED_JOBS:
                raise ScannerJobError(
                    "The scanner queue is full. Finish or discard an existing scan before uploading another image."
                )
            try:
                free_bytes = shutil.disk_usage(self.jobs_root).free
            except OSError:
                free_bytes = self.MINIMUM_FREE_BYTES
            required = max(self.MINIMUM_FREE_BYTES, len(data) * 8)
            if free_bytes < required:
                raise ScannerJobError(
                    "The Control Center does not have enough free storage for another scanner job."
                )
        job_id = f"SCNJ-{datetime.now().year}-{uuid4().hex[:12].upper()}"
        directory = self.jobs_root / job_id
        directory.mkdir(parents=True, exist_ok=False)
        original = directory / f"original.{extension}"
        temporary_upload = directory / f".original.{extension}.tmp"
        temporary_upload.write_bytes(data)
        os.replace(temporary_upload, original)
        now = _utc_now()
        job = {
            "job_id": job_id,
            "status": "queued",
            "processing_stage": "waiting_for_processing_slot",
            "message": "The image was uploaded and is waiting for a processing slot.",
            "operator_user_id": str(operator.get("user_id") or ""),
            "operator_username": str(operator.get("username") or ""),
            "operator_display_name": str(operator.get("display_name") or ""),
            "scanner_session_id": str(session_id or ""),
            "access_transport": (
                "internet" if access_transport == "internet" else "local"
            ),
            "test_mode": bool(test_mode),
            "created_at": now,
            "uploaded_at": now,
            "processing_started_at": "",
            "processing_completed_at": "",
            "review_completed_at": "",
            "finalized_at": "",
            "cancelled_at": "",
            "original_path": original.relative_to(self.project_root).as_posix(),
            "source_path": "",
            "canonical_path": "",
            "corrected_path": "",
            "image_sha256": hashlib.sha256(data).hexdigest(),
            "mime_type": mime_type,
            "upload_bytes": len(data),
            "image_width": None,
            "image_height": None,
            "error": "",
            "recognition_error": "",
            "recognition_available": False,
            "recognition": {},
            "rotation_degrees": 0,
            "contrast": 1.0,
            "manual_corners": None,
            "manual_language": None,
            "processing_attempt": 0,
            "control_number": "",
            "record_id": "",
            "test_result_id": "",
        }
        with self._condition:
            self._persist_job_locked(job)
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._condition.notify_all()
            return self._status_locked(job_id)

    def status(self, job_id: str, operator_user_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            return self._status_locked(str(job["job_id"]))

    def list_jobs(self, operator_user_id: str, *, include_completed: bool = False) -> list[dict[str, Any]]:
        """Return recoverable jobs owned by one authenticated Scanner Operator."""

        with self._lock:
            jobs = [
                self._status_locked(job_id)
                for job_id, job in self._jobs.items()
                if str(job.get("operator_user_id") or "") == str(operator_user_id or "")
                and (include_completed or str(job.get("status") or "") != "completed")
            ]
        jobs.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("job_id") or "")), reverse=True)
        return jobs

    def source_path(self, job_id: str, operator_user_id: str) -> Path:
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            relative = str(job.get("source_path") or job.get("original_path") or "")
            if not relative:
                raise ScannerJobError("The source scanner image is not ready yet.")
        return self._safe_existing_path(relative, "The source scanner image was not found.")

    def preview_path(self, job_id: str, operator_user_id: str) -> Path:
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            relative = str(job.get("corrected_path") or "")
            if not relative:
                raise ScannerJobError("The corrected image is not ready yet.")
        return self._safe_existing_path(relative, "The corrected scanner image was not found.")

    def canonical_path(self, job_id: str, operator_user_id: str) -> Path:
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            relative = str(job.get("canonical_path") or "")
            if not relative:
                raise ScannerJobError("The canonical form image is not ready yet.")
        return self._safe_existing_path(relative, "The canonical scanner image was not found.")

    def artifact_path(self, job_id: str, operator_user_id: str, filename: str) -> Path:
        safe_name = str(filename or "").strip()
        allowed = {"barcode.jpg", "control_number.jpg", *{f"date_digit_{number}.jpg" for number in range(1, 9)}}
        if safe_name not in allowed:
            raise ScannerJobError("The requested scanner review artifact is not available.")
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            relative = str(job.get("original_path") or "")
            if not relative:
                raise ScannerJobError("The scanner job directory is unavailable.")
        original = self._safe_existing_path(relative, "The scanner job directory is unavailable.")
        target = original.parent / safe_name
        if not target.is_file():
            raise ScannerJobError("The requested scanner review artifact is unavailable.")
        return target

    def cancel(self, job_id: str, operator_user_id: str) -> dict[str, Any]:
        """Cancel or discard any unfinished scan so the operator can rescan."""

        with self._condition:
            job = self._owned_job_locked(job_id, operator_user_id)
            status = str(job.get("status") or "")
            if status == "completed":
                raise ScannerJobError("A finalized scan cannot be discarded or rescanned.")
            if status == "cancelled":
                return self._status_locked(job_id)

            job["cancel_requested"] = True
            if status == "queued":
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job["status"] = "cancelled"
                job["processing_stage"] = "cancelled"
                job["message"] = "The queued scan was discarded. A new photograph may now be taken."
                job["cancelled_at"] = _utc_now()
            elif status in {"ready_for_review", "failed"}:
                job["status"] = "cancelled"
                job["processing_stage"] = "cancelled"
                job["message"] = "The unfinished scan was discarded for a rescan."
                job["cancelled_at"] = _utc_now()
            else:
                job["message"] = "The current scan is being stopped so a new photograph can be taken."
            self._persist_job_locked(job)
            self._condition.notify_all()
            return self._status_locked(job_id)

    def command(
        self,
        job_id: str,
        operator_user_id: str,
        command: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply an operator review command and place the job back in the queue."""

        params = dict(parameters or {})
        action = str(command or "").strip().casefold()
        with self._condition:
            job = self._owned_job_locked(job_id, operator_user_id)
            status = str(job.get("status") or "")
            if status == "completed":
                raise ScannerJobError("A finalized scan can no longer be reprocessed.")
            if status in {"queued", "processing"}:
                raise ScannerJobError("Wait for the current scanner operation to finish before issuing another command.")
            if status == "cancelled":
                raise ScannerJobError("A cancelled scan cannot be reprocessed.")

            if action == "rotate_clockwise":
                job["rotation_degrees"] = (int(job.get("rotation_degrees") or 0) + 90) % 360
            elif action == "rotate_counterclockwise":
                job["rotation_degrees"] = (int(job.get("rotation_degrees") or 0) - 90) % 360
            elif action == "increase_contrast":
                job["contrast"] = min(2.0, round(float(job.get("contrast") or 1.0) + 0.15, 2))
            elif action == "decrease_contrast":
                job["contrast"] = max(0.6, round(float(job.get("contrast") or 1.0) - 0.15, 2))
            elif action == "set_corners":
                corners = params.get("corners")
                if not isinstance(corners, Sequence) or len(corners) != 4:
                    raise ScannerJobError("Exactly four page-corner coordinates are required.")
                cleaned: list[list[float]] = []
                for point in corners:
                    if not isinstance(point, Sequence) or len(point) != 2:
                        raise ScannerJobError("Every page corner must contain an X and Y coordinate.")
                    cleaned.append([float(point[0]), float(point[1])])
                job["manual_corners"] = cleaned
                language = str(params.get("language") or "").strip().casefold()
                if language not in {"en", "fil", "war"}:
                    raise ScannerJobError("Select the MRS form language before applying manual page corners.")
                job["manual_language"] = language
            elif action == "clear_corners":
                job["manual_corners"] = None
                job["manual_language"] = None
            elif action != "reprocess":
                raise ScannerJobError("Unsupported scanner processing command.")

            job.pop("cancel_requested", None)
            job["status"] = "queued"
            job["processing_stage"] = "waiting_for_processing_slot"
            if action in {"increase_contrast", "decrease_contrast"}:
                job["message"] = (
                    "The revised scan is waiting for processing. Page corners will be detected from the original "
                    "photograph; contrast will be applied only after perspective correction."
                )
            else:
                job["message"] = "The revised scan is waiting for a processing slot."
            job["error"] = ""
            job["recognition_error"] = ""
            job["recognition_available"] = False
            job["recognition"] = {}
            job["processing_started_at"] = ""
            job["processing_completed_at"] = ""
            self._queue.append(job_id)
            self._persist_job_locked(job)
            self._condition.notify_all()
            return self._status_locked(job_id)

    def finalization_context(
        self,
        job_id: str,
        operator_user_id: str,
        review_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build a reviewed v0.4 MRS submission from the owned server job."""

        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            if str(job.get("status") or "") == "completed":
                return {
                    "already_completed": True,
                    "record_id": str(job.get("record_id") or ""),
                    "control_number": str(job.get("control_number") or ""),
                    "test_mode": bool(job.get("test_mode")),
                    "test_result_id": str(job.get("test_result_id") or ""),
                }
            if str(job.get("status") or "") != "ready_for_review":
                raise ScannerJobError("The scan is not ready for final review.")
            if not bool(job.get("recognition_available")):
                raise ScannerJobError("The form has not been recognized. Retake or reprocess the image first.")
            if not bool(review_payload.get("operator_review_completed")):
                raise ScannerJobError("Confirm that the interpreted responses were reviewed against the form.")

            recognition = deepcopy(dict(job.get("recognition") or {}))
            fields = recognition.get("fields") if isinstance(recognition.get("fields"), Mapping) else {}
            barcode = recognition.get("barcode") if isinstance(recognition.get("barcode"), Mapping) else {}
            date_result = recognition.get("date_recognition") if isinstance(recognition.get("date_recognition"), Mapping) else {}
            manual = review_payload.get("manual") if isinstance(review_payload.get("manual"), Mapping) else {}
            corrections = review_payload.get("responses") if isinstance(review_payload.get("responses"), Mapping) else {}

            test_mode = bool(job.get("test_mode"))
            source_control = " ".join(str(manual.get("control_number") or barcode.get("value") or "").split())[:120].upper()
            if not source_control and not test_mode:
                raise ScannerJobError("Confirm or enter the printed MRS control number.")
            if not source_control:
                source_control = "FIELD-TEST-NO-CONTROL"
            barcode_status = str(manual.get("barcode_status") or barcode.get("status") or "Uncertain").strip()
            normalized_barcode = barcode_status.casefold().replace("_", " ")
            if normalized_barcode not in {"normal", "crossed out", "damaged", "unreadable", "uncertain"}:
                raise ScannerJobError("Select a valid barcode status.")
            if normalized_barcode in {"crossed out", "uncertain"} and not bool(review_payload.get("barcode_status_confirmed")):
                raise ScannerJobError("Confirm the crossed-out or uncertain barcode classification.")
            spoiled = normalized_barcode == "crossed out"

            final_responses: dict[str, Any] = {}
            missing: list[str] = []
            expected_fields = {
                "age_bracket", "sex", "client_type", "region", "service_availed",
                "cc1", "cc2", "cc3", *{f"sqd{number}" for number in range(9)},
            }
            unavailable = sorted(expected_fields.difference(str(name) for name in fields))
            if unavailable and not spoiled:
                raise ScannerJobError(
                    "The recognition result is incomplete. Reprocess the form before finalization: "
                    + ", ".join(name.upper() for name in unavailable) + "."
                )
            for field_name, result in fields.items():
                if not isinstance(result, Mapping):
                    continue
                automatic = result.get("value")
                selected = corrections.get(field_name, automatic)
                selected_text = str(selected if selected is not None else "").strip()
                valid_options = {str(code) for code in (result.get("scores") or {})}
                if selected_text not in valid_options:
                    if not spoiled:
                        missing.append(str(field_name).upper())
                else:
                    final_responses[str(field_name)] = selected_text
            if missing:
                raise ScannerJobError("Review and select a valid answer for: " + ", ".join(missing) + ".")

            survey_date_text = str(manual.get("survey_date") or date_result.get("value") or "").strip()
            if not survey_date_text and not spoiled:
                raise ScannerJobError("Confirm the date accomplished.")
            if survey_date_text:
                try:
                    survey_date_value = date.fromisoformat(survey_date_text)
                except ValueError as exc:
                    raise ScannerJobError("Survey date must use YYYY-MM-DD.") from exc
                if survey_date_value > date.today():
                    raise ScannerJobError("Survey date cannot be later than today.")
                survey_date_iso = survey_date_value.isoformat()
            else:
                survey_date_iso = date.today().isoformat()

            comments = str(manual.get("comments") or "").strip()[:2000]
            final_responses.update({
                "survey_date": survey_date_iso,
                "control_number": source_control,
                "comments": comments,
                "barcode_status": barcode_status,
            })
            now = _utc_now()
            job["review_completed_at"] = now
            self._persist_job_locked(job)
            return {
                "source": "scanned_hardcopy",
                "access_transport": (
                    "internet"
                    if str(job.get("access_transport") or "local") == "internet"
                    else "local"
                ),
                "test_mode": bool(job.get("test_mode")),
                "scanner_submission_id": str(job["job_id"]),
                "scanner_job_id": str(job["job_id"]),
                "verification_status": "verified",
                "response_validity": "invalid" if spoiled else "valid",
                "invalid_reason": "Respondent mistake or spoiled form" if spoiled else "",
                "barcode_status": barcode_status,
                "barcode_status_confirmed": bool(review_payload.get("barcode_status_confirmed")),
                "verified_at": now,
                "scanner_app": {
                    "name": "CSM Sheet Scanner Remote",
                    "version": ENGINE_VERSION,
                    "server_processed": True,
                },
                "template": {
                    "template_id": recognition.get("template_id"),
                    "language": recognition.get("language"),
                    "coordinate_map_version": recognition.get("coordinate_map_version"),
                    "corner_marker_ids": recognition.get("marker_ids") or [],
                },
                "recognition": {
                    "method": "server_aruco_homography_bubble_density_barcode_and_date_box_review",
                    "engine_version": recognition.get("engine_version") or ENGINE_VERSION,
                    "overall_confidence": recognition.get("overall_confidence"),
                    "detected_fields": recognition.get("detected_fields"),
                    "ambiguous_fields": recognition.get("ambiguous_fields"),
                    "blank_fields": recognition.get("blank_fields"),
                    "barcode": deepcopy(dict(barcode)),
                    "date_recognition": deepcopy(dict(date_result)),
                    "fields": {
                        field_name: {
                            "automatic_value": result.get("value"),
                            "final_value": final_responses.get(field_name),
                            "status": result.get("status"),
                            "confidence": result.get("confidence"),
                            "scores": result.get("scores") or {},
                        }
                        for field_name, result in fields.items() if isinstance(result, Mapping)
                    },
                },
                "responses": final_responses,
                "verification": {
                    "operator_review_completed": True,
                    "barcode_status_confirmed": bool(review_payload.get("barcode_status_confirmed")),
                },
                "scanner_job": {
                    "job_id": str(job["job_id"]),
                    "access_transport": (
                        "internet"
                        if str(job.get("access_transport") or "local") == "internet"
                        else "local"
                    ),
                    "capture_session_id": str(job.get("scanner_session_id") or ""),
                    "uploaded_at": str(job.get("uploaded_at") or ""),
                    "processing_started_at": str(job.get("processing_started_at") or ""),
                    "processing_completed_at": str(job.get("processing_completed_at") or ""),
                    "review_completed_at": now,
                    "original_path": str(job.get("original_path") or ""),
                    "source_path": str(job.get("source_path") or ""),
                    "canonical_path": str(job.get("canonical_path") or ""),
                    "corrected_path": str(job.get("corrected_path") or ""),
                    "image_sha256": str(job.get("image_sha256") or ""),
                },
            }

    def mark_completed(
        self,
        job_id: str,
        operator_user_id: str,
        *,
        record_id: str,
        control_number: str,
        test_result_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            job = self._owned_job_locked(job_id, operator_user_id)
            job["status"] = "completed"
            job["processing_stage"] = "completed"
            if bool(job.get("test_mode")):
                job["message"] = "The field-test scan was recorded and excluded from official analysis."
            else:
                job["message"] = "The verified scanned response was recorded successfully."
            job["record_id"] = str(record_id or "")
            job["control_number"] = str(control_number or "")
            job["test_result_id"] = str(test_result_id or "")
            job["finalized_at"] = _utc_now()
            self._persist_job_locked(job)
            return self._status_locked(job_id)

    def system_status(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_jobs": int(self._active),
                "waiting_jobs": len(self._queue),
                "processing_limit": self._processing_limit(),
            }

    def notify_limit_changed(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        deadline = time.monotonic() + 2.0
        for worker in self._workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(self._can_start_locked)
                if self._closed:
                    return
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or str(job.get("status") or "") != "queued":
                    continue
                self._active += 1
                job["status"] = "processing"
                job["processing_stage"] = "opening_image"
                job["message"] = "Opening and validating the uploaded image."
                job["processing_started_at"] = _utc_now()
                job["processing_attempt"] = int(job.get("processing_attempt") or 0) + 1
                try:
                    self._persist_job_locked(job)
                except Exception as exc:
                    job["status"] = "failed"
                    job["processing_stage"] = "failed"
                    job["message"] = "The scanner job could not be saved before processing."
                    job["error"] = str(exc)
                    job["processing_completed_at"] = _utc_now()
                    self._active = max(0, self._active - 1)
                    self._condition.notify_all()
                    continue
            try:
                self._process(job_id)
            except Exception as exc:  # Defensive boundary for background workers.
                with self._condition:
                    job = self._jobs.get(job_id)
                    if job is not None and str(job.get("status") or "") != "cancelled":
                        job["status"] = "failed"
                        job["processing_stage"] = "failed"
                        job["message"] = "The Control Center could not process this image."
                        job["error"] = str(exc)
                        job["processing_completed_at"] = _utc_now()
                        try:
                            self._persist_job_locked(job)
                        except Exception:
                            # Retain the in-memory failure and keep the worker
                            # alive; the storage condition is reported through
                            # the job status when possible.
                            pass
            finally:
                with self._condition:
                    self._active = max(0, self._active - 1)
                    self._condition.notify_all()

    def _can_start_locked(self) -> bool:
        return self._closed or (bool(self._queue) and self._active < self._processing_limit())

    def _processing_limit(self) -> int:
        try:
            value = int(self.processing_limit_provider())
        except Exception:
            value = 1
        return max(1, min(10, value))

    def _process(self, job_id: str) -> None:
        from PIL import Image, ImageOps

        with self._lock:
            job = self._jobs[job_id]
            if bool(job.get("cancel_requested")):
                self._mark_cancelled_locked(job)
                self._persist_job_locked(job)
                return
            original_path = (self.project_root / str(job["original_path"])).resolve()
            directory = original_path.parent
            options = ScannerProcessingOptions(
                rotation_degrees=int(job.get("rotation_degrees") or 0),
                contrast=float(job.get("contrast") or 1.0),
                manual_corners=tuple(tuple(float(value) for value in point) for point in job["manual_corners"])
                if job.get("manual_corners")
                else None,
                manual_language=str(job.get("manual_language") or "") or None,
            )

        with Image.open(original_path) as source:
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width < 640 or height < 640:
                raise ScannerJobError("The image resolution is too low. Use a clearer photograph with at least 640 pixels on each side.")
            with self._lock:
                job = self._jobs[job_id]
                job["image_width"] = int(width)
                job["image_height"] = int(height)
                job["processing_stage"] = "detecting_form"
                job["message"] = "Detecting the MRS corner markers and page perspective."
                self._persist_job_locked(job)
                if bool(job.get("cancel_requested")):
                    self._mark_cancelled_locked(job)
                    self._persist_job_locked(job)
                    return

        try:
            recognition = process_mrs_image(
                original_path,
                directory,
                self.install_root,
                options=options,
            )
        except ScannerRecognitionError as exc:
            # Retain a clean preview so the operator can inspect, rotate, or replace the photograph.
            fallback = directory / "corrected.jpg"
            with Image.open(original_path) as source:
                preview = ImageOps.exif_transpose(source).convert("RGB")
                if options.rotation_degrees:
                    preview = preview.rotate(-options.rotation_degrees, expand=True)
                if max(preview.size) > 2400:
                    preview.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                preview = ImageOps.autocontrast(preview, cutoff=1)
                preview.save(fallback, "JPEG", quality=88, optimize=True)
            with self._lock:
                job = self._jobs[job_id]
                source_preview = directory / "source.jpg"
                if source_preview.is_file():
                    job["source_path"] = source_preview.relative_to(self.project_root).as_posix()
                else:
                    job["source_path"] = fallback.relative_to(self.project_root).as_posix()
                job["corrected_path"] = fallback.relative_to(self.project_root).as_posix()
                job["canonical_path"] = ""
                job["recognition_available"] = False
                job["recognition"] = {}
                job["recognition_error"] = str(exc)
                job["status"] = "ready_for_review"
                job["processing_stage"] = "recognition_needs_attention"
                job["processing_completed_at"] = _utc_now()
                job["message"] = str(exc)
                self._persist_job_locked(job)
            return

        with self._lock:
            job = self._jobs[job_id]
            if bool(job.get("cancel_requested")):
                self._mark_cancelled_locked(job)
                self._persist_job_locked(job)
                return
            job["source_path"] = (directory / str(recognition["source_filename"])).relative_to(self.project_root).as_posix()
            job["canonical_path"] = (directory / str(recognition["canonical_filename"])).relative_to(self.project_root).as_posix()
            job["corrected_path"] = (directory / str(recognition["corrected_filename"])).relative_to(self.project_root).as_posix()
            job["recognition"] = recognition
            job["recognition_available"] = True
            job["recognition_error"] = ""
            job["status"] = "ready_for_review"
            job["processing_stage"] = "ready_for_operator_review"
            job["processing_completed_at"] = _utc_now()
            ambiguous = int(recognition.get("ambiguous_fields") or 0)
            blank = int(recognition.get("blank_fields") or 0)
            if ambiguous or blank:
                job["message"] = (
                    f"Recognition completed. Review {ambiguous} ambiguous and {blank} blank field(s) before finalizing."
                )
            else:
                job["message"] = "Recognition completed. Verify the interpreted responses against the corrected form."
            self._persist_job_locked(job)

    def _load_existing_jobs(self) -> None:
        """Recover queued and reviewable jobs after a Control Center restart."""

        recovered_queue: list[tuple[str, str]] = []
        for metadata_path in self.jobs_root.glob("*/job.json"):
            try:
                parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(parsed, dict):
                continue
            job_id = str(parsed.get("job_id") or metadata_path.parent.name).strip()
            if not job_id or job_id in self._jobs:
                continue
            parsed["job_id"] = job_id
            status = str(parsed.get("status") or "failed")
            original = (self.project_root / str(parsed.get("original_path") or "")).resolve()
            if not original.is_file() and status not in {"completed", "cancelled"}:
                parsed["status"] = "failed"
                parsed["processing_stage"] = "failed"
                parsed["message"] = "The uploaded scanner image is no longer available."
                parsed["error"] = parsed["message"]
            elif status in {"queued", "processing"}:
                parsed["status"] = "queued"
                parsed["processing_stage"] = "recovered_after_restart"
                parsed["message"] = "The scan was recovered after the Control Center restarted and is waiting for processing."
                parsed["processing_started_at"] = ""
                recovered_queue.append((str(parsed.get("created_at") or ""), job_id))
            parsed.pop("cancel_requested", None)
            self._jobs[job_id] = parsed
        for _created, job_id in sorted(recovered_queue):
            self._queue.append(job_id)
            self._persist_job_locked(self._jobs[job_id])

    def _persist_job_locked(self, job: Mapping[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return
        directory = self.jobs_root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "job.json"
        payload = deepcopy(dict(job))
        payload.pop("cancel_requested", None)
        atomic_write_json(target, payload)

    def _prune_retained_artifacts(self) -> None:
        """Remove old image artifacts while retaining compact job metadata."""

        now = datetime.now(timezone.utc)
        for job in self._jobs.values():
            status = str(job.get("status") or "")
            if status == "completed":
                retention_days = self.COMPLETED_ARTIFACT_RETENTION_DAYS
                stamp = job.get("finalized_at") or job.get("processing_completed_at")
            elif status in {"failed", "cancelled"}:
                retention_days = self.FAILED_ARTIFACT_RETENTION_DAYS
                stamp = (
                    job.get("cancelled_at")
                    or job.get("processing_completed_at")
                    or job.get("created_at")
                )
            else:
                continue
            parsed = _parse_utc_timestamp(stamp)
            if parsed is None or (now - parsed).days < retention_days:
                continue
            job_id = str(job.get("job_id") or "").strip()
            directory = self.jobs_root / job_id
            removed = False
            if directory.is_dir():
                for artifact in directory.iterdir():
                    if artifact.is_file() and artifact.name != "job.json":
                        try:
                            artifact.unlink()
                            removed = True
                        except OSError:
                            pass
            if removed or job.get("artifacts_retained") is not False:
                job["artifacts_retained"] = False
                job["artifacts_purged_at"] = _utc_now()
                for key in (
                    "original_path",
                    "source_path",
                    "canonical_path",
                    "corrected_path",
                ):
                    job[key] = ""
                try:
                    self._persist_job_locked(job)
                except OSError:
                    pass

    def _owned_job_locked(self, job_id: str, operator_user_id: str) -> dict[str, Any]:
        wanted = str(job_id or "").strip()
        job = self._jobs.get(wanted)
        if job is None:
            raise ScannerJobNotFoundError("Scanner job was not found.")
        if str(job.get("operator_user_id") or "") != str(operator_user_id or ""):
            raise ScannerJobNotFoundError("Scanner job was not found.")
        return job

    def _status_locked(self, job_id: str) -> dict[str, Any]:
        job = deepcopy(self._jobs[job_id])
        try:
            position = list(self._queue).index(job_id) + 1
        except ValueError:
            position = 0
        public = {
            key: value
            for key, value in job.items()
            if key not in {"original_path", "source_path", "canonical_path", "corrected_path", "cancel_requested", "manual_corners", "image_sha256"}
        }
        if not str(public.get("control_number") or ""):
            public.pop("control_number", None)
        if not str(public.get("record_id") or ""):
            public.pop("record_id", None)
        public["queue_position"] = position if public.get("status") == "queued" else 0
        public.update(self.system_status())
        if str(job.get("source_path") or job.get("original_path") or ""):
            public["source_url"] = f"/api/scanner/jobs/{job_id}/source"
        if str(job.get("corrected_path") or ""):
            public["preview_url"] = f"/api/scanner/jobs/{job_id}/preview"
        if str(job.get("canonical_path") or ""):
            public["canonical_url"] = f"/api/scanner/jobs/{job_id}/canonical"
        return public

    def _safe_existing_path(self, relative: str, missing_message: str) -> Path:
        path = (self.project_root / relative).resolve()
        try:
            path.relative_to(self.project_root.resolve())
        except ValueError as exc:
            raise ScannerJobError("The scanner image path is invalid.") from exc
        if not path.is_file():
            raise ScannerJobError(missing_message)
        return path

    @staticmethod
    def _mark_cancelled_locked(job: dict[str, Any]) -> None:
        job["status"] = "cancelled"
        job["processing_stage"] = "cancelled"
        job["message"] = "The scan was cancelled."
        job["cancelled_at"] = _utc_now()
        job["processing_completed_at"] = _utc_now()


def _decode_image_data_url(value: str, maximum_bytes: int) -> tuple[str, str, bytes]:
    text = str(value or "")
    match = re.match(r"^data:image/(jpeg|jpg|png|webp);base64,(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise ScannerJobError("Upload a JPEG, PNG, or WebP image.")
    subtype = match.group(1).casefold()
    mime_type = "image/jpeg" if subtype in {"jpeg", "jpg"} else f"image/{subtype}"
    extension = "jpg" if subtype in {"jpeg", "jpg"} else subtype
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ScannerJobError("The uploaded image data is invalid.") from exc
    if not data:
        raise ScannerJobError("The uploaded image is empty.")
    if len(data) > maximum_bytes:
        raise ScannerJobError("The uploaded image exceeds the 12 MB limit.")
    return mime_type, extension, data


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
