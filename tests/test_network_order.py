"""Connexion chiffrée vue « sur le fil » : poignée de main, ordre des trames, refus.

Le client de test parle directement à une ``PeerConnection`` acceptée, en jouant
l'initiateur Noise à la main.
"""

from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from socket_chat import network, noise
from socket_chat.identity import PROLOGUE
from socket_chat.network import OLD_VERSION_ERROR, PeerConnection
from socket_chat.protocol import FrameDecoder, Message, MessageType, encode, new_id

TIMEOUT = 5000


def accepted_pair(qtbot):
    server = QTcpServer()
    assert server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    client = QTcpSocket()
    client.connectToHost("127.0.0.1", server.serverPort())
    assert client.waitForConnected(TIMEOUT)
    qtbot.waitUntil(server.hasPendingConnections, timeout=TIMEOUT)
    return server, client, server.nextPendingConnection()


class NoiseClient:
    """Initiateur Noise minimal au-dessus d'une QTcpSocket de test."""

    def __init__(self, qtbot, socket):
        self.qtbot, self.socket = qtbot, socket
        self.keys = noise.KeyPair.generate()
        self.state = noise.HandshakeState(True, self.keys, PROLOGUE)
        self.reader = noise.FrameReader()
        self.decoder = FrameDecoder()
        self.frames = []

    def _messages(self, count):
        got = []

        def enough():
            if self.socket.bytesAvailable():
                got.extend(self.reader.feed(bytes(self.socket.readAll().data())))
            return len(got) >= count

        self.qtbot.waitUntil(enough, timeout=TIMEOUT)
        return got

    def handshake(self):
        self.socket.write(noise.frame(self.state.write_message()))
        (msg2,) = self._messages(1)
        self.state.read_message(msg2)
        self.socket.write(noise.frame(self.state.write_message()))
        assert self.state.complete

    def send(self, message):
        self.socket.write(noise.encrypt_stream(self.state.send, encode(message)))

    def received(self, count):
        def enough():
            if self.socket.bytesAvailable():
                for msg in self.reader.feed(bytes(self.socket.readAll().data())):
                    self.frames.extend(self.decoder.feed(self.state.receive.decrypt(b"", msg)))
            return len(self.frames) >= count

        self.qtbot.waitUntil(enough, timeout=TIMEOUT)
        return self.frames


def test_handshake_then_hello_then_frames(qtbot, monkeypatch):
    monkeypatch.setattr(network, "HANDSHAKE_TIMEOUT_MS", 300)
    server, client, socket = accepted_pair(qtbot)
    me, my_keys = new_id(), noise.KeyPair.generate()
    conn = PeerConnection(socket, Message.hello(me, "Moi", 1), outgoing=False, identity=my_keys)
    closed, ready = [], []
    conn.closed.connect(closed.append)
    conn.ready.connect(lambda *args: ready.append(args))
    # Comme Messenger, on envoie quelque chose dès que le pair est identifié.
    conn.ready.connect(lambda *_args: conn.send(Message.typing(True)))

    peer = NoiseClient(qtbot, client)
    peer.handshake()
    # Notre présentation part dès la fin de la poignée de main, chiffrée.
    frames = peer.received(1)
    assert frames[0].type is MessageType.HELLO and frames[0].id == me
    assert peer.state.remote_static == my_keys.public

    peer_id = new_id()
    peer.send(Message.hello(peer_id, "Pair", 2))
    frames = peer.received(2)
    assert frames[1] == Message.typing(True)
    assert ready == [(peer_id, "Pair", 2)]
    assert conn.peer_key == peer.keys.public and conn.secure

    # Le délai de présentation ne doit pas couper une connexion déjà identifiée.
    qtbot.wait(600)
    assert closed == [] and conn.is_ready
    assert [f for f in peer.frames if f.type is MessageType.HELLO] == [frames[0]]

    conn.abort()
    for obj in (conn, client, server):
        obj.deleteLater()


def test_plaintext_peer_gets_old_version_error(qtbot):
    server, client, socket = accepted_pair(qtbot)
    conn = PeerConnection(
        socket,
        Message.hello(new_id(), "Moi", 1),
        outgoing=False,
        identity=noise.KeyPair.generate(),
    )
    with qtbot.waitSignal(conn.closed, timeout=TIMEOUT) as blocker:
        client.write(
            b'{"type":"hello","version":3,"id":"%s","name":"Vieux","port":1}\n' % new_id().encode()
        )
    assert blocker.args == [OLD_VERSION_ERROR]
    for obj in (conn, client, server):
        obj.deleteLater()


def test_corrupted_handshake_closes_connection(qtbot):
    server, client, socket = accepted_pair(qtbot)
    conn = PeerConnection(
        socket,
        Message.hello(new_id(), "Moi", 1),
        outgoing=False,
        identity=noise.KeyPair.generate(),
    )
    peer = NoiseClient(qtbot, client)
    client.write(noise.frame(peer.state.write_message()))
    (msg2,) = peer._messages(1)
    peer.state.read_message(msg2)
    msg3 = bytearray(peer.state.write_message())
    msg3[5] ^= 0xFF  # clé statique chiffrée altérée
    with qtbot.waitSignal(conn.closed, timeout=TIMEOUT) as blocker:
        client.write(noise.frame(bytes(msg3)))
    assert blocker.args[0].startswith("Connexion chiffrée impossible")
    assert not conn.is_ready
    for obj in (conn, client, server):
        obj.deleteLater()


def test_silent_peer_times_out(qtbot, monkeypatch):
    monkeypatch.setattr(network, "HANDSHAKE_TIMEOUT_MS", 200)
    server, client, socket = accepted_pair(qtbot)
    conn = PeerConnection(
        socket,
        Message.hello(new_id(), "Moi", 1),
        outgoing=False,
        identity=noise.KeyPair.generate(),
    )
    with qtbot.waitSignal(conn.closed, timeout=TIMEOUT) as blocker:
        pass  # le client ne commence jamais la poignée de main
    assert "présenté à temps" in blocker.args[0]
    for obj in (conn, client, server):
        obj.deleteLater()
