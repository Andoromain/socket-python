"""Appel audio de bout en bout entre deux postes : vrai WebRTC (Qt WebEngine), faux micros.

Exige Qt WebEngine (paquet PySide6-WebEngine). Ignoré si WebEngine ne peut pas démarrer
sur la machine, sauf si SOCKET_CHAT_REQUIRE_WEBENGINE est défini (CI Linux).
"""

import os

import pytest

pytest.importorskip("PySide6.QtWebEngineCore")

from socket_chat.call_engine import CallHost, WebEngineCallEngine
from socket_chat.calls import CallManager, CallState
from socket_chat.store import CallOutcome

TIMEOUT = 30_000


def wait_ready(qtbot, host):
    host.ensure_page()
    try:
        qtbot.waitUntil(lambda: host.ready, timeout=TIMEOUT)
    except AssertionError:
        if os.environ.get("SOCKET_CHAT_REQUIRE_WEBENGINE"):
            raise
        pytest.skip("Qt WebEngine ne démarre pas sur cette machine")


@pytest.fixture
def phones(qtbot, make_messenger):
    def make(name):
        messenger = make_messenger(name)
        host = CallHost(messenger)
        wait_ready(qtbot, host)
        manager = CallManager(messenger, lambda h=host: WebEngineCallEngine(h), parent=messenger)
        return manager, host

    (alice, alice_host), (bob, bob_host) = make("Alice"), make("Bob")
    a, b = alice.parent(), bob.parent()
    with qtbot.waitSignals([a.presence_changed, b.presence_changed], timeout=5000):
        a.add_contact("127.0.0.1", b.port)
    return alice, alice_host, bob, bob_host


def inbound_bytes(qtbot, host):
    with qtbot.waitSignal(host.bridge.inbound_bytes, timeout=5000) as blocker:
        host.send("statsRequested")
    return blocker.args[0]


def test_real_audio_call_between_two_pcs(qtbot, phones):
    alice, alice_host, bob, bob_host = phones
    with qtbot.waitSignal(bob.incoming_call, timeout=5000):
        alice.start_call(bob.parent().user_id)
    bob.accept()
    qtbot.waitUntil(
        lambda: (
            alice.call is not None
            and bob.call is not None
            and alice.call.state is CallState.ACTIVE
            and bob.call.state is CallState.ACTIVE
        ),
        timeout=TIMEOUT,
    )
    # Le son circule dans les deux sens (faux micro de Chromium : un bip régulier).
    qtbot.waitUntil(
        lambda: inbound_bytes(qtbot, alice_host) > 2000 and inbound_bytes(qtbot, bob_host) > 2000,
        timeout=TIMEOUT,
    )
    alice.set_muted(True)
    with qtbot.waitSignal(bob.call_ended, timeout=5000) as blocker:
        alice.hangup()
    assert blocker.args[1] == CallOutcome.COMPLETED.value

    # La page est réutilisée pour l'appel suivant.
    with qtbot.waitSignal(alice.incoming_call, timeout=5000):
        bob.start_call(alice.parent().user_id)
    alice.accept()
    qtbot.waitUntil(
        lambda: bob.call is not None and bob.call.state is CallState.ACTIVE, timeout=TIMEOUT
    )
    bob.hangup()


def test_ringtone_starts_and_stops(qtbot, phones):
    _alice, alice_host, _bob, _bob_host = phones
    alice_host.ring(True)  # pas d'exception côté page : vérifié par l'absence d'échec
    alice_host.ring(False)
    assert inbound_bytes(qtbot, alice_host) == 0
