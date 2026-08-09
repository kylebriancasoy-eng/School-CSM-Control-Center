"""In-window authoring, approval, preview, and printing for Narrative Reports."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.services.narrative_report import (
    LocalNarrativeGenerator,
    SECTION_ORDER,
    validate_narrative_document,
)
from school_csm_control_center.services.openai_narrative import (
    OpenAINarrativeClient,
    OpenAINarrativeError,
)
from school_csm_control_center.storage.dashboard_snapshot_store import (
    DashboardSnapshotStore,
    sha256_canonical_json,
)
from school_csm_control_center.storage.narrative_report_store import NarrativeReportStore
from school_csm_control_center.storage.print_history_store import PrintHistoryStore
from school_csm_control_center.storage.windows_credential_store import (
    OpenAIApiKeyCredentialStore,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.narrative_report_printing import (
    NarrativePrintDocument,
    NarrativeReportRenderer,
)
from school_csm_control_center.ui.overlays import ClickScrim
from school_csm_control_center.ui.widgets import TooltipIconButton


PrinterProvider = Callable[[], list[QPrinterInfo]]
PrintExecutor = Callable[[QPrinter, Sequence[QImage]], int]


class _WorkerSignals(QObject):
    completed = Signal(object, object)
    failed = Signal(str)


class _OpenAIGenerationWorker(QRunnable):
    """Perform the optional network request away from the Qt UI thread."""

    def __init__(self, client: OpenAINarrativeClient, snapshot: Mapping[str, Any], key: str) -> None:
        super().__init__()
        self.client = client
        self.snapshot = deepcopy(dict(snapshot))
        self.key = key
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            document = self.client.generate(self.snapshot, api_key=self.key)
            metadata = deepcopy(self.client.last_response_metadata)
        except Exception as exc:
            # The OpenAI service boundary deliberately emits sanitized errors.
            message = (
                str(exc).strip()
                if isinstance(exc, OpenAINarrativeError)
                else "OpenAI-assisted generation could not be completed."
            )
            self.signals.failed.emit(message)
        else:
            self.signals.completed.emit(document, metadata)
        finally:
            self.key = ""


class NarrativeReportOverlay(QWidget):
    """Full-workspace Narrative Report editor tied to one verified snapshot.

    Integration API:

    * Construct with the application shell as ``parent`` and its project root.
    * Call :meth:`open_for_print_record` with a successful Dashboard history row.
    * Refresh History when ``report_changed`` is emitted.
    """

    closed = Signal()
    report_changed = Signal(object)
    print_completed = Signal(object, str)
    print_failed = Signal(str)

    PARAGRAPH_SECTIONS = frozenset(
        {"executive_summary", "scope_and_methodology", "key_findings", "conclusion"}
    )
    API_KEYS_URL = "https://platform.openai.com/api-keys"

    def __init__(
        self,
        parent: QWidget,
        *,
        project_root: str | Path,
        snapshot_store: DashboardSnapshotStore | None = None,
        report_store: NarrativeReportStore | None = None,
        print_store: PrintHistoryStore | None = None,
        credential_store: OpenAIApiKeyCredentialStore | None = None,
        local_generator: LocalNarrativeGenerator | None = None,
        openai_client: OpenAINarrativeClient | None = None,
        printer_provider: PrinterProvider | None = None,
        print_executor: PrintExecutor | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.snapshot_store = snapshot_store or DashboardSnapshotStore(self.project_root)
        self.report_store = report_store or NarrativeReportStore(
            self.project_root,
            snapshot_store=self.snapshot_store,
        )
        self.print_store = print_store
        self.credential_store = credential_store or OpenAIApiKeyCredentialStore()
        self.local_generator = local_generator or LocalNarrativeGenerator()
        self.openai_client = openai_client or OpenAINarrativeClient()
        self._printer_provider = printer_provider or self._system_printers
        self._print_executor = print_executor or NarrativeReportRenderer.paint
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: _OpenAIGenerationWorker | None = None
        self._print_record: dict[str, Any] = {}
        self._snapshot_reference: dict[str, Any] = {}
        self._snapshot: dict[str, Any] = {}
        self._report: dict[str, Any] = {}
        self._document: dict[str, Any] = {}
        self._pages: list[QImage] = []
        self._current_page = 0
        self._operator = ""
        self._printer_infos: dict[str, QPrinterInfo] = {}
        self._key_remove_armed = False

        self.setObjectName("narrative_report_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("narrative_report_scrim")
        self.scrim.clicked.connect(self.close_overlay)
        self.panel = QFrame(self)
        self.panel.setObjectName("narrative_report_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("narrative_report_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 13, 14, 13)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title = QLabel("Narrative Report")
        title.setObjectName("narrative_report_title")
        title_layout.addWidget(title)
        self.source_label = QLabel("Choose an eligible Dashboard print from History.")
        self.source_label.setObjectName("narrative_report_subtitle")
        title_layout.addWidget(self.source_label)
        header_layout.addLayout(title_layout, 1)
        self.close_button = TooltipIconButton("close", "Close Narrative Report")
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button)
        panel_layout.addWidget(header)

        body = QWidget()
        body.setObjectName("narrative_report_body")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(12)

        self.tools_scroll = QScrollArea()
        self.tools_scroll.setObjectName("narrative_tools_scroll")
        self.tools_scroll.setWidgetResizable(True)
        tools = QWidget()
        tools.setObjectName("narrative_tools")
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(3, 3, 3, 3)
        tools_layout.setSpacing(10)

        self.lifecycle_label = QLabel("Draft not loaded")
        self.lifecycle_label.setObjectName("narrative_lifecycle")
        self.lifecycle_label.setWordWrap(True)
        tools_layout.addWidget(self.lifecycle_label)

        self.section_tabs = QTabWidget()
        self.section_tabs.setObjectName("narrative_section_tabs")
        self.section_editors: dict[str, QTextEdit] = {}
        for key in SECTION_ORDER:
            editor = QTextEdit()
            editor.setObjectName(f"narrative_section_{key}")
            editor.setAcceptRichText(False)
            editor.setPlaceholderText("Enter one paragraph or item per line.")
            editor.textChanged.connect(self._document_edited)
            self.section_tabs.addTab(editor, self._heading_for(key))
            self.section_editors[key] = editor
        tools_layout.addWidget(self.section_tabs, 1)

        self.save_button = QPushButton("Save edited revision")
        self.save_button.setObjectName("narrative_primary_button")
        self.save_button.clicked.connect(self._save_revision)
        tools_layout.addWidget(self.save_button)

        ai_card = QFrame()
        ai_card.setObjectName("narrative_card")
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(10, 10, 10, 10)
        ai_heading = QLabel("Optional AI assistance")
        ai_heading.setObjectName("narrative_card_title")
        ai_layout.addWidget(ai_heading)
        ai_note = QLabel(
            "Local report creation needs no account. AI assistance uses your own "
            "OpenAI API key and API billing; your OpenAI password is never requested."
        )
        ai_note.setObjectName("narrative_note")
        ai_note.setWordWrap(True)
        ai_layout.addWidget(ai_note)
        self.key_status_label = QLabel("Checking saved API key…")
        self.key_status_label.setObjectName("narrative_key_status")
        ai_layout.addWidget(self.key_status_label)
        self.api_key_input = QLineEdit()
        self.api_key_input.setObjectName("narrative_api_key")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Paste a new API key to add or replace")
        ai_layout.addWidget(self.api_key_input)
        key_actions = QHBoxLayout()
        self.save_key_button = QPushButton("Save key")
        self.save_key_button.clicked.connect(self._save_api_key)
        key_actions.addWidget(self.save_key_button)
        self.remove_key_button = QPushButton("Remove key")
        self.remove_key_button.clicked.connect(self._remove_api_key)
        key_actions.addWidget(self.remove_key_button)
        ai_layout.addLayout(key_actions)
        self.open_keys_button = QPushButton("Open API key page")
        self.open_keys_button.clicked.connect(self._open_api_keys_page)
        ai_layout.addWidget(self.open_keys_button)
        self.ai_generate_button = QPushButton("Create AI-assisted revision")
        self.ai_generate_button.setObjectName("narrative_primary_button")
        self.ai_generate_button.clicked.connect(self._generate_with_openai)
        ai_layout.addWidget(self.ai_generate_button)
        tools_layout.addWidget(ai_card)

        approval_card = QFrame()
        approval_card.setObjectName("narrative_card")
        approval_layout = QVBoxLayout(approval_card)
        approval_layout.setContentsMargins(10, 10, 10, 10)
        approval_heading = QLabel("Approval")
        approval_heading.setObjectName("narrative_card_title")
        approval_layout.addWidget(approval_heading)
        approval_note = QLabel(
            "Review every section before approval. Any saved edit creates a new revision "
            "and removes the previous approval."
        )
        approval_note.setObjectName("narrative_note")
        approval_note.setWordWrap(True)
        approval_layout.addWidget(approval_note)
        self.approver_input = QLineEdit()
        self.approver_input.setObjectName("narrative_approver")
        self.approver_input.setPlaceholderText("Approver's full name")
        approval_layout.addWidget(self.approver_input)
        self.approve_button = QPushButton("Approve current revision")
        self.approve_button.setObjectName("narrative_success_button")
        self.approve_button.clicked.connect(self._approve)
        approval_layout.addWidget(self.approve_button)
        tools_layout.addWidget(approval_card)
        tools_layout.addStretch(1)
        self.tools_scroll.setWidget(tools)
        body_layout.addWidget(self.tools_scroll)

        preview_card = QFrame()
        preview_card.setObjectName("narrative_preview_card")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(8)
        preview_toolbar = QHBoxLayout()
        self.previous_button = QPushButton("Previous page")
        self.previous_button.clicked.connect(self._previous_page)
        preview_toolbar.addWidget(self.previous_button)
        self.page_label = QLabel("Page 0 of 0")
        self.page_label.setObjectName("narrative_page_label")
        preview_toolbar.addWidget(self.page_label)
        self.next_button = QPushButton("Next page")
        self.next_button.clicked.connect(self._next_page)
        preview_toolbar.addWidget(self.next_button)
        preview_toolbar.addStretch(1)
        self.printer_combo = QComboBox()
        self.printer_combo.setObjectName("narrative_printer_combo")
        self.printer_combo.setMinimumWidth(190)
        preview_toolbar.addWidget(self.printer_combo)
        self.copies_spin = QSpinBox()
        self.copies_spin.setObjectName("narrative_copies")
        self.copies_spin.setRange(1, 99)
        self.copies_spin.setPrefix("Copies: ")
        preview_toolbar.addWidget(self.copies_spin)
        preview_layout.addLayout(preview_toolbar)

        self.preview_stack = QStackedWidget()
        self.preview_stack.setObjectName("narrative_preview_stack")
        self.preview_empty = QLabel("The report preview will appear here.")
        self.preview_empty.setObjectName("narrative_preview_empty")
        self.preview_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_empty.setWordWrap(True)
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("narrative_preview_scroll")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_image = QLabel()
        self.preview_image.setObjectName("narrative_preview_image")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_scroll.setWidget(self.preview_image)
        self.preview_stack.addWidget(self.preview_scroll)
        preview_layout.addWidget(self.preview_stack, 1)

        print_row = QHBoxLayout()
        self.preview_status_label = QLabel("A verified report is required.")
        self.preview_status_label.setObjectName("narrative_preview_status")
        self.preview_status_label.setWordWrap(True)
        print_row.addWidget(self.preview_status_label, 1)
        self.print_button = QPushButton("Print approved report")
        self.print_button.setObjectName("narrative_success_button")
        self.print_button.clicked.connect(self._print)
        print_row.addWidget(self.print_button)
        preview_layout.addLayout(print_row)
        body_layout.addWidget(preview_card, 1)
        panel_layout.addWidget(body, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("narrative_status")
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)

        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.close_overlay)
        self._apply_styles()
        self._set_controls_enabled(False)

    def open_for_print_record(
        self,
        print_record: Mapping[str, Any],
        *,
        operator: str = "",
    ) -> bool:
        """Open an eligible record, returning ``False`` when verification fails."""

        if self._worker is not None:
            self.status_label.setText(
                "Please wait for AI-assisted generation to finish before opening another report."
            )
            return False
        self._reset_state()
        self._operator = " ".join(str(operator or "").split())
        try:
            print_record = self._resolve_current_print_record(print_record)
            eligible, reason = self.snapshot_store.narrative_eligibility(print_record)
            if not eligible:
                raise ValueError(reason)
            reference = self.snapshot_store.reference_from_print_record(print_record)
            if not reference:
                raise ValueError("The Dashboard snapshot reference is missing.")
            snapshot = self.snapshot_store.load(reference)
            source = snapshot.get("source")
            if not isinstance(source, Mapping):
                raise ValueError("The Dashboard snapshot source is incomplete.")
            expected_control = str(print_record.get("control_number") or "").strip()
            actual_control = str(source.get("dashboard_control_number") or "").strip()
            if not expected_control or actual_control != expected_control:
                raise ValueError(
                    "The verified Dashboard snapshot does not match this print-history record."
                )
            expected_print_id = str(print_record.get("id") or "").strip()
            actual_print_id = str(source.get("print_record_id") or "").strip()
            if expected_print_id and actual_print_id != expected_print_id:
                raise ValueError(
                    "The verified Dashboard snapshot belongs to a different print-history record."
                )
            snapshot_id = str(snapshot.get("snapshot_id") or "")
            report = self.report_store.get_for_snapshot(snapshot_id)
            if report is None:
                document = self.local_generator.generate(snapshot)
                report = self.report_store.create(
                    reference,
                    document,
                    generation_method="local",
                    generation_metadata={"generator": "deterministic-local-v1"},
                    operator=self._operator,
                )
            self.report_store.verify(str(report.get("id") or ""))
            current_report = self.report_store.get(str(report.get("id") or ""))
            if current_report is None:
                raise ValueError("The Narrative Report no longer exists.")
            report = current_report
            document = self._verified_report_document(report)
            validate_narrative_document(document, snapshot)
        except Exception as exc:
            message = f"The Narrative Report could not be opened: {exc}"
            self.status_label.setText(message)
            self.print_failed.emit(message)
            return False

        self._print_record = deepcopy(dict(print_record))
        self._snapshot_reference = deepcopy(reference)
        self._snapshot = deepcopy(snapshot)
        self._report = deepcopy(report)
        self._document = deepcopy(document)
        self.source_label.setText(
            f"Dashboard {actual_control} · Narrative {report.get('control_number', '')}"
        )
        self._load_document_into_editors()
        self._populate_printers()
        self._refresh_key_status()
        self._set_controls_enabled(True)
        self._refresh_preview()
        self._refresh_lifecycle()
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.status_label.setText(
            "This draft uses the exact preserved values from the successful Dashboard print."
        )
        self.report_changed.emit(deepcopy(self._report))
        return True

    def close_overlay(self) -> None:
        if not self.isVisible():
            return
        if self._worker is not None:
            self.status_label.setText("Please wait for AI-assisted generation to finish.")
            return
        self.hide()
        self.closed.emit()

    def _reset_state(self) -> None:
        self._print_record = {}
        self._snapshot_reference = {}
        self._snapshot = {}
        self._report = {}
        self._document = {}
        self._pages = []
        self._current_page = 0
        self._set_controls_enabled(False)
        self.preview_stack.setCurrentWidget(self.preview_empty)
        self.page_label.setText("Page 0 of 0")

    def _resolve_current_print_record(
        self,
        print_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resolve and validate the durable Dashboard print source."""

        if not isinstance(print_record, Mapping):
            raise ValueError("The Dashboard print-history record is unavailable.")
        current = deepcopy(dict(print_record))
        if self.print_store is not None:
            identity = str(
                current.get("id") or current.get("control_number") or ""
            ).strip()
            if not identity:
                raise ValueError("The Dashboard print-history identity is missing.")
            stored = self.print_store.get(identity)
            if stored is None:
                raise ValueError(
                    "The Dashboard print-history record no longer exists. Refresh History and try again."
                )
            current = stored
        eligible, reason = PrintHistoryStore.narrative_eligibility(current)
        if not eligible:
            raise ValueError(reason)
        return current

    @staticmethod
    def _reference_identity(reference: Mapping[str, Any] | None) -> tuple[str, str, str]:
        value = reference if isinstance(reference, Mapping) else {}
        return (
            str(value.get("snapshot_id") or "").strip().casefold(),
            str(value.get("manifest_path") or "")
            .strip()
            .replace("\\", "/")
            .casefold(),
            str(value.get("digest_sha256") or "").strip().casefold(),
        )

    def _verify_current_print_source(self) -> dict[str, Any]:
        """Recheck source success and evidence immediately before a mutation."""

        current = self._resolve_current_print_record(self._print_record)
        reference = self.snapshot_store.reference_from_print_record(current)
        if not reference or self._reference_identity(reference) != self._reference_identity(
            self._snapshot_reference
        ):
            raise ValueError(
                "The Dashboard snapshot reference changed; reopen the Narrative Report."
            )
        manifest = self.snapshot_store.load(reference)
        source = manifest.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("The Dashboard snapshot source is incomplete.")
        if str(source.get("print_record_id") or "").strip() != str(
            current.get("id") or ""
        ).strip() or str(source.get("dashboard_control_number") or "").strip() != str(
            current.get("control_number") or ""
        ).strip():
            raise ValueError(
                "The Dashboard snapshot no longer matches its print-history record."
            )
        self._print_record = deepcopy(current)
        return current

    @staticmethod
    def _current_revision_evidence(
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        wanted = int(report.get("current_revision") or 0)
        revisions = report.get("revisions")
        if not isinstance(revisions, list):
            raise ValueError("The Narrative Report revision history is unavailable.")
        revision = next(
            (
                item
                for item in revisions
                if isinstance(item, Mapping)
                and int(item.get("number") or 0) == wanted
            ),
            None,
        )
        if not isinstance(revision, Mapping):
            raise ValueError("The current Narrative Report revision is unavailable.")
        return deepcopy(dict(revision))

    @classmethod
    def _verified_report_document(
        cls,
        report: Mapping[str, Any],
    ) -> dict[str, Any]:
        revision = cls._current_revision_evidence(report)
        document = revision.get("document")
        digest = str(revision.get("content_sha256") or "").casefold()
        if not isinstance(document, Mapping) or sha256_canonical_json(document) != digest:
            raise ValueError("The current Narrative Report revision failed verification.")
        return deepcopy(dict(document))

    @staticmethod
    def _approval_identity(report: Mapping[str, Any]) -> str:
        approval = report.get("approval")
        return (
            str(approval.get("approval_id") or "").strip()
            if isinstance(approval, Mapping)
            else ""
        )

    def _verify_current_report_state(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Return the current verified record only if this editor is not stale."""

        report_id = str(self._report.get("id") or "").strip()
        if not report_id:
            raise ValueError("The Narrative Report identity is unavailable.")
        self.report_store.verify(report_id)
        current = self.report_store.get(report_id)
        if current is None:
            raise ValueError("The Narrative Report no longer exists.")
        opened_revision = self._current_revision_evidence(self._report)
        current_revision = self._current_revision_evidence(current)
        for key in ("revision_id", "content_sha256"):
            if str(current_revision.get(key) or "") != str(
                opened_revision.get(key) or ""
            ):
                raise ValueError(
                    "The Narrative Report changed after it was opened. Reopen it before continuing."
                )
        if self._approval_identity(current) != self._approval_identity(self._report):
            raise ValueError(
                "The Narrative Report approval changed after it was opened. Reopen it before continuing."
            )
        current_document = self._verified_report_document(current)
        if current_document != self._document:
            raise ValueError(
                "The Narrative Report content changed after it was opened. Reopen it before continuing."
            )
        source = current.get("source")
        source = source if isinstance(source, Mapping) else {}
        expected_source = {
            "print_record_id": str(self._print_record.get("id") or "").strip(),
            "dashboard_control_number": str(
                self._print_record.get("control_number") or ""
            ).strip(),
            "snapshot_id": self._reference_identity(self._snapshot_reference)[0],
            "snapshot_sha256": self._reference_identity(self._snapshot_reference)[2],
        }
        actual_source = {
            "print_record_id": str(source.get("print_record_id") or "").strip(),
            "dashboard_control_number": str(
                source.get("dashboard_control_number") or ""
            ).strip(),
            "snapshot_id": str(source.get("snapshot_id") or "").strip().casefold(),
            "snapshot_sha256": str(source.get("snapshot_sha256") or "")
            .strip()
            .casefold(),
        }
        if actual_source != expected_source:
            raise ValueError(
                "The Narrative Report no longer references this Dashboard print evidence."
            )
        validate_narrative_document(current_document, self._snapshot)
        return current, current_document, current_revision

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.section_tabs,
            self.save_button,
            self.ai_generate_button,
            self.approver_input,
            self.approve_button,
            self.print_button,
        ):
            widget.setEnabled(enabled)

    def _load_document_into_editors(self) -> None:
        by_key = {
            str(section.get("key") or ""): section
            for section in self._document.get("sections", [])
            if isinstance(section, Mapping)
        }
        for key, editor in self.section_editors.items():
            section = by_key.get(key, {})
            values = section.get("paragraphs") if key in self.PARAGRAPH_SECTIONS else section.get("bullets")
            editor.blockSignals(True)
            separator = "\n\n" if key in self.PARAGRAPH_SECTIONS else "\n"
            editor.setPlainText(separator.join(str(item) for item in (values or [])))
            editor.blockSignals(False)

    def _document_from_editors(self) -> dict[str, Any]:
        document = deepcopy(self._document)
        sections = []
        existing = {
            str(section.get("key") or ""): section
            for section in document.get("sections", [])
            if isinstance(section, Mapping)
        }
        for key in SECTION_ORDER:
            section = deepcopy(existing[key])
            separator = "\n\n" if key in self.PARAGRAPH_SECTIONS else "\n"
            values = [
                value.strip()
                for value in self.section_editors[key].toPlainText().split(separator)
                if value.strip()
            ]
            section["paragraphs"] = values if key in self.PARAGRAPH_SECTIONS else []
            section["bullets"] = values if key not in self.PARAGRAPH_SECTIONS else []
            sections.append(section)
        document["sections"] = sections
        return document

    def _document_edited(self) -> None:
        if not self._document:
            return
        self.save_button.setEnabled(True)
        self.preview_status_label.setText(
            "Save this edit to create a revision before approving or printing."
        )
        self.print_button.setEnabled(False)

    def _save_revision(self) -> None:
        try:
            self._verify_current_print_source()
            current_report, _current_document, current_revision = (
                self._verify_current_report_state()
            )
            document = self._document_from_editors()
            validate_narrative_document(document, self._snapshot)
            report = self.report_store.append_revision(
                str(self._report.get("id") or ""),
                document,
                editor=self._operator,
                reason="Narrative sections edited in Control Center",
                expected_current_revision_id=str(
                    current_revision.get("revision_id") or ""
                ),
                expected_content_sha256=str(
                    current_revision.get("content_sha256") or ""
                ),
                expected_approval_id=self._approval_identity(current_report),
            )
        except ValueError as exc:
            if "unchanged" in str(exc).casefold():
                self.status_label.setText("No changes need to be saved.")
                self._refresh_lifecycle()
                self._refresh_preview()
            else:
                self.status_label.setText(f"This revision could not be saved: {exc}")
            return
        except Exception as exc:
            self.status_label.setText(f"This revision could not be saved: {exc}")
            return
        self._accept_report(report)
        self.status_label.setText(
            "Edited revision saved. It must be reviewed and approved before printing."
        )

    def _approve(self) -> None:
        approver = " ".join(self.approver_input.text().split())
        if not approver:
            self.status_label.setText("Enter the approver's full name before approval.")
            self.approver_input.setFocus()
            return
        try:
            self._verify_current_print_source()
            current_report, _current_document, current_revision = (
                self._verify_current_report_state()
            )
            current = self._document_from_editors()
            if current != self._document:
                raise ValueError("Save the edited revision before approval.")
            report = self.report_store.approve(
                str(self._report.get("id") or ""),
                approver,
                expected_current_revision_id=str(
                    current_revision.get("revision_id") or ""
                ),
                expected_content_sha256=str(
                    current_revision.get("content_sha256") or ""
                ),
                expected_approval_id=self._approval_identity(current_report),
            )
            self.report_store.verify(str(report.get("id") or ""))
        except Exception as exc:
            self.status_label.setText(f"The report could not be approved: {exc}")
            return
        self._accept_report(report)
        self.status_label.setText("The current revision is approved and ready to print.")

    def _generate_with_openai(self) -> None:
        if self._worker is not None:
            return
        try:
            self._verify_current_print_source()
            self._verify_current_report_state()
        except Exception as exc:
            self.status_label.setText(
                f"AI-assisted generation could not start: {exc}"
            )
            return
        try:
            key = self.credential_store.load()
        except Exception as exc:
            self.status_label.setText(f"The saved API key is unavailable: {exc}")
            return
        if not key:
            self.status_label.setText(
                "Add an OpenAI API key first. Local report creation remains available without it."
            )
            self.api_key_input.setFocus()
            return
        worker = _OpenAIGenerationWorker(self.openai_client, self._snapshot, key)
        worker.signals.completed.connect(self._openai_completed)
        worker.signals.failed.connect(self._openai_failed)
        self._worker = worker
        self._set_busy(True)
        self.status_label.setText("Creating an AI-assisted revision from aggregate Dashboard values…")
        self._thread_pool.start(worker)

    @Slot(object, object)
    def _openai_completed(self, document: object, metadata: object) -> None:
        self._worker = None
        self._set_busy(False)
        try:
            self._verify_current_print_source()
            current_report, _current_document, current_revision = (
                self._verify_current_report_state()
            )
            if not isinstance(document, Mapping):
                raise ValueError("The assisted draft was incomplete.")
            validate_narrative_document(document, self._snapshot)
            report = self.report_store.append_revision(
                str(self._report.get("id") or ""),
                document,
                editor=self._operator,
                reason="Optional OpenAI-assisted narrative generated",
                generation_method="openai",
                generation_metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
                expected_current_revision_id=str(
                    current_revision.get("revision_id") or ""
                ),
                expected_content_sha256=str(
                    current_revision.get("content_sha256") or ""
                ),
                expected_approval_id=self._approval_identity(current_report),
            )
        except Exception as exc:
            self.status_label.setText(f"The AI-assisted revision was not saved: {exc}")
            return
        self._accept_report(report)
        self.status_label.setText(
            "AI-assisted revision saved. Review every section, then approve it before printing."
        )

    @Slot(str)
    def _openai_failed(self, message: str) -> None:
        self._worker = None
        self._set_busy(False)
        self.status_label.setText(message)

    def _set_busy(self, busy: bool) -> None:
        self.ai_generate_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.approve_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.print_button.setEnabled(
            not busy
            and self._is_approved()
            and bool(self._pages)
            and bool(self.printer_combo.currentData())
        )

    def _save_api_key(self) -> None:
        key = self.api_key_input.text().strip()
        if not key:
            self.status_label.setText("Paste an OpenAI API key before saving it.")
            return
        try:
            self.credential_store.save(key)
        except Exception as exc:
            self.status_label.setText(f"The API key could not be saved: {exc}")
            return
        self.api_key_input.clear()
        self._refresh_key_status()
        self.status_label.setText("API key saved securely in Windows Credential Manager.")

    def _remove_api_key(self) -> None:
        if not self._key_remove_armed:
            self._key_remove_armed = True
            self.remove_key_button.setText("Confirm remove saved key")
            self.status_label.setText(
                "Select Confirm remove saved key to remove it from Windows Credential Manager."
            )
            return
        try:
            removed = self.credential_store.delete()
        except Exception as exc:
            self._key_remove_armed = False
            self.remove_key_button.setText("Remove key")
            self.status_label.setText(f"The API key could not be removed: {exc}")
            return
        self._key_remove_armed = False
        self.remove_key_button.setText("Remove key")
        self.api_key_input.clear()
        self._refresh_key_status()
        self.status_label.setText(
            "Saved API key removed." if removed else "No saved API key was found."
        )

    def _refresh_key_status(self) -> None:
        self._key_remove_armed = False
        self.remove_key_button.setText("Remove key")
        try:
            exists = self.credential_store.exists()
        except Exception:
            self.key_status_label.setText("Windows Credential Manager is unavailable.")
            self.remove_key_button.setEnabled(False)
            return
        self.key_status_label.setText(
            "An API key is saved securely." if exists else "No API key is saved."
        )
        self.remove_key_button.setEnabled(exists)

    def _open_api_keys_page(self) -> None:
        QDesktopServices.openUrl(QUrl(self.API_KEYS_URL))

    def _accept_report(self, report: Mapping[str, Any]) -> None:
        self._report = deepcopy(dict(report))
        document = self._verified_report_document(self._report)
        validate_narrative_document(document, self._snapshot)
        self._document = deepcopy(document)
        self._load_document_into_editors()
        self._refresh_preview()
        self._refresh_lifecycle()
        self.report_changed.emit(deepcopy(self._report))

    def _refresh_lifecycle(self) -> None:
        revision = int(self._report.get("current_revision") or 0)
        approval = self._report.get("approval")
        if isinstance(approval, Mapping):
            approver = str(approval.get("approved_by") or "")
            self.lifecycle_label.setText(
                f"APPROVED · Revision {revision} · Approved by {approver}"
            )
            self.print_button.setEnabled(
                bool(self._pages) and bool(self.printer_combo.currentData())
            )
            self.preview_status_label.setText("Approved current revision. Ready to print.")
        else:
            self.lifecycle_label.setText(f"DRAFT · Revision {revision} · Approval required")
            self.print_button.setEnabled(False)
            self.preview_status_label.setText("Review and approve this revision before printing.")
        self.save_button.setEnabled(True)

    def _is_approved(self) -> bool:
        approval = self._report.get("approval")
        return (
            self._report.get("status") == "approved"
            and isinstance(approval, Mapping)
            and int(approval.get("revision_number") or 0)
            == int(self._report.get("current_revision") or 0)
        )

    def _refresh_preview(self) -> None:
        try:
            document = self._print_document()
            self._pages = NarrativeReportRenderer.render_pages(document)
        except Exception as exc:
            self._pages = []
            self.preview_stack.setCurrentWidget(self.preview_empty)
            self.preview_empty.setText(f"The preview could not be prepared: {exc}")
            self.page_label.setText("Page 0 of 0")
            return
        self._current_page = 0
        self.preview_stack.setCurrentWidget(self.preview_scroll)
        self._show_page()

    def _print_document(self) -> NarrativePrintDocument:
        report_meta = self._snapshot.get("report")
        report_meta = report_meta if isinstance(report_meta, Mapping) else {}
        school = self._snapshot.get("school")
        school = school if isinstance(school, Mapping) else {}
        approval = self._report.get("approval")
        approval = approval if isinstance(approval, Mapping) else {}
        revisions = self._report.get("revisions")
        current_number = int(self._report.get("current_revision") or 1)
        current_revision = next(
            (
                item
                for item in revisions
                if isinstance(item, Mapping)
                and int(item.get("number") or 0) == current_number
            ),
            {},
        ) if isinstance(revisions, list) else {}
        generation = current_revision.get("generation") if isinstance(current_revision, Mapping) else {}
        generation = generation if isinstance(generation, Mapping) else {}
        return NarrativePrintDocument(
            dashboard_control_number=str(self._document.get("dashboard_control_number") or ""),
            analysis=deepcopy(self._snapshot.get("analysis") or {}),
            narrative=deepcopy(self._document),
            school=deepcopy(dict(school)),
            report_scope=str(report_meta.get("scope") or report_meta.get("filter_scope") or "Complete Dashboard"),
            source_generated_at=str(
                report_meta.get("generated_at") or self._snapshot.get("captured_at") or ""
            ),
            approved_by=str(approval.get("approved_by") or ""),
            approved_at=str(approval.get("approved_at") or ""),
            revision_number=current_number,
            generation_mode=str(generation.get("method") or "local"),
        )

    def _show_page(self) -> None:
        count = len(self._pages)
        if not count:
            return
        self._current_page = max(0, min(self._current_page, count - 1))
        image = self._pages[self._current_page]
        pixmap = QPixmap.fromImage(image)
        available = max(280, self.preview_scroll.viewport().width() - 28)
        if pixmap.width() > available:
            pixmap = pixmap.scaledToWidth(
                available,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.preview_image.setPixmap(pixmap)
        self.preview_image.setMinimumSize(pixmap.size())
        self.page_label.setText(f"Page {self._current_page + 1} of {count}")
        self.previous_button.setEnabled(self._current_page > 0)
        self.next_button.setEnabled(self._current_page + 1 < count)

    def _previous_page(self) -> None:
        self._current_page -= 1
        self._show_page()

    def _next_page(self) -> None:
        self._current_page += 1
        self._show_page()

    def _populate_printers(self) -> None:
        self.printer_combo.clear()
        self._printer_infos = {}
        try:
            printers = list(self._printer_provider())
        except Exception:
            printers = []
        default_name = QPrinterInfo.defaultPrinter().printerName()
        selected = -1
        for info in printers:
            name = str(info.printerName() or "").strip()
            if not name:
                continue
            self._printer_infos[name] = info
            self.printer_combo.addItem(name, name)
            if name == default_name:
                selected = self.printer_combo.count() - 1
        if selected >= 0:
            self.printer_combo.setCurrentIndex(selected)
        if self.printer_combo.count() == 0:
            self.printer_combo.addItem("No Windows printer available", "")
        self.print_button.setEnabled(self._is_approved() and bool(self.printer_combo.currentData()))

    def _print(self) -> None:
        if not self._is_approved():
            self.status_label.setText("Approve the current revision before printing.")
            return
        printer_name = str(self.printer_combo.currentData() or "")
        if not printer_name:
            self.status_label.setText("Choose an available Windows printer.")
            return
        try:
            self._verify_current_print_source()
            current_report, current_document, current_revision = (
                self._verify_current_report_state()
            )
            if self._document_from_editors() != self._document:
                raise ValueError("Save the edited revision before printing.")
            self._report = deepcopy(current_report)
            self._document = deepcopy(current_document)
            pages = NarrativeReportRenderer.render_pages(self._print_document())
            if not pages:
                raise RuntimeError("The approved Narrative Report has no printable pages.")
            self._pages = pages
            self._show_page()
            printer = QPrinter(self._printer_infos[printer_name], QPrinter.PrinterMode.HighResolution)
            printer.setDocName(
                f"Narrative Report - {self._document.get('dashboard_control_number', '')}"
            )
            copies = int(self.copies_spin.value())
            printer.setCopyCount(copies)
            printed = int(self._print_executor(printer, self._pages))
            if printed <= 0:
                raise RuntimeError("The printer did not accept any report pages.")
            if printed != len(self._pages):
                raise RuntimeError(
                    "The printer did not accept the complete Narrative Report. "
                    "No successful print audit was recorded."
                )
            self._verify_current_print_source()
            source = current_report.get("source")
            source = source if isinstance(source, Mapping) else {}
            report = self.report_store.record_print(
                str(self._report.get("id") or ""),
                printer_name=printer_name,
                page_count=printed,
                copies=copies,
                operator=self._operator,
                settings={"page_size": "A4", "page_numbers": list(range(1, printed + 1))},
                expected_current_revision_id=str(
                    current_revision.get("revision_id") or ""
                ),
                expected_content_sha256=str(
                    current_revision.get("content_sha256") or ""
                ),
                expected_approval_id=self._approval_identity(current_report),
                expected_snapshot_sha256=str(
                    source.get("snapshot_sha256") or ""
                ),
            )
        except Exception as exc:
            message = f"The Narrative Report was not completed: {exc}"
            self.status_label.setText(message)
            self.print_failed.emit(message)
            return
        self._report = deepcopy(report)
        kind = "reprint" if len(report.get("print_audits", [])) > 1 else "original print"
        message = (
            f"Narrative {report.get('control_number', '')} {kind} sent to {printer_name} "
            f"and recorded in the audit history."
        )
        self.status_label.setText(message)
        self._refresh_lifecycle()
        self.report_changed.emit(deepcopy(report))
        self.print_completed.emit(deepcopy(report), message)

    @staticmethod
    def _system_printers() -> list[QPrinterInfo]:
        return list(QPrinterInfo.availablePrinters())

    @staticmethod
    def _heading_for(key: str) -> str:
        return key.replace("_", " ").title()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()
        self._show_page()

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        margin = 10 if self.width() < 900 else 18
        self.panel.setGeometry(
            margin,
            margin,
            max(1, self.width() - margin * 2),
            max(1, self.height() - margin * 2),
        )
        self.tools_scroll.setFixedWidth(380 if self.panel.width() >= 1050 else 330)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#narrative_report_overlay {{ background: transparent; }}
            QFrame#narrative_report_scrim {{ background: rgba(1,8,18,0.84); border: none; }}
            QFrame#narrative_report_panel {{ background: {theme.WINDOW_BG_ALT}; border: 1px solid rgba(43,217,197,0.72); border-radius: 16px; }}
            QFrame#narrative_report_header {{ background: {theme.TITLE_BG}; border: none; border-bottom: 1px solid {theme.DIVIDER}; border-top-left-radius: 16px; border-top-right-radius: 16px; }}
            QLabel#narrative_report_title {{ color: {theme.TEXT_PRIMARY}; font-size: 19px; font-weight: 800; }}
            QLabel#narrative_report_subtitle, QLabel#narrative_note {{ color: {theme.TEXT_SECONDARY}; font-size: 10px; }}
            QWidget#narrative_report_body, QWidget#narrative_tools {{ background: {theme.WINDOW_BG_ALT}; }}
            QScrollArea#narrative_tools_scroll {{ background: transparent; border: none; }}
            QFrame#narrative_card {{ background: rgba(10,29,48,0.94); border: 1px solid {theme.CARD_BORDER}; border-radius: 9px; }}
            QLabel#narrative_card_title {{ color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 800; }}
            QLabel#narrative_lifecycle {{ color: {theme.ACCENT_CYAN}; background: rgba(7,25,42,0.72); border: 1px solid rgba(27,85,114,0.46); border-radius: 8px; padding: 9px; font-size: 10px; font-weight: 800; }}
            QFrame#narrative_preview_card {{ background: rgba(5,19,33,0.72); border: 1px solid {theme.CARD_BORDER}; border-radius: 10px; }}
            QScrollArea#narrative_preview_scroll {{ background: #14283A; border: none; }}
            QLabel#narrative_preview_image {{ background: #14283A; padding: 12px; }}
            QLabel#narrative_preview_empty {{ color: {theme.TEXT_SECONDARY}; background: #14283A; }}
            QLabel#narrative_page_label, QLabel#narrative_preview_status {{ color: {theme.TEXT_PRIMARY}; font-size: 10px; font-weight: 700; }}
            QLabel#narrative_key_status {{ color: {theme.ACCENT_CYAN}; font-size: 10px; font-weight: 700; }}
            QLabel#narrative_status {{ color: {theme.TEXT_SECONDARY}; background: {theme.TITLE_BG}; border-top: 1px solid {theme.DIVIDER}; padding: 10px 16px; font-size: 10px; font-weight: 700; }}
            QTextEdit, QLineEdit, QComboBox, QSpinBox {{ background: {theme.CARD_BG}; color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.CARD_BORDER}; border-radius: 6px; padding: 5px; selection-background-color: {theme.ACCENT_TEAL}; }}
            QTextEdit {{ min-height: 210px; }}
            QTabWidget::pane {{ border: 1px solid {theme.CARD_BORDER}; border-radius: 6px; }}
            QTabBar::tab {{ background: {theme.PANEL_BG}; color: {theme.TEXT_SECONDARY}; padding: 7px 9px; border: 1px solid {theme.DIVIDER}; }}
            QTabBar::tab:selected {{ color: {theme.TEXT_PRIMARY}; border-bottom: 2px solid {theme.ACCENT_TEAL}; }}
            QPushButton {{ background: {theme.PANEL_BG}; color: {theme.TEXT_PRIMARY}; border: 1px solid {theme.CARD_BORDER}; border-radius: 6px; padding: 7px 10px; font-weight: 700; }}
            QPushButton:hover {{ border-color: {theme.ACCENT_TEAL}; }}
            QPushButton:disabled {{ color: {theme.TEXT_MUTED}; background: rgba(20,37,53,0.72); }}
            QPushButton#narrative_primary_button {{ background: {theme.ACCENT_TEAL}; color: #031B20; }}
            QPushButton#narrative_success_button {{ background: {theme.SUCCESS}; color: #062116; }}
            """
        )
