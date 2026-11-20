import os

import pytest
from PySide6.QtGui import QColor, QImage

from socket_chat import transfers as transfers_module
from socket_chat.protocol import Message
from socket_chat.store import ME, FileState, Status, Store
from socket_chat.transfers import make_thumbnail

TIMEOUT = 10_000


@pytest.fixture
def pair(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    with qtbot.waitSignals([alice.presence_changed, bob.presence_changed], timeout=TIMEOUT):
        alice.add_contact("127.0.0.1", bob.port)
    return alice, bob


def state(m, message_id):
    return m.store.message(message_id).file.state


def offer(qtbot, alice, bob, path):
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT) as blocker:
        record = alice.send_file(bob.user_id, path)
    received = blocker.args[0]
    assert received.id == record.id and received.file.state is FileState.OFFERED
    return record


def wait_state(qtbot, m, message_id, expected):
    qtbot.waitUntil(lambda: state(m, message_id) is expected, timeout=TIMEOUT)


@pytest.mark.parametrize("size", [0, 10, 3 * 1024 * 1024 + 123])
def test_file_transfer(qtbot, pair, tmp_path, size):
    alice, bob = pair
    source = tmp_path / "rapport final.bin"
    content = os.urandom(size)
    source.write_bytes(content)
    record = offer(qtbot, alice, bob, source)
    assert record.file.name == "rapport final.bin" and record.file.size == size
    qtbot.waitUntil(
        lambda: alice.store.message(record.id).status is Status.DELIVERED, timeout=TIMEOUT
    )

    progress = []
    bob.file_progress.connect(lambda mid, done, total: progress.append((done, total)))
    destination = tmp_path / "reçu" / "rapport.bin"
    bob.accept_file(record.id, destination)
    assert state(bob, record.id) in (FileState.ACCEPTED, FileState.TRANSFERRING, FileState.DONE)
    wait_state(qtbot, bob, record.id, FileState.DONE)
    wait_state(qtbot, alice, record.id, FileState.DONE)

    assert destination.read_bytes() == content
    assert not destination.with_name("rapport.bin.part").exists()
    assert bob.store.message(record.id).file.path == str(destination)
    assert progress and progress[-1] == (size, size)
    if size > transfers_module.PROGRESS_STEP:
        assert len(progress) > 2  # progression intermédiaire


def test_decline(qtbot, pair, tmp_path):
    alice, bob = pair
    source = tmp_path / "a.txt"
    source.write_text("x")
    record = offer(qtbot, alice, bob, source)
    bob.decline_file(record.id)
    assert state(bob, record.id) is FileState.DECLINED
    wait_state(qtbot, alice, record.id, FileState.DECLINED)
    with pytest.raises(ValueError):
        bob.accept_file(record.id, tmp_path / "b.txt")


@pytest.mark.parametrize("canceller", ["sender", "receiver"])
def test_cancel_during_transfer(qtbot, pair, tmp_path, monkeypatch, canceller):
    monkeypatch.setattr(transfers_module, "PROGRESS_STEP", 1)
    alice, bob = pair
    source = tmp_path / "gros.bin"
    source.write_bytes(os.urandom(40 * 1024 * 1024))
    record = offer(qtbot, alice, bob, source)
    destination = tmp_path / "gros-recu.bin"
    with qtbot.waitSignal(bob.file_progress, timeout=TIMEOUT):
        bob.accept_file(record.id, destination)
    (alice if canceller == "sender" else bob).cancel_file(record.id)
    wait_state(qtbot, alice, record.id, FileState.CANCELLED)
    wait_state(qtbot, bob, record.id, FileState.CANCELLED)
    assert not destination.exists()
    assert not destination.with_name("gros-recu.bin.part").exists()


def test_sender_can_withdraw_an_offer(qtbot, pair, tmp_path):
    alice, bob = pair
    source = tmp_path / "a.txt"
    source.write_text("x")
    record = offer(qtbot, alice, bob, source)
    alice.cancel_file(record.id)
    assert state(alice, record.id) is FileState.CANCELLED
    wait_state(qtbot, bob, record.id, FileState.CANCELLED)


def test_missing_source_file(qtbot, pair, tmp_path):
    alice, bob = pair
    source = tmp_path / "parti.txt"
    source.write_text("bientôt supprimé")
    record = offer(qtbot, alice, bob, source)
    source.unlink()
    bob.accept_file(record.id, tmp_path / "x.txt")
    wait_state(qtbot, alice, record.id, FileState.FAILED)
    wait_state(qtbot, bob, record.id, FileState.CANCELLED)
    assert not (tmp_path / "x.txt.part").exists()


def test_corruption_is_detected(qtbot, pair, tmp_path):
    alice, bob = pair
    source = tmp_path / "a.bin"
    source.write_bytes(os.urandom(5000))
    record = offer(qtbot, alice, bob, source)
    bob.accept_file(record.id, tmp_path / "b.bin")
    # Bloc incohérent injecté avant les vrais : le destinataire doit tout rejeter.
    conn = bob._connections[alice.user_id]
    bob.transfers.handle(conn, alice.user_id, Message.file_chunk(record.id, 999, b"x"))
    assert state(bob, record.id) is FileState.FAILED
    wait_state(qtbot, alice, record.id, FileState.FAILED)
    assert not (tmp_path / "b.bin").exists()


def test_interrupted_transfer_can_be_retried(qtbot, make_messenger, tmp_path, monkeypatch):
    monkeypatch.setattr(transfers_module, "PROGRESS_STEP", 1)
    alice = make_messenger("Alice")
    bob_store = Store(tmp_path / "bob.sqlite3")
    bob = make_messenger("Bob", store=bob_store)
    bob_id = bob.user_id
    with qtbot.waitSignals([alice.presence_changed, bob.presence_changed], timeout=TIMEOUT):
        alice.add_contact("127.0.0.1", bob.port)
    source = tmp_path / "gros.bin"
    content = os.urandom(40 * 1024 * 1024)
    source.write_bytes(content)
    record = offer(qtbot, alice, bob, source)
    destination = tmp_path / "recu.bin"
    with qtbot.waitSignal(bob.file_progress, timeout=TIMEOUT):
        bob.accept_file(record.id, destination)

    bob.stop()  # coupure en plein transfert
    assert state(bob, record.id) is FileState.FAILED
    wait_state(qtbot, alice, record.id, FileState.FAILED)

    bob2 = make_messenger("Bob", user_id=bob_id, store=bob_store, identity=bob.identity)
    with qtbot.waitSignal(bob2.presence_changed, timeout=TIMEOUT):
        alice.on_peer_seen(bob_id, "Bob", "127.0.0.1", bob2.port)
        alice.connect_to(bob_id)
    bob2.accept_file(record.id, destination)
    wait_state(qtbot, bob2, record.id, FileState.DONE)
    assert destination.read_bytes() == content


def test_accept_requires_sender_online(qtbot, pair, tmp_path):
    alice, bob = pair
    source = tmp_path / "a.txt"
    source.write_text("x")
    record = offer(qtbot, alice, bob, source)
    with qtbot.waitSignal(bob.presence_changed, timeout=TIMEOUT):
        alice.stop()
    with pytest.raises(ValueError, match="pas connecté"):
        bob.accept_file(record.id, tmp_path / "b.txt")


def test_send_file_validation(make_messenger, tmp_path):
    alice = make_messenger("Alice")
    alice.store.upsert_contact("11111111-1111-4111-8111-111111111111", "Bob")
    with pytest.raises(ValueError):
        alice.send_file("11111111-1111-4111-8111-111111111111", tmp_path / "absent.txt")
    with pytest.raises(ValueError):
        alice.send_file("11111111-1111-4111-8111-111111111111", tmp_path)  # un dossier
    with pytest.raises(ValueError):
        alice.send_file("inconnu", tmp_path / "absent.txt")


def test_image_offer_carries_a_thumbnail(qtbot, pair, tmp_path):
    alice, bob = pair
    image = QImage(1600, 1200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2a7ab8"))
    source = tmp_path / "capture.png"
    assert image.save(str(source))
    received = offer(qtbot, alice, bob, source)
    stored = bob.store.message(received.id)
    assert stored.file.is_image
    thumb = QImage.fromData(stored.file.thumb)
    assert not thumb.isNull() and max(thumb.width(), thumb.height()) <= 360


def test_make_thumbnail_ignores_non_images(tmp_path):
    text = tmp_path / "a.txt"
    text.write_text("x")
    assert make_thumbnail(text) == b""
    fake = tmp_path / "faux.png"
    fake.write_text("pas une image")
    assert make_thumbnail(fake) == b""


def test_reactions_are_shared(qtbot, pair):
    alice, bob = pair
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT):
        record = alice.send(bob.user_id, "Réunion à 14 h ?")

    with qtbot.waitSignal(alice.message_changed, timeout=TIMEOUT) as blocker:
        bob.react(record.id, "👍")
    assert blocker.args == [record.id]
    assert alice.store.message(record.id).reaction_of(bob.user_id) == "👍"
    assert bob.store.message(record.id).reaction_of(ME) == "👍"

    with qtbot.waitSignal(alice.message_changed, timeout=TIMEOUT):
        bob.react(record.id, "")
    assert alice.store.message(record.id).reactions == ()


def test_offline_reaction_is_sent_on_reconnection(qtbot, make_messenger, tmp_path):
    alice = make_messenger("Alice")
    bob_store = Store(tmp_path / "bob.sqlite3")
    bob = make_messenger("Bob", store=bob_store)
    bob_id = bob.user_id
    with qtbot.waitSignals([alice.presence_changed, bob.presence_changed], timeout=TIMEOUT):
        alice.add_contact("127.0.0.1", bob.port)
    with qtbot.waitSignal(bob.message_added, timeout=TIMEOUT):
        record = alice.send(bob.user_id, "Coucou")
    with qtbot.waitSignal(bob.presence_changed, timeout=TIMEOUT):
        alice.stop()
    alice.start(0)

    bob.react(record.id, "❤️")  # Alice est injoignable : réaction mise de côté
    with qtbot.waitSignal(alice.message_changed, timeout=TIMEOUT):
        bob.on_peer_seen(alice.user_id, "Alice", "127.0.0.1", alice.port)
        bob.connect_to(alice.user_id)
    assert alice.store.message(record.id).reaction_of(bob_id) == "❤️"
