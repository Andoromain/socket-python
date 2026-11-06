"""Fenêtre d'appel : appel entrant (Répondre, Refuser), puis communication (micro, durée)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..calls import Call, CallManager, CallState
from ..messenger import Messenger
from ..store import format_duration
from .theme import avatar_pixmap

_STATUS = {
    CallState.CALLING: "Appel en cours…",
    CallState.RINGING_OUT: "Ça sonne…",
    CallState.INCOMING: "Appel audio entrant",
    CallState.CONNECTING: "Connexion…",
}

_GREEN = "background:#2bb673; color:white; font-weight:bold; padding:8px 18px; border-radius:6px;"
_RED = "background:#e53935; color:white; font-weight:bold; padding:8px 18px; border-radius:6px;"


class CallWindow(QWidget):
    """Toujours au premier plan pendant un appel ; fermer la fenêtre raccroche."""

    def __init__(
        self,
        calls: CallManager,
        messenger: Messenger,
        ring: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Appel — Socket Chat")
        self.setMinimumWidth(320)
        self._calls = calls
        self._messenger = messenger
        self._ring = ring
        self._ringing = False

        self.avatar = QLabel(self, alignment=Qt.AlignmentFlag.AlignCenter)
        self.name_label = QLabel(self, alignment=Qt.AlignmentFlag.AlignCenter)
        font = QFont(self.name_label.font())
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.name_label.setFont(font)
        self.status_label = QLabel(self, alignment=Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)

        self.answer_button = QPushButton("Répondre", self)
        self.answer_button.setStyleSheet(_GREEN)
        self.answer_button.clicked.connect(calls.accept)
        self.decline_button = QPushButton("Refuser", self)
        self.decline_button.setStyleSheet(_RED)
        self.decline_button.clicked.connect(calls.decline)
        self.mute_button = QPushButton("Couper le micro", self, checkable=True)
        self.mute_button.toggled.connect(self._on_mute)
        self.hangup_button = QPushButton("Raccrocher", self)
        self.hangup_button.setStyleSheet(_RED)
        self.hangup_button.clicked.connect(calls.hangup)

        buttons = QHBoxLayout()
        for button in (
            self.answer_button,
            self.decline_button,
            self.mute_button,
            self.hangup_button,
        ):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(self.avatar)
        layout.addWidget(self.name_label)
        layout.addWidget(self.status_label)
        layout.addSpacing(10)
        layout.addLayout(buttons)

        self._clock = QTimer(self, interval=1000)
        self._clock.timeout.connect(self._refresh_status)
        calls.state_changed.connect(self.update_call)

    # -- Mise à jour ---------------------------------------------------------
    def update_call(self, call: Call | None) -> None:
        if call is None:
            self._set_ringing(False)
            self._clock.stop()
            self.mute_button.setChecked(False)
            self.hide()
            return
        contact = self._messenger.store.contact(call.contact_id)
        name = contact.name if contact else "Correspondant"
        self.name_label.setText(name)
        self.avatar.setPixmap(
            avatar_pixmap(
                name,
                call.contact_id,
                96,
                self.devicePixelRatioF(),
                photo=contact.avatar if contact else b"",
            )
        )
        incoming = call.state is CallState.INCOMING
        self.answer_button.setVisible(incoming)
        self.decline_button.setVisible(incoming)
        self.mute_button.setVisible(not incoming)
        self.hangup_button.setVisible(not incoming)
        self._set_ringing(incoming)
        if call.state is CallState.ACTIVE:
            self._clock.start()
        self._refresh_status()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()

    def _refresh_status(self) -> None:
        call = self._calls.call
        if call is None:
            return
        if call.state is CallState.ACTIVE:
            text = format_duration(call.duration)
            if call.muted:
                text += " · micro coupé"
        else:
            text = _STATUS.get(call.state, "")
        self.status_label.setText(text)

    def _on_mute(self, muted: bool) -> None:
        self.mute_button.setText("Rétablir le micro" if muted else "Couper le micro")
        if self._calls.call is not None and self._calls.call.muted != muted:
            self._calls.set_muted(muted)
        self._refresh_status()

    def _set_ringing(self, on: bool) -> None:
        if on != self._ringing and self._ring is not None:
            self._ring(on)
        self._ringing = on

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        if self._calls.call is not None:
            self._calls.hangup()
        super().closeEvent(event)
