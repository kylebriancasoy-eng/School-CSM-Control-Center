# MRS Field Validation Guide

Run `RUN_MRS_FIELD_TEST_CHECK.cmd` first. It verifies the installed English, Filipino, and Waray-Waray templates, corner-marker recognition, Code 128 round-trip decoding, the isolated field-test record store, and current printer discovery. The report now lists queues excluded as virtual, offline, paused, error-state, or non-A4.

## Use Scanner Field-test Mode

1. Start the Control Center and sign in to the CSM Sheet Scanner Remote.
2. Enable **Field-test mode** before taking or selecting the form image.
3. Upload and process the form normally. Queue limits, rotation, contrast, automatic markers, and manual corner correction remain active.
4. Review every interpreted answer.
5. Record the printer or print source, phone/camera, capture condition, expected result, observed outcome, and calibration notes.
6. Finalize the field test.

The result receives an internal ID such as `MRS-FT-2026-00001`. It does not create an official response, consume a control number, change the Printed MRS Registry, or affect CSM analysis.

Use the field-test-results icon in **MRS Printing & Tracking** to review totals, average confidence, language coverage, outcomes, and recent tests.

## Printer validation

For each eligible USB, Wi-Fi, network, or shared physical printer:

1. Confirm that virtual printers do not appear in the eligible list and are identified in diagnostics as excluded.
2. Print one English, one Filipino, and one Waray-Waray form at A4 Actual Size / 100%.
3. Measure the page border and confirm that no content is cropped.
4. Confirm that the barcode, control number, date boxes, response bubbles, and four markers are sharp and complete.
5. Test printer disconnection, paused queue, paper jam, blank output, partial output, and reprint recovery.
6. Complete the post-print confirmation and verify red-font accounting in the registry.

## Scanner validation matrix

Run field tests for each language under:

- straight and angled camera positions;
- clockwise and counter-clockwise rotation;
- bright light, shadows, and mild glare;
- faint, normal, and heavy shading;
- one blank answer and one multiple-mark answer;
- complete barcode cross-out and accidental short marks;
- damaged barcode with readable human control number;
- handwritten MM/DD/YYYY digits;
- low-resolution phone camera; and
- manual four-corner correction.

Mark the observed outcome as **Pass**, **Needs calibration**, **Fail**, or **Needs review**. Confirm that no field-test result enters official analysis.

## Queue validation

Use at least two phones. Set the simultaneous processing limit to 1, then upload several field-test scans. Confirm that:

- only one job processes at a time;
- later jobs display queue position;
- queued jobs start automatically;
- cancellation removes the correct job;
- operators cannot view another operator's image or answers;
- field-test records retain the correct Scanner Operator identity; and
- no field-test scan creates an official survey response.

Record defects with the field-test ID, language, printer, phone model, lighting condition, expected result, and generated `MRS_FIELD_TEST_REPORT.txt`.


## Recognition-quality checks added in Development Build 7

- Keep the complete A4 sheet inside the frame, but move close enough that the shorter image dimension is at least 1,200 pixels.
- Hold the phone steady and wait for camera focus before capture. The Scanner Remote now warns when the uploaded image appears blurred.
- Avoid direct glare across the Code 128 bars and the handwritten date boxes.
- Review the separate barcode and human-readable control-number crops. A printed-text OCR fallback is used only when a compatible local OCR runtime is available.
- Confirm every boxed date digit. Date constraints assist recognition but never remove the operator-review requirement for low-confidence handwriting.
- Type free-form comments manually from the corrected image; the system intentionally does not guess handwritten sentences.
