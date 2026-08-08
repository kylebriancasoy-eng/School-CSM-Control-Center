from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from school_csm_control_center.ui import theme
from school_csm_control_center.ui.widgets import TooltipIconButton


class ClickScrim(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class WorkspaceOverlay(QWidget):
    """A full-workspace utility panel hosted inside the main application."""

    closed = Signal()

    def __init__(
        self,
        parent: QWidget,
        content: QWidget,
        *,
        title: str,
        subtitle: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workspace_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._return_focus: QWidget | None = None
        self.hide()

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("workspace_overlay_scrim")
        self.scrim.clicked.connect(self.close_overlay)

        self.panel = QFrame(self)
        self.panel.setObjectName("workspace_overlay_panel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("workspace_overlay_header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 14, 14, 14)
        header_layout.setSpacing(12)
        copy_layout = QVBoxLayout()
        copy_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("workspace_overlay_title")
        copy_layout.addWidget(title_label)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("workspace_overlay_subtitle")
        subtitle_label.setWordWrap(True)
        subtitle_label.setVisible(bool(subtitle))
        copy_layout.addWidget(subtitle_label)
        header_layout.addLayout(copy_layout, 1)
        self.close_button = TooltipIconButton(
            "close",
            f"Close {title}",
            icon_color=theme.TEXT_SECONDARY,
            role="subtle",
        )
        self.close_button.clicked.connect(self.close_overlay)
        header_layout.addWidget(self.close_button)
        panel_layout.addWidget(header)

        self.content = content
        panel_layout.addWidget(self.content, 1)
        self.setStyleSheet(
            f"""
            QWidget#workspace_overlay {{ background: transparent; }}
            QFrame#workspace_overlay_scrim {{
                background: rgba(1, 8, 18, 0.76);
                border: none;
            }}
            QFrame#workspace_overlay_panel {{
                background: {theme.WINDOW_BG};
                border: 1px solid rgba(50, 217, 204, 0.62);
                border-radius: 18px;
            }}
            QFrame#workspace_overlay_header {{
                background: {theme.PANEL_BG};
                border: none;
                border-bottom: 1px solid rgba(112, 139, 164, 0.22);
                border-top-left-radius: 18px;
                border-top-right-radius: 18px;
            }}
            QLabel#workspace_overlay_title {{
                color: {theme.TEXT_PRIMARY};
                background: transparent;
                font-size: 18px;
                font-weight: 800;
            }}
            QLabel#workspace_overlay_subtitle {{
                color: {theme.TEXT_SECONDARY};
                background: transparent;
                font-size: 13px;
            }}
            """
        )

    def open_overlay(self, return_focus: QWidget | None = None) -> None:
        self._return_focus = return_focus
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.close_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def close_overlay(self) -> None:
        if not self.isVisible():
            return
        return_focus = self._return_focus
        self._return_focus = None
        self.hide()
        self.closed.emit()
        if return_focus is not None:
            return_focus.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            event.accept()
            return
        super().keyPressEvent(event)

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        margin = 18 if min(self.width(), self.height()) >= 720 else 8
        self.panel.setGeometry(self.rect().adjusted(margin, margin, -margin, -margin))


class OverlayPrompt(QWidget):
    """An in-window prompt; it never creates a dialog or another top-level window."""

    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("overlay_prompt")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()
        self._on_accept: Callable[[], None] | None = None
        self._on_reject: Callable[[], None] | None = None
        self._on_alternate: Callable[[], None] | None = None

        self.scrim = ClickScrim(self)
        self.scrim.setObjectName("overlay_prompt_scrim")
        self.scrim.clicked.connect(self._reject)

        self.panel = QFrame(self)
        self.panel.setObjectName("overlay_prompt_panel")
        self.panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(26, 24, 26, 22)
        panel_layout.setSpacing(12)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(12)
        self.marker = QLabel("i")
        self.marker.setObjectName("overlay_prompt_marker")
        self.marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.marker.setFixedSize(34, 34)
        heading_row.addWidget(self.marker, 0, Qt.AlignmentFlag.AlignTop)

        heading_copy = QVBoxLayout()
        heading_copy.setSpacing(5)
        self.title_label = QLabel()
        self.title_label.setObjectName("overlay_prompt_title")
        self.title_label.setWordWrap(True)
        heading_copy.addWidget(self.title_label)
        self.message_label = QLabel()
        self.message_label.setObjectName("overlay_prompt_message")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        heading_copy.addWidget(self.message_label)
        heading_row.addLayout(heading_copy, 1)
        panel_layout.addLayout(heading_row)

        self.detail_label = QLabel()
        self.detail_label.setObjectName("overlay_prompt_detail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.hide()
        panel_layout.addWidget(self.detail_label)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.alternate_button = TooltipIconButton(
            "reset",
            "Alternate action",
            icon_color=theme.WARNING,
            role="subtle",
        )
        self.alternate_button.clicked.connect(self._alternate)
        self.alternate_button.hide()
        actions.addWidget(self.alternate_button)
        self.cancel_button = TooltipIconButton(
            "cancel",
            "Cancel",
            icon_color=theme.TEXT_SECONDARY,
            role="subtle",
        )
        self.cancel_button.clicked.connect(self._reject)
        actions.addWidget(self.cancel_button)
        self.accept_button = TooltipIconButton("check", "Confirm", role="primary")
        self.accept_button.clicked.connect(self._accept)
        actions.addWidget(self.accept_button)
        panel_layout.addLayout(actions)

        self.setStyleSheet(
            f"""
            QWidget#overlay_prompt {{ background: transparent; }}
            QFrame#overlay_prompt_scrim {{
                background: rgba(1, 8, 18, 0.76);
                border: none;
            }}
            QFrame#overlay_prompt_panel {{
                background: {theme.PANEL_BG};
                border: 1px solid rgba(50, 217, 204, 0.68);
                border-radius: 18px;
            }}
            QLabel#overlay_prompt_marker {{
                background: rgba(50, 217, 204, 0.14);
                border: 1px solid rgba(50, 217, 204, 0.52);
                border-radius: 17px;
                color: {theme.ACCENT_CYAN};
                font-size: 17px;
                font-weight: 800;
            }}
            QLabel#overlay_prompt_title {{
                color: {theme.TEXT_PRIMARY};
                font-size: 18px;
                font-weight: 800;
                background: transparent;
            }}
            QLabel#overlay_prompt_message {{
                color: {theme.TEXT_SECONDARY};
                font-size: 13px;
                background: transparent;
            }}
            QLabel#overlay_prompt_detail {{
                color: {theme.TEXT_MUTED};
                font-size: 12px;
                background: rgba(3, 18, 37, 0.68);
                border: 1px solid rgba(112, 139, 164, 0.22);
                border-radius: 10px;
                padding: 12px;
            }}
            """
        )

    def show_prompt(
        self,
        title: str,
        message: str,
        *,
        detail: str = "",
        accept_tooltip: str = "Confirm",
        cancel_tooltip: str = "Cancel",
        marker: str = "i",
        destructive: bool = False,
        show_cancel: bool = True,
        alternate_tooltip: str = "",
        on_accept: Callable[[], None] | None = None,
        on_reject: Callable[[], None] | None = None,
        on_alternate: Callable[[], None] | None = None,
    ) -> None:
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.marker.setText(marker)
        self.cancel_button.setVisible(show_cancel)
        self.alternate_button.setVisible(bool(alternate_tooltip and on_alternate is not None))
        if alternate_tooltip:
            self.alternate_button.setToolTip(alternate_tooltip)
            self.alternate_button.setAccessibleName(alternate_tooltip)
        self.cancel_button.setToolTip(cancel_tooltip)
        self.cancel_button.setAccessibleName(cancel_tooltip)
        self.accept_button.setToolTip(accept_tooltip)
        self.accept_button.setAccessibleName(accept_tooltip)
        self.accept_button.set_role("danger" if destructive else "primary")
        self._on_accept = on_accept
        self._on_reject = on_reject
        self._on_alternate = on_alternate
        self.setGeometry(self.parentWidget().rect())
        self._position_children()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.accept_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def show_information(
        self,
        title: str,
        message: str,
        *,
        detail: str = "",
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self.show_prompt(
            title,
            message,
            detail=detail,
            accept_tooltip="Close",
            marker="i",
            show_cancel=False,
            on_accept=on_close,
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._position_children()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self._reject()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept()
            event.accept()
            return
        super().keyPressEvent(event)

    def _position_children(self) -> None:
        self.scrim.setGeometry(self.rect())
        panel_width = min(610, max(330, self.width() - 48))
        self.panel.setFixedWidth(panel_width)
        self.panel.adjustSize()
        panel_height = min(max(220, self.panel.sizeHint().height()), max(220, self.height() - 48))
        self.panel.setGeometry(
            (self.width() - panel_width) // 2,
            (self.height() - panel_height) // 2,
            panel_width,
            panel_height,
        )

    def _accept(self) -> None:
        callback = self._on_accept
        self.hide()
        self._clear_callbacks()
        self.accepted.emit()
        if callback is not None:
            callback()

    def _reject(self) -> None:
        callback = self._on_reject
        self.hide()
        self._clear_callbacks()
        self.rejected.emit()
        if callback is not None:
            callback()

    def _alternate(self) -> None:
        callback = self._on_alternate
        self.hide()
        self._clear_callbacks()
        if callback is not None:
            callback()

    def _clear_callbacks(self) -> None:
        self._on_accept = None
        self._on_reject = None
        self._on_alternate = None


class Toast(QFrame):
    """Short-lived in-app status flag."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("app_toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(9)
        self.dot = QLabel("●")
        self.dot.setObjectName("app_toast_dot")
        layout.addWidget(self.dot)
        self.label = QLabel()
        self.label.setObjectName("app_toast_text")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.setStyleSheet(
            f"""
            QFrame#app_toast {{
                background: rgba(5, 31, 50, 0.98);
                border: 1px solid rgba(50, 217, 204, 0.64);
                border-radius: 12px;
            }}
            QLabel#app_toast_dot {{ color: {theme.SUCCESS}; background: transparent; }}
            QLabel#app_toast_text {{
                color: {theme.TEXT_PRIMARY};
                background: transparent;
                font-size: 12px;
                font-weight: 700;
            }}
            """
        )

    def show_message(self, message: str, *, kind: str = "success", timeout_ms: int = 3200) -> None:
        color = {
            "success": theme.SUCCESS,
            "warning": theme.WARNING,
            "error": theme.DANGER,
            "info": theme.ACCENT_CYAN,
        }.get(kind, theme.ACCENT_CYAN)
        self.dot.setStyleSheet(f"color: {color}; background: transparent;")
        self.label.setText(message)
        width = min(460, max(260, self.parentWidget().width() - 42))
        self.setFixedWidth(width)
        self.adjustSize()
        self.move(max(18, self.parentWidget().width() - width - 24), 24)
        self.effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self.timer.start(timeout_ms)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ParentChange and self.parentWidget() is not None:
            self.raise_()
        return super().event(event)
