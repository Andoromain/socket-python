import pytest
from PySide6.QtNetwork import QHostAddress, QTcpServer

from socket_chat.network import ChatSession, SessionState

TIMEOUT = 5000


@pytest.fixture
def host(qtbot):
    session = ChatSession("Hôte")
    with qtbot.waitSignal(session.state_changed, timeout=TIMEOUT):
        session.host(0)
    assert session.state == SessionState.LISTENING
    yield session
    session.stop()


def join(qtbot, host, name="Invité"):
    guest = ChatSession(name)
    with qtbot.waitSignals([host.peer_joined, guest.peer_joined], timeout=TIMEOUT) as blocker:
        guest.join("127.0.0.1", host.listening_port)
    del blocker
    return guest


def test_handshake_and_messages(qtbot, host):
    guest = join(qtbot, host)
    assert host.state == guest.state == SessionState.CONNECTED
    assert host.peer_name == "Invité"
    assert guest.peer_name == "Hôte"

    with qtbot.waitSignal(host.message_received, timeout=TIMEOUT) as blocker:
        guest.send("Salut !\nÇa va ?")
    assert blocker.args == ["Invité", "Salut !\nÇa va ?"]

    with qtbot.waitSignal(guest.message_received, timeout=TIMEOUT) as blocker:
        host.send("Oui")
    assert blocker.args == ["Hôte", "Oui"]
    guest.stop()


def test_guest_leaving_puts_host_back_to_listening(qtbot, host):
    guest = join(qtbot, host)
    with qtbot.waitSignal(host.peer_left, timeout=TIMEOUT) as blocker:
        guest.stop()
    assert blocker.args == ["Invité", ""]
    assert guest.state == SessionState.IDLE
    assert host.state == SessionState.LISTENING

    # Un nouveau correspondant peut se connecter ensuite.
    other = join(qtbot, host, "Autre")
    assert host.peer_name == "Autre"
    other.stop()


def test_host_stopping_notifies_guest(qtbot, host):
    guest = join(qtbot, host)
    with qtbot.waitSignal(guest.peer_left, timeout=TIMEOUT) as blocker:
        host.stop()
    assert blocker.args[0] == "Hôte"
    qtbot.waitUntil(lambda: guest.state == SessionState.IDLE, timeout=TIMEOUT)


def test_second_guest_is_refused(qtbot, host):
    guest = join(qtbot, host)
    intruder = ChatSession("Intrus")
    with qtbot.waitSignal(intruder.error, timeout=TIMEOUT) as blocker:
        intruder.join("127.0.0.1", host.listening_port)
    assert "déjà en conversation" in blocker.args[0]
    assert intruder.state == SessionState.IDLE
    assert host.peer_name == "Invité"
    guest.stop()


def test_connection_refused(qtbot):
    # Réserve un port puis le libère : plus personne n'y écoute.
    probe = QTcpServer()
    assert probe.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    port = probe.serverPort()
    probe.close()

    session = ChatSession("Seul")
    with qtbot.waitSignal(session.error, timeout=TIMEOUT) as blocker:
        session.join("127.0.0.1", port)
    assert "refusée" in blocker.args[0]
    assert session.state == SessionState.IDLE


def test_host_on_busy_port_reports_error(qtbot, host):
    other = ChatSession("Autre")
    with qtbot.waitSignal(other.error, timeout=TIMEOUT):
        other.host(host.listening_port)
    assert other.state == SessionState.IDLE


def test_invalid_data_closes_connection(qtbot, host):
    from PySide6.QtNetwork import QTcpSocket

    raw = QTcpSocket()
    raw.connectToHost("127.0.0.1", host.listening_port)
    assert raw.waitForConnected(TIMEOUT)
    raw.write(b"ceci n'est pas du JSON\n")
    qtbot.waitUntil(lambda: raw.state() == QTcpSocket.SocketState.UnconnectedState, timeout=TIMEOUT)
    assert host.state == SessionState.LISTENING
