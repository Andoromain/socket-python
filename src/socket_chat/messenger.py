"""Service de messagerie : écoute, connexions par contact, file d'envoi, accusés.

Chaque poste écoute en permanence et peut converser avec plusieurs contacts.
Une seule connexion TCP est conservée par contact ; elle est ouverte à la
demande (envoi d'un message, contact détecté avec des messages en attente).
Les messages écrits hors ligne sont conservés et transmis au retour du contact.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from .discovery import Discovery
from .network import PeerConnection
from .protocol import (
    MAX_TEXT_LENGTH,
    AckStatus,
    Message,
    MessageType,
    ProtocolError,
    new_id,
    validate_name,
)
from .store import MessageRecord, Status, Store, now_ms

log = logging.getLogger(__name__)

#: Intervalle minimal entre deux indications « écrit… » envoyées (s).
TYPING_THROTTLE_S = 3.0
#: Durée d'affichage de « écrit… » sans nouvelle indication (ms).
TYPING_TIMEOUT_MS = 6_000
#: Intervalle des nouvelles tentatives d'envoi des messages en attente (ms).
RETRY_INTERVAL_MS = 30_000

DUPLICATE_REASON = "duplicate"

_ACK_TO_STATUS = {AckStatus.DELIVERED: Status.DELIVERED, AckStatus.READ: Status.READ}


class Messenger(QObject):
    contacts_changed = Signal()  # liste, aperçus ou compteurs à rafraîchir
    message_added = Signal(object)  # MessageRecord
    message_status_changed = Signal(str, str)  # identifiant du message, Status
    typing_changed = Signal(str, bool)  # contact, en train d'écrire
    presence_changed = Signal(str, bool)  # contact, en ligne
    error = Signal(str)

    def __init__(self, store: Store, user_id: str, name: str, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.user_id = user_id
        self._name = validate_name(name)
        self._server: QTcpServer | None = None
        self._connections: dict[str, PeerConnection] = {}  # contacts identifiés
        self._pending: set[PeerConnection] = set()  # présentation pas encore reçue
        self._dialing: dict[str, PeerConnection] = {}  # connexions sortantes par contact
        self._manual: set[PeerConnection] = set()  # ajouts par adresse (erreurs affichées)
        self._discovered: set[str] = set()
        self._typing_timers: dict[str, QTimer] = {}
        self._typing: set[str] = set()  # contacts en train d'écrire
        self._typing_sent: dict[str, tuple[bool, float]] = {}

        self._retry_timer = QTimer(self, interval=RETRY_INTERVAL_MS)
        self._retry_timer.timeout.connect(self._retry_undelivered)

    # -- Propriétés ------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = validate_name(value)

    @property
    def port(self) -> int | None:
        return self._server.serverPort() if self._server else None

    @property
    def running(self) -> bool:
        return self._server is not None

    def is_online(self, contact_id: str) -> bool:
        return contact_id in self._connections or contact_id in self._discovered

    def is_typing(self, contact_id: str) -> bool:
        return contact_id in self._typing

    # -- Cycle de vie ----------------------------------------------------
    def start(self, port: int) -> bool:
        """Écoute les connexions entrantes (``port`` 0 : choisi par le système)."""
        if self._server is not None:
            return True
        server = QTcpServer(self)
        if not server.listen(QHostAddress(QHostAddress.SpecialAddress.Any), port):
            message = (
                f"Impossible d'écouter sur le port {port} : {server.errorString()}\n"
                "Socket Chat est peut-être déjà ouvert sur ce poste, ou le port est "
                "utilisé par un autre programme (voir Préférences)."
            )
            server.deleteLater()
            self.error.emit(message)
            return False
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        self._retry_timer.start()
        log.info("En écoute sur le port %d", server.serverPort())
        return True

    def stop(self) -> None:
        """Ferme toutes les connexions et arrête l'écoute."""
        self._retry_timer.stop()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        connections = [*self._connections.values(), *self._pending, *self._dialing.values()]
        self._connections.clear()
        self._pending.clear()
        self._dialing.clear()
        self._manual.clear()
        for conn in set(connections):
            conn.closed.connect(conn.deleteLater)
            conn.close()
        for contact_id in list(self._typing):
            self._set_peer_typing(contact_id, False)
        online = list(self._discovered)
        self._discovered.clear()
        for contact_id in online:
            self.presence_changed.emit(contact_id, False)

    # -- Actions ---------------------------------------------------------
    def send(self, contact_id: str, text: str) -> MessageRecord:
        """Envoie (ou met en attente) un message ; il est enregistré immédiatement."""
        text = text.strip()
        if not text:
            raise ValueError("Message vide.")
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"Un message ne peut pas dépasser {MAX_TEXT_LENGTH} caractères.")
        if self.store.contact(contact_id) is None:
            raise ValueError("Contact inconnu.")
        record = MessageRecord(new_id(), contact_id, True, text, now_ms(), Status.PENDING)
        self.store.add_message(record)
        self.message_added.emit(record)
        self.contacts_changed.emit()
        self._typing_sent.pop(contact_id, None)
        conn = self._connections.get(contact_id)
        if conn is not None:
            self._transmit(conn, record)
        else:
            self.connect_to(contact_id)
        return record

    def mark_read(self, contact_id: str) -> None:
        """Marque la conversation comme lue et prévient l'expéditeur."""
        ids = self.store.mark_incoming_read(contact_id)
        if not ids:
            return
        conn = self._connections.get(contact_id)
        if conn is not None:
            for message_id in ids:
                conn.send(Message.ack(message_id, AckStatus.READ))
        for message_id in ids:
            self.message_status_changed.emit(message_id, Status.READ.value)
        self.contacts_changed.emit()

    def set_typing(self, contact_id: str, active: bool) -> None:
        """Signale au contact que l'on écrit (envoi limité dans le temps)."""
        conn = self._connections.get(contact_id)
        if conn is None:
            return
        last_state, last_time = self._typing_sent.get(contact_id, (False, 0.0))
        now = time.monotonic()
        if active == last_state and (not active or now - last_time < TYPING_THROTTLE_S):
            return
        self._typing_sent[contact_id] = (active, now)
        conn.send(Message.typing(active))

    def add_contact(self, host: str, port: int) -> None:
        """Se connecte à une adresse ; le contact est créé à sa présentation."""
        conn = self._dial(host, port)
        self._manual.add(conn)

    def connect_to(self, contact_id: str) -> None:
        """Ouvre la connexion vers un contact connu, si ce n'est pas déjà fait."""
        if contact_id in self._connections or contact_id in self._dialing or not self.running:
            return
        contact = self.store.contact(contact_id)
        if contact is None or not contact.host or not contact.port:
            return
        self._dialing[contact_id] = self._dial(contact.host, contact.port)

    # -- Détection (reliée à Discovery) ----------------------------------
    def attach_discovery(self, discovery: Discovery) -> None:
        discovery.peer_seen.connect(self.on_peer_seen)
        discovery.peer_lost.connect(self.on_peer_lost)

    def on_peer_seen(self, contact_id: str, name: str, host: str, port: int) -> None:
        if contact_id == self.user_id:
            return
        self.store.upsert_contact(contact_id, name, host=host, port=port, last_seen=now_ms())
        if contact_id not in self._discovered:
            was_online = self.is_online(contact_id)
            self._discovered.add(contact_id)
            if not was_online:
                self.presence_changed.emit(contact_id, True)
            self.contacts_changed.emit()
            if self.store.undelivered_outgoing(contact_id):
                self.connect_to(contact_id)

    def on_peer_lost(self, contact_id: str) -> None:
        if contact_id in self._discovered:
            self._discovered.discard(contact_id)
            if not self.is_online(contact_id):
                self._set_peer_typing(contact_id, False)
                self.presence_changed.emit(contact_id, False)
            self.contacts_changed.emit()

    # -- Connexions ------------------------------------------------------
    def _hello(self) -> Message:
        return Message.hello(self.user_id, self._name, self.port or 0)

    def _dial(self, host: str, port: int) -> PeerConnection:
        conn = PeerConnection(QTcpSocket(), self._hello(), outgoing=True, parent=self)
        self._watch(conn)
        log.info("Connexion à %s:%d", host, port)
        conn.connect_to_host(host, port)
        return conn

    def _on_new_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            log.info("Connexion entrante de %s", socket.peerAddress().toString())
            self._watch(PeerConnection(socket, self._hello(), outgoing=False, parent=self))

    def _watch(self, conn: PeerConnection) -> None:
        self._pending.add(conn)
        conn.ready.connect(lambda pid, name, port, c=conn: self._on_ready(c, pid, name, port))
        conn.message_received.connect(lambda msg, c=conn: self._on_message(c, msg))
        conn.closed.connect(lambda reason, c=conn: self._on_closed(c, reason))

    def _on_ready(self, conn: PeerConnection, peer_id: str, name: str, port: int) -> None:
        self._pending.discard(conn)
        self._manual.discard(conn)
        if self._dialing.get(peer_id) is conn:
            del self._dialing[peer_id]
        if peer_id == self.user_id:
            log.info("Connexion à soi-même ignorée")
            conn.close()
            return

        existing = self._connections.get(peer_id)
        if existing is not None and existing is not conn:
            # Connexions croisées : les deux postes gardent celle ouverte par le plus
            # petit identifiant, pour converger vers la même connexion.
            keeper_initiator = min(self.user_id, peer_id)
            initiator = self.user_id if conn.outgoing else peer_id
            if initiator != keeper_initiator:
                conn.close(DUPLICATE_REASON)
                return
            existing_conn, self._connections[peer_id] = existing, conn
            existing_conn.close(DUPLICATE_REASON)
        else:
            self._connections[peer_id] = conn

        was_known = self.store.contact(peer_id) is not None
        host = conn.peer_address.removeprefix("::ffff:")
        self.store.upsert_contact(peer_id, name, host=host, port=port, last_seen=now_ms())
        if existing is None and peer_id not in self._discovered:
            self.presence_changed.emit(peer_id, True)
        if not was_known:
            log.info("Nouveau contact : %s", name)
        self.contacts_changed.emit()
        for record in self.store.undelivered_outgoing(peer_id):
            self._transmit(conn, record)

    def _on_message(self, conn: PeerConnection, message: Message) -> None:
        peer_id = conn.peer_id
        if peer_id is None or self._connections.get(peer_id) is not conn:
            return
        match message.type:
            case MessageType.CHAT:
                self._receive_chat(conn, peer_id, message)
            case MessageType.ACK:
                record = self.store.message(message.id)
                if record is None or not record.outgoing or record.contact_id != peer_id:
                    return
                assert message.status is not None
                status = _ACK_TO_STATUS[message.status]
                if self.store.update_status(message.id, status):
                    self.message_status_changed.emit(message.id, status.value)
                    self.contacts_changed.emit()
            case MessageType.TYPING:
                self._set_peer_typing(peer_id, message.active)

    def _receive_chat(self, conn: PeerConnection, peer_id: str, message: Message) -> None:
        record = MessageRecord(message.id, peer_id, False, message.text, now_ms(), Status.DELIVERED)
        try:
            inserted = self.store.add_message(record)
        except Exception:
            log.exception("Impossible d'enregistrer le message reçu")
            return
        conn.send(Message.ack(message.id, AckStatus.DELIVERED))
        self._set_peer_typing(peer_id, False)
        if inserted:
            self.message_added.emit(record)
            self.contacts_changed.emit()
        else:
            # Retransmission d'un message déjà reçu : renvoyer son état réel.
            stored = self.store.message(message.id)
            if stored is not None and stored.status is Status.READ:
                conn.send(Message.ack(message.id, AckStatus.READ))

    def _on_closed(self, conn: PeerConnection, reason: str) -> None:
        conn.deleteLater()
        self._pending.discard(conn)
        for contact_id, dialing in list(self._dialing.items()):
            if dialing is conn:
                del self._dialing[contact_id]
                log.info("Contact %s injoignable : %s", contact_id, reason or "fermé")
        if conn in self._manual:
            self._manual.discard(conn)
            self.error.emit(f"Impossible de joindre ce poste : {reason or 'connexion fermée.'}")
        peer_id = conn.peer_id
        if peer_id is not None and self._connections.get(peer_id) is conn:
            del self._connections[peer_id]
            self._typing_sent.pop(peer_id, None)
            if not self.is_online(peer_id):
                self._set_peer_typing(peer_id, False)
                self.presence_changed.emit(peer_id, False)
            self.contacts_changed.emit()

    def _transmit(self, conn: PeerConnection, record: MessageRecord) -> None:
        try:
            conn.send(Message.chat(record.id, record.text))
        except (RuntimeError, ProtocolError) as exc:
            log.warning("Envoi impossible : %s", exc)
            return
        if self.store.update_status(record.id, Status.SENT):
            self.message_status_changed.emit(record.id, Status.SENT.value)

    def _retry_undelivered(self) -> None:
        for contact_id in self.store.contacts_with_undelivered():
            self.connect_to(contact_id)

    def _set_peer_typing(self, contact_id: str, active: bool) -> None:
        timer = self._typing_timers.get(contact_id)
        if active:
            if timer is None:
                timer = QTimer(self, singleShot=True, interval=TYPING_TIMEOUT_MS)
                timer.timeout.connect(lambda c=contact_id: self._set_peer_typing(c, False))
                self._typing_timers[contact_id] = timer
            timer.start()
            if contact_id not in self._typing:
                self._typing.add(contact_id)
                self.typing_changed.emit(contact_id, True)
        elif contact_id in self._typing:
            self._typing.discard(contact_id)
            if timer is not None:
                timer.stop()
            self.typing_changed.emit(contact_id, False)
