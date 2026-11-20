import json

import pytest
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from socket_chat import discovery as discovery_module
from socket_chat.discovery import Discovery, broadcast_targets
from socket_chat.protocol import new_id

TIMEOUT = 5000


@pytest.fixture
def pair(qtbot):
    """Deux instances qui s'annoncent l'une à l'autre en unicast sur 127.0.0.1."""
    created = []

    def make(name, tcp_port):
        d = Discovery(new_id(), name, tcp_port, udp_port=0, targets=[])
        assert d.start()
        created.append(d)
        return d

    alice, bob = make("Alice", 1111), make("Bob", 2222)
    alice.targets = [("127.0.0.1", bob.local_port)]
    bob.targets = [("127.0.0.1", alice.local_port)]
    yield alice, bob
    for d in created:
        d.stop()
        d.deleteLater()
    qtbot.wait(20)


def test_peers_discover_each_other(qtbot, pair):
    alice, bob = pair
    with qtbot.waitSignals([alice.peer_seen, bob.peer_seen], timeout=TIMEOUT) as blocker:
        alice.announce()
    del blocker
    assert alice.peers == [bob.user_id] and bob.peers == [alice.user_id]


def test_peer_seen_carries_identity_and_address(qtbot, pair):
    alice, bob = pair
    with qtbot.waitSignal(bob.peer_seen, timeout=TIMEOUT) as blocker:
        alice.announce()
    assert blocker.args == [alice.user_id, "Alice", "127.0.0.1", 1111]


def test_leave_is_reported(qtbot, pair):
    alice, bob = pair
    with qtbot.waitSignal(bob.peer_seen, timeout=TIMEOUT):
        alice.announce()
    with qtbot.waitSignal(bob.peer_lost, timeout=TIMEOUT) as blocker:
        alice.stop()
    assert blocker.args == [alice.user_id]


def test_silent_peer_times_out(qtbot, pair, monkeypatch):
    monkeypatch.setattr(discovery_module, "PEER_TIMEOUT_MS", 100)
    alice, bob = pair
    with qtbot.waitSignal(alice.peer_seen, timeout=TIMEOUT):
        bob.announce()
    # Bob se tait (sans annoncer son départ) : Alice l'oublie.
    bob._announce_timer.stop()
    bob.targets = []
    with qtbot.waitSignal(alice.peer_lost, timeout=TIMEOUT) as blocker:
        pass
    assert blocker.args == [bob.user_id]


def test_invalid_and_own_datagrams_are_ignored(qtbot, pair):
    _alice, bob = pair
    seen = []
    bob.peer_seen.connect(lambda *args: seen.append(args))
    sender = QUdpSocket()
    for datagram in [
        b"bruit",
        json.dumps(
            {"type": "announce", "version": 1, "id": new_id(), "name": "V1", "port": 1}
        ).encode(),
        json.dumps(
            {"type": "announce", "version": 2, "id": bob.user_id, "name": "Moi", "port": 1}
        ).encode(),
    ]:
        sender.writeDatagram(datagram, QHostAddress("127.0.0.1"), bob.local_port)
    qtbot.wait(200)
    assert seen == []
    sender.deleteLater()


def test_broadcast_targets_include_global_broadcast():
    targets = broadcast_targets(12801)
    assert ("255.255.255.255", 12801) in targets
    assert all(port == 12801 for _, port in targets)


def test_discovery_feeds_messenger(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    alice_disc = Discovery(alice.user_id, "Alice", alice.port, udp_port=0, targets=[])
    bob_disc = Discovery(bob.user_id, "Bob", bob.port, udp_port=0, targets=[])
    assert alice_disc.start() and bob_disc.start()
    alice_disc.targets = [("127.0.0.1", bob_disc.local_port)]
    bob_disc.targets = [("127.0.0.1", alice_disc.local_port)]
    alice.attach_discovery(alice_disc)
    bob.attach_discovery(bob_disc)

    with qtbot.waitSignals([alice.presence_changed, bob.presence_changed], timeout=TIMEOUT):
        alice_disc.announce()
    assert [s.contact.name for s in alice.store.contacts()] == ["Bob"]
    # Le contact détecté est joignable directement : le message part sans saisie d'adresse.
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT):
        alice.send(bob.user_id, "Trouvé tout seul !")

    for d in (alice_disc, bob_disc):
        d.stop()
        d.deleteLater()


def test_announces_outside_allowed_networks_are_ignored(qtbot, pair, monkeypatch):
    alice, bob = pair
    monkeypatch.setattr(discovery_module, "is_allowed", lambda address, networks: not networks)
    bob.allowed_networks = ["10.20.0.0/16"]
    seen = []
    bob.peer_seen.connect(lambda *args: seen.append(args))
    alice.announce()
    with qtbot.waitSignal(alice.peer_seen, timeout=TIMEOUT):
        bob.announce()  # témoin : les annonces circulent bien (Alice ne filtre pas)
    qtbot.wait(100)
    assert seen == [] and bob.peers == []
