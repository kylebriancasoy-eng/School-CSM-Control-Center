from __future__ import annotations

"""School identity and contact-information dashboard."""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.runtime_paths import storage_root_for
from school_csm_control_center.storage.control_center_settings import ControlCenterSettingsStore
from school_csm_control_center.storage.control_center_settings import validate_school_id
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.icons import action_icon
from school_csm_control_center.ui.widgets import TooltipIconButton


class SchoolInformationBoard(QFrame):
    """Editable school profile with a persistent uploaded school logo."""

    school_information_saved = Signal(object)
    notice_requested = Signal(str, str)

    def __init__(self, project_root: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("school_information_board")
        self.project_root = Path(project_root)
        self.data_root = storage_root_for(self.project_root)
        self.settings_store = ControlCenterSettingsStore(self.project_root)
        self.logo_target = self.data_root / "data" / "csm_survey" / "school_logo.png"
        self._registered_school_id = ""
        self._build_ui()
        self.load_settings()
        self._apply_styles()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("school_information_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("school_information_content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 34)
        layout.setSpacing(18)
        scroll.setWidget(content)

        title_row = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(4)
        title = QLabel("School Information")
        title.setObjectName("school_information_title")
        subtitle = QLabel(
            "Maintain the school identity used by the Control Center, local Survey Form, and branding footer."
        )
        subtitle.setObjectName("school_information_subtitle")
        subtitle.setWordWrap(True)
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        title_row.addLayout(title_column, 1)
        self.save_button = TooltipIconButton(
            "save",
            "Save school information",
            button_size=40,
            icon_size=21,
            role="success",
        )
        title_row.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        identity_card = self._card("Official school profile")
        identity_grid = identity_card.layout()
        self.school_name = QLineEdit()
        self.school_id = QLineEdit()
        self.region = QLineEdit()
        self.division = QLineEdit()
        self.district = QLineEdit()
        self.address = QLineEdit()
        self.email = QLineEdit()
        self.contact = QLineEdit()
        self.school_head = QLineEdit()
        self.csm_focal_person = QLineEdit()

        self.school_name.setPlaceholderText("Official school name")
        self.school_id.setPlaceholderText("School ID")
        self.region.setPlaceholderText("Regional Office")
        self.division.setPlaceholderText("Schools Division Office")
        self.district.setPlaceholderText("Schools District")
        self.address.setPlaceholderText("School address")
        self.email.setPlaceholderText("Official email address")
        self.contact.setPlaceholderText("Contact number")
        self.school_head.setPlaceholderText("Name of School Head")
        self.csm_focal_person.setPlaceholderText("CSM focal person or operator")

        fields = (
            ("School name", self.school_name),
            ("School ID", self.school_id),
            ("Region", self.region),
            ("Schools Division", self.division),
            ("Schools District", self.district),
            ("School address", self.address),
            ("Official email", self.email),
            ("Contact number", self.contact),
            ("School Head", self.school_head),
            ("CSM focal person", self.csm_focal_person),
        )
        for index, (label_text, field) in enumerate(fields):
            row = (index // 2) * 2 + 1
            column = index % 2
            identity_grid.addWidget(self._field_label(label_text), row, column)
            identity_grid.addWidget(field, row + 1, column)
        layout.addWidget(identity_card)

        logo_card = self._card("School logo")
        logo_grid = logo_card.layout()
        self.logo_preview = QLabel()
        self.logo_preview.setObjectName("school_logo_preview")
        self.logo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_preview.setFixedSize(190, 190)
        self.logo_preview.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        logo_grid.addWidget(self.logo_preview, 1, 0, 3, 1, Qt.AlignmentFlag.AlignTop)

        logo_text = QLabel(
            "Upload the official school logo in PNG, JPG, JPEG, BMP, or WEBP format. "
            "The Control Center stores a normalized PNG copy and uses it in its fixed footer and the CSM Survey Form footer."
        )
        logo_text.setObjectName("school_information_note")
        logo_text.setWordWrap(True)
        logo_grid.addWidget(logo_text, 1, 1, 1, 2)

        self.upload_logo_button = TooltipIconButton(
            "upload", "Upload or replace the school logo", button_size=40, icon_size=21
        )
        self.remove_logo_button = TooltipIconButton(
            "delete", "Remove the current school logo", button_size=40, icon_size=21, role="danger"
        )
        logo_actions = QHBoxLayout()
        logo_actions.setSpacing(8)
        logo_actions.addWidget(self.upload_logo_button)
        logo_actions.addWidget(self.remove_logo_button)
        logo_actions.addStretch(1)
        logo_grid.addLayout(logo_actions, 2, 1, 1, 2)

        self.logo_path_label = QLabel("No school logo uploaded")
        self.logo_path_label.setObjectName("school_information_logo_path")
        self.logo_path_label.setWordWrap(True)
        logo_grid.addWidget(self.logo_path_label, 3, 1, 1, 2)
        layout.addWidget(logo_card)
        layout.addStretch(1)

        self.save_button.clicked.connect(self._save_requested)
        self.upload_logo_button.clicked.connect(self._upload_requested)
        self.remove_logo_button.clicked.connect(self._remove_requested)

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
        label = QLabel(text)
        label.setObjectName("school_information_field_label")
        return label

    def _save_requested(self) -> None:
        try:
            self.save_settings()
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")

    def _upload_requested(self) -> None:
        try:
            self.upload_logo()
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")

    def _remove_requested(self) -> None:
        try:
            self.remove_logo()
        except Exception as exc:
            self.notice_requested.emit(str(exc), "error")

    def load_settings(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        field_values = {
            self.school_name: settings.get("school_name", ""),
            self.school_id: settings.get("school_id", ""),
            self.region: settings.get("school_region", ""),
            self.division: settings.get("school_division", ""),
            self.district: settings.get("school_district", ""),
            self.address: settings.get("school_address", ""),
            self.email: settings.get("school_email", ""),
            self.contact: settings.get("school_contact", ""),
            self.school_head: settings.get("school_head", ""),
            self.csm_focal_person: settings.get("csm_focal_person", ""),
        }
        for field, value in field_values.items():
            field.setText(str(value or ""))
        self._refresh_logo_preview(settings)
        return settings

    def save_settings(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        school_id = validate_school_id(self._clean(self.school_id.text()), required=True)
        if self._registered_school_id and school_id != self._registered_school_id:
            self.school_id.setText(self._registered_school_id)
            raise ValueError(
                "School ID is locked because this installation is registered for Internet Gateway access. Use Server Transfer for another device or contact the gateway administrator to correct an official School ID."
            )
        settings.update(
            {
                "school_name": self._clean(self.school_name.text()) or "School",
                "school_id": school_id,
                "school_region": self._clean(self.region.text()),
                "school_division": self._clean(self.division.text()),
                "school_district": self._clean(self.district.text()),
                "school_address": self._clean(self.address.text()),
                "school_email": self._clean(self.email.text()),
                "school_contact": self._clean(self.contact.text()),
                "school_head": self._clean(self.school_head.text()),
                "csm_focal_person": self._clean(self.csm_focal_person.text()),
                "school_logo_path": (
                    str(self.logo_target.relative_to(self.data_root)).replace("\\", "/")
                    if self.logo_target.is_file()
                    else ""
                ),
            }
        )
        saved = self.settings_store.save(settings)
        self._refresh_logo_preview(saved)
        self.school_information_saved.emit(saved)
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

    def upload_logo(self) -> None:
        source_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Upload school logo",
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
        self.logo_target.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.toImage().save(str(self.logo_target), "PNG"):
            raise OSError("The school logo could not be saved.")
        self.save_settings()

    def remove_logo(self) -> None:
        try:
            self.logo_target.unlink(missing_ok=True)
        except OSError as exc:
            raise OSError(f"The school logo could not be removed: {exc}") from exc
        self.save_settings()

    def _refresh_logo_preview(self, settings: dict[str, Any] | None = None) -> None:
        settings = settings or self.settings_store.load()
        configured = str(settings.get("school_logo_path") or "").strip()
        path = self.data_root / configured if configured else self.logo_target
        pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
        if pixmap.isNull():
            self.logo_preview.setPixmap(action_icon("school", theme.ACCENT_CYAN, 72).pixmap(72, 72))
            self.logo_path_label.setText("No school logo uploaded")
            self.remove_logo_button.setEnabled(False)
            return
        self.logo_preview.setPixmap(
            pixmap.scaled(
                164,
                164,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.logo_path_label.setText(str(path))
        self.remove_logo_button.setEnabled(True)

    @staticmethod
    def _clean(value: object) -> str:
        return " ".join(str(value or "").split())

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#school_information_board, QWidget#school_information_content {{
                background: {theme.WINDOW_BG};
            }}
            QScrollArea#school_information_scroll {{
                background: {theme.WINDOW_BG};
                border: none;
            }}
            QLabel#school_information_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 26px;
                font-weight: 900;
            }}
            QLabel#school_information_subtitle,
            QLabel#school_information_note,
            QLabel#school_information_logo_path {{
                color: {theme.TEXT_MUTED};
                font-size: 11px;
            }}
            QFrame#school_information_card {{
                background: rgba(10, 29, 48, 0.94);
                border: 1px solid {theme.CARD_BORDER};
                border-radius: 12px;
            }}
            QLabel#school_information_card_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 15px;
                font-weight: 850;
            }}
            QLabel#school_information_field_label {{
                color: {theme.TEXT_SECONDARY};
                font-size: 11px;
                font-weight: 750;
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
            QLineEdit:focus {{
                border-color: {theme.ACCENT_CYAN};
            }}
            QLabel#school_logo_preview {{
                background: rgba(7, 23, 39, 0.78);
                border: 1px dashed rgba(98, 219, 255, 0.48);
                border-radius: 16px;
            }}
            """
        )
