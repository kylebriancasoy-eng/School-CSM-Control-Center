from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox, QWidget


class _ClickActivatedWheelMixin:
    """Allow wheel changes only after the editor was explicitly clicked.

    Hovering a field while scrolling a surrounding panel must never alter its
    value.  Losing focus disarms the field again.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._wheel_armed = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._wheel_armed = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._wheel_armed and self.hasFocus():
            super().wheelEvent(event)
            return
        event.ignore()


class NoWheelComboBox(_ClickActivatedWheelMixin, QComboBox):
    """Ignore hover-wheel selection changes until the combo is clicked."""


class NoWheelSpinBox(_ClickActivatedWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(_ClickActivatedWheelMixin, QDoubleSpinBox):
    pass


class NoWheelDateEdit(_ClickActivatedWheelMixin, QDateEdit):
    pass


class OptionalAgeSpinBox(NoWheelSpinBox):
    """Optional age editor whose special text clears only while editing."""

    EMPTY_TEXT = "Not Provided"
    EMPTY_VALUE = -1

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(self.EMPTY_VALUE, 130)
        self.setSpecialValueText(self.EMPTY_TEXT)
        self.setValue(self.EMPTY_VALUE)

    def optional_value(self) -> int | None:
        return None if self.value() == self.EMPTY_VALUE else self.value()

    def set_optional_value(self, value: object) -> None:
        if value in (None, ""):
            self.setValue(self.EMPTY_VALUE)
            if self.hasFocus() or self.lineEdit().hasFocus():
                self.setSpecialValueText("")
                self.lineEdit().clear()
            else:
                self.setSpecialValueText(self.EMPTY_TEXT)
            return
        self.setValue(int(value))

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        empty = self.value() == self.EMPTY_VALUE
        if empty:
            self.setSpecialValueText("")
        super().focusInEvent(event)
        if empty:
            self.lineEdit().clear()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.lineEdit().text().strip():
            self.setValue(self.EMPTY_VALUE)
        super().focusOutEvent(event)
        if self.value() == self.EMPTY_VALUE:
            self.setSpecialValueText(self.EMPTY_TEXT)
