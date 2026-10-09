"""Motiong district visual identity shared by the native district windows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


DISTRICT_SEAL_RELATIVE_PATH = Path("registration_service/static/district-seal.png")


def district_icon(install_root: Path) -> QIcon:
    """Prefer the district seal, retaining the legacy icon as a safe fallback."""
    seal_path = install_root / DISTRICT_SEAL_RELATIVE_PATH
    if seal_path.is_file():
        seal = QPixmap(str(seal_path))
        if not seal.isNull():
            return QIcon(seal)
    fallback_path = install_root / "School CSM Control Center Icon.ico"
    return QIcon(str(fallback_path)) if fallback_path.is_file() else QIcon()


def district_header(
    install_root: Path,
    title: str,
    subtitle: str = "",
    parent: QWidget | None = None,
) -> QWidget:
    """Render the unchanged seal beside a readable, wrapping district heading."""
    header = QWidget(parent)
    header.setObjectName("districtHeader")
    row = QHBoxLayout(header)
    row.setContentsMargins(18, 16, 18, 16)
    row.setSpacing(18)

    seal_path = install_root / DISTRICT_SEAL_RELATIVE_PATH
    if seal_path.is_file():
        pixmap = QPixmap(str(seal_path))
        if not pixmap.isNull():
            seal = QLabel(header)
            seal.setObjectName("districtSeal")
            seal.setAccessibleName("Official Motiong District seal")
            seal.setPixmap(
                pixmap.scaled(
                    96,
                    96,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
            seal.setFixedSize(96, 96)
            row.addWidget(seal)

    text = QVBoxLayout()
    text.setSpacing(5)
    district = QLabel("MOTIONG DISTRICT", header)
    district.setObjectName("districtEyebrow")
    heading = QLabel(title, header)
    heading.setObjectName("title")
    heading.setWordWrap(True)
    text.addWidget(district)
    text.addWidget(heading)
    if subtitle:
        description = QLabel(subtitle, header)
        description.setObjectName("districtSubtitle")
        description.setWordWrap(True)
        text.addWidget(description)
    row.addLayout(text, 1)
    return header


DISTRICT_STYLESHEET = """
QWidget {
    background: #f8fbef;
    color: #173b43;
    font-family: 'Segoe UI';
    font-size: 14px;
}
QWidget#districtHeader {
    background: #ffffff;
    border: 1px solid #b9e2e3;
    border-top: 4px solid #05bfdc;
    border-radius: 12px;
}
QWidget#districtHeader QLabel { background: transparent; border: none; }
QLabel#districtEyebrow { color: #357f38; font-size: 11px; font-weight: 800; }
QLabel#title { color: #08627a; font-size: 23px; font-weight: 800; }
QLabel#districtSubtitle { color: #4a6268; font-size: 13px; }
QWidget#serviceStateCard {
    background: #ffffff;
    border: 1px solid #b9e2e3;
    border-left: 4px solid #357f38;
    border-radius: 10px;
}
QWidget#serviceStateCard QLabel { background: transparent; border: none; }
QLabel#state { color: #08627a; font-size: 28px; font-weight: 800; }
QLabel#providerNote { color: #4a6268; font-size: 12px; }
QPushButton {
    background: #ffffff;
    color: #08627a;
    border: 2px solid #93c4c9;
    border-radius: 8px;
    padding: 10px 15px;
    font-weight: 700;
    min-height: 18px;
}
QPushButton:hover { background: #e8f7f5; border-color: #08627a; }
QPushButton:pressed { background: #d5efeb; }
QPushButton#primaryAction { background: #08627a; color: #ffffff; border-color: #08627a; }
QPushButton#primaryAction:hover { background: #074e62; border-color: #074e62; }
QPushButton#primaryAction:pressed { background: #053d4d; border-color: #053d4d; }
QPushButton#stopAction { background: #fff8e5; color: #634a12; border-color: #d7b85b; }
QPushButton#stopAction:hover { background: #ffefbf; border-color: #a67b12; }
QPushButton:focus { border-color: #a67b12; }
QPushButton#primaryAction:focus { border-color: #f1b51c; }
QPushButton:disabled,
QPushButton#primaryAction:disabled,
QPushButton#stopAction:disabled { background: #edf0eb; color: #657477; border-color: #b8c5c5; }
QLineEdit {
    background: #ffffff;
    color: #173b43;
    border: 2px solid #93c4c9;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #08627a;
    selection-color: #ffffff;
}
QLineEdit:focus { border-color: #08627a; }
QLineEdit:read-only { background: #eef4ec; color: #405c61; }
QLineEdit:disabled { background: #edf0eb; color: #657477; border-color: #b8c5c5; }
QCheckBox { spacing: 8px; padding: 6px 0; }
QCheckBox:focus { color: #08627a; }
QMenu { background: #ffffff; border: 1px solid #93c4c9; padding: 4px; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #08627a; color: #ffffff; }
QMenu::separator { height: 1px; background: #c8dddc; margin: 4px 8px; }
"""
