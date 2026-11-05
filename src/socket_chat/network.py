"""Couche réseau : connexion à un pair et session de discussion.

Tout repose sur la boucle d'événements de Qt (``QTcpServer``/``QTcpSocket``) :
aucun thread n'est nécessaire et les signaux peuvent être reliés directement
à l'interface graphique sans risque.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkInterface,
    QTcpServer,
    QTcpSocket,
)

from .protocol import (
    FrameDecoder,
    Message,
    MessageType,
    ProtocolError,
    encode,
    validate_name,
)

log = logging.getLogger(__name__)

#: Délai maximal pour établir la connexion TCP (ms).
CONNECT_TIMEOUT_MS = 10_000
#: Délai maximal pour recevoir la présentation (« hello ») du pair (ms).
HANDSHAKE_TIMEOUT_MS = 10_000
#: Délai maximal accordé à une fermeture polie avant de couper la connexion (ms).
CLOSE_TIMEOUT_MS = 3_000

BUSY_REASON = "busy"

_SOCKET_ERRORS = {
    QAbstractSocket.SocketError.ConnectionRefusedError: (
        "Connexion refusée : aucune conversation n'est hébergée à cette adresse."
    ),
    QAbstractSocket.SocketError.HostNotFoundError: "Hôte introuvable.",
    QAbstractSocket.SocketError.NetworkError: "Erreur réseau.",
    QAbstractSocket.SocketError.SocketTimeoutError: "Délai de connexion dépassé.",
    QAbstractSocket.SocketError.RemoteHostClosedError: "Le correspondant a fermé la connexion.",
}


def describe_socket_error(socket: QAbstractSocket) -> str:
    return _SOCKET_ERRORS.get(socket.error(), socket.errorString())


def local_ipv4_addresses() -> list[str]:
    """Adresses IPv4 non locales de la machine (pour les communiquer au correspondant)."""
    addresses = []
    for address in QNetworkInterface.allAddresses():
        if address.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol and not (
            address.isLoopback() or address.isLinkLocal()
        ):
            addresses.append(address.toString())
    return addresses


class PeerConnection(QObject):
    """Connexion avec un pair, au-dessus d'une ``QTcpSocket``.

    Gère la présentation mutuelle, le découpage des trames et la fermeture.
    Le signal :attr:`closed` est émis exactement une fois.
    """

    ready = Signal(str)  # nom du pair, une fois la présentation reçue
    message_received = Signal(str)
    closed = Signal(str)  # motif lisible ; chaîne vide = fermeture normale

    def __init__(self, socket: QTcpSocket, local_name: str, parent: QObject | None = None):
        super().__init__(parent)
        self._socket = socket
        socket.setParent(self)
        self._local_name = local_name
        self._decoder = FrameDecoder()
        self._peer_name: str | None = None
        self._finished = False
        self._close_reason = ""
        self._hello_sent = False

        self._handshake_timer = QTimer(self, singleShot=True, interval=HANDSHAKE_TIMEOUT_MS)
        self._handshake_timer.timeout.connect(
            lambda: self._fail("Le correspondant ne s'est pas présenté à temps.")
        )
        # Minuteurs enfants de cette connexion : ils sont détruits avec elle, ce qui
        # évite qu'un QTimer.singleShot ne touche une socket déjà supprimée.
        self._close_timer = QTimer(self, singleShot=True, interval=CLOSE_TIMEOUT_MS)
        self._close_timer.timeout.connect(self._on_close_timeout)

        socket.connected.connect(self._on_connected)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_error)

        if socket.state() == QAbstractSocket.SocketState.ConnectedState:
            # Côté serveur : la socket est déjà connectée. La présentation est
            # différée pour laisser l'appelant brancher ses signaux.
            self._start_timer = QTimer(self, singleShot=True, interval=0)
            self._start_timer.timeout.connect(self._on_connected)
            self._start_timer.start()

    # -- Propriétés ------------------------------------------------------
    @property
    def peer_name(self) -> str | None:
        return self._peer_name

    @property
    def peer_address(self) -> str:
        return self._socket.peerAddress().toString()

    @property
    def is_ready(self) -> bool:
        return self._peer_name is not None and not self._finished

    # -- API -------------------------------------------------------------
    def send_chat(self, text: str) -> None:
        if not self.is_ready:
            raise RuntimeError("La connexion n'est pas établie.")
        self._write(Message.chat(text))

    def close(self, reason: str = "") -> None:
        """Termine poliment la conversation (envoie « bye » puis ferme)."""
        if self._finished:
            return
        if self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self._close_reason = reason
            self._write(Message.bye(reason))
            self._socket.disconnectFromHost()
            # disconnectFromHost peut terminer immédiatement ou plus tard ;
            # on borne l'attente pour ne jamais rester bloqué.
            if not self._finished:
                self._close_timer.start()
        else:
            self._socket.abort()
            self._finish(reason)

    def abort(self, reason: str = "") -> None:
        """Ferme brutalement, sans prévenir le pair."""
        self._socket.abort()
        self._finish(reason)

    # -- Interne ---------------------------------------------------------
    def _write(self, message: Message) -> None:
        self._socket.write(encode(message))

    def _on_connected(self) -> None:
        if self._finished or self._hello_sent:
            return
        self._hello_sent = True
        log.debug("Connecté à %s, envoi de la présentation", self.peer_address)
        self._write(Message.hello(self._local_name))
        self._handshake_timer.start()

    def _on_ready_read(self) -> None:
        try:
            messages = self._decoder.feed(bytes(self._socket.readAll().data()))
        except ProtocolError as exc:
            log.warning("Erreur de protocole avec %s : %s", self.peer_address, exc)
            self._fail(f"Données invalides reçues : {exc}")
            return
        for message in messages:
            if self._finished:
                return
            self._handle(message)

    def _handle(self, message: Message) -> None:
        if message.type is MessageType.HELLO:
            if self._peer_name is not None:
                return  # présentation en double : ignorée
            self._handshake_timer.stop()
            self._peer_name = message.name
            log.info("Pair prêt : %s (%s)", message.name, self.peer_address)
            self.ready.emit(message.name)
        elif message.type is MessageType.CHAT:
            if self._peer_name is None:
                self._fail("Message reçu avant la présentation du correspondant.")
                return
            self.message_received.emit(message.text)
        elif message.type is MessageType.BYE:
            self._close_reason = message.reason
            self._socket.disconnectFromHost()

    def _on_close_timeout(self) -> None:
        log.debug("Fermeture polie trop longue, connexion coupée")
        self.abort(self._close_reason)

    def _fail(self, reason: str) -> None:
        if self._finished:
            return
        self.abort(reason)

    def _on_error(self, _error: QAbstractSocket.SocketError) -> None:
        if self._finished:
            return
        if self._socket.error() == QAbstractSocket.SocketError.RemoteHostClosedError:
            return  # fermeture normale, traitée par _on_disconnected
        self._finish(describe_socket_error(self._socket))

    def _on_disconnected(self) -> None:
        self._finish(self._close_reason)

    def _finish(self, reason: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._handshake_timer.stop()
        self._close_timer.stop()
        log.info("Connexion terminée (%s)", reason or "normale")
        self.closed.emit(reason)


class SessionState(Enum):
    IDLE = auto()  # rien en cours
    LISTENING = auto()  # en attente d'un correspondant
    CONNECTING = auto()  # connexion sortante en cours
    CONNECTED = auto()  # conversation active


class ChatSession(QObject):
    """Orchestration d'une conversation à deux, en mode hôte ou invité.

    En mode hôte, un seul correspondant est accepté à la fois ; les connexions
    supplémentaires sont refusées poliment. Lorsque le correspondant part,
    l'hôte se remet en écoute.
    """

    state_changed = Signal(SessionState)
    peer_joined = Signal(str)
    peer_left = Signal(str, str)  # nom, motif
    message_received = Signal(str, str)  # nom, texte
    error = Signal(str)

    def __init__(self, local_name: str, parent: QObject | None = None):
        super().__init__(parent)
        self._local_name = validate_name(local_name)
        self._state = SessionState.IDLE
        self._server: QTcpServer | None = None
        self._peer: PeerConnection | None = None
        self._connect_timer = QTimer(self, singleShot=True, interval=CONNECT_TIMEOUT_MS)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

    # -- Propriétés ------------------------------------------------------
    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def local_name(self) -> str:
        return self._local_name

    @local_name.setter
    def local_name(self, name: str) -> None:
        self._local_name = validate_name(name)

    @property
    def is_hosting(self) -> bool:
        return self._server is not None

    @property
    def listening_port(self) -> int | None:
        return self._server.serverPort() if self._server else None

    @property
    def peer_name(self) -> str | None:
        return self._peer.peer_name if self._peer else None

    # -- API -------------------------------------------------------------
    def host(self, port: int) -> None:
        """Héberge une conversation sur ``port`` (0 = port choisi par le système)."""
        self.stop()
        server = QTcpServer(self)
        if not server.listen(QHostAddress(QHostAddress.SpecialAddress.Any), port):
            message = f"Impossible d'écouter sur le port {port} : {server.errorString()}"
            server.deleteLater()
            self.error.emit(message)
            return
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        log.info("En écoute sur le port %d", server.serverPort())
        self._set_state(SessionState.LISTENING)

    def join(self, host: str, port: int) -> None:
        """Rejoint une conversation hébergée sur ``host:port``."""
        self.stop()
        socket = QTcpSocket()
        self._attach_peer(PeerConnection(socket, self._local_name, self))
        self._set_state(SessionState.CONNECTING)
        self._connect_timer.start()
        log.info("Connexion à %s:%d", host, port)
        socket.connectToHost(host, port)

    def send(self, text: str) -> None:
        if self._peer is None or not self._peer.is_ready:
            raise RuntimeError("Aucun correspondant connecté.")
        self._peer.send_chat(text)

    def stop(self) -> None:
        """Termine la conversation et arrête l'écoute éventuelle."""
        self._connect_timer.stop()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._peer is not None:
            # Une fois self._peer remis à None, _on_peer_closed ignore cette connexion :
            # inutile de déconnecter le signal.
            peer, self._peer = self._peer, None
            name = peer.peer_name
            peer.closed.connect(peer.deleteLater)
            peer.close()
            if name:
                self.peer_left.emit(name, "")
        self._set_state(SessionState.IDLE)

    # -- Interne ---------------------------------------------------------
    def _set_state(self, state: SessionState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _attach_peer(self, peer: PeerConnection) -> None:
        self._peer = peer
        peer.ready.connect(self._on_peer_ready)
        peer.message_received.connect(self._on_peer_message)
        peer.closed.connect(self._on_peer_closed)

    def _on_new_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            address = socket.peerAddress().toString()
            if self._peer is not None:
                log.info("Connexion de %s refusée : conversation déjà en cours", address)
                socket.write(encode(Message.bye(BUSY_REASON)))
                socket.disconnected.connect(socket.deleteLater)
                socket.disconnectFromHost()
                continue
            log.info("Nouvelle connexion de %s", address)
            self._attach_peer(PeerConnection(socket, self._local_name, self))

    def _on_connect_timeout(self) -> None:
        if self._peer is not None and self._state == SessionState.CONNECTING:
            self._peer.abort("Délai de connexion dépassé.")

    def _on_peer_ready(self, name: str) -> None:
        self._connect_timer.stop()
        self._set_state(SessionState.CONNECTED)
        self.peer_joined.emit(name)

    def _on_peer_message(self, text: str) -> None:
        peer = self.sender()
        if isinstance(peer, PeerConnection) and peer.peer_name:
            self.message_received.emit(peer.peer_name, text)

    def _on_peer_closed(self, reason: str) -> None:
        peer = self.sender()
        if peer is not self._peer:
            return
        assert isinstance(peer, PeerConnection)
        self._connect_timer.stop()
        self._peer = None
        peer.deleteLater()
        reason = _humanize_reason(reason)

        if peer.peer_name:
            self.peer_left.emit(peer.peer_name, reason)
        elif reason and not self.is_hosting:
            self.error.emit(reason)
        elif reason:
            log.info("Tentative de connexion échouée : %s", reason)

        self._set_state(SessionState.LISTENING if self.is_hosting else SessionState.IDLE)


def _humanize_reason(reason: str) -> str:
    if reason == BUSY_REASON:
        return "Le correspondant est déjà en conversation avec quelqu'un d'autre."
    return reason
