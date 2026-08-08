from __future__ import annotations

"""Shared visual language for the School CSM Control Center.

The palette deliberately keeps the dark navy character of the surrounding
MoSSLab tools while shifting the interactive accent toward teal.  Text colors
are selected for strong contrast on every declared surface.
"""

from PySide6.QtWidgets import QApplication


FONT_FAMILY = "Segoe UI"

WINDOW_BG = "#06101E"
WINDOW_BG_ALT = "#09192B"
TITLE_BG = "#040C17"
PANEL_BG = "#0A1D30"
PANEL_BG_ALT = "#0D263B"
CARD_BG = "#0D2940"
CARD_BG_HOVER = "#123A55"
CARD_BORDER = "#1B5572"
DIVIDER = "#173B53"

TEXT_PRIMARY = "#F4FAFF"
TEXT_SECONDARY = "#C2D2DF"
TEXT_MUTED = "#91A7B9"

ACCENT_TEAL = "#2BD9C5"
ACCENT_TEAL_DARK = "#168E88"
ACCENT_CYAN = "#62DBFF"
ACCENT_BLUE = "#3B82F6"
SUCCESS = "#39DB91"
WARNING = "#F7BE5B"
DANGER = "#FF6B78"
PURPLE = "#B794F6"
PINK = "#F472B6"

CHART_COLORS = (
    ACCENT_TEAL,
    ACCENT_CYAN,
    ACCENT_BLUE,
    PURPLE,
    WARNING,
    PINK,
    SUCCESS,
    DANGER,
)


SCROLLBAR_STYLESHEET = f"""
QScrollBar:vertical {{
    background: rgba(6, 16, 30, 0.72);
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(43, 217, 197, 0.58);
    min-height: 38px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(98, 219, 255, 0.82);
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: rgba(6, 16, 30, 0.72);
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: rgba(43, 217, 197, 0.58);
    min-width: 38px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{
    background: rgba(98, 219, 255, 0.82);
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QAbstractScrollArea::corner {{
    background: {WINDOW_BG};
}}
"""


ROOT_STYLESHEET = f"""
* {{
    font-family: '{FONT_FAMILY}';
    color: {TEXT_PRIMARY};
}}
QWidget {{
    background-color: {WINDOW_BG};
}}
QFrame, QLabel {{
    background: transparent;
}}
QToolTip {{
    background-color: #102C43;
    color: {TEXT_PRIMARY};
    border: 1px solid {ACCENT_TEAL};
    border-radius: 6px;
    padding: 6px 9px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton {{
    border: none;
}}
QPushButton#csm_icon_button {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(18, 58, 85, 0.98), stop:1 rgba(9, 29, 48, 0.98));
    border: 1px solid rgba(43, 217, 197, 0.55);
    border-radius: 9px;
    padding: 0;
}}
QPushButton#csm_icon_button:hover,
QPushButton#csm_icon_button:focus {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(22, 142, 136, 0.88), stop:1 rgba(17, 91, 120, 0.90));
    border-color: {ACCENT_CYAN};
}}
QPushButton#csm_icon_button:pressed {{
    background: rgba(22, 142, 136, 0.72);
}}
QPushButton#csm_icon_button:disabled {{
    background: rgba(9, 29, 48, 0.52);
    border-color: rgba(145, 167, 185, 0.22);
}}
QPushButton#csm_icon_button[role="primary"] {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 rgba(22, 142, 136, 0.94), stop:1 rgba(24, 92, 126, 0.94));
    border-color: {ACCENT_TEAL};
}}
QPushButton#csm_icon_button[role="primary"]:hover,
QPushButton#csm_icon_button[role="primary"]:focus {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 rgba(30, 171, 160, 0.96), stop:1 rgba(36, 125, 161, 0.96));
    border-color: {ACCENT_CYAN};
}}
QPushButton#csm_icon_button[role="subtle"] {{
    background: rgba(8, 27, 45, 0.44);
    border-color: rgba(145, 167, 185, 0.36);
}}
QPushButton#csm_icon_button[role="subtle"]:hover,
QPushButton#csm_icon_button[role="subtle"]:focus {{
    background: rgba(18, 58, 85, 0.78);
    border-color: {TEXT_SECONDARY};
}}
QPushButton#csm_icon_button[role="danger"] {{
    background: rgba(83, 30, 43, 0.72);
    border-color: rgba(255, 107, 120, 0.70);
}}
QPushButton#csm_icon_button[role="danger"]:hover,
QPushButton#csm_icon_button[role="danger"]:focus {{
    background: rgba(150, 48, 65, 0.66);
    border-color: {DANGER};
}}
QPushButton#csm_icon_button[role="danger"]:disabled {{
    background: rgba(41, 29, 39, 0.48);
    border-color: rgba(145, 167, 185, 0.22);
}}
QPushButton#csm_icon_button[role="success"] {{
    background: rgba(20, 91, 68, 0.72);
    border-color: rgba(57, 219, 145, 0.70);
}}
QPushButton#csm_icon_button[role="success"]:hover,
QPushButton#csm_icon_button[role="success"]:focus {{
    background: rgba(27, 132, 96, 0.72);
    border-color: {SUCCESS};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit {{
    background: rgba(5, 19, 33, 0.92);
    color: {TEXT_PRIMARY};
    border: 1px solid rgba(43, 217, 197, 0.42);
    border-radius: 8px;
    min-height: 34px;
    padding-left: 10px;
    padding-right: 10px;
    selection-background-color: {ACCENT_TEAL_DARK};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{
    border-color: {ACCENT_CYAN};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDateEdit:disabled {{
    background: rgba(10, 26, 42, 0.72);
    color: {TEXT_MUTED};
    border-color: rgba(43, 217, 197, 0.24);
}}
QComboBox QAbstractItemView {{
    background: {PANEL_BG_ALT};
    color: {TEXT_PRIMARY};
    border: 1px solid {CARD_BORDER};
    selection-background-color: {ACCENT_TEAL_DARK};
    outline: none;
}}
QCheckBox, QRadioButton {{
    color: {TEXT_SECONDARY};
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    background: rgba(4, 18, 31, 0.96);
    border: 1px solid rgba(145, 167, 185, 0.72);
    border-radius: 4px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT_TEAL};
    border: 2px solid {ACCENT_CYAN};
}}
""" + SCROLLBAR_STYLESHEET


def apply_theme(app: QApplication) -> None:
    """Apply the shared application stylesheet to an existing QApplication."""

    app.setStyleSheet(ROOT_STYLESHEET)
