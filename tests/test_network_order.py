"""Ordre de la présentation (« hello ») sur une connexion entrante.

Notre « hello » est différé d'un tour de boucle sur une connexion acceptée. Si celui
du pair est traité avant, aucune trame ne doit partir avant notre présentation (le pair
couperait la connexion), et le délai de présentation ne doit pas être relancé.
"""

from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from socket_chat import network
from socket_chat.network import PeerConnection
from socket_chat.protocol import FrameDecoder, Message, MessageType, new_id

TIMEOUT = 5000


def accepted_pair(qtbot):
    server = QTcpServer()
    assert server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    client = QTcpSocket()
    client.connectToHost("127.0.0.1", server.serverPort())
    assert client.waitForConnected(TIMEOUT)
    qtbot.waitUntil(server.hasPendingConnections, timeout=TIMEOUT)
    return server, client, server.nextPendingConnection()


def received(qtbot, client, count):
    decoder = FrameDecoder()
    frames = []

    def enough():
        if client.bytesAvailable():
            frames.extend(decoder.feed(bytes(client.readAll().data())))
        return len(frames) >= count

    qtbot.waitUntil(enough, timeout=TIMEOUT)
    return frames


def test_our_hello_precedes_frames_sent_on_ready(qtbot, monkeypatch):
    monkeypatch.setattr(network, "HANDSHAKE_TIMEOUT_MS", 300)
    server, client, socket = accepted_pair(qtbot)
    me = new_id()
    conn = PeerConnection(socket, Message.hello(me, "Moi", 1), outgoing=False)
    closed = []
    conn.closed.connect(closed.append)
    # Comme Messenger, on envoie quelque chose dès que le pair est identifié.
    conn.ready.connect(lambda *_args: conn.send(Message.typing(True)))

    # Le « hello » du pair est traité avant notre minuteur différé (0 ms).
    conn._handle(Message.hello(new_id(), "Pair", 2))

    frames = received(qtbot, client, 2)
    assert frames[0].type is MessageType.HELLO and frames[0].id == me
    assert frames[1] == Message.typing(True)

    # Le délai de présentation ne doit pas couper une connexion déjà identifiée.
    qtbot.wait(600)
    assert closed == [] and conn.is_ready
    hellos = [f for f in received(qtbot, client, 0) if f.type is MessageType.HELLO]
    assert hellos == []  # notre présentation n'est pas envoyée deux fois

    conn.abort()
    for obj in (conn, client, server):
        obj.deleteLater()


def test_send_before_hello_sends_hello_first(qtbot):
    server, client, socket = accepted_pair(qtbot)
    me = new_id()
    conn = PeerConnection(socket, Message.hello(me, "Moi", 1), outgoing=False)
    conn._peer_id = new_id()  # pair identifié par un autre chemin
    conn.send(Message.typing(False))
    frames = received(qtbot, client, 2)
    assert [f.type for f in frames] == [MessageType.HELLO, MessageType.TYPING]
    conn.abort()
    for obj in (conn, client, server):
        obj.deleteLater()
