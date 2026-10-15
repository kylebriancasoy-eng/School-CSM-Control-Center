from __future__ import annotations

"""Required, three-step school registration and profile editor."""

from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.control_center_settings import (
    DEFAULT_SCHOOL_DISTRICT,
    DEFAULT_SCHOOL_DIVISION,
    SCHOOL_REGISTRATION_FIELD_LABELS,
    SCHOOL_REGISTRATION_SECTIONS,
    ControlCenterSettingsStore,
    clean_registration_text,
    first_incomplete_registration_section,
    school_registration_errors,
    school_registration_complete,
    school_registration_section_errors,
    validate_school_id,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.icons import action_icon
from school_csm_control_center.ui.widgets import TooltipIconButton


class SchoolInformationBoard(QFrame):
    """Layered school registration whose required state survives restarts."""

    school_information_saved = Signal(object)
    notice_requested = Signal(str, str)
    activation_requested = Signal()
    directory_sync_requested = Signal()
    server_dashboard_requested = Signal()
    local_usage_requested = Signal()

    def __init__(self, project_root: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("school_information_board")
        self.project_root = Path(project_root)
        self.data_root = storage_root_for(self.project_root)
        self.settings_store = ControlCenterSettingsStore(self.project_root)
        self.logo_target = self.data_root / "data" / "csm_survey" / "school_logo.png"
        self.logo_pending = (
            self.data_root / "data" / "csm_survey" / "school_logo.pending.png"
        )
        self._registered_school_id = ""
        self._registration_required = False
        self._step = 0
        self._profile_complete = False
        self._directory_state: dict[str, Any] = {}
        self._gateway_state: dict[str, Any] = {}
        self._saved_identity: dict[str, str] = {}
        self._build_ui()
        self.load_settings()
        self._apply_styles()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(14)

        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(3)
        self.title = QLabel("School Registration")
        self.title.setObjectName("school_information_title")
        self.subtitle = QLabel(
            "Complete all three sections. Every item marked Required must remain valid before the Control Center can be used."
        )
        self.subtitle.setObjectName("school_information_subtitle")
        self.subtitle.setWordWrap(True)
        heading_copy.addWidget(self.title)
        heading_copy.addWidget(self.subtitle)
        outer.addLayout(heading_copy)

        navigation = QHBoxLayout()
        self.profile_button = QPushButton("School information")
        self.internet_button = QPushButton("Internet activation (optional)")
        for button in (self.profile_button, self.internet_button):
            button.setObjectName("school_registration_back")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            navigation.addWidget(button)
        navigation.addStretch(1)
        outer.addLayout(navigation)
        self.workspace_stack = QStackedWidget()
        self.workspace_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        outer.addWidget(self.workspace_stack, 1)
        profile_page = QWidget()
        self.workspace_stack.addWidget(profile_page)
        outer = QVBoxLayout(profile_page)
        outer.setContentsMargins(0, 0, 0, 0)

        progress = QHBoxLayout()
        progress.setSpacing(8)
        self.step_labels: list[QLabel] = []
        for index, (section_title, _keys) in enumerate(SCHOOL_REGISTRATION_SECTIONS):
            label = QLabel(f"{index + 1}  {section_title}")
            label.setObjectName("school_information_step")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setProperty("activeStep", index == 0)
            self.step_labels.append(label)
            progress.addWidget(label, 1)
        outer.addLayout(progress)

        self.district_registration_note = QLabel(
            "After registration, only School ID, name, district, and division are queued for district review. All other profile information stays local. Internet Gateway access still requires approval and one-time activation."
        )
        self.district_registration_note.setObjectName("school_information_note")
        self.district_registration_note.setTextFormat(Qt.TextFormat.PlainText)
        self.district_registration_note.setWordWrap(True)
        outer.addWidget(self.district_registration_note)

        self.section_stack = QStackedWidget()
        self.section_stack.setObjectName("school_registration_stack")
        self.section_stack.addWidget(self._identity_page())
        self.section_stack.addWidget(self._personnel_page())
        self.section_stack.addWidget(self._contact_page())
        outer.addWidget(self.section_stack, 1)

        self.validation_message = QLabel()
        self.validation_message.setObjectName("school_information_validation")
        self.validation_message.setWordWrap(True)
        self.validation_message.hide()
        outer.addWidget(self.validation_message)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.required_note = QLabel("All fields in this section are required.")
        self.required_note.setObjectName("school_information_required_note")
        actions.addWidget(self.required_note)
        actions.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("school_registration_back")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("school_registration_continue")
        self.continue_button.setProperty("primaryAction", True)
        self.save_button = self.continue_button
        actions.addWidget(self.back_button)
        actions.addWidget(self.continue_button)
        outer.addLayout(actions)

        self.back_button.clicked.connect(self._back_requested)
        self.continue_button.clicked.connect(self._continue_requested)
        self.workspace_stack.addWidget(self._internet_page())
        self.workspace_stack.currentChanged.connect(self._update_workspace_navigation)
        self._update_workspace_navigation(0)
        self.profile_button.clicked.connect(lambda: self.workspace_stack.setCurrentIndex(0))
        self.internet_button.clicked.connect(self.show_internet_activation)

    def _update_workspace_navigation(self, index: int) -> None:
        self.profile_button.setChecked(index == 0)
        self.internet_button.setChecked(index == 1)

    def _internet_page(self) -> QWidget:
        page, layout = self._page("Internet activation", "Optional: let respondents use mobile data or another Wi-Fi network. Local surveys work without Internet activation.")
        self.activation_identity = QLabel()
        self.activation_identity.setWordWrap(True)
        self.activation_identity.setTextFormat(Qt.TextFormat.PlainText)
        self.activation_identity.setObjectName("school_information_card_title")
        layout.addWidget(self.activation_identity)
        self.activation_steps: list[QLabel] = []
        for title in ("1. Save school information", "2. District approval", "3. Activate Internet access"):
            card = self._card(title)
            label = QLabel()
            label.setObjectName("school_information_note")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            card.layout().addWidget(label, 1, 0, 1, 2)
            self.activation_steps.append(label)
            layout.addWidget(card)
        self.activation_check_button = QPushButton("Check district approval")
        self.activation_button = QPushButton("Activate Internet Gateway")
        self.activation_server_button = QPushButton("Open Server Dashboard")
        self.activation_local_button = QPushButton("Use local surveys for now")
        for button in (self.activation_check_button, self.activation_button,
                       self.activation_server_button, self.activation_local_button):
            button.setObjectName("school_registration_continue")
            if button in (self.activation_check_button, self.activation_server_button):
                layout.addWidget(button)
        footer = QHBoxLayout()
        footer.addWidget(self.activation_local_button)
        footer.addWidget(self.activation_button)
        page.layout().addLayout(footer)
        self.activation_button.setProperty("primaryAction", True)
        self.activation_check_button.clicked.connect(self.directory_sync_requested.emit)
        self.activation_button.clicked.connect(self.activation_requested.emit)
        self.activation_server_button.clicked.connect(self.server_dashboard_requested.emit)
        self.activation_local_button.clicked.connect(self.local_usage_requested.emit)
        privacy = QLabel("Only School ID, school name, district, and division are shared for review. Personnel, contact details, the school seal, and survey records stay on this computer. Never enter a Cloudflare API token here.")
        privacy.setWordWrap(True)
        privacy.setObjectName("school_information_note")
        layout.addWidget(privacy)
        layout.addStretch(1)
        return page

    def show_internet_activation(self) -> None:
        if not self._profile_complete:
            self.workspace_stack.setCurrentIndex(0)
            self.notice_requested.emit("Complete and save all three school information sections first.", "info")
            return
        self.workspace_stack.setCurrentIndex(1)
        self.activation_button.setFocus()

    def apply_activation_state(self, *, directory: dict[str, Any] | None = None,
                               gateway: dict[str, Any] | None = None) -> None:
        if directory is not None:
            self._directory_state = dict(directory)
        if gateway is not None:
            self._gateway_state = dict(gateway)
        request, connection = self._directory_state, self._gateway_state
        registered = connection.get("registration_state") == "registered"
        existing = registered or bool(connection.get("school_id") and connection.get("public_host"))
        retired = bool(connection.get("retired") or connection.get("retirement") or connection.get("authorization_state") in {"transferred", "revoked"})
        status = str(request.get("status") or "incomplete")
        busy = bool(request.get("busy") or connection.get("operation") or connection.get("gateway_state") == "connecting")
        self.internet_button.setEnabled(self._profile_complete)
        self.activation_identity.setText(f"{self._saved_identity.get('school_name', '')} · School ID {self._saved_identity.get('school_id', '')}")
        self.activation_steps[0].setText("School information saved on this computer." if self._profile_complete else "Complete all three required school information sections first.")
        labels = {
            "approved": "Approved. Ask the district administrator for your one-time School Activation Code.",
            "pending": "Waiting for district approval. You can close the app or continue using local surveys; your request is saved.",
            "queued": "Saved for sending. The app will retry when the district service and Internet connection are available.",
            "rejected": "Not approved. Contact the district administrator, correct School Information, then submit the corrected request.",
            "provider_unavailable": "District connection settings are not installed. Ask the district administrator for the configured school installer; local surveys still work.",
            "storage_error": "The request needs attention. Your school information is saved locally. Try sending again or contact the district administrator.",
        }
        self.activation_steps[1].setText("This server is already registered. No new activation code is needed." if registered else labels.get(status, "Your saved school information will be submitted for district review."))
        if request.get("detail") and not registered:
            self.activation_steps[1].setText(self.activation_steps[1].text() + "\n" + str(request["detail"]))
        if retired:
            guidance = "This installation is retired or transferred. Use the Server Dashboard for transfer or recovery; do not activate it again."
        elif existing:
            guidance = "An Internet setup already exists. Connect, reconnect, or review any authorization problem in the Server Dashboard; do not request a new initial activation code."
        elif status == "approved":
            guidance = "Choose Activate Internet Gateway. Enter the district-issued activation code in the secure browser and create a passkey. Then paste the browser's separate completion code back into the app."
        else:
            guidance = "Activation becomes available after district approval. No activation code is required for local surveys."
        self.activation_steps[2].setText(guidance)
        self.activation_button.setVisible(not existing and not retired)
        self.activation_button.setEnabled(self._profile_complete and status == "approved" and not busy and not retired and not existing)
        self.activation_server_button.setVisible(existing or retired)
        self.activation_check_button.setVisible(not existing and not retired)
        self.activation_check_button.setEnabled(self._profile_complete and not busy and status != "provider_unavailable" and not retired)
        self.activation_check_button.setText("Checking…" if busy else "Submit corrected request" if status == "rejected" else "Send / check district approval")
        self.activation_local_button.setEnabled(self._profile_complete)
        self.activation_local_button.setText("Continue to Control Center" if existing else "Use local surveys for now")

    def _identity_page(self) -> QWidget:
        page, layout = self._page(
            "Official school identity",
            "School ID and School Name start blank on a new installation. The district and division are prefilled and may be corrected when necessary.",
        )
        card = self._card("School identity")
        grid = card.layout()
        self.school_id = QLineEdit()
        self.school_name = QLineEdit()
        self.district = QLineEdit()
        self.division = QLineEdit()
        self.school_id.setPlaceholderText("Enter the official School ID")
        self.school_id.setMaxLength(12)
        self.school_id.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,12}"), self)
        )
        self.school_name.setPlaceholderText("Enter the official School Name")
        self.school_name.setMaxLength(180)
        self.district.setPlaceholderText(DEFAULT_SCHOOL_DISTRICT)
        self.district.setMaxLength(180)
        self.division.setPlaceholderText(DEFAULT_SCHOOL_DIVISION)
        self.division.setMaxLength(180)
        identity_fields = (
            ("School ID", self.school_id),
            ("School Name", self.school_name),
            ("Schools District", self.district),
            ("Schools Division", self.division),
        )
        for index, (label_text, field) in enumerate(identity_fields):
            row = (index // 2) * 2 + 1
            column = index % 2
            grid.addWidget(self._field_label(label_text), row, column)
            grid.addWidget(field, row + 1, column)
        layout.addWidget(card)

        logo_card = self._card("School Seal / Logo")
        logo_grid = logo_card.layout()
        self.logo_preview = QLabel()
        self.logo_preview.setObjectName("school_logo_preview")
        self.logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_preview.setFixedSize(150, 150)
        self.logo_preview.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        logo_grid.addWidget(self.logo_preview, 1, 0, 3, 1, Qt.AlignmentFlag.AlignTop)
        logo_text = QLabel(
            "Required. Upload the official school seal or logo in PNG, JPG, JPEG, BMP, or WEBP format. A safe PNG copy is stored with this school's data."
        )
        logo_text.setObjectName("school_information_note")
        logo_text.setWordWrap(True)
        logo_grid.addWidget(logo_text, 1, 1)
        self.upload_logo_button = TooltipIconButton(
            "upload",
            "Upload or replace the school logo",
            button_size=40,
            icon_size=21,
        )
        logo_grid.addWidget(self.upload_logo_button, 2, 1, Qt.AlignmentFlag.AlignLeft)
        self.logo_path_label = QLabel("No school seal or logo uploaded")
        self.logo_path_label.setObjectName("school_information_logo_path")
        self.logo_path_label.setWordWrap(True)
        logo_grid.addWidget(self.logo_path_label, 3, 1)
        self.upload_logo_button.clicked.connect(self._upload_requested)
        layout.addWidget(logo_card)
        layout.addStretch(1)
        return page

    def _personnel_page(self) -> QWidget:
        page, layout = self._page(
            "Essential personnel",
            "Identify the three officials responsible for school leadership, administration, and the Client Satisfaction Measurement program.",
        )
        card = self._card("Required personnel")
        grid = card.layout()
        self.school_head = QLineEdit()
        self.school_administrator = QLineEdit()
        self.csm_focal_person = QLineEdit()
        self.school_head.setPlaceholderText("Full name of the School Head")
        self.school_administrator.setPlaceholderText(
            "Full name of the School Administrator"
        )
        self.csm_focal_person.setPlaceholderText("Full name of the CSM Coordinator")
        for field in (
            self.school_head,
            self.school_administrator,
            self.csm_focal_person,
        ):
            field.setMaxLength(180)
        personnel_fields = (
            ("School Head", self.school_head),
            ("School Administrator", self.school_administrator),
            ("CSM Coordinator", self.csm_focal_person),
        )
        for index, (label_text, field) in enumerate(personnel_fields):
            row = index * 2 + 1
            grid.addWidget(self._field_label(label_text), row, 0, 1, 2)
            grid.addWidget(field, row + 1, 0, 1, 2)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _contact_page(self) -> QWidget:
        page, layout = self._page(
            "School contact details",
            "Use official contact information that the school can keep current.",
        )
        card = self._card("Required contact details")
        grid = card.layout()
        self.address = QLineEdit()
        self.email = QLineEdit()
        self.contact = QLineEdit()
        self.address.setPlaceholderText("Complete school address")
        self.address.setMaxLength(300)
        self.email.setPlaceholderText("Official school email address")
        self.email.setMaxLength(254)
        self.contact.setPlaceholderText("Mobile or telephone number")
        self.contact.setMaxLength(32)
        contact_fields = (
            ("School Address", self.address),
            ("School Email Address", self.email),
            ("School Contact Number (mobile / telephone)", self.contact),
        )
        for index, (label_text, field) in enumerate(contact_fields):
            row = index * 2 + 1
            grid.addWidget(self._field_label(label_text), row, 0, 1, 2)
            grid.addWidget(field, row + 1, 0, 1, 2)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    @staticmethod
    def _page(title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("school_registration_page")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setObjectName("school_information_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("school_information_content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 8, 2, 8)
        content_layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("school_registration_section_title")
        description_label = QLabel(description)
        description_label.setObjectName("school_information_note")
        description_label.setWordWrap(True)
        content_layout.addWidget(heading)
        content_layout.addWidget(description_label)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        return page, content_layout

    @staticmethod
    def _card(title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("school_information_card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 16, 18, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        card_title = QLabel(title)
        card_title.setObjectName("school_information_card_title")
        grid.addWidget(card_title, 0, 0, 1, 2)
        return card

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(f"{text}  ·  Required")
        label.setObjectName("school_information_field_label")
        return label

    def set_registration_required(self, required: bool) -> None:
        self._registration_required = bool(required)
        self._profile_complete = not required
        self.workspace_stack.setCurrentIndex(0)
        self.apply_activation_state()
        self.title.setText("Register this school" if required else "School Information")
        self.subtitle.setText(
            "Complete all three required sections before the Control Center, Survey Server, and Internet Gateway become available. Your completed sections are saved on this computer."
            if required
            else "Review or update the required school identity, essential personnel, and contact details."
        )
        if required:
            self._set_step(
                first_incomplete_registration_section(
                    self._candidate_settings(), data_root=self.data_root
                )
            )
        else:
            self._set_step(0)

    @property
    def registration_required(self) -> bool:
        return self._registration_required

    def _back_requested(self) -> None:
        if self._step > 0:
            self._set_step(self._step - 1)

    def _continue_requested(self) -> None:
        candidate = self._candidate_settings()
        errors = school_registration_section_errors(
            candidate, self._step, data_root=self.data_root
        )
        if errors:
            self._show_validation_errors(errors)
            return
        try:
            if self._step == 0:
                logo_replaced = self._commit_pending_logo()
                candidate = self._candidate_settings()
                if logo_replaced:
                    candidate["school_logo_path"] = self._logo_target_relative()
            self._persist_candidate(candidate)
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")
            return
        if self._step < len(SCHOOL_REGISTRATION_SECTIONS) - 1:
            self._set_step(self._step + 1)
            return
        try:
            self.save_settings()
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")

    def load_settings(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        self._saved_identity = {key: str(settings.get(key) or "") for key in ("school_name", "school_id")}
        values = {
            self.school_name: settings.get("school_name", ""),
            self.school_id: self._registered_school_id or settings.get("school_id", ""),
            self.division: settings.get("school_division", "") or DEFAULT_SCHOOL_DIVISION,
            self.district: settings.get("school_district", "") or DEFAULT_SCHOOL_DISTRICT,
            self.address: settings.get("school_address", ""),
            self.email: settings.get("school_email", ""),
            self.contact: settings.get("school_contact", ""),
            self.school_head: settings.get("school_head", ""),
            self.school_administrator: settings.get("school_administrator", ""),
            self.csm_focal_person: settings.get("csm_focal_person", ""),
        }
        for field, value in values.items():
            field.setText(str(value or ""))
        self._refresh_logo_preview(settings)
        self._clear_validation()
        self._profile_complete = school_registration_complete(settings, data_root=self.data_root)
        self.apply_activation_state()
        return settings

    def save_settings(self) -> dict[str, Any]:
        candidate = self._candidate_settings()
        school_id = validate_school_id(candidate.get("school_id"), required=True)
        if self._registered_school_id and school_id != self._registered_school_id:
            self.school_id.setText(self._registered_school_id)
            raise ValueError(
                "School ID is locked because this installation is registered for Internet Gateway access. Use Server Transfer for another device or contact the gateway administrator to correct an official School ID."
            )
        logo_replaced = self._commit_pending_logo()
        candidate = self._candidate_settings()
        if logo_replaced:
            candidate["school_logo_path"] = self._logo_target_relative()
        errors = school_registration_errors(candidate, data_root=self.data_root)
        if errors:
            self._set_step(
                first_incomplete_registration_section(candidate, data_root=self.data_root)
            )
            self._show_validation_errors(errors)
            raise ValueError(next(iter(errors.values())))
        saved = self._persist_candidate(candidate)
        self._saved_identity = {key: str(saved.get(key) or "") for key in ("school_name", "school_id")}
        self._refresh_logo_preview(saved)
        self._registration_required = False
        self._profile_complete = True
        self.apply_activation_state()
        self.school_information_saved.emit(saved)
        return saved

    def save_draft(self) -> dict[str, Any] | None:
        """Persist each complete section when an unfinished registration closes."""

        candidate = self._candidate_settings()
        if self.logo_pending.is_file() and not school_registration_section_errors(
            candidate, 0, data_root=self.data_root
        ):
            try:
                logo_replaced = self._commit_pending_logo()
                candidate = self._candidate_settings()
                if logo_replaced:
                    candidate["school_logo_path"] = self._logo_target_relative()
            except OSError:
                pass
        existing = self.settings_store.load()
        for section_index, (_title, keys) in enumerate(SCHOOL_REGISTRATION_SECTIONS):
            if school_registration_section_errors(
                candidate, section_index, data_root=self.data_root
            ):
                continue
            for key in keys:
                existing[key] = candidate.get(key, "")
        try:
            return self.settings_store.save(existing)
        except Exception:
            return None

    def _candidate_settings(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        settings.update(
            {
                "school_name": self._clean(self.school_name.text()),
                "school_id": self._clean(self.school_id.text()),
                "school_division": self._clean(self.division.text()),
                "school_district": self._clean(self.district.text()),
                "school_address": self._clean(self.address.text()),
                "school_email": self._clean(self.email.text()),
                "school_contact": self._clean(self.contact.text()),
                "school_head": self._clean(self.school_head.text()),
                "school_administrator": self._clean(self.school_administrator.text()),
                "csm_focal_person": self._clean(self.csm_focal_person.text()),
                "school_logo_path": self._available_logo_relative(),
            }
        )
        return settings

    def _persist_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        saved = self.settings_store.save(candidate)
        self._clear_validation()
        return saved

    def set_registered_school_id(self, school_id: str = "") -> None:
        """Lock the routing identity after Internet Gateway registration."""

        self._registered_school_id = self._clean(school_id)
        locked = bool(self._registered_school_id)
        if locked:
            self.school_id.setText(self._registered_school_id)
            self.school_id.setToolTip(
                "Locked to the School ID registered for Internet Gateway routing"
            )
        else:
            self.school_id.setToolTip("Official School ID")
        self.school_id.setReadOnly(locked)
        self.school_id.setProperty("gatewayIdentityLocked", locked)
        self.school_id.style().unpolish(self.school_id)
        self.school_id.style().polish(self.school_id)

    def _upload_requested(self) -> None:
        try:
            self.upload_logo()
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")

    def upload_logo(self) -> None:
        source_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Upload school seal or logo",
            str(Path.home()),
            "Image files (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not source_name:
            return
        source = Path(source_name)
        try:
            if source.stat().st_size > 12 * 1024 * 1024:
                raise ValueError("The selected logo exceeds the 12 MB limit.")
        except OSError as exc:
            raise ValueError(f"The selected logo could not be read: {exc}") from exc
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            raise ValueError("The selected file is not a supported image.")
        self.logo_pending.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.toImage().save(str(self.logo_pending), "PNG"):
            raise OSError("The school logo could not be saved.")
        self._refresh_logo_preview(self._candidate_settings())
        self._clear_field_error("school_logo_path")
        self.notice_requested.emit(
            "School seal / logo is ready. Continue to save this section.", "success"
        )

    def remove_logo(self) -> None:
        """Discard only a pending replacement; the required saved logo remains."""

        try:
            self.logo_pending.unlink(missing_ok=True)
        except OSError as exc:
            raise OSError(f"The pending school logo could not be removed: {exc}") from exc
        self._refresh_logo_preview(self.settings_store.load())

    def _commit_pending_logo(self) -> bool:
        if not self.logo_pending.is_file():
            return False
        self.logo_target.parent.mkdir(parents=True, exist_ok=True)
        self.logo_pending.replace(self.logo_target)
        return True

    def _logo_target_relative(self) -> str:
        return str(self.logo_target.relative_to(self.data_root)).replace("\\", "/")

    def _available_logo_relative(self) -> str:
        if self.logo_pending.is_file():
            return str(self.logo_pending.relative_to(self.data_root)).replace("\\", "/")
        configured = str(self.settings_store.load().get("school_logo_path") or "").strip()
        if configured:
            try:
                configured_path = (self.data_root / configured).resolve()
                configured_path.relative_to(self.data_root.resolve())
                if configured_path.is_file():
                    return configured
            except (OSError, ValueError):
                pass
        if self.logo_target.is_file():
            return self._logo_target_relative()
        return ""

    def _refresh_logo_preview(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or self.settings_store.load()
        configured = str(settings.get("school_logo_path") or "").strip()
        candidates = [self.logo_pending]
        if configured:
            candidates.append(self.data_root / configured)
        candidates.append(self.logo_target)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        if pixmap.isNull():
            self.logo_preview.setPixmap(
                action_icon("school", theme.ACCENT_CYAN, 72).pixmap(72, 72)
            )
            self.logo_path_label.setText("No school seal or logo uploaded")
            return
        self.logo_preview.setPixmap(
            pixmap.scaled(
                126,
                126,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        suffix = " (ready to save)" if path == self.logo_pending else ""
        self.logo_path_label.setText(f"School seal / logo selected{suffix}")

    def _set_step(self, index: int) -> None:
        self._step = max(0, min(len(SCHOOL_REGISTRATION_SECTIONS) - 1, int(index)))
        self.section_stack.setCurrentIndex(self._step)
        for label_index, label in enumerate(self.step_labels):
            label.setProperty("activeStep", label_index == self._step)
            label.setProperty("completedStep", label_index < self._step)
            label.style().unpolish(label)
            label.style().polish(label)
        self.back_button.setVisible(self._step > 0)
        self.continue_button.setText(
            "Save and finish"
            if self._step == len(SCHOOL_REGISTRATION_SECTIONS) - 1
            else "Save and continue"
        )
        self.validation_message.hide()
        self._clear_validation()
        first_field = self._field_widget(SCHOOL_REGISTRATION_SECTIONS[self._step][1][0])
        if first_field is not None:
            first_field.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _show_validation_errors(self, errors: dict[str, str]) -> None:
        relevant = [
            (key, message)
            for key, message in errors.items()
            if key in SCHOOL_REGISTRATION_SECTIONS[self._step][1]
        ]
        if not relevant:
            return
        self.validation_message.setText(
            "Please complete this section: "
            + " ".join(message for _key, message in relevant)
        )
        self.validation_message.show()
        for key, _message in relevant:
            widget = self._field_widget(key)
            if widget is not None:
                widget.setProperty("registrationInvalid", True)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        first_widget = self._field_widget(relevant[0][0])
        if first_widget is not None:
            first_widget.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.notice_requested.emit(relevant[0][1], "warning")

    def _clear_validation(self) -> None:
        for key in SCHOOL_REGISTRATION_FIELD_LABELS:
            self._clear_field_error(key)

    def _clear_field_error(self, key: str) -> None:
        widget = self._field_widget(key)
        if widget is None:
            return
        widget.setProperty("registrationInvalid", False)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _field_widget(self, key: str) -> QWidget | None:
        return {
            "school_id": getattr(self, "school_id", None),
            "school_name": getattr(self, "school_name", None),
            "school_district": getattr(self, "district", None),
            "school_division": getattr(self, "division", None),
            "school_logo_path": getattr(self, "upload_logo_button", None),
            "school_head": getattr(self, "school_head", None),
            "school_administrator": getattr(self, "school_administrator", None),
            "csm_focal_person": getattr(self, "csm_focal_person", None),
            "school_address": getattr(self, "address", None),
            "school_email": getattr(self, "email", None),
            "school_contact": getattr(self, "contact", None),
        }.get(key)

    @staticmethod
    def _clean(value: object) -> str:
        return clean_registration_text(value)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#school_information_board,
            QWidget#school_information_content,
            QWidget#school_registration_page {{ background: {theme.WINDOW_BG}; }}
            QScrollArea#school_information_scroll {{
                background: {theme.WINDOW_BG};
                border: none;
            }}
            QLabel#school_information_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 25px;
                font-weight: 900;
            }}
            QLabel#school_information_subtitle,
            QLabel#school_information_note,
            QLabel#school_information_logo_path,
            QLabel#school_information_required_note {{
                color: {theme.TEXT_MUTED};
                font-size: 11px;
            }}
            QLabel#school_information_step {{
                min-height: 34px;
                padding: 0 9px;
                color: {theme.TEXT_MUTED};
                background: rgba(7, 23, 39, 0.88);
                border: 1px solid rgba(112, 139, 164, 0.28);
                border-radius: 9px;
                font-size: 11px;
                font-weight: 750;
            }}
            QLabel#school_information_step[activeStep="true"] {{
                color: {theme.TEXT_PRIMARY};
                background: rgba(50, 217, 204, 0.16);
                border-color: {theme.ACCENT_TEAL};
            }}
            QLabel#school_information_step[completedStep="true"] {{
                color: {theme.ACCENT_CYAN};
                border-color: rgba(50, 217, 204, 0.54);
            }}
            QLabel#school_registration_section_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 17px;
                font-weight: 850;
            }}
            QFrame#school_information_card {{
                background: rgba(10, 29, 48, 0.94);
                border: 1px solid {theme.CARD_BORDER};
                border-radius: 12px;
            }}
            QLabel#school_information_card_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 14px;
                font-weight: 850;
            }}
            QLabel#school_information_field_label {{
                color: {theme.TEXT_SECONDARY};
                font-size: 11px;
                font-weight: 750;
            }}
            QLabel#school_information_validation {{
                color: #ffd7b2;
                background: rgba(255, 153, 51, 0.12);
                border: 1px solid rgba(255, 170, 72, 0.58);
                border-radius: 9px;
                padding: 9px 11px;
                font-size: 11px;
            }}
            QLineEdit {{
                min-height: 38px;
                padding: 0 11px;
                color: {theme.TEXT_PRIMARY};
                background: rgba(7, 23, 39, 0.96);
                border: 1px solid rgba(43, 217, 197, 0.42);
                border-radius: 8px;
                selection-background-color: {theme.ACCENT_TEAL};
            }}
            QLineEdit:focus {{ border-color: {theme.ACCENT_CYAN}; }}
            QLineEdit[registrationInvalid="true"] {{
                border-color: {theme.DANGER};
                background: rgba(73, 22, 35, 0.92);
            }}
            QLabel#school_logo_preview {{
                background: rgba(7, 23, 39, 0.78);
                border: 1px dashed rgba(98, 219, 255, 0.48);
                border-radius: 16px;
            }}
            QPushButton#school_registration_back,
            QPushButton#school_registration_continue {{
                min-height: 38px;
                min-width: 112px;
                padding: 0 16px;
                color: {theme.TEXT_SECONDARY};
                background: rgba(16, 43, 67, 0.92);
                border: 1px solid rgba(112, 139, 164, 0.42);
                border-radius: 9px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#school_registration_back:hover,
            QPushButton#school_registration_continue:hover {{
                border-color: {theme.ACCENT_CYAN};
            }}
            QPushButton#school_registration_back:checked {{
                border-color: {theme.ACCENT_TEAL};
                background: rgba(50, 217, 204, 0.16);
            }}
            QPushButton#school_registration_continue[primaryAction="true"] {{
                color: #04212a;
                background: {theme.ACCENT_TEAL};
                border-color: {theme.ACCENT_TEAL};
            }}
            QPushButton#school_registration_continue:disabled,
            QPushButton#school_registration_back:disabled {{
                color: {theme.TEXT_MUTED};
                background: rgba(16, 35, 51, 0.65);
                border-color: rgba(112, 139, 164, 0.22);
            }}
            """
        )
