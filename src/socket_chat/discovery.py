"""Détection des collègues sur le réseau local par annonces UDP.

Chaque instance diffuse régulièrement une annonce (identifiant, nom, port TCP)
et écoute celles des autres. Un pair silencieux depuis :data:`PEER_TIMEOUT_MS`
est considéré comme parti. Le broadcast ne franchit pas les routeurs : pour un
autre sous-réseau ou un VPN, on ajoute le contact par son adresse.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QHostAddress,
    QNetworkInterface,
    QUdpSocket,
)

from .protocol import (
    DISCOVERY_PORT,
    MAX_DATAGRAM_SIZE,
    ProtocolError,
    decode_announce,
    encode_announce,
    encode_leave,
    validate_name,
)

log = logging.getLogger(__name__)

#: Intervalle entre deux annonces (ms).
ANNOUNCE_INTERVAL_MS = 5_000
#: Silence au-delà duquel un pair est considéré comme parti (ms).
PEER_TIMEOUT_MS = 15_000
#: Intervalle de vérification des pairs silencieux (ms).
EXPIRE_CHECK_MS = 1_000


def broadcast_targets(port: int) -> list[tuple[str, int]]:
    """Adresses de diffusion de toutes les interfaces IPv4 actives."""
    targets = {"255.255.255.255"}
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        iflags = interface.flags()
        if not (iflags & flags.IsUp and iflags & flags.CanBroadcast) or iflags & flags.IsLoopBack:
            continue
        for entry in interface.addressEntries():
            broadcast = entry.broadcast()
            if (
                entry.ip().protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                and not broadcast.isNull()
            ):
                targets.add(broadcast.toString())
    return [(host, port) for host in sorted(targets)]


class Discovery(QObject):
    peer_seen = Signal(str, str, str, int)  # identifiant, nom, adresse, port TCP
    peer_lost = Signal(str)

    def __init__(
        self,
        user_id: str,
        name: str,
        tcp_port: int,
        *,
        udp_port: int = DISCOVERY_PORT,
        targets: list[tuple[str, int]] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.user_id = user_id
        self._name = validate_name(name)
        self.tcp_port = tcp_port
        self._udp_port = udp_port
        #: Destinataires des annonces ; ``None`` = diffusion sur toutes les interfaces.
        self.targets = targets
        self._socket: QUdpSocket | None = None
        self._peers: dict[str, float] = {}  # identifiant -> dernière annonce (monotonic)

        self._announce_timer = QTimer(self, interval=ANNOUNCE_INTERVAL_MS)
        self._announce_timer.timeout.connect(self.announce)
        self._expire_timer = QTimer(self, interval=EXPIRE_CHECK_MS)
        self._expire_timer.timeout.connect(self._expire)

    @property
    def local_port(self) -> int:
        return self._socket.localPort() if self._socket else 0

    @property
    def peers(self) -> list[str]:
        return list(self._peers)

    def set_name(self, name: str) -> None:
        self._name = validate_name(name)
        self.announce()

    def start(self) -> bool:
        if self._socket is not None:
            return True
        socket = QUdpSocket(self)
        mode = QAbstractSocket.BindFlag.ShareAddress | QAbstractSocket.BindFlag.ReuseAddressHint
        if not socket.bind(QHostAddress(QHostAddress.SpecialAddress.AnyIPv4), self._udp_port, mode):
            log.warning(
                "Détection indisponible (port UDP %d) : %s", self._udp_port, socket.errorString()
            )
            socket.deleteLater()
            return False
        socket.readyRead.connect(self._on_ready_read)
        self._socket = socket
        self._announce_timer.start()
        self._expire_timer.start()
        self.announce()
        log.info("Détection active sur le port UDP %d", socket.localPort())
        return True

    def stop(self) -> None:
        if self._socket is None:
            return
        self._announce_timer.stop()
        self._expire_timer.stop()
        self._send(encode_leave(self.user_id))
        self._socket.close()
        self._socket.deleteLater()
        self._socket = None
        for peer_id in list(self._peers):
            del self._peers[peer_id]
            self.peer_lost.emit(peer_id)

    def announce(self) -> None:
        if self._socket is not None:
            self._send(encode_announce(self.user_id, self._name, self.tcp_port))

    # -- Interne ---------------------------------------------------------
    def _send(self, datagram: bytes) -> None:
        assert self._socket is not None
        targets = self.targets if self.targets is not None else broadcast_targets(self._udp_port)
        for host, port in targets:
            self._socket.writeDatagram(datagram, QHostAddress(host), port)

    def _on_ready_read(self) -> None:
        assert self._socket is not None
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram(MAX_DATAGRAM_SIZE + 1)
            try:
                announcement = decode_announce(bytes(datagram.data().data()))
            except ProtocolError as exc:
                log.debug("Annonce ignorée : %s", exc)
                continue
            if announcement.id == self.user_id:
                continue
            if announcement.leaving:
                if self._peers.pop(announcement.id, None) is not None:
                    self.peer_lost.emit(announcement.id)
                continue
            is_new = announcement.id not in self._peers
            self._peers[announcement.id] = time.monotonic()
            host = datagram.senderAddress().toString().removeprefix("::ffff:")
            self.peer_seen.emit(announcement.id, announcement.name, host, announcement.port)
            if is_new:
                self.announce()  # le nouveau venu nous découvre sans attendre

    def _expire(self) -> None:
        limit = time.monotonic() - PEER_TIMEOUT_MS / 1000
        for peer_id, last_seen in list(self._peers.items()):
            if last_seen < limit:
                del self._peers[peer_id]
                log.info("Pair %s perdu de vue", peer_id)
                self.peer_lost.emit(peer_id)
