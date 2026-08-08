from __future__ import annotations

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.app_identity import (
    APPLICATION_SUBTITLE,
    FORMAL_APPLICATION_NAME,
    SHORT_APPLICATION_NAME,
)
from school_csm_control_center.ui import theme
from school_csm_control_center.ui.widgets import TooltipIconButton


class BrandedTitleBar(QFrame):
    """Recognizable in-app window chrome using the CSM visual language."""

    def __init__(self, host_window: QMainWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host_window = host_window
        self.setObjectName("custom_title_bar")
        self.setAccessibleName(f"{FORMAL_APPLICATION_NAME} title bar")
        self.setFixedHeight(60)
        self._manual_drag = False
        self._drag_offset = QPoint()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(10)

        self.brand_label = QLabel("CSMS")
        self.brand_label.setObjectName("title_bar_brand")
        self.brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_label.setFixedSize(42, 42)
        self.brand_label.setAccessibleName(f"{FORMAL_APPLICATION_NAME} application icon")
        self.brand_label.setToolTip(FORMAL_APPLICATION_NAME)
        if str(self.host_window.property("applicationIconPath") or "").strip():
            brand_pixmap = self.host_window.windowIcon().pixmap(38, 38)
            if not brand_pixmap.isNull():
                self.brand_label.setText("")
                self.brand_label.setPixmap(brand_pixmap)
        self.brand_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self.brand_label)

        copy = QVBoxLayout()
        copy.setSpacing(1)
        self.title_label = QLabel(FORMAL_APPLICATION_NAME.upper())
        self.title_label.setObjectName("title_bar_title")
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        copy.addWidget(self.title_label)
        self.subtitle_label = QLabel(APPLICATION_SUBTITLE.upper())
        self.subtitle_label.setObjectName("title_bar_subtitle")
        self.subtitle_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        copy.addWidget(self.subtitle_label)
        layout.addLayout(copy)
        layout.addStretch(1)

        self.mosslab_logo = QLabel()
        self.mosslab_logo.setObjectName("title_bar_mosslab_logo")
        self.mosslab_logo.setFixedSize(72, 42)
        self.mosslab_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mosslab_logo.setToolTip("MoSSLab · Motiong Schools Systems Laboratory")
        self.mosslab_logo.setAccessibleName("MoSSLab logo")
        logo_path = str(self.host_window.property("mosslabLogoPath") or "").strip()
        if logo_path:
            logo_pixmap = QPixmap(logo_path)
            if not logo_pixmap.isNull():
                self.mosslab_logo.setPixmap(
                    logo_pixmap.scaled(
                        68,
                        40,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self.mosslab_logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.mosslab_logo)

        self.status_label = QLabel("●  LIVE ANALYSIS")
        self.status_label.setObjectName("title_bar_status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setToolTip("Dashboard analysis refreshes after every saved response")
        self.status_label.setAccessibleName("Live analysis active")
        self.status_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self.status_label)

        self.minimize_button = TooltipIconButton(
            "minimize",
            "Minimize window",
            button_size=34,
            icon_size=17,
            role="subtle",
        )
        self.minimize_button.clicked.connect(self.host_window.showMinimized)
        layout.addWidget(self.minimize_button)

        self.maximize_button = TooltipIconButton(
            "maximize",
            "Maximize window",
            button_size=34,
            icon_size=17,
            role="subtle",
        )
        self.maximize_button.clicked.connect(self.toggle_max_restore)
        layout.addWidget(self.maximize_button)

        self.close_button = TooltipIconButton(
            "close",
            f"Close {SHORT_APPLICATION_NAME}",
            icon_color=theme.DANGER,
            button_size=34,
            icon_size=17,
            role="danger",
        )
        self.close_button.clicked.connect(self.host_window.close)
        layout.addWidget(self.close_button)

        self._apply_styles()
        self.sync_window_state()

    def toggle_max_restore(self) -> None:
        if self.host_window.isMaximized():
            self.host_window.showNormal()
        else:
            self.host_window.showMaximized()
        QTimer.singleShot(0, self.sync_window_state)

    def sync_window_state(self) -> None:
        maximized = self.host_window.isMaximized()
        self.maximize_button.set_action(
            "restore" if maximized else "maximize",
            "Restore window" if maximized else "Maximize window",
        )
        self.setProperty("windowMaximized", maximized)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def set_compact(self, available_width: int) -> None:
        width = int(available_width)
        self.subtitle_label.setVisible(width >= 880)
        self.status_label.setVisible(width >= 1040)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._drag_offset = (
            event.globalPosition().toPoint()
            - self.host_window.frameGeometry().topLeft()
        )
        window_handle = self.host_window.windowHandle()
        started = bool(window_handle and window_handle.startSystemMove())
        self._manual_drag = not started and not self.host_window.isMaximized()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._manual_drag and event.buttons() & Qt.MouseButton.LeftButton:
            self.host_window.move(
                event.globalPosition().toPoint() - self._drag_offset
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._manual_drag = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame#custom_title_bar {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {theme.TITLE_BG},
                    stop:0.46 {theme.PANEL_BG},
                    stop:1 {theme.TITLE_BG});
                border: none;
                border-bottom: 1px solid rgba(43, 217, 197, 0.58);
            }}
            QLabel#title_bar_brand {{
                color: {theme.ACCENT_TEAL};
                background: qradialgradient(cx:0.5,cy:0.5,radius:0.72,
                    stop:0 rgba(43,217,197,0.18), stop:1 rgba(6,16,30,0.94));
                border: 1px solid rgba(98, 219, 255, 0.72);
                border-radius: 12px;
                font-size: 11px;
                font-weight: 900;
            }}
            QLabel#title_bar_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 900;
            }}
            QLabel#title_bar_subtitle {{
                color: {theme.TEXT_MUTED};
                font-size: 9px;
                font-weight: 700;
            }}
            QLabel#title_bar_mosslab_logo {{
                background: rgba(4, 16, 30, 0.38);
                border: 1px solid rgba(98, 219, 255, 0.24);
                border-radius: 9px;
                padding: 1px;
            }}
            QLabel#title_bar_status {{
                color: {theme.SUCCESS};
                background: rgba(20, 91, 68, 0.28);
                border: 1px solid rgba(57, 219, 145, 0.46);
                border-radius: 10px;
                padding: 5px 10px;
                font-size: 9px;
                font-weight: 800;
            }}
            QFrame#custom_title_bar QPushButton#csm_icon_button {{
                background: rgba(8, 27, 45, 0.68);
                border: 1px solid rgba(98, 219, 255, 0.32);
                border-radius: 8px;
            }}
            QFrame#custom_title_bar QPushButton#csm_icon_button:hover,
            QFrame#custom_title_bar QPushButton#csm_icon_button:focus {{
                background: rgba(22, 142, 136, 0.58);
                border-color: {theme.ACCENT_CYAN};
            }}
            QFrame#custom_title_bar QPushButton#csm_icon_button[role="danger"] {{
                background: rgba(83, 30, 43, 0.52);
                border-color: rgba(255, 107, 120, 0.46);
            }}
            QFrame#custom_title_bar QPushButton#csm_icon_button[role="danger"]:hover {{
                background: rgba(150, 48, 65, 0.78);
                border-color: {theme.DANGER};
            }}
            """
        )


class WindowResizeHandle(QFrame):
    """Transparent edge that delegates resizing to the operating system."""

    def __init__(
        self,
        host_window: QMainWindow,
        edges,
        cursor: Qt.CursorShape,
        name: str,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.host_window = host_window
        self.edges = edges
        self.setObjectName("window_resize_handle")
        self.setProperty("edgeName", name)
        self.setAccessibleName(f"Resize window from {name.replace('_', ' ')}")
        self.setCursor(cursor)
        self.setStyleSheet(
            "QFrame#window_resize_handle { background: transparent; border: none; }"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.host_window.isMaximized()
            and not self.host_window.isFullScreen()
        ):
            window_handle = self.host_window.windowHandle()
            if window_handle is not None:
                window_handle.startSystemResize(self.edges)
                event.accept()
                return
        super().mousePressEvent(event)
