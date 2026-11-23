"""Signalisation des appels entre deux postes (moteur média simulé, sans micro)."""

from typing import ClassVar

import pytest
from PySide6.QtCore import QTimer

from socket_chat import calls as calls_module
from socket_chat.calls import CallManager, CallState, MediaEngine
from socket_chat.store import CallOutcome

TIMEOUT = 5000


class FakeEngine(MediaEngine):
    """Échange de descriptions simulé : connecté dès que l'offre et la réponse sont passées."""

    instances: ClassVar[list["FakeEngine"]] = []

    def __init__(self):
        super().__init__()
        self.remote_ice: list[tuple[str, str, int]] = []
        self.muted = False
        self.camera = True
        self.stopped = False
        FakeEngine.instances.append(self)

    def _later(self, ms, emit):
        """Émission différée, annulée si le moteur est arrêté ou détruit entre-temps
        (CallManager détruit le moteur d'un appel terminé par deleteLater)."""

        def fire():
            if not self.stopped:
                emit()

        QTimer.singleShot(ms, self, fire)  # contexte : minuteur annulé avec le moteur

    def start(self, caller, media):
        self.media = media
        if caller:
            self._later(0, lambda: self.local_description.emit("offer", "v=0 offre"))
        self._later(
            0, lambda: self.local_ice.emit("candidate:1 1 udp 1 10.0.0.1 1 typ host", "0", 0)
        )

    def set_remote_description(self, kind, sdp):
        if kind == "offer":
            self._later(0, lambda: self.local_description.emit("answer", "v=0 réponse"))
        self._later(10, self.connected.emit)

    def add_remote_ice(self, candidate, mid, index):
        self.remote_ice.append((candidate, mid, index))

    def set_camera_enabled(self, on):
        self.camera = on

    def set_muted(self, muted):
        self.muted = muted

    def stop(self):
        self.stopped = True


@pytest.fixture
def phones(qtbot, make_messenger):
    FakeEngine.instances.clear()

    def make(name):
        messenger = make_messenger(name)
        return CallManager(messenger, FakeEngine, parent=messenger)

    alice, bob = make("Alice"), make("Bob")
    a, b = alice.parent(), bob.parent()
    with qtbot.waitSignals([a.presence_changed, b.presence_changed], timeout=TIMEOUT):
        a.add_contact("127.0.0.1", b.port)
    return alice, bob


def ids(manager):
    return manager.parent().user_id


def last_call_record(manager, contact_id):
    records = [m for m in manager.parent().store.messages(contact_id) if m.call is not None]
    return records[-1]


def test_complete_call(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT) as blocker:
        alice.start_call(ids(bob))
    assert blocker.args == [ids(alice), "audio"]
    qtbot.waitUntil(lambda: alice.call.state is CallState.RINGING_OUT, timeout=TIMEOUT)

    bob.accept()
    qtbot.waitUntil(
        lambda: alice.call and bob.call and alice.call.state is bob.call.state is CallState.ACTIVE,
        timeout=TIMEOUT,
    )
    engines = FakeEngine.instances
    assert len(engines) == 2 and all(e.remote_ice for e in engines)  # candidats relayés

    alice.set_muted(True)
    assert alice.call.engine.muted
    with qtbot.waitSignal(bob.call_ended, timeout=TIMEOUT) as blocker:
        alice.hangup()
    assert blocker.args == [ids(alice), CallOutcome.COMPLETED.value]
    assert alice.call is None and bob.call is None
    assert all(e.stopped for e in engines)
    mine = last_call_record(alice, ids(bob))
    assert mine.outgoing and mine.call.outcome is CallOutcome.COMPLETED and not mine.unread
    theirs = last_call_record(bob, ids(alice))
    assert not theirs.outgoing and theirs.call.outcome is CallOutcome.COMPLETED
    assert theirs.preview.startswith("📞 Appel audio · ")


def test_declined(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    with qtbot.waitSignal(alice.call_ended, timeout=TIMEOUT) as blocker:
        bob.decline()
    assert blocker.args[1] == CallOutcome.DECLINED.value
    assert last_call_record(bob, ids(alice)).call.outcome is CallOutcome.DECLINED
    assert FakeEngine.instances == []  # aucun micro ouvert


def test_caller_hangs_up_while_ringing_is_a_missed_call(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    with qtbot.waitSignal(bob.call_ended, timeout=TIMEOUT) as blocker:
        alice.hangup()
    assert blocker.args[1] == CallOutcome.MISSED.value
    assert last_call_record(alice, ids(bob)).call.outcome is CallOutcome.CANCELLED
    missed = last_call_record(bob, ids(alice))
    assert missed.unread and missed.preview == "📞 Appel audio manqué"


def test_ring_timeout(qtbot, phones, monkeypatch):
    monkeypatch.setattr(calls_module, "RING_TIMEOUT_MS", 200)
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    with qtbot.waitSignals([alice.call_ended, bob.call_ended], timeout=TIMEOUT):
        pass
    assert last_call_record(alice, ids(bob)).call.outcome is CallOutcome.NO_ANSWER
    assert last_call_record(bob, ids(alice)).call.outcome is CallOutcome.MISSED


def test_busy(qtbot, make_messenger, phones):
    alice, bob = phones
    carol_messenger = make_messenger("Carol")
    carol = CallManager(carol_messenger, FakeEngine, parent=carol_messenger)
    with qtbot.waitSignals(
        [carol_messenger.presence_changed, bob.parent().presence_changed], timeout=TIMEOUT
    ):
        carol_messenger.add_contact("127.0.0.1", bob.parent().port)
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    with qtbot.waitSignal(carol.call_ended, timeout=TIMEOUT) as blocker:
        carol.start_call(ids(bob))
    assert blocker.args[1] == CallOutcome.BUSY.value
    assert bob.call.contact_id == ids(alice)  # l'appel en cours n'est pas dérangé


def test_contact_without_calls_support(qtbot, phones, monkeypatch):
    monkeypatch.setattr(calls_module, "RINGING_ACK_TIMEOUT_MS", 200)
    alice, bob = phones
    bob.parent().call_frame.disconnect(bob._on_frame)  # version antérieure : trames ignorées
    with qtbot.waitSignal(alice.call_ended, timeout=TIMEOUT) as blocker:
        alice.start_call(ids(bob))
    assert blocker.args[1] == CallOutcome.UNSUPPORTED.value
    assert "mettre Socket Chat à jour" in last_call_record(alice, ids(bob)).preview


def test_connection_lost_during_call(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    bob.accept()
    qtbot.waitUntil(lambda: alice.call and alice.call.state is CallState.ACTIVE, timeout=TIMEOUT)
    with qtbot.waitSignal(alice.call_ended, timeout=TIMEOUT) as blocker:
        bob.parent().stop()
    assert blocker.args[1] == CallOutcome.COMPLETED.value


def test_media_failure(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT):
        alice.start_call(ids(bob))
    bob.accept()
    qtbot.waitUntil(lambda: len(FakeEngine.instances) == 2, timeout=TIMEOUT)
    errors = []
    alice.error.connect(errors.append)
    with qtbot.waitSignal(bob.call_ended, timeout=TIMEOUT):
        alice.call.engine.failed.emit("micro introuvable")
    assert errors == ["micro introuvable"]
    assert alice.call is None and bob.call is None


def test_crossed_calls_converge(qtbot, phones):
    alice, bob = phones
    alice.start_call(ids(bob))
    bob.start_call(ids(alice))
    qtbot.waitUntil(
        lambda: (
            alice.call is not None
            and bob.call is not None
            and alice.call.id == bob.call.id
            and {alice.call.state, bob.call.state} == {CallState.RINGING_OUT, CallState.INCOMING}
        ),
        timeout=TIMEOUT,
    )
    keeper = min(ids(alice), ids(bob))
    caller = alice if ids(alice) == keeper else bob
    assert caller.call.outgoing


def test_start_call_errors(qtbot, phones, make_messenger):
    alice, bob = phones
    with pytest.raises(ValueError, match="pas en ligne"):
        alice.start_call("11111111-1111-4111-8111-111111111111")
    alice.start_call(ids(bob))
    with pytest.raises(ValueError, match="déjà en cours"):
        alice.start_call(ids(bob))


def test_fake_engine_deleted_with_pending_emits(qtbot):
    """Un moteur détruit (fin d'appel) n'émet plus rien, même s'il avait des émissions
    en attente : sinon « Signal source has been deleted » sur une machine lente."""
    engine = FakeEngine()
    engine.start(True, "audio")
    engine.set_remote_description("offer", "v=0")
    engine.stop()
    engine.deleteLater()  # comme CallManager._finish
    qtbot.wait(50)


def test_video_call_and_camera(qtbot, phones):
    alice, bob = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT) as blocker:
        call = alice.start_call(ids(bob), "video")
    assert blocker.args[1] == "video" and call.video
    alice.set_camera_enabled(False)  # avant le décroché : appliqué au moteur à son démarrage
    bob.accept()
    qtbot.waitUntil(
        lambda: alice.call is not None and alice.call.state is CallState.ACTIVE, timeout=TIMEOUT
    )
    engine = alice.call.engine
    assert engine.media == "video" and engine.camera is False
    alice.set_camera_enabled(True)
    assert engine.camera and not alice.call.camera_off
    with qtbot.waitSignal(bob.call_ended, timeout=TIMEOUT):
        alice.hangup()
    assert last_call_record(alice, ids(bob)).display_text.startswith("🎥 Appel vidéo · ")


def test_camera_ignored_for_audio_calls(qtbot, phones):
    alice, bob = phones
    alice.start_call(ids(bob))
    alice.set_camera_enabled(False)
    assert not alice.call.camera_off
    alice.hangup()


# -- Collègue seulement détecté (aucune connexion encore ouverte) ---------------------------
@pytest.fixture
def discovered(qtbot, make_messenger):
    """Alice voit Bob par la détection (point vert), sans avoir encore échangé avec lui."""
    FakeEngine.instances.clear()
    a, b = make_messenger("Alice"), make_messenger("Bob")
    alice = CallManager(a, FakeEngine, parent=a)
    bob = CallManager(b, FakeEngine, parent=b)
    a.on_peer_seen(b.user_id, "Bob", "127.0.0.1", b.port)
    assert a.is_online(b.user_id) and not a.is_connected(b.user_id)
    return alice, bob


def test_call_to_discovered_contact_connects_first(qtbot, discovered):
    """Régression : « Bob n'est pas en ligne : appel impossible » alors qu'il l'était."""
    alice, bob = discovered
    with qtbot.waitSignal(bob.incoming_call, timeout=TIMEOUT) as blocker:
        call = alice.start_call(ids(bob))
    assert call.state is CallState.CALLING and blocker.args == [ids(alice), "audio"]
    qtbot.waitUntil(lambda: alice.call.state is CallState.RINGING_OUT, timeout=TIMEOUT)
    bob.accept()
    qtbot.waitUntil(lambda: alice.call and alice.call.state is CallState.ACTIVE, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: bob.call and bob.call.state is CallState.ACTIVE, timeout=TIMEOUT)
    alice.hangup()
    qtbot.waitUntil(lambda: alice.call is None and bob.call is None, timeout=TIMEOUT)


def test_call_to_unreachable_contact_fails_with_message(qtbot, make_messenger, monkeypatch):
    monkeypatch.setattr(calls_module, "DIAL_TIMEOUT_MS", 300)
    a = make_messenger("Alice")
    alice = CallManager(a, FakeEngine, parent=a)
    ghost = "22222222-2222-4222-8222-222222222222"
    a.on_peer_seen(ghost, "Fantôme", "127.0.0.1", 1)  # port fermé : connexion impossible
    errors, ended = [], []
    alice.error.connect(errors.append)
    alice.call_ended.connect(lambda *args: ended.append(args))
    alice.start_call(ghost)
    qtbot.waitUntil(lambda: alice.call is None, timeout=TIMEOUT)
    assert ended == [(ghost, CallOutcome.FAILED.value)]
    assert errors and "injoignable" in errors[0]
