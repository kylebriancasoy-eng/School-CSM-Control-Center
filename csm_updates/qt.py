"""Non-blocking Qt update notification and plain-text release details."""
from __future__ import annotations

from threading import Thread
import sys
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QRectF
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QPen, QColor, QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QMessageBox, QCheckBox
from .release import fetch_latest, launch_setup


class UpdateController(QObject):
    changed = Signal()
    completed = Signal(object)

    def __init__(self, installed: str, parent=None, *, district: bool = False):
        super().__init__(parent)
        self.installed = installed
        self.district = district
        self.info = None
        self.status = "Updates have not been checked yet."
        self.busy = False
        self.completed.connect(self._complete)
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        if getattr(sys, "frozen", False):
            QTimer.singleShot(30000, self.check)
            self.timer.start()

    @property
    def available(self):
        return self.info is not None and self.info.newer_than(self.installed)

    def check(self):
        if self.busy:
            return
        self.busy = True
        self.status = "Checking for updates…"
        self.changed.emit()
        def work():
            try:
                result = fetch_latest(district=self.district)
            except Exception:
                result = None
            try:
                self.completed.emit(result)
            except RuntimeError:
                pass  # The application may have closed during the bounded request.
        try:
            Thread(target=work, daemon=True, name="CSM-update-check").start()
        except RuntimeError:
            self._complete(None)

    def _complete(self, result):
        self.busy = False
        if result is not None:
            self.info = result
            self.status = f"Version {result.version} is available." if self.available else "Your application is up to date."
        else:
            self.status = "Could not check for updates. Your current application keeps working."
        self.changed.emit()


class UpdateDialog(QDialog):
    def __init__(self, controller, install_root, exit_application, parent=None, *, can_update=lambda: True):
        super().__init__(parent)
        self.controller = controller
        self.install_root = install_root
        self.exit_application = exit_application
        self.can_update = can_update
        self.setWindowTitle("Software updates")
        self.setStyleSheet("""
            QDialog { background: #081827; color: #edf6fa; }
            QLabel { color: #edf6fa; }
            QTextEdit { background: #06121e; color: #edf6fa; border: 1px solid #245266;
                        border-radius: 8px; padding: 10px; }
            QPushButton { background: #103247; color: #edf6fa; border: 1px solid #297086;
                          border-radius: 7px; padding: 9px 15px; }
            QPushButton:hover { border-color: #34d4ca; }
            QPushButton:disabled { color: #7c94a5; border-color: #203646; }
        """)
        if controller.district:
            self.setWindowTitle("District software updates")
            self.setStyleSheet("""
                QDialog { background: #f8fbef; color: #173b43; }
                QLabel, QCheckBox { color: #173b43; background: transparent; }
                QTextEdit { background: white; color: #173b43; border: 1px solid #b9e2e3;
                            border-radius: 8px; padding: 10px; }
                QPushButton { background: #08627a; color: white; border: 1px solid #08627a;
                              border-radius: 7px; padding: 9px 15px; }
                QPushButton:hover { background: #087f95; }
                QPushButton:disabled { background: #e2ecec; color: #71858a; border-color: #b9e2e3; }
            """)
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        self.resize(610, 450)
        layout = QVBoxLayout(self)
        self.installed_label = QLabel(f"Installed version: {controller.installed}")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setAccessibleName("Update release details")
        layout.addWidget(self.installed_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.notes)
        note = QLabel("Updating opens the bundled setup wizard. Save your work first; the survey server and Internet Gateway will stop when this app closes. Saved data is preserved.")
        if controller.district:
            note.setText("Create a fresh encrypted district recovery backup before updating. District Setup preserves the roster, configuration and credentials. The district service and portal will stop until you reopen the app; existing school tunnels are not stopped by this action.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.backup_confirmation = QCheckBox("I created a fresh encrypted district recovery backup.")
        self.backup_confirmation.setVisible(controller.district)
        self.backup_confirmation.toggled.connect(self.refresh)
        layout.addWidget(self.backup_confirmation)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check now")
        self.setup_button = QPushButton("Open Setup")
        close = QPushButton("Close")
        for button in (self.check_button, self.setup_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.check_button.clicked.connect(controller.check)
        self.setup_button.clicked.connect(self._open_setup)
        close.clicked.connect(self.reject)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        controller = self.controller
        self.status_label.setText(controller.status)
        self.notes.setPlainText((controller.info.title + "\n\n" + controller.info.notes)
                                if controller.info else "Release details will appear after a successful update check.")
        self.check_button.setEnabled(not controller.busy)
        self.setup_button.setText("Update…" if controller.available else "Open Setup")
        self.setup_button.setEnabled(bool(getattr(sys, "frozen", False)) and
                                     (not controller.district or self.backup_confirmation.isChecked()))

    def _open_setup(self):
        if self.controller.district and not self.backup_confirmation.isChecked():
            QMessageBox.information(self, "District recovery backup required", "Create a fresh encrypted district recovery backup, then tick the confirmation before proceeding.")
            return
        if not self.can_update():
            QMessageBox.information(self, "Operation in progress", "Finish the current gateway or data operation before updating.")
            return
        prompt = ("Open District Setup and close this app? The district service and portal will stop until you reopen it. District records and credentials are preserved.\n\nIn Setup, choose Check for Update. Nothing is installed automatically."
                  if self.controller.district else
                  "Save any unfinished work and create a backup first.\n\nOpen the setup wizard and close this application, stopping its server and Internet Gateway?\n\nIn Setup, choose Check for Update. Nothing is installed automatically.")
        if QMessageBox.question(self, "Open Setup and close the application?", prompt) != QMessageBox.StandardButton.Yes:
            return
        try:
            launch_setup(self.install_root, district=self.controller.district)
        except Exception as error:
            QMessageBox.warning(self, "Setup could not open", str(error))
            return
        self.accept()
        self.exit_application()


def district_bell_icon(available: bool = False) -> QIcon:
    """Font-independent bell; no imports from the school application."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(0.5, 0.5)
    painter.setPen(QPen(QColor("#08627a"), 4, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath()
    path.moveTo(14, 46)
    path.lineTo(19, 38)
    path.lineTo(19, 27)
    path.cubicTo(19, 9, 45, 9, 45, 27)
    path.lineTo(45, 38)
    path.lineTo(50, 46)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawArc(QRectF(26, 46, 12, 10), 180 * 16, 180 * 16)
    if available:
        painter.setBrush(QColor("#efb72d"))
        painter.drawEllipse(QRectF(44, 8, 13, 13))
    painter.end()
    return QIcon(pixmap)
