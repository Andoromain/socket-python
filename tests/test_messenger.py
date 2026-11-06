import pytest
from PySide6.QtNetwork import QHostAddress, QTcpServer

from socket_chat import messenger as messenger_module
from socket_chat.store import Status, Store

TIMEOUT = 5000


def link(qtbot, a, b):
    """a ajoute b par son adresse ; attend que chacun voie l'autre en ligne."""
    with qtbot.waitSignals([a.presence_changed, b.presence_changed], timeout=TIMEOUT):
        a.add_contact("127.0.0.1", b.port)
    assert a.is_online(b.user_id) and b.is_online(a.user_id)


def status(m, message_id):
    return m.store.message(message_id).status


def test_add_contact_by_address_creates_contacts_on_both_sides(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    link(qtbot, alice, bob)
    assert [s.contact.name for s in alice.store.contacts()] == ["Bob"]
    assert [s.contact.name for s in bob.store.contacts()] == ["Alice"]
    # Le port mémorisé est le port d'écoute, pas le port éphémère de la connexion.
    assert bob.store.contact(alice.user_id).port == alice.port


def test_message_delivery_and_read_receipts(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    link(qtbot, alice, bob)

    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT) as blocker:
        record = alice.send(bob.user_id, "  Salut !\nÇa va ?  ")
    received = blocker.args[0]
    assert (received.id, received.text, received.outgoing) == (record.id, "Salut !\nÇa va ?", False)
    assert received.unread and bob.store.total_unread() == 1

    qtbot.waitUntil(lambda: status(alice, record.id) is Status.DELIVERED, timeout=TIMEOUT)
    with qtbot.waitSignal(
        alice.message_status_changed,
        check_params_cb=lambda mid, st: mid == record.id and st == "read",
        timeout=TIMEOUT,
    ):
        bob.mark_read(alice.user_id)
    assert bob.store.total_unread() == 0


def test_typing_indicator(qtbot, make_messenger, monkeypatch):
    monkeypatch.setattr(messenger_module, "TYPING_TIMEOUT_MS", 200)
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    link(qtbot, alice, bob)

    with qtbot.waitSignal(bob.typing_changed, timeout=TIMEOUT) as blocker:
        alice.set_typing(bob.user_id, True)
    assert blocker.args == [alice.user_id, True]
    assert bob.is_typing(alice.user_id)
    # Sans nouvelle indication, « écrit… » disparaît tout seul.
    with qtbot.waitSignal(bob.typing_changed, timeout=TIMEOUT) as blocker:
        pass
    assert blocker.args == [alice.user_id, False]

    # Un message reçu efface aussi l'indication.
    alice.set_typing(bob.user_id, False)
    with qtbot.waitSignal(bob.typing_changed, timeout=TIMEOUT):
        alice.set_typing(bob.user_id, True)
    with qtbot.waitSignal(bob.typing_changed, timeout=TIMEOUT) as blocker:
        alice.send(bob.user_id, "fini")
    assert blocker.args == [alice.user_id, False]


def test_offline_messages_are_queued_then_delivered(qtbot, make_messenger, tmp_path):
    bob_store = Store(tmp_path / "bob.sqlite3")
    alice = make_messenger("Alice")
    bob = make_messenger("Bob", store=bob_store)
    bob_id = bob.user_id
    link(qtbot, alice, bob)

    with qtbot.waitSignal(alice.presence_changed, timeout=TIMEOUT) as blocker:
        bob.stop()
    assert blocker.args == [bob_id, False]

    record = alice.send(bob_id, "Tu verras ça en revenant")
    qtbot.wait(100)
    assert status(alice, record.id) is Status.PENDING

    # Bob revient (même identité, même historique, nouveau port) et est détecté.
    bob2 = make_messenger("Bob", user_id=bob_id, store=bob_store)
    with qtbot.waitSignal(bob2.message_added, timeout=TIMEOUT) as blocker:
        alice.on_peer_seen(bob_id, "Bob", "127.0.0.1", bob2.port)
    assert blocker.args[0].text == "Tu verras ça en revenant"
    qtbot.waitUntil(lambda: status(alice, record.id) is Status.DELIVERED, timeout=TIMEOUT)


def test_retransmitted_message_is_not_duplicated(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    link(qtbot, alice, bob)
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT):
        record = alice.send(bob.user_id, "une seule fois")
    bob.mark_read(alice.user_id)
    qtbot.waitUntil(lambda: status(alice, record.id) is Status.READ, timeout=TIMEOUT)

    # Simule une retransmission (accusé perdu) : Bob ne l'affiche pas deux fois
    # et renvoie l'état réel du message.
    added = []
    bob.message_added.connect(added.append)
    alice._transmit(alice._connections[bob.user_id], record)
    qtbot.wait(200)
    assert added == []
    assert len(bob.store.messages(alice.user_id)) == 1


def test_crossed_connections_converge(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    alice.add_contact("127.0.0.1", bob.port)
    bob.add_contact("127.0.0.1", alice.port)
    qtbot.waitUntil(
        lambda: alice.is_online(bob.user_id) and bob.is_online(alice.user_id), timeout=TIMEOUT
    )
    qtbot.wait(300)  # laisse se fermer la connexion en double
    assert list(alice._connections) == [bob.user_id]
    assert list(bob._connections) == [alice.user_id]
    with qtbot.waitSignal(alice.message_added, timeout=TIMEOUT):
        bob.send(alice.user_id, "ping")
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT):
        alice.send(bob.user_id, "pong")


def test_unreachable_address_reports_error(qtbot, make_messenger):
    probe = QTcpServer()
    assert probe.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    port = probe.serverPort()
    probe.close()
    probe.deleteLater()

    alice = make_messenger("Alice")
    with qtbot.waitSignal(alice.error, timeout=TIMEOUT) as blocker:
        alice.add_contact("127.0.0.1", port)
    assert "Impossible de joindre" in blocker.args[0]


def test_connecting_to_self_is_explained(qtbot, make_messenger):
    alice = make_messenger("Alice")
    errors = []
    alice.error.connect(errors.append)
    alice.add_contact("127.0.0.1", alice.port)
    qtbot.wait(300)
    assert alice.store.contacts() == []
    assert errors == [messenger_module.SELF_ERROR]


@pytest.mark.parametrize("bye_first", [False, True])
def test_self_connection_error_whatever_the_event_order(make_messenger, bye_first):
    """Sous Windows, le « bye » de l'autre extrémité peut arriver avant le « hello »."""
    alice = make_messenger("Alice", start=False)
    errors = []
    alice.error.connect(errors.append)

    class FakeConn:
        outgoing = True
        peer_id = None
        peer_address = "127.0.0.1"

        def close(self, reason=""):
            pass

        def deleteLater(self):  # noqa: N802 - API Qt
            pass

    conn = FakeConn()
    alice._manual.add(conn)
    alice._pending.add(conn)
    if bye_first:
        alice._on_closed(conn, messenger_module.SELF_REASON)
    else:
        alice._on_ready(conn, alice.user_id, "Alice", 1)
        alice._on_closed(conn, messenger_module.SELF_REASON)
    assert errors == [messenger_module.SELF_ERROR]


def test_port_in_use_reports_error(qtbot, make_messenger):
    alice = make_messenger("Alice")
    bob = make_messenger("Bob", start=False)
    with qtbot.waitSignal(bob.error, timeout=TIMEOUT):
        assert not bob.start(alice.port)


def test_send_validation(make_messenger):
    alice = make_messenger("Alice")
    with pytest.raises(ValueError):
        alice.send("inconnu", "x")
    alice.store.upsert_contact("11111111-1111-4111-8111-111111111111", "Bob")
    with pytest.raises(ValueError):
        alice.send("11111111-1111-4111-8111-111111111111", "   ")


def test_new_address_replaces_stale_connection_attempt(qtbot, make_messenger):
    """Régression : une tentative vers une ancienne adresse ne doit pas bloquer la nouvelle.

    Sous Windows, une connexion refusée met ~2 s à échouer ; ici, un serveur qui
    accepte sans jamais répondre simule une tentative qui traîne.
    """
    black_hole = QTcpServer()
    assert black_hole.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0)
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    alice.store.upsert_contact(bob.user_id, "Bob", host="127.0.0.1", port=black_hole.serverPort())
    record = alice.send(bob.user_id, "Tu es là ?")  # tentative vers l'ancienne adresse
    assert bob.user_id in alice._dialing

    with qtbot.waitSignal(bob.message_added, timeout=2000) as blocker:
        alice.on_peer_seen(bob.user_id, "Bob", "127.0.0.1", bob.port)
    assert blocker.args[0].id == record.id
    black_hole.close()
    black_hole.deleteLater()


JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 100


def test_profile_photos_are_exchanged_and_updated(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    alice.set_avatar(JPEG)  # avant la connexion : envoyée dès qu'elle est prête
    with qtbot.waitSignal(bob.avatar_changed, timeout=TIMEOUT) as blocker:
        alice.add_contact("127.0.0.1", bob.port)
    assert blocker.args == [alice.user_id]
    assert bob.store.contact(alice.user_id).avatar == JPEG
    assert alice.store.contact(bob.user_id).avatar == b""  # Bob n'a pas de photo

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    with qtbot.waitSignal(alice.avatar_changed, timeout=TIMEOUT):
        bob.set_avatar(png)
    assert alice.store.contact(bob.user_id).avatar == png

    with qtbot.waitSignal(bob.avatar_changed, timeout=TIMEOUT):
        alice.set_avatar(b"")  # photo retirée
    assert bob.store.contact(alice.user_id).avatar == b""


def test_invalid_profile_photo_is_refused(make_messenger):
    alice = make_messenger("Alice", start=False)
    with pytest.raises(ValueError):
        alice.set_avatar(b"GIF89a")
    with pytest.raises(ValueError):
        alice.set_avatar(b"\xff\xd8\xff" + b"x" * 40_000)
    assert alice.avatar == b""
