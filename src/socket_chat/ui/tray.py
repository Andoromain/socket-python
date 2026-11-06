"""Icône dans la zone de notification : notifications et compteur de non-lus."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .main_window import APP_TITLE

PREVIEW_LENGTH = 120


def icon_with_badge(base: QIcon, count: int, size: int = 64) -> QIcon:
    """Icône de l'application avec une pastille rouge indiquant les non-lus."""
    if count <= 0:
        return base
    pixmap = base.pixmap(size, size)
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    diameter = size * 0.52
    rect = pixmap.rect().adjusted(int(size - diameter), 0, 0, -int(size - diameter))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#e53935"))
    painter.drawEllipse(rect)
    font = QFont(painter.font())
    font.setBold(True)
    font.setPixelSize(int(diameter * 0.6))
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(count) if count < 10 else "9+")
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    open_requested = Signal()
    conversation_requested = Signal(str)
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None):
        super().__init__(parent)
        self._icon = icon
        self._last_contact: str | None = None
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_TITLE)

        menu = QMenu()
        open_action = QAction("Ouvrir Socket Chat", menu)
        open_action.triggered.connect(self.open_requested)
        quit_action = QAction("Quitter", menu)
        quit_action.triggered.connect(self.quit_requested)
        menu.addAction(open_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu
        self.tray.setContextMenu(menu)

        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self._on_message_clicked)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def set_unread(self, count: int) -> None:
        self.tray.setIcon(icon_with_badge(self._icon, count))
        if count:
            plural = "s" if count > 1 else ""
            self.tray.setToolTip(f"{APP_TITLE} — {count} message{plural} non lu{plural}")
        else:
            self.tray.setToolTip(APP_TITLE)

    def notify(self, contact_id: str, name: str, text: str) -> None:
        if len(text) > PREVIEW_LENGTH:
            text = text[: PREVIEW_LENGTH - 1] + "…"
        self._last_contact = contact_id
        self.tray.showMessage(name, text, self._icon, 6000)

    def info(self, title: str, text: str) -> None:
        self._last_contact = None
        self.tray.showMessage(title, text, self._icon, 6000)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()

    def _on_message_clicked(self) -> None:
        if self._last_contact:
            self.conversation_requested.emit(self._last_contact)
        else:
            self.open_requested.emit()
