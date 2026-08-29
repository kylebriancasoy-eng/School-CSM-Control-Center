# School CSM Control Center 0.5.0

School CSM Control Center is an offline-first Windows application for collecting,
scanning, analyzing, printing, and auditing School Client Satisfaction Measurement
results.

## Operator installation

Operators install and run only compiled Windows programs. They do not need Python
and should not open the source-tree command files.

1. Download [`School-CSM-Control-Center-Setup.exe`](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases/latest/download/School-CSM-Control-Center-Setup.exe)
   from the [latest GitHub release](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center/releases/latest).
2. Open Setup and approve the Windows administrator prompt.
3. Select **Install**. The application is placed in
   `C:\Program Files (x86)\MoSSLab\School CSM Control Center`.
4. Start **School CSM Control Center** from its desktop or Start menu shortcut.

The same Setup program provides **Check for Update**, **Repair**, **Roll Back**,
and **Uninstall**. Every downloaded release is checked against its published size
and SHA-256 checksum before it can replace the installed application. See
[`docs/INSTALLATION.md`](docs/INSTALLATION.md) for the complete operator guide.

Survey records, settings, print evidence, narrative revisions, exports, logs, and
backups remain in `Documents\MoSSLab Data\School CSM Control Center`. Normal
install, update, repair, rollback, and uninstall operations preserve that folder,
the optional OpenAI API key, Internet Gateway device credentials, and an
administrator-installed provider configuration. The uninstall data-removal option
must be selected explicitly; it removes the current account's standard saved-data
folder and named application credentials, but retains the machine-wide provider
configuration.

## 0.5.0 highlights

- The optional Internet Gateway adds a stable School-ID HTTPS address while the
  school computer remains the authoritative server and data store. With no valid
  deployment provider configuration, the application stays **Local Only** and
  makes no registration-service or tunnel requests.
- Browser-based Windows Hello or security-key passkeys authorize initial Internet
  Server registration, additional administrator passkeys, and one-active-server
  transfer. The Control Center does not collect OpenAI, ChatGPT, GitHub, or
  gateway passwords.
- Registration and transfer credentials are committed with the corresponding
  authorization change, delivered only to the destination installation, and
  acknowledged only after Windows Credential Manager and local registration
  state have been saved. An interrupted response can safely retry the same
  short-lived code without creating a second registration or transfer.
- A live source server can create an encrypted, destination-bound `.mossmig`
  Server-and-Data package. A separately prepared encrypted `.mossbak` portable
  recovery backup supports backup-assisted replacement when the source computer
  is unavailable. Each 256-bit key is shown once and must travel separately from
  its file.
- Imports decrypt only into inert staging, enforce manifest, identity, version,
  path, hash, and record-count checks, and atomically activate data with rollback
  evidence before Internet Server authority can move. School-owned scanner
  previews are included in both migration and recovery-backup round trips.
- A saved `ACTIVE` label never enables Internet listening by itself. Each app
  process must complete a fresh signed provider check before the server may bind
  its private tunnel origin; failed or lost authorization stops the tunnel and
  returns the local server to its selected-interface binding.
- Public access-session creation, submissions, scanner operations, and
  installation-secret checks use bounded stores and throttles that do not retain
  raw client addresses.
- The compiled Windows package includes the integrity-pinned tunnel connector;
  operators still run only the application EXE and Setup EXE and do not need
  Python installed.
- Provider endpoints, managed domain, public signing keys, Cloudflare secrets,
  and production hosting remain deployment-owned and are intentionally absent
  from the public release.

## 0.4.2 highlights

- Setup now survives temporary GitHub connection closures with five bounded
  attempts and clear retry messages instead of immediately aborting an install.
- An interrupted application-package transfer continues from its verified
  partial byte position when the server supports ranges, or safely restarts when
  it does not. The completed package must still match the release's exact size
  and SHA-256 checksum before extraction or installation.

## 0.4.1 highlights

- Server Settings now provides an opt-in **Start with Windows and run Survey
  Server in background** switch. It starts the compiled app in the Windows
  notification area and starts the saved local server automatically.
- The notification-area menu shows server status and provides Open Control
  Center, Start Server, Stop Server, Open Survey Form, startup-toggle, and
  explicit Exit actions. Closing the window keeps the server running only while
  background startup is enabled.
- Setup authorizes the installed EXE for Private, local-subnet access once, so
  automatic server startup does not repeatedly request administrator approval.
  The rule and per-user startup entry are removed by uninstall while saved CSM
  data remains preserved.

## 0.4.0 highlights

- Every successful Dashboard print retains an immutable, checksummed snapshot of
  the exact rendered Dashboard, aggregate data, filters, school identity, graphs,
  and branding used for that print.
- Successful print-history rows provide **Narrative Report** and **Reprint
  Dashboard** actions. Historical reprints use only the stored snapshot and retain
  the original Dashboard print control number.
- Narrative Reports work locally without an account or internet connection. They
  support fixed-section editing, immutable revisions, explicit approval,
  approval invalidation after edits, audited prints/reprints, repeated Dashboard
  control numbers, required signatures, and the mandatory validity statement.
- Optional OpenAI-assisted drafting is used only when the operator selects it.
  It sends aggregate snapshot values, requests structured output, rejects unknown
  numerical claims, and uses the operator's own API key stored in Windows
  Credential Manager. The app never asks for an OpenAI or ChatGPT password.
- Direct IPv4 access is the reliable primary local Survey Form address and must
  pass the built-in health check before it is advertised. The friendly
  `.home.arpa` address is shown only when the app-owned DNS responder is actually
  running and passes its resolution self-test.
- The end-user package contains a compiled EXE and runtime assets only. Online
  Setup performs verified install/update/repair, keeps a verified rollback copy,
  and automatically restores the previous installation if replacement fails.

## Repository and releases

The [public repository](https://github.com/kylebriancasoy-eng/School-CSM-Control-Center)
contains application sources, automated tests, a PyInstaller
one-folder definition, a dependency-free .NET Framework online maintenance
program, deterministic release/checksum tools, and GitHub Actions workflows.
Maintainer instructions are in [`docs/RELEASING.md`](docs/RELEASING.md).

## Development Build 11 baseline and historical release notes

Version 0.4.0 was developed from the verified v0.3.0 Development Build 11
baseline. The notes below are retained as historical implementation context.

## Development Build 11 — durable shared storage and operational hardening

- Mutable records, print audits, scanner jobs, exports, logs, backups, and settings now live under `Documents\MoSSLab Data\School CSM Control Center`.
- The application creates its stable directory on first launch and performs a non-destructive, SHA-256-verified migration of legacy data left beside an older build.
- Startup now prevents two Control Center processes from editing the same records concurrently.
- JSON/MOSSJSON stores use cross-process locks, atomic replacement, last-known-good backups, and fail-closed corrupt-file handling.
- Dashboard print controls reserve an audit identity before preview and retain submitted, failed, cancelled, and interrupted outcomes in History.
- The main board contains only Dashboard and History; MRS Printing, Survey Server, and School Information open as in-window workspace overlays.
- Confirmation prompts use in-window overlays. The scanner remote also uses an accessible overlay instead of browser confirmation windows.
- Browser Survey demographics and the 18-item service catalog now share one canonical schema with the desktop and scanner workflows.
- Browser and Control Center responses use the requested monthly `YYYY-MM-####` control-number format and one collision-safe sequence; registered MRS forms retain their School-ID-bearing printed identity.
- The Add Survey Result drawer uses an overlay calendar, exclusive tick boxes for Client Type and Sex, a collapsible school-transaction checklist, field prefill/lock controls, and cell-wide SQD selection. Every visible SQD row is required before submission.
- Dashboard cards use larger readable text, a connected satisfaction-trend line, and a compact SQD response mix with the enlarged chart on the right.
- Dashboard printing uses an in-window multi-page vertical preview, printer and advanced driver settings, timestamped control numbers, machine-readable codes, and auditable submitted/failed/cancelled/interrupted print history.
- MRS printing and recognition both use the canonical v0.4.2 assets in `assets\mrs_v0.4`.
- Dynamic school headings are constrained to the marker-safe print area so inserting a school name cannot erase the top-right MRS marker.
- First launch uses neutral school information and opens the School Information overlay for local setup instead of shipping another school's identity.
- The startup screen now follows the shared MoSSLab application-family layout; only the Control Center title and subtitle differ.
- The app runs without whole-process administrator rights. Only the narrowly scoped firewall helper requests elevation.
- `requirements-lock.txt` records the exact validated Python dependency versions.
- `BUILD_CLEAN_RELEASE.cmd` creates a clean ZIP and SHA-256 manifest while excluding mutable data, logs, caches, tests, and old test reports.

## v0.3.0 Development Build 8 — survey-history import and Dashboard date scope

- Added an icon-only **Import CSM survey-history CSV** action in History.
- Imports both the current Control Center CSV export and the attached legacy DepEd CSMS survey-history layout.
- Preserves compatible UUIDs, control numbers, survey dates, and original creation timestamps.
- Splits semicolon-delimited service selections and normalizes decimal-form whole-number responses.
- Recomputes positive rates and performance bands instead of trusting calculated CSV columns.
- Skips duplicate record IDs and duplicate control numbers on repeated imports.
- Added Dashboard **Single month** and **Custom range** date filtering.
- Custom range provides explicit **FROM** and **TO** calendar controls.
- The selected date scope filters Dashboard analysis, filtered CSV export, Print Preview, and printed Dashboard output.
- Dashboard print headers now state the exact date range and active categorical filters.
- Scanner engine metadata version updated to `0.3.0-dev8`.

## v0.3.0 Development Build 7 — barcode and handwriting recognition hardening

- Barcode recognition now evaluates the original corrected page and the operator-adjusted image through multiple decoding passes: original, enlarged, CLAHE, unsharp, Otsu, adaptive threshold, and slight deskew variants.
- Code 128 scanline decoding now uses dynamic thresholds and multiple scanlines instead of one fixed darkness threshold.
- The complete MRS control-number format is checksum-decoded and format-validated before automatic acceptance.
- A separate human-readable control-number crop is shown during review. When an optional local Tesseract OCR installation is available, the printed number can recover a damaged or unreadable barcode; the result remains marked for operator confirmation.
- Boxed handwritten date digits now use a bundled 1,797-sample handwritten-digit reference set in addition to synthetic digit templates.
- Date-level calendar constraints can resolve low-confidence digit disagreements while still requiring operator review.
- Source-image quality checks now warn about blur, low resolution, and extreme lighting before finalization.
- Free-form handwritten comments remain manual transcription by design; automatic free-text OCR is not used to avoid silently changing the respondent's statement.
- The Scanner Remote now displays separate crops for the barcode, printed control number, and all eight date boxes.
- Coordinate-map version updated to `0.4.2`; the MRS sheet geometry itself is unchanged.
- Scanner engine version updated to `0.3.0-dev7`.


## v0.3.0 Development Build 6 — contrast-safe corner detection

- Corrected the Scanner Remote reprocessing order so operator contrast adjustments no longer affect ArUco corner-marker detection.
- Corner detection now always uses the original uploaded photograph after EXIF orientation and operator rotation only.
- Contrast is applied after perspective correction, where it can improve shaded-answer recognition and the corrected review preview without damaging page geometry.
- Added a mild local-equalization fallback for uneven lighting while retaining the original photograph as the authoritative geometry source.
- Scanner results now identify the marker-detection source, detection pass, and post-perspective contrast stage for diagnostics.
- Contrast reprocessing messages explicitly state that the original photograph is retained for corner detection.
- Added a regression test proving that normal and maximum-contrast processing receive identical corner-detector pixels and retain the same four page corners.
- Scanner engine version updated to `0.3.0-dev6`.

### Build 6 validation

- Python compilation passed.
- Browser Survey and Scanner Remote JavaScript syntax checks passed.
- 131 selected non-PySide6 automated tests passed.
- Contrast/corner regression test passed.
- MRS software self-check passed 12 checks; physical-printer availability remains a deployment-laptop check.
- Existing v0.4.1 English, Filipino, and Waray-Waray template geometry remains unchanged.
- Existing 15 survey records were preserved.

## v0.3.0 Development Build 5 — scanner rescan and raised barcode geometry

- Added a persistent **Rescan / Take New Photo** action on both the scanner status and response-review screens.
- Rescanning discards the unfinished queued, processing, failed, or ready-for-review job without signing out the Scanner Operator.
- The camera/file picker reopens immediately after the operator confirms the rescan.
- Raised the Code 128 barcode and human-readable MRS control number within the barcode cell.
- Removed the template placeholder control-number text beneath the official number so it no longer competes with the spoiled-form instruction.
- Updated the recognition coordinate map to `0.4.1`.
- The scanner checks both the raised barcode region and the previous v0.4 region, allowing recognition of newly printed sheets and previously printed test sheets.
- Scanner engine version updated to `0.3.0-dev5`.

### Build 5 validation

- Python compilation passed.
- Scanner Remote JavaScript syntax passed.
- 121 selected non-PySide6 automated tests passed.
- The MRS software self-check passed 12 checks; physical-printer availability remains a deployment-laptop check.
- Newly rendered English, Filipino, and Waray-Waray sheets all completed barcode round-trip recognition.
- The previous English v0.4 preview sheet remains readable through legacy barcode-region fallback.
- Existing 15 survey records are preserved.


## v0.3.0 Development Build 4 — responsive MRS printing layout

- Corrected the overlapping labels, language selector, new-form counter, template display, and printer selector in **Controlled MRS Printing**.
- Reorganized print configuration into a compact two-level grid: Language, New Forms, and Template share the first row, while the physical-printer selector uses the full row below.
- Added explicit control heights, column stretch rules, and minimum card heights so Windows display scaling cannot collapse the controls into one another.
- Made the complete **MRS Printing & Tracking** board vertically scrollable. On smaller laptop displays, content now scrolls instead of compressing or clipping the print controls and lower action buttons.
- Preserved icon-only printer actions, direct physical printing, MRS registry accounting, reprints, scanner field-test mode, and all v0.4 recognition behavior.
- Scanner engine version updated to `0.3.0-dev4`.

### Build 4 validation

- Python compilation passed.
- Browser Survey and Scanner Remote JavaScript syntax checks passed.
- 133 non-PySide6 automated tests and 27 subtests passed.
- The package contains no PDF or XPS files.
- Existing 15 survey records are preserved.

PySide6 visual rendering and Windows display-scaling behavior still require confirmation on the school laptop. The corrected layout is protected by source-contract tests and no longer depends on the card being given an unrestricted height.

## v0.3.0 Development Build 3 — field-validation hardening

- Added **Field-test mode** to the authenticated CSM Sheet Scanner Remote. It runs the complete upload, queue, recognition, correction, and review workflow without creating an official response, consuming a control number, changing the Printed MRS Registry, or affecting CSM analysis.
- Field-test scans receive internal IDs such as `MRS-FT-2026-00001` and retain the Scanner Operator, printer/source, phone or camera, capture condition, expected result, observed outcome, recognition confidence, image references, and calibration notes.
- Added a Control Center action for reviewing field-test totals, average confidence, language coverage, outcomes, and recent test records.
- Added explicit printer-queue diagnostics showing why virtual, offline, paused, error-state, or non-A4 queues were excluded.
- Field-test jobs remain queue-aware and support the same recovery, manual-corner correction, rotation, contrast, and reprocessing controls as official scans.
- Scanner engine version updated to `0.3.0-dev3`.

This build is intended for hardware validation. A field-test result is never an official CSM response.

## MRS Form Printing, Tracking, Scanning, and Analysis v0.4

This development build implements the controlled Machine-Readable Survey Form lifecycle for English, Filipino, and Waray-Waray template set `CSM-MRS-A4-2026-04`.

### Official MRS production

- Added a dedicated **MRS Printing & Tracking** dashboard.
- Official MRS pages are rendered temporarily in memory, assigned a registered `CSM-MRS` control number, given a Code 128 barcode, and sent directly to a selected physical printer.
- The interface does not provide Generate PDF, Export PDF, Save Printable Form, Download Blank Form, or an external printable-file workflow.
- Printer discovery excludes common virtual/file-output queues and checks that A4 is supported before a printer is offered.
- School ID from **School Information** is required before official MRS control numbers can be generated.
- The MRS sequence continues across months and resets only when the year changes.

### Printing accountability and reprints

- Every generated control number is retained in the Printed MRS Form Registry.
- Each Windows print submission receives a separate print-batch and print-attempt record.
- A scrollable post-print confirmation overlay lets the operator mark missing, blank, jammed, incomplete, severely misaligned, or otherwise unusable pages.
- Failed pages remain registered and enter the Pending MRS Reprints queue.
- Reprints retain the original control number and receive a new print-attempt record.
- Successfully printed forms awaiting a scan are shown in red. Their control number returns to the regular interface color when the current physical copy is accounted for by a valid, invalid, duplicate, held, or otherwise recorded scan.

### v0.4 scanner recognition and registry matching

- Uses all four unique ArUco markers to identify orientation and correct perspective to the canonical A4 image.
- Recognizes the Code 128 control number, detects an instructed end-to-end barcode cross-out, and returns barcode/date crops for operator review.
- Reads the eight `MM/DD/YYYY` digit boxes, age bracket, region, sex, client type, service, CC1–CC3, and SQD0–SQD8.
- Displays field confidence, blank/multiple-mark warnings, corrected page preview, barcode status, and registry-match status in the hosted Scanner Remote.
- Unknown, duplicate, print-unconfirmed, crossed-out, and valid scans are retained as separate scan-attempt records.
- Only one verified valid response per registered control number is saved to the response database and included in CSM analysis.
- Crossed-out spoiled forms are excluded from analysis, count as accounted physical copies, and are queued for replacement.
- Scanner Operator attribution remains attached to every scan attempt and accepted response.

### Multilingual v0.4 template package

The package now includes English, Filipino, and Waray-Waray v0.4 template images, preview images, language-specific coordinate maps, and a combined scanner map under `assets/mrs_v0.4`. The Filipino and Waray-Waray layouts use the established survey translations and the same fixed response geometry as the English template. Each language uses a different four-marker set, allowing the Scanner Remote to identify the language automatically.

The blank template images contain no reusable official control number. The application inserts the unique Code 128 barcode and human-readable control number only during controlled printing.

### Field-validation support

- Added an icon-only **MRS software self-check** action to the MRS Printing & Tracking dashboard.
- Added `RUN_MRS_FIELD_TEST_CHECK.cmd`, which validates all installed language templates, marker detection, Code 128 round-trip decoding, recognition dependencies, and current physical-printer discovery.
- Added `MRS_FIELD_TEST_GUIDE.md` covering printer, camera, barcode-cross-out, queue, duplicate, replacement, and multi-phone validation.
- The generated text report is saved as `MRS_FIELD_TEST_REPORT.txt`; it does not create a printable form or PDF.

### Development-build validation

- Python compilation passed.
- 119 selected non-PySide6 automated tests passed.
- Scanner Remote and Browser Survey JavaScript syntax checks passed.
- The bundled MRS self-check passed 10 software/template checks; physical-printer discovery remains environment-dependent.
- English, Filipino, and Waray-Waray official in-memory renderings were recognized with the correct language-specific template IDs and decoded the same registered Code 128 control number.
- Generated official pages were decoded successfully, including a synthetic end-to-end barcode-cross-out case.
- Existing 15 survey records are preserved.

This remains a development build. Native Windows printer discovery/direct printing, printer disconnection and paper-jam behavior, physical 100-percent-scale output, actual pen shading, camera lighting, damaged forms, and multi-device field operation require testing on the school laptop and phones. Use the bundled field-test command and guide to record those results.

---

## Previous release notes

## v0.2.0 Release Candidate 1 — completed implementation phase

This release candidate completes the planned v0.2.0 scanner integration. It is ready for Windows-laptop and mobile-phone field validation.

Final integration additions include:

- manual four-corner selection when automatic ArUco detection is incomplete or inaccurate;
- server-side corner validation, point ordering, perspective correction, and reprocessing;
- persistent scanner-job metadata and recovery after a browser refresh, operator re-login, or Control Center restart;
- operator-owned job lists and source-image retrieval for safe recovery;
- stricter final-response validation for required fields, dates, ages, and questionnaire values;
- idempotent scanner finalization to prevent a retry from creating a second official record;
- duplicate protection using scanner job ID, image SHA-256, and original hardcopy control number;
- immutable original scanner attribution and processing metadata;
- History filters for response source and Scanner Operator;
- expanded scanned-record details and CSV export fields; and
- scanner engine version `0.2.0-rc1`.

The development phases are complete. Remaining work after this package is field testing and correction of defects found with actual printed MRS sheets, Windows camera/network conditions, and multiple phones.

## CSM Sheet Scanner integration

The primary scanner interface is now the server-hosted **CSM Sheet Scanner Remote** at `/scanner`. The phone captures the image and controls the review workflow, while the Control Center queues, processes, validates, and stores the response.

The older authenticated endpoint `POST /api/scanner/submissions` and bundled files under `scanner_app/` remain for compatibility and template reference. New deployments should use the hosted Scanner Remote opened from the scan icon in the Browser Survey Form.

## v0.1.22 borderless branding footer

- Removed the printed frames around the school logo, CSM application logo, and MoSSLab seal.
- Removed logo-container borders from the fixed Control Center footer.
- Print Preview and actual print output now use the same transparent, borderless branding layout.

## v0.1.21 corrected CSM logo update

- Replaced the previous CSM application logo with the operator-corrected PNG and ICO assets.
- The corrected logo is used by the application window, title bar, Windows taskbar identity, and Dashboard print footer.
- MoSSLab splash and institutional footer branding remain unchanged.

## DepEd branding, expanded print footer, and wheel zoom

- Added the official DepEd logo to the fixed Control Center footer and the CSM Survey Form footer.
- Expanded the fixed Control Center footer so the school logo, DepEd logo, MoSSLab logo, and MoSSLab seal remain legible without crowding.
- Expanded the Dashboard print footer from 10 mm to 22 mm and reserved the same footer area in both Print Preview and physical output.
- The printed footer now contains the school logo, DepEd logo, MoSSLab logo, MoSSLab seal, school name, generation timestamp, control number, and page number.
- Missing school logos use a compact SCHOOL placeholder instead of collapsing the footer layout.
- Mouse-wheel movement anywhere over Dashboard Print Preview now zooms in or out. The existing zoom, fit-page, and fit-width icon buttons remain available.

## School Information and shared branding footer

- Added a dedicated **School Information** dashboard accessible from the school-building icon in the navigation rail.
- Operators can maintain the school name, School ID, Region, Schools Division, Schools District, address, official email, contact number, School Head, and CSM focal person.
- Added icon-only controls for saving, uploading/replacing, and removing the official school logo.
- Uploaded logos are normalized and stored as `data/csm_survey/school_logo.png`.
- Added a fixed Control Center footer containing the school logo and name, MoSSLab logo, and MoSSLab seal.
- Added the same school/MoSSLab branding group to the CSM Survey Form footer.
- School information changes are reflected by the local web server without requiring a manual data import.

## Dashboard print-output corrections in v0.1.15

- Corrected the printer coordinate origin so page margins are not applied twice.
- The left and right print margins are now synchronized to one uniform effective value.
- Reserved a full 22 mm branded footer area inside the printable page.
- Footer text now uses a fixed 7-point font and is clipped/elided safely instead of being cut.
- Print timestamps now use Philippine Standard Time (PHT, UTC+08:00) instead of the Windows system timezone label.


## Dashboard Print Preview refinements in v0.1.13

- Uses a minimum **0.5 cm (5 mm)** allowance on all four paper edges.
- Prevents margin controls from being reduced below 5 mm; larger printer hardware margins remain respected.
- Removes the full-page dark application background from Dashboard Print Preview and printed output.
- Retains the color fills of report headers, filter strips, metric cards, charts, tables, and analysis sections.
- Uses a clean white paper canvas behind those sections.


## Live startup progress in v0.1.12
The MoSSLab loading screen now uses a real, live progress bar. Its percentage and status message advance as the application loads records, builds dashboards, prepares the survey server, applies styling, and renders the main workspace.


- Fixed `NameError: name 'background' is not defined` during construction of the custom title bar.
- The MoSSLab logo stylesheet now uses escaped braces correctly inside its Python f-string.
- Added a regression test that detects accidental runtime names introduced by unescaped stylesheet braces.



## Startup diagnostics added in v0.1.10

- Every launcher and application startup appends diagnostic information to `DEBUG_LOG.txt`.
- Fatal startup errors display a Windows message directing the operator to the log file.
- The log records Python version, executable path, working directory, administrator status, required-file checks, dependency versions, Qt messages, and full Python tracebacks.
- `START_SCHOOL_CSM_CONTROL_CENTER_DEBUG.cmd` provides a visible console for troubleshooting.
- `INSTALL_REQUIREMENTS.cmd` installs the packages listed in `requirements.txt`.
- The hidden `.vbs` launcher now records errors even when Python itself cannot be started.

When the application does not open, run `START_SCHOOL_CSM_CONTROL_CENTER_DEBUG.cmd`, then send the newest section of `DEBUG_LOG.txt`.

This build integrates the desktop **School CSM Control Center** with the locally hosted multilingual **CSM Survey Form**.


## Primary changes in v0.1.8

- Removed unintended dark background patches from anonymous container widgets in the Survey Server dashboard.
- Combo boxes, date fields, integer fields, and decimal fields ignore mouse-wheel input until explicitly clicked.
- A field is disarmed again when focus leaves it, allowing ordinary page scrolling without changing values.
- Applied the same protected wheel behavior to server settings and print margin controls.

## Primary changes in v0.1.7

- Added full-section vertical scrolling to the mobile language-selection screen.
- Kept the language confirmation control reachable on short screens using a sticky footer and safe-area padding.
- Changed the transaction/service selector on phones to scroll as one complete dialog instead of limiting scrolling to the choices list.
- Kept the transaction confirmation controls reachable through a sticky footer while preserving full-dialog scrolling.
- Added visible thin scrollbars for both mobile overlays.

## Changes retained from v0.1.6

### Compact Respondent QR access panel

The former full-width QR section has been replaced with three compact cards:

1. **Connect to hotspot** — primary Wi-Fi QR.
2. **Configured local address** — preferred manual Survey Form access.
3. **Direct-IP fallback** — emergency access when local DNS is unavailable.

The long address rows and oversized action strip were removed. Each card now contains its own QR, concise address field, and local action controls.

### Icon-only controls with tooltip flags

All Control Center action buttons now use icon-only controls. Hovering or focusing a button displays its tooltip flag, which also serves as its accessible name and status description.

The Survey Server board now uses icon controls for:

- apply settings;
- start and stop server;
- refresh adapters;
- open Windows Mobile Hotspot settings;
- copy and open addresses;
- save QR images;
- regenerate the access key; and
- save the complete access sheet.

### Captive Portal reliability correction

The captive-portal redirect no longer assumes that the configured local hostname can always be resolved by a connected phone.

- When the application successfully owns UDP port 53 and runs its local DNS responder, the portal redirects to the configured school address:

  ```text
  http://csm.[school-identifier].home.arpa:8080/portal
  ```

- When Windows Mobile Hotspot reserves DNS port 53, the portal redirects to the detected hotspot IPv4 address instead:

  ```text
  http://192.168.137.1:8080/portal
  ```

This prevents the captive-portal window from opening and then failing because the configured `.home.arpa` hostname could not be resolved. The configured-address QR remains available and still uses the school hostname.

The Control Center now reports **Captive Portal: Active** only when both the HTTP redirector and the application-owned DNS responder are actually running. A Windows hosts-file mapping is shown as **Partial** because it cannot guarantee that phones receive the same DNS result through Windows Mobile Hotspot.

Additional Android-vendor and browser connectivity-check hostnames are included in the best-effort probe mappings.

## Primary hotspot QR

The hotspot QR uses the compact Wi-Fi payload format generated by Windows for a visible secured hotspot:

```text
WIFI:T:WPA;S:<hotspot name>;P:<hotspot password>;;
```

`H:true` is added only for a hidden network. A secured Wi-Fi QR is generated only after both the hotspot name and password are entered.

## Local Survey Form addresses

The health-checked direct IPv4 URL is the primary address shown to operators and
encoded into the reliable fallback QR:

```text
http://192.168.137.1:8080/access/[key]
```

The optional friendly address has this form:

```text
http://csm.[school-identifier].home.arpa:8080/access/[key]
```

For example:

```text
http://csm.calapies.home.arpa:8080/access/[key]
```

It is displayed only after the app-owned DNS listener starts and resolves the name
back to the selected server address. Windows Internet Connection Sharing or the
phone's resolver may still prevent friendly-name use, so it is never presented as
the guaranteed path.

## Start a respondent session

1. Open the installed **School CSM Control Center** EXE.
2. Open **Survey Server and Access**.
3. Open Windows Mobile Hotspot settings and turn on the laptop hotspot.
4. Enter the exact same hotspot name and password in the Control Center.
5. Keep these selected:

   ```text
   Respondent network: Laptop Hotspot
   Access method: Captive Portal
   Internet access: Survey Network Only
   ```

6. Set the Survey Form to **Online**.
7. Click the Start Server icon and approve the narrowly scoped firewall prompt
   when Windows requests it.
8. Scan the primary hotspot QR and approve the connection prompt.

When the phone displays a network-login notification, open it to continue to the Survey Form. If no notification appears and the Control Center reports **Partial** or **Unavailable**, scan the configured-address QR or direct-IP QR.

## Existing functions retained

- School CSM Control Center application identity.
- Dashboard, History, manual encoding, reports, export, and analysis.
- Embedded local Survey Form server on TCP port 8080 by default.
- English, Filipino, and Waray-Waray Survey Form.
- Automatic recording and analysis of valid browser responses.
- Online, Offline, and Under Maintenance Survey Form statuses.
- Optional configurable school identifier in `csm.[school-identifier].home.arpa`
  when app-owned DNS is verified.
- Laptop Hotspot as the default respondent network mode.
- Captive Portal as the default access mode.
- Port-80 connectivity-check redirector.
- Wildcard local DNS responder on UDP port 53 when available.
- Direct IPv4 health-checked primary access when DNS is reserved or unavailable.
- Five-minute sessions that expire after successful submission or timeout.
- Compiled, narrowly scoped Windows Firewall configuration when the server starts.
- Server startup remains independent of firewall or captive-portal verification results.

## Developer diagnostics

The source repository retains developer-only diagnostics such as
`NETWORK_DIAGNOSTICS.cmd`. They are excluded from the compiled operator package.
Operators use Setup's **Repair** action and the in-app status explanations instead.

## Important limitation

Windows Mobile Hotspot does not provide a guaranteed programmable captive-portal
facility. Automatic opening depends on the phone's connectivity-check behavior
and whether the application can provide DNS interception on the hotspot interface.
The direct-IP QR remains the reliable fallback on unsupported Windows adapters or
phones.


## v0.1.15 startup and server visual-stability update

- Windows helper commands now use `CREATE_NO_WINDOW`, preventing `ipconfig`, `netsh`, and DNS-refresh console flashes.
- Repeated adapter discovery is cached during dashboard construction instead of launching `ipconfig` several times.
- The main window completes its first maximized paint behind the MoSSLab splash before the splash is removed.
- Server Start and Stop now run outside the Qt interface thread, so firewall setup and captive-portal preparation no longer freeze or partially repaint the dashboard.
- Buttons remain disabled while a server operation is in progress and return to their proper states when it finishes.

## v0.1.18 print correction

- Dashboard action buttons are temporarily hidden while the printable Dashboard image is captured.
- Info, print, export, add, and reset-filter icons no longer appear in Print Preview or physical output.
- Button visibility is restored immediately after capture, including controls that were already intentionally hidden.

## v0.1.19 application identity update

- Replaced the School CSM Control Center application icon with the supplied DepEd CSM logo.
- Updated both the transparent PNG and multi-resolution Windows ICO assets.
- The new logo is used by the application window, custom title bar, and Windows taskbar identity.
- The MoSSLab seal remains the application loading screen, while MoSSLab and DepEd institutional branding remain in their assigned footer areas.

## v0.1.20 printed-footer branding update

- Added the CSM application logo to the Dashboard Print Preview and physical print footer.
- The printed branding sequence now includes the school logo, DepEd logo, CSM application logo, MoSSLab logo, and MoSSLab seal.
- The application logo uses the packaged transparent PNG so it remains clear in print output.
- Existing 5 mm paper margins, the expanded footer area, and shared preview/output renderer remain unchanged.
## v0.2.0 Development Build 1 — authenticated Scanner Remote foundation

This development build begins the v0.2.0 scanner architecture. It preserves the existing 15 survey records and adds the server, account, numbering, queue, and mobile-capture foundation required by the next recognition-engine phase.

### Source-specific response numbers

New responses use independent annual sequences based on the official School ID and response source:

```text
CSM-WBS-123627-2026-07-0001  Browser Survey Form
CSM-MRS-123627-2026-07-0001  Scanned MRS hardcopy
CSM-SCC-123627-2026-07-0001  Control Center manual entry
```

The month remains visible, but each source sequence continues across all months of the same year and resets only when the year changes. Existing records retain their previous control numbers.

The School ID in **School Information** is required before the application can activate the public Survey Form or save WBS, MRS, or SCC responses.

### Scanner Operator accounts

The **Survey Server and Access** dashboard now contains **Scanner Operator accounts**. Create at least one account before using the Scanner Remote.

1. Enter the operator's display name.
2. Enter a unique username.
3. Enter a password or PIN containing at least six characters.
4. Keep **Account enabled** selected.
5. Click the Save icon.

Passwords are stored as salted PBKDF2 hashes rather than readable text. Repeated failed sign-ins trigger a temporary lockout. Disabling or archiving an account revokes its active scanner sessions while preserving historical record attribution.

### Opening the Scanner Remote

1. Start the local server.
2. Open the Browser Survey Form on the phone.
3. Tap the scan icon in the upper-right corner of the language-selection screen.
4. Sign in with a registered Scanner Operator account.
5. Capture or select a clear image of the completed MRS form.
6. Upload the image to the Control Center.

The Scanner Remote is hosted at:

```text
http://<laptop-ip>:<port>/scanner
```

No scanner package must be installed on the phone.

### Processing limit and queue

**Maximum Simultaneous Scan Processing** is configurable from 1 to 10. The slider changes from blue at 1 to red at 10. A value of 1 is the recommended default for ordinary school laptops. Selecting 10 requires confirmation.

Uploads beyond the configured limit enter a first-in, first-out queue. The phone displays:

- queued or processing state;
- the operator's queue position;
- active processing count;
- anonymous waiting-job count;
- automatic-start notice; and
- a cancellation control.

Scanner login sessions and uploaded jobs are separate. Multiple operators may remain signed in, and each login may process several forms. The limit applies only to simultaneous image-processing work.

### Implemented server-side image processing

The current release performs:

- authenticated image upload and operator ownership checks;
- FIFO queuing with a configurable simultaneous-processing limit;
- EXIF orientation correction, image-size validation, RGB conversion, and large-image reduction;
- automatic ArUco corner-marker detection and MRS language/template identification;
- manual four-corner adjustment and reprocessing when automatic detection needs correction;
- perspective correction to the canonical A4 form layout;
- contrast normalization and corrected-form preview preparation;
- interpretation of client type, sex, service, CC1–CC3, and SQD0–SQD8;
- field-level confidence and ink-density results;
- mobile review and correction of interpreted values;
- strict final validation, duplicate checks, and direct MRS finalization; and
- persistent job recovery after browser or server interruption.

### Scanner record attribution

Finalized MRS submissions are assigned an `MRS` control number and permanently record:

- Scanner Operator user ID, username, and display name;
- scanner login session and processing job IDs;
- upload, processing, review, and finalization timestamps;
- processing engine and template versions;
- original image hash and archived image references; and
- original hardcopy control number when provided.

The server derives these fields from the authenticated session. Original attribution and processing provenance remain immutable when a record is later edited.

### Release-candidate status

No planned implementation phase remains after this release candidate. Physical validation is still required using actual printed MRS sheets, varying phone cameras and lighting, the Windows laptop, and concurrent mobile devices. Recognition thresholds or layout coordinates may require calibration from those field results.


## v0.3.0 Development Build 9

- Added an icon-only Clear Survey History action.
- Added All History, Single Day, Single Month, and Custom From-To deletion scopes.
- The selected range shows the exact number of survey responses before continuing.
- Clearing requires two separate destructive confirmations.
- Date bounds are inclusive and reversed From-To inputs are normalized safely.
- Dashboard analysis and record counts refresh immediately after deletion.
- Dashboard print history and MRS printing/registry records are not affected.


## v0.3.0 Development Build 10

- Added an icon-only button that opens the selected printer's native Windows preferences.
- Vendor-specific quality modes such as Draft, Draft Vivid, Economy, or equivalent can be selected in the printer driver.
- Added a Use Windows driver preferences option that prevents generic app quality controls from overriding driver-managed settings.
- Native print jobs use a fresh printer object after the preferences window closes so saved driver settings are loaded.
- Added Standard and Low-ink light Dashboard appearances.
- Low-ink mode lightens broad fills while preserving text, table rules, and chart details.
- The selected appearance is used by both Print Preview and physical output and is recorded in print history.
