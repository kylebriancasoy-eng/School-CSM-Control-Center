"""Readable, keyboard-accessible actions for operational dashboards."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton, QSizePolicy

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.icons import action_icon


class ActionButton(QPushButton):
    def __init__(self, icon: str, text: str, tooltip: str = "", *, role: str = "secondary"):
        super().__init__(text)
        self.setObjectName("dashboard_action")
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or text)
        self.setAccessibleName(text)
        self.setAccessibleDescription(tooltip or text)
        color = theme.DANGER if role == "danger" else theme.TEXT_PRIMARY
        background = "rgba(105, 35, 48, 0.22)" if role == "danger" else theme.CARD_BG
        border = theme.CARD_BORDER
        if role in {"primary", "success"}:
            color, background, border = "#04212a", theme.ACCENT_TEAL, theme.ACCENT_TEAL
        self.setIcon(action_icon(icon, color, 18))
        self.setIconSize(QSize(18, 18))
        self.setStyleSheet(f"""
            QPushButton {{ color: {color}; background: {background};
                border: 1px solid {border}; border-radius: 8px;
                padding: 6px 10px; font-size: 12px; font-weight: 650; }}
            QPushButton:hover {{ border-color: {theme.ACCENT_CYAN}; }}
            QPushButton:focus {{ border: 2px solid {theme.ACCENT_CYAN}; }}
            QPushButton:pressed {{ background: rgba(50, 217, 204, 0.25); }}
            QPushButton:disabled {{ color: {theme.TEXT_MUTED}; background: rgba(16, 35, 51, 0.5);
                border-color: rgba(112, 139, 164, 0.2); }}
        """)
