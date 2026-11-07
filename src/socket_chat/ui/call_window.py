"""Fenêtre d'appel : appel entrant (Répondre, Refuser), puis communication (micro, caméra,
durée ; image du correspondant pour un appel vidéo)."""

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
    CallState.CONNECTING: "Connexion…",
}

#: Crée la vue de l'image des appels vidéo (page WebRTC), ou None si indisponible.
VideoViewFactory = Callable[[QWidget], QWidget]

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
        video_view: VideoViewFactory | None = None,
    ):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Appel — Socket Chat")
        self.setMinimumWidth(320)
        self._calls = calls
        self._messenger = messenger
        self._ring = ring
        self._ringing = False
        self._video_view_factory = video_view
        self.video_view: QWidget | None = None
        self._video_shown = False

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
        self.camera_button = QPushButton("Couper la caméra", self, checkable=True)
        self.camera_button.toggled.connect(self._on_camera)
        self.hangup_button = QPushButton("Raccrocher", self)
        self.hangup_button.setStyleSheet(_RED)
        self.hangup_button.clicked.connect(calls.hangup)

        buttons = QHBoxLayout()
        for button in (
            self.answer_button,
            self.decline_button,
            self.mute_button,
            self.camera_button,
            self.hangup_button,
        ):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        self._layout = layout
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
            self.camera_button.setChecked(False)
            self._show_video(False)
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
        self.camera_button.setVisible(call.video and not incoming)
        self.hangup_button.setVisible(not incoming)
        self.setWindowTitle(("Appel vidéo" if call.video else "Appel") + " — Socket Chat")
        # Appel vidéo décroché : l'image remplace la photo du correspondant.
        self._show_video(call.video and call.state in (CallState.CONNECTING, CallState.ACTIVE))
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
            if call.camera_off:
                text += " · caméra coupée"
        elif call.state is CallState.INCOMING:
            text = "Appel vidéo entrant" if call.video else "Appel audio entrant"
        else:
            text = _STATUS.get(call.state, "")
        self.status_label.setText(text)

    def _on_mute(self, muted: bool) -> None:
        self.mute_button.setText("Rétablir le micro" if muted else "Couper le micro")
        if self._calls.call is not None and self._calls.call.muted != muted:
            self._calls.set_muted(muted)
        self._refresh_status()

    def _on_camera(self, off: bool) -> None:
        self.camera_button.setText("Rétablir la caméra" if off else "Couper la caméra")
        if self._calls.call is not None and self._calls.call.camera_off != off:
            self._calls.set_camera_enabled(not off)
        self._refresh_status()

    def _show_video(self, on: bool) -> None:
        if on == self._video_shown:
            return
        if on and self.video_view is None and self._video_view_factory is not None:
            self.video_view = self._video_view_factory(self)
            self.video_view.setMinimumSize(480, 300)
            self._layout.insertWidget(0, self.video_view, 1)
        self._video_shown = on
        if self.video_view is not None:
            self.video_view.setVisible(on)
        self.avatar.setVisible(not on)
        if on:
            self.resize(max(self.width(), 760), max(self.height(), 600))
        else:
            self.resize(self.minimumSizeHint())

    def _set_ringing(self, on: bool) -> None:
        if on != self._ringing and self._ring is not None:
            self._ring(on)
        self._ringing = on

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        if self._calls.call is not None:
            self._calls.hangup()
        super().closeEvent(event)
