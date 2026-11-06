"""Couche réseau : une connexion TCP avec un pair.

Tout repose sur la boucle d'événements de Qt (``QTcpSocket``) : aucun thread
n'est nécessaire et les signaux peuvent être reliés directement à l'interface.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface, QTcpSocket

from .protocol import FrameDecoder, Message, MessageType, ProtocolError, encode

log = logging.getLogger(__name__)

#: Délai maximal pour établir la connexion TCP (ms).
CONNECT_TIMEOUT_MS = 10_000
#: Délai maximal pour recevoir la présentation (« hello ») du pair (ms).
HANDSHAKE_TIMEOUT_MS = 10_000
#: Délai maximal accordé à une fermeture polie avant de couper la connexion (ms).
CLOSE_TIMEOUT_MS = 3_000

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

    Gère la présentation mutuelle (« hello »), le découpage des trames et la
    fermeture. Le signal :attr:`closed` est émis exactement une fois.

    ``outgoing`` indique qui a ouvert la connexion : ce poste (connexion
    sortante, ``socket`` pas encore connectée) ou le pair (connexion acceptée).
    """

    ready = Signal(str, str, int)  # identifiant, nom et port d'écoute du pair
    message_received = Signal(object)  # Message (hors hello et bye)
    closed = Signal(str)  # motif lisible ; chaîne vide = fermeture normale
    bytes_written = Signal()  # des données ont quitté la file d'envoi (contrôle de flux)

    def __init__(
        self,
        socket: QTcpSocket,
        hello: Message,
        *,
        outgoing: bool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._socket = socket
        socket.setParent(self)
        self._hello = hello
        self.outgoing = outgoing
        self._decoder = FrameDecoder()
        self._peer_id: str | None = None
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
        self._connect_timer = QTimer(self, singleShot=True, interval=CONNECT_TIMEOUT_MS)
        self._connect_timer.timeout.connect(lambda: self._fail("Délai de connexion dépassé."))

        socket.connected.connect(self._on_connected)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_error)
        socket.bytesWritten.connect(lambda _n: self.bytes_written.emit())

        if socket.state() == QAbstractSocket.SocketState.ConnectedState:
            # Côté serveur : la socket est déjà connectée. La présentation est
            # différée pour laisser l'appelant brancher ses signaux.
            self._start_timer = QTimer(self, singleShot=True, interval=0)
            self._start_timer.timeout.connect(self._on_connected)
            self._start_timer.start()
        else:
            self._connect_timer.start()

    # -- Propriétés ------------------------------------------------------
    @property
    def peer_id(self) -> str | None:
        return self._peer_id

    @property
    def peer_name(self) -> str | None:
        return self._peer_name

    @property
    def peer_address(self) -> str:
        return self._socket.peerAddress().toString()

    @property
    def pending_bytes(self) -> int:
        """Octets en attente d'envoi (pour ne pas saturer la mémoire lors d'un transfert)."""
        return self._socket.bytesToWrite()

    @property
    def is_ready(self) -> bool:
        return self._peer_id is not None and not self._finished

    # -- API -------------------------------------------------------------
    def send(self, message: Message) -> None:
        if not self.is_ready:
            raise RuntimeError("La connexion n'est pas établie.")
        self._send_hello()  # notre présentation précède toujours toute autre trame
        self._write(message)

    def connect_to_host(self, host: str, port: int) -> None:
        """Ouvre une connexion sortante (``outgoing=True``)."""
        self._socket.connectToHost(host, port)

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
        self._send_hello()

    def _send_hello(self) -> None:
        """Envoie notre présentation une seule fois (idempotent)."""
        if self._finished or self._hello_sent:
            return
        self._hello_sent = True
        self._connect_timer.stop()
        log.debug("Connecté à %s, envoi de la présentation", self.peer_address)
        self._write(self._hello)
        if self._peer_id is None:
            # Délai accordé au pair pour se présenter, s'il ne l'a pas déjà fait.
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
            if self._peer_id is not None:
                return  # présentation en double : ignorée
            self._handshake_timer.stop()
            self._peer_id, self._peer_name = message.id, message.name
            # Sur une connexion acceptée, notre présentation est différée d'un tour de
            # boucle : si celle du pair arrive avant, on l'envoie maintenant, avant que
            # quoi que ce soit d'autre ne parte (le pair couperait la connexion).
            self._send_hello()
            log.info("Pair prêt : %s (%s)", message.name, self.peer_address)
            self.ready.emit(message.id, message.name, message.port)
        elif message.type is MessageType.BYE:
            self._close_reason = message.reason
            self._socket.disconnectFromHost()
        elif self._peer_id is None:
            self._fail("Message reçu avant la présentation du correspondant.")
        else:
            self.message_received.emit(message)

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
        self._connect_timer.stop()
        log.info("Connexion terminée (%s)", reason or "normale")
        self.closed.emit(reason)
