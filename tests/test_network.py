import pytest
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from socket_chat import network
from socket_chat.network import SessionState

TIMEOUT = 5000


@pytest.fixture
def host(qtbot, make_session):
    session = make_session("Hôte")
    with qtbot.waitSignal(session.state_changed, timeout=TIMEOUT):
        session.host(0)
    assert session.state == SessionState.LISTENING
    return session


@pytest.fixture
def join(qtbot, make_session):
    def _join(host, name="Invité"):
        guest = make_session(name)
        with qtbot.waitSignals([host.peer_joined, guest.peer_joined], timeout=TIMEOUT):
            guest.join("127.0.0.1", host.listening_port)
        return guest

    return _join


def test_handshake_and_messages(qtbot, host, join):
    guest = join(host)
    assert host.state == guest.state == SessionState.CONNECTED
    assert host.peer_name == "Invité"
    assert guest.peer_name == "Hôte"

    with qtbot.waitSignal(host.message_received, timeout=TIMEOUT) as blocker:
        guest.send("Salut !\nÇa va ?")
    assert blocker.args == ["Invité", "Salut !\nÇa va ?"]

    with qtbot.waitSignal(guest.message_received, timeout=TIMEOUT) as blocker:
        host.send("Oui")
    assert blocker.args == ["Hôte", "Oui"]


def test_guest_leaving_puts_host_back_to_listening(qtbot, host, join):
    guest = join(host)
    with qtbot.waitSignal(host.peer_left, timeout=TIMEOUT) as blocker:
        guest.stop()
    assert blocker.args == ["Invité", ""]
    assert guest.state == SessionState.IDLE
    assert host.state == SessionState.LISTENING

    # Un nouveau correspondant peut se connecter ensuite.
    join(host, "Autre")
    assert host.peer_name == "Autre"


def test_host_stopping_notifies_guest(qtbot, host, join):
    guest = join(host)
    with qtbot.waitSignal(guest.peer_left, timeout=TIMEOUT) as blocker:
        host.stop()
    assert blocker.args[0] == "Hôte"
    qtbot.waitUntil(lambda: guest.state == SessionState.IDLE, timeout=TIMEOUT)


def test_second_guest_is_refused(qtbot, host, join, make_session):
    join(host)
    intruder = make_session("Intrus")
    with qtbot.waitSignal(intruder.error, timeout=TIMEOUT) as blocker:
        intruder.join("127.0.0.1", host.listening_port)
    assert "déjà en conversation" in blocker.args[0]
    assert intruder.state == SessionState.IDLE
    assert host.peer_name == "Invité"


def test_connection_refused(qtbot, make_session):
    # Réserve un port puis le libère : plus personne n'y écoute.
    probe = QTcpServer()
    assert probe.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    port = probe.serverPort()
    probe.close()
    probe.deleteLater()

    session = make_session("Seul")
    # Sous Windows, un refus n'est signalé qu'après plusieurs tentatives (~2 s).
    with qtbot.waitSignal(session.error, timeout=TIMEOUT) as blocker:
        session.join("127.0.0.1", port)
    assert "refusée" in blocker.args[0]
    assert session.state == SessionState.IDLE


def test_host_on_busy_port_reports_error(qtbot, host, make_session):
    other = make_session("Autre")
    with qtbot.waitSignal(other.error, timeout=TIMEOUT):
        other.host(host.listening_port)
    assert other.state == SessionState.IDLE


def test_invalid_data_closes_connection(qtbot, host):
    raw = QTcpSocket()
    raw.connectToHost("127.0.0.1", host.listening_port)
    assert raw.waitForConnected(TIMEOUT)
    raw.write(b"ceci n'est pas du JSON\n")
    qtbot.waitUntil(lambda: raw.state() == QTcpSocket.SocketState.UnconnectedState, timeout=TIMEOUT)
    assert host.state == SessionState.LISTENING
    raw.deleteLater()


@pytest.mark.parametrize("leaver", ["guest", "host"])
def test_no_late_callback_after_close(qtbot, monkeypatch, host, join, leaver):
    """Régression : le délai de fermeture ne doit pas viser une socket détruite.

    pytest-qt fait échouer le test si une exception survient dans la boucle Qt.
    """
    monkeypatch.setattr(network, "CLOSE_TIMEOUT_MS", 50)
    guest = join(host)
    leaving, staying = (guest, host) if leaver == "guest" else (host, guest)
    with qtbot.waitSignal(staying.peer_left, timeout=TIMEOUT):
        leaving.stop()
    qtbot.wait(300)  # laisse expirer le délai de fermeture
