"""Interface des appels sur PC : bouton 📞, fenêtre d'appel, sonnerie, historique."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QLabel
from test_calls import FakeEngine

from socket_chat.calls import CallManager, CallState
from socket_chat.settings import Settings
from socket_chat.store import CallOutcome
from socket_chat.ui.main_window import MainWindow

TIMEOUT = 5000


@pytest.fixture
def windows(qtbot, tmp_path, make_messenger):
    FakeEngine.instances.clear()

    def make(name):
        settings = Settings(QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat))
        settings.name = name
        messenger = make_messenger(name)
        rings = []
        calls = CallManager(messenger, FakeEngine, parent=messenger)
        window = MainWindow(
            settings,
            messenger,
            calls=calls,
            ring=rings.append,
            video_view=lambda parent: QLabel("image", parent),
        )
        window.rings = rings
        qtbot.addWidget(window)
        window.show()
        return window

    alice, bob = make("Alice"), make("Bob")
    with qtbot.waitSignals(
        [alice.messenger.presence_changed, bob.messenger.presence_changed], timeout=TIMEOUT
    ):
        alice.messenger.add_contact("127.0.0.1", bob.messenger.port)
    qtbot.waitUntil(lambda: alice.contacts.rowCount() == 1, timeout=TIMEOUT)
    alice.open_conversation(bob.messenger.user_id)
    return alice, bob


def test_call_from_conversation_header(qtbot, windows):
    alice, bob = windows
    button = alice.conversation.call_button
    assert button.isVisible() and button.isEnabled()

    with qtbot.waitSignal(bob.calls.incoming_call, timeout=TIMEOUT):
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    incoming = bob.call_window
    assert incoming.isVisible() and incoming.answer_button.isVisible()
    assert incoming.name_label.text() == "Alice"
    assert bob.rings == [True]  # sonnerie

    qtbot.mouseClick(incoming.answer_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bob.calls.call and bob.calls.call.state is CallState.ACTIVE)
    assert bob.rings == [True, False]
    assert incoming.hangup_button.isVisible() and not incoming.answer_button.isVisible()
    assert alice.call_window.isVisible()

    qtbot.mouseClick(alice.call_window.mute_button, Qt.MouseButton.LeftButton)
    assert alice.calls.call.muted and "micro coupé" in alice.call_window.status_label.text()

    with qtbot.waitSignal(bob.calls.call_ended, timeout=TIMEOUT):
        qtbot.mouseClick(alice.call_window.hangup_button, Qt.MouseButton.LeftButton)
    assert not alice.call_window.isVisible()
    qtbot.waitUntil(lambda: not bob.call_window.isVisible())
    model = alice.conversation.model
    last = model.item(model.rowCount() - 1).record
    assert last.call.outcome is CallOutcome.COMPLETED
    assert model.data(model.index(model.rowCount() - 1)).startswith("📞 Appel audio · ")


def test_decline_and_close_window(qtbot, windows):
    alice, bob = windows
    with qtbot.waitSignal(bob.calls.incoming_call, timeout=TIMEOUT):
        alice.start_call(bob.messenger.user_id)
    with qtbot.waitSignal(alice.calls.call_ended, timeout=TIMEOUT) as blocker:
        qtbot.mouseClick(bob.call_window.decline_button, Qt.MouseButton.LeftButton)
    assert blocker.args[1] == CallOutcome.DECLINED.value

    with qtbot.waitSignal(bob.calls.incoming_call, timeout=TIMEOUT):
        alice.start_call(bob.messenger.user_id)
    with qtbot.waitSignal(bob.calls.call_ended, timeout=TIMEOUT):
        alice.call_window.close()  # fermer la fenêtre raccroche
    assert alice.calls.call is None


def test_missed_call_is_notified_but_not_completed_calls(qtbot, windows):
    alice, bob = windows
    notified = []
    bob.notify.connect(lambda *args: notified.append(args[2]))
    with qtbot.waitSignal(bob.calls.incoming_call, timeout=TIMEOUT):
        alice.start_call(bob.messenger.user_id)
    with qtbot.waitSignal(bob.calls.call_ended, timeout=TIMEOUT):
        alice.calls.hangup()
    assert notified == ["📞 Appel audio entrant", "📞 Appel audio manqué"]


def test_call_button_disabled_when_offline(qtbot, windows):
    alice, bob = windows
    with qtbot.waitSignal(alice.messenger.presence_changed, timeout=TIMEOUT):
        bob.messenger.stop()
    qtbot.waitUntil(lambda: not alice.conversation.call_button.isEnabled(), timeout=TIMEOUT)


def test_no_call_button_without_webengine(qtbot, tmp_path, make_messenger):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    window = MainWindow(settings, make_messenger("Solo"))
    qtbot.addWidget(window)
    assert window.call_window is None and not window.conversation.call_button.isVisibleTo(window)


def test_video_call_window(qtbot, windows):
    alice, bob = windows
    assert alice.conversation.video_button.isVisible()
    with qtbot.waitSignal(bob.calls.incoming_call, timeout=TIMEOUT):
        qtbot.mouseClick(alice.conversation.video_button, Qt.MouseButton.LeftButton)
    incoming = bob.call_window
    assert incoming.status_label.text() == "Appel vidéo entrant"
    assert incoming.video_view is None and incoming.avatar.isVisible()

    qtbot.mouseClick(incoming.answer_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bob.calls.call and bob.calls.call.state is CallState.ACTIVE)
    window = alice.call_window
    qtbot.waitUntil(lambda: window.video_view is not None and window.video_view.isVisible())
    assert not window.avatar.isVisible() and window.camera_button.isVisible()
    qtbot.mouseClick(window.camera_button, Qt.MouseButton.LeftButton)
    assert alice.calls.call.camera_off and "caméra coupée" in window.status_label.text()

    with qtbot.waitSignal(bob.calls.call_ended, timeout=TIMEOUT):
        qtbot.mouseClick(window.hangup_button, Qt.MouseButton.LeftButton)
    assert not window.isVisible() and not window.camera_button.isChecked()
    model = alice.conversation.model
    assert model.data(model.index(model.rowCount() - 1)).startswith("🎥 Appel vidéo · ")
