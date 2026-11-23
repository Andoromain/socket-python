"""Appels audio (et vidéo) entre deux postes : signalisation et déroulement.

Le média circule directement entre les deux appareils (WebRTC, voir ``call_engine.py``
sur PC) ; ce module ne fait que la signalisation, par la connexion déjà établie avec le
contact :

1. l'appelant envoie ``call_invite`` ; l'appelé répond ``call_ringing`` et sonne ;
2. s'il décroche (``call_accept``), l'appelant crée l'offre WebRTC (``call_sdp`` offer),
   l'appelé répond (``call_sdp`` answer) et les candidats réseau s'échangent
   (``call_ice``) jusqu'à la connexion ;
3. l'un ou l'autre raccroche (``call_end``).

Un contact en version antérieure ignore l'invitation : sans ``call_ringing`` au bout de
quelques secondes, l'appel est abandonné avec un message explicite. Chaque appel est noté
dans la conversation (durée, manqué, refusé…), localement.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from .messenger import Messenger
from .protocol import Message, MessageType, new_id
from .store import CallInfo, CallOutcome

log = logging.getLogger(__name__)

#: Délais (ms) : accusé de sonnerie, sonnerie, établissement de la liaison média.
RINGING_ACK_TIMEOUT_MS = 6_000
RING_TIMEOUT_MS = 30_000
CONNECT_TIMEOUT_MS = 20_000

#: Motifs de fin (champ ``reason`` de ``call_end``).
HANGUP, DECLINED, BUSY, TIMEOUT, FAILED = "hangup", "declined", "busy", "timeout", "failed"


class CallState(Enum):
    CALLING = "calling"  # invitation envoyée, pas encore de sonnerie chez le correspondant
    RINGING_OUT = "ringing_out"  # le correspondant sonne
    INCOMING = "incoming"  # nous sonnons
    CONNECTING = "connecting"  # décroché, liaison média en cours d'établissement
    ACTIVE = "active"  # en communication


class MediaEngine(QObject):
    """Moteur média d'un appel (WebRTC). Une instance par appel."""

    local_description = Signal(str, str)  # type (offer/answer), SDP
    local_ice = Signal(str, str, int)  # candidat, mid, index
    connected = Signal()
    failed = Signal(str)  # motif lisible

    def start(self, caller: bool, media: str) -> None:
        """Ouvre le micro ; l'appelant produit ensuite son offre (``local_description``)."""
        raise NotImplementedError

    def set_remote_description(self, kind: str, sdp: str) -> None:
        raise NotImplementedError

    def add_remote_ice(self, candidate: str, mid: str, index: int) -> None:
        raise NotImplementedError

    def set_muted(self, muted: bool) -> None:
        raise NotImplementedError

    def set_camera_enabled(self, on: bool) -> None:
        """Appel vidéo : coupe ou rétablit la caméra (image noire chez le correspondant)."""

    def stop(self) -> None:
        raise NotImplementedError


@dataclass
class Call:
    id: str
    contact_id: str
    media: str
    outgoing: bool
    state: CallState
    started: float | None = None  # début de la conversation (time.monotonic)
    muted: bool = False
    camera_off: bool = False  # appel vidéo : caméra coupée
    engine: MediaEngine | None = field(default=None, repr=False)

    @property
    def video(self) -> bool:
        return self.media == "video"

    @property
    def duration(self) -> int:
        return int(time.monotonic() - self.started) if self.started is not None else 0


class CallManager(QObject):
    """Un appel à la fois : signalisation, délais, historique."""

    state_changed = Signal(object)  # Call en cours, ou None une fois terminé
    incoming_call = Signal(str, str)  # contact, média
    call_ended = Signal(str, str)  # contact, CallOutcome
    error = Signal(str)

    def __init__(
        self,
        messenger: Messenger,
        engine_factory: Callable[[], MediaEngine],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._messenger = messenger
        self._engine_factory = engine_factory
        self.call: Call | None = None
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self._on_timeout)
        messenger.call_frame.connect(self._on_frame)
        messenger.peer_disconnected.connect(self._on_disconnected)

    # -- Actions locales ---------------------------------------------------
    def start_call(self, contact_id: str, media: str = "audio") -> Call:
        if self.call is not None:
            raise ValueError("Un appel est déjà en cours.")
        if not self._messenger.is_connected(contact_id):
            contact = self._messenger.store.contact(contact_id)
            name = contact.name if contact else "Ce contact"
            raise ValueError(f"{name} n'est pas en ligne : appel impossible.")
        call = Call(new_id(), contact_id, media, True, CallState.CALLING)
        self.call = call
        self._messenger.send_to(contact_id, Message.call_invite(call.id, media))
        self._arm(RINGING_ACK_TIMEOUT_MS)
        self.state_changed.emit(call)
        return call

    def accept(self) -> None:
        call = self.call
        if call is None or call.state is not CallState.INCOMING:
            return
        if not self._messenger.send_to(call.contact_id, Message.call_accept(call.id)):
            self._finish(CallOutcome.FAILED)
            return
        self._set_state(CallState.CONNECTING)
        self._start_engine(caller=False)
        self._arm(CONNECT_TIMEOUT_MS)

    def decline(self) -> None:
        call = self.call
        if call is None or call.state is not CallState.INCOMING:
            return
        self._messenger.send_to(call.contact_id, Message.call_end(call.id, DECLINED))
        self._finish(CallOutcome.DECLINED)

    def hangup(self) -> None:
        call = self.call
        if call is None:
            return
        if call.state is CallState.INCOMING:
            self.decline()
            return
        self._messenger.send_to(call.contact_id, Message.call_end(call.id, HANGUP))
        if call.state is CallState.ACTIVE:
            self._finish(CallOutcome.COMPLETED)
        else:
            self._finish(CallOutcome.CANCELLED)

    def set_muted(self, muted: bool) -> None:
        call = self.call
        if call is None:
            return
        call.muted = muted
        if call.engine is not None:
            call.engine.set_muted(muted)
        self.state_changed.emit(call)

    def set_camera_enabled(self, on: bool) -> None:
        call = self.call
        if call is None or not call.video:
            return
        call.camera_off = not on
        if call.engine is not None:
            call.engine.set_camera_enabled(on)
        self.state_changed.emit(call)

    def stop(self) -> None:
        """Arrêt de l'application : raccroche proprement."""
        self.hangup()

    # -- Trames reçues -----------------------------------------------------
    def _on_frame(self, peer_id: str, message: Message) -> None:
        if message.type is MessageType.CALL_INVITE:
            self._on_invite(peer_id, message)
            return
        call = self.call
        if call is None or call.id != message.id or call.contact_id != peer_id:
            return  # appel inconnu ou terminé
        match message.type:
            case MessageType.CALL_RINGING if call.state is CallState.CALLING:
                self._set_state(CallState.RINGING_OUT)
                self._arm(RING_TIMEOUT_MS + 5_000)  # l'appelé abandonne le premier
            case MessageType.CALL_ACCEPT if call.outgoing and call.state in (
                CallState.CALLING,
                CallState.RINGING_OUT,
            ):
                self._set_state(CallState.CONNECTING)
                self._start_engine(caller=True)
                self._arm(CONNECT_TIMEOUT_MS)
            case MessageType.CALL_SDP if call.engine is not None:
                call.engine.set_remote_description(message.sdp_kind, message.sdp)
            case MessageType.CALL_ICE if call.engine is not None:
                if message.candidate:
                    call.engine.add_remote_ice(message.candidate, message.mid, message.index)
            case MessageType.CALL_END:
                self._finish(self._remote_outcome(call, message.reason))

    def _on_invite(self, peer_id: str, message: Message) -> None:
        current = self.call
        if current is not None:
            glare = (
                current.outgoing
                and current.contact_id == peer_id
                and current.state in (CallState.CALLING, CallState.RINGING_OUT)
            )
            if glare and peer_id < self._messenger.user_id:
                # Appels croisés : on garde celui de l'identifiant le plus petit (le sien).
                self._messenger.send_to(peer_id, Message.call_end(current.id, HANGUP))
                self._timer.stop()
                self.call = None
            else:
                self._messenger.send_to(peer_id, Message.call_end(message.id, BUSY))
                return
        call = Call(message.id, peer_id, message.media, False, CallState.INCOMING)
        self.call = call
        self._messenger.send_to(peer_id, Message.call_ringing(call.id))
        self._arm(RING_TIMEOUT_MS)
        self.state_changed.emit(call)
        self.incoming_call.emit(peer_id, message.media)

    def _on_disconnected(self, contact_id: str) -> None:
        call = self.call
        if call is not None and call.contact_id == contact_id:
            if call.state is CallState.INCOMING:
                self._finish(CallOutcome.MISSED)
            elif call.state is CallState.ACTIVE:
                self._finish(CallOutcome.COMPLETED)
            else:
                self._finish(CallOutcome.FAILED)

    # -- Moteur média ------------------------------------------------------
    def _start_engine(self, caller: bool) -> None:
        call = self.call
        assert call is not None
        engine = self._engine_factory()
        call.engine = engine
        call_id, contact_id = call.id, call.contact_id
        engine.local_description.connect(
            lambda kind, sdp: self._send_if_current(
                call_id, contact_id, Message.call_sdp(call_id, kind, sdp)
            )
        )
        engine.local_ice.connect(
            lambda cand, mid, idx: self._send_if_current(
                call_id, contact_id, Message.call_ice(call_id, cand, mid, idx)
            )
        )
        engine.connected.connect(lambda: self._on_media_connected(call_id))
        engine.failed.connect(lambda reason: self._on_media_failed(call_id, reason))
        engine.start(caller, call.media)
        if call.muted:
            engine.set_muted(True)
        if call.camera_off:
            engine.set_camera_enabled(False)

    def _send_if_current(self, call_id: str, contact_id: str, message: Message) -> None:
        if self.call is not None and self.call.id == call_id:
            self._messenger.send_to(contact_id, message)

    def _on_media_connected(self, call_id: str) -> None:
        call = self.call
        if call is None or call.id != call_id or call.state is CallState.ACTIVE:
            return
        self._timer.stop()
        call.started = time.monotonic()
        self._set_state(CallState.ACTIVE)

    def _on_media_failed(self, call_id: str, reason: str) -> None:
        call = self.call
        if call is None or call.id != call_id:
            return
        log.warning("Appel interrompu : %s", reason)
        self._messenger.send_to(call.contact_id, Message.call_end(call.id, FAILED))
        self.error.emit(reason)
        outcome = CallOutcome.COMPLETED if call.state is CallState.ACTIVE else CallOutcome.FAILED
        self._finish(outcome)

    # -- Fin d'appel ---------------------------------------------------------
    @staticmethod
    def _remote_outcome(call: Call, reason: str) -> CallOutcome:
        if call.state is CallState.ACTIVE:
            return CallOutcome.COMPLETED  # conversation terminée, quel qu'en soit le motif
        if call.state is CallState.CONNECTING:
            return CallOutcome.CANCELLED if reason == HANGUP else CallOutcome.FAILED
        if call.outgoing:
            return {
                DECLINED: CallOutcome.DECLINED,
                BUSY: CallOutcome.BUSY,
                TIMEOUT: CallOutcome.NO_ANSWER,
            }.get(reason, CallOutcome.FAILED)
        if call.state is CallState.INCOMING:
            return CallOutcome.MISSED  # l'appelant a raccroché avant la réponse
        return CallOutcome.FAILED

    def _on_timeout(self) -> None:
        call = self.call
        if call is None:
            return
        if call.state is CallState.CALLING:
            # Aucun accusé de sonnerie : version trop ancienne chez le correspondant.
            self._messenger.send_to(call.contact_id, Message.call_end(call.id, HANGUP))
            self._finish(CallOutcome.UNSUPPORTED)
        elif call.state is CallState.RINGING_OUT:
            self._messenger.send_to(call.contact_id, Message.call_end(call.id, TIMEOUT))
            self._finish(CallOutcome.NO_ANSWER)
        elif call.state is CallState.INCOMING:
            self._messenger.send_to(call.contact_id, Message.call_end(call.id, TIMEOUT))
            self._finish(CallOutcome.MISSED)
        elif call.state is CallState.CONNECTING:
            self._messenger.send_to(call.contact_id, Message.call_end(call.id, FAILED))
            self.error.emit("La liaison audio n'a pas pu s'établir (pare-feu ou réseau).")
            self._finish(CallOutcome.FAILED)

    def _finish(self, outcome: CallOutcome) -> None:
        call = self.call
        if call is None:
            return
        self.call = None
        self._timer.stop()
        if call.engine is not None:
            call.engine.stop()
            call.engine.deleteLater()
            call.engine = None
        duration = call.duration if outcome is CallOutcome.COMPLETED else 0
        self._messenger.add_call_record(
            call.contact_id, call.outgoing, CallInfo(call.media, outcome, duration)
        )
        self.state_changed.emit(None)
        self.call_ended.emit(call.contact_id, outcome.value)

    def _set_state(self, state: CallState) -> None:
        assert self.call is not None
        self.call.state = state
        self.state_changed.emit(self.call)

    def _arm(self, ms: int) -> None:
        self._timer.start(ms)
