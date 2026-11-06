"""Média des appels sur PC : WebRTC dans une page Qt WebEngine invisible.

Le moteur de Chrome apporte ce qu'il faut à un appel de qualité : annulation d'écho,
réduction de bruit, contrôle automatique du gain, adaptation au réseau, codec Opus,
compatible avec l'application Android (flutter_webrtc). La page ``resources/call.html``
contient le code WebRTC ; Python la pilote par QWebChannel.

Une page par :class:`CallHost` (un par fenêtre) : créée à la première utilisation puis
réutilisée, elle joue aussi la sonnerie. Le micro n'est autorisé que pour cette page.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
    QWebEngineSettings,
)

from .calls import MediaEngine

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("resources") / "call.html"
#: Origine de la page : seule à pouvoir ouvrir le micro.
PAGE_URL = QUrl("qrc:/socket-chat/call/")
_CAPTURE = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
}


class _Bridge(QObject):
    """Objet partagé avec la page (nom JavaScript : ``engine``)."""

    # Python → page
    startRequested = Signal(bool, str)  # noqa: N815 - nom vu depuis JavaScript
    remoteDescriptionReceived = Signal(str, str)  # noqa: N815
    remoteIceReceived = Signal(str, str, int)  # noqa: N815
    muteRequested = Signal(bool)  # noqa: N815
    stopRequested = Signal()  # noqa: N815
    ringRequested = Signal(bool)  # noqa: N815
    statsRequested = Signal()  # noqa: N815
    # page → Python (relayés par CallHost)
    page_ready = Signal()
    local_description = Signal(str, str)
    local_ice = Signal(str, str, int)
    media_connected = Signal()
    media_failed = Signal(str)
    inbound_bytes = Signal(int)

    @Slot()
    def ready(self) -> None:
        self.page_ready.emit()

    @Slot(str, str)
    def localDescription(self, kind: str, sdp: str) -> None:  # noqa: N802
        self.local_description.emit(kind, sdp)

    @Slot(str, str, int)
    def localIce(self, candidate: str, mid: str, index: int) -> None:  # noqa: N802
        self.local_ice.emit(candidate, mid, index)

    @Slot()
    def connected(self) -> None:
        self.media_connected.emit()

    @Slot(str)
    def failed(self, message: str) -> None:
        self.media_failed.emit(message)

    @Slot(int)
    def stats(self, received: int) -> None:
        self.inbound_bytes.emit(received)


class CallHost(QObject):
    """Page WebEngine invisible des appels (créée à la demande, puis réutilisée)."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.bridge = _Bridge(self)
        self._page: QWebEnginePage | None = None
        self._ready = False
        self._queue: list[tuple[Signal, tuple]] = []
        self.bridge.page_ready.connect(self._on_ready)

    @property
    def ready(self) -> bool:
        return self._ready

    def ensure_page(self) -> None:
        if self._page is not None:
            return
        profile = QWebEngineProfile()  # sans nom : rien n'est écrit sur le disque
        page = QWebEnginePage(profile, self)
        profile.setParent(page)  # détruit après la page, comme l'exige WebEngine
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False
        )
        page.permissionRequested.connect(self._on_permission)
        page.javaScriptConsoleMessage = self._console  # type: ignore[method-assign]
        channel = QWebChannel(page)
        channel.registerObject("engine", self.bridge)
        page.setWebChannel(channel)
        page.setHtml(PAGE.read_text(encoding="utf-8"), PAGE_URL)
        self._page = page

    def send(self, signal_name: str, *args: object) -> None:
        """Transmet une commande à la page (mise en attente tant qu'elle se charge)."""
        signal = getattr(self.bridge, signal_name)
        if self._ready:
            signal.emit(*args)
        else:
            self._queue.append((signal, args))
            self.ensure_page()

    def ring(self, on: bool) -> None:
        if on or self._page is not None:
            self.send("ringRequested", on)

    def _on_ready(self) -> None:
        self._ready = True
        queue, self._queue = self._queue, []
        for signal, args in queue:
            signal.emit(*args)

    @staticmethod
    def _on_permission(permission: QWebEnginePermission) -> None:
        if permission.permissionType() in _CAPTURE and permission.origin().scheme() == "qrc":
            permission.grant()
        else:
            permission.deny()

    @staticmethod
    def _console(_level: object, message: str, line: int, _source: str) -> None:
        log.debug("call.html:%d %s", line, message)


class WebEngineCallEngine(MediaEngine):
    """Moteur d'un appel, adossé à la page du :class:`CallHost`."""

    def __init__(self, host: CallHost):
        super().__init__()
        self._host = host
        self._active = True
        bridge = host.bridge
        bridge.local_description.connect(self._relay_description)
        bridge.local_ice.connect(self._relay_ice)
        bridge.media_connected.connect(self._relay_connected)
        bridge.media_failed.connect(self._relay_failed)

    def start(self, caller: bool, media: str) -> None:
        self._host.ring(False)
        self._host.send("startRequested", caller, media)

    def set_remote_description(self, kind: str, sdp: str) -> None:
        self._host.send("remoteDescriptionReceived", kind, sdp)

    def add_remote_ice(self, candidate: str, mid: str, index: int) -> None:
        self._host.send("remoteIceReceived", candidate, mid, index)

    def set_muted(self, muted: bool) -> None:
        self._host.send("muteRequested", muted)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        bridge = self._host.bridge
        for signal, slot in (
            (bridge.local_description, self._relay_description),
            (bridge.local_ice, self._relay_ice),
            (bridge.media_connected, self._relay_connected),
            (bridge.media_failed, self._relay_failed),
        ):
            signal.disconnect(slot)
        self._host.send("stopRequested")

    def _relay_description(self, kind: str, sdp: str) -> None:
        self.local_description.emit(kind, sdp)

    def _relay_ice(self, candidate: str, mid: str, index: int) -> None:
        self.local_ice.emit(candidate, mid, index)

    def _relay_connected(self) -> None:
        self.connected.emit()

    def _relay_failed(self, message: str) -> None:
        self.failed.emit(message)
