import pytest
from PySide6.QtCore import QSettings, Qt

from socket_chat.network import SessionState
from socket_chat.settings import Settings
from socket_chat.ui.main_window import MainWindow


@pytest.fixture
def settings(tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    s = Settings(store)
    s.name = "Testeur"
    return s


def test_settings_roundtrip(settings):
    settings.port = 4000
    settings.remember_host("a")
    settings.remember_host("b")
    settings.remember_host("a")
    assert settings.port == 4000
    assert settings.recent_hosts == ["a", "b"]
    with pytest.raises(ValueError):
        settings.name = ""


def test_window_idle_state(qtbot, settings):
    window = MainWindow(settings)
    qtbot.addWidget(window)
    assert not window.input.isEnabled()
    assert window.host_action.isEnabled()
    assert not window.stop_action.isEnabled()


def test_two_windows_can_chat(qtbot, settings, tmp_path):
    host = MainWindow(settings)
    qtbot.addWidget(host)
    host.session.host(0)
    assert host.session.state == SessionState.LISTENING
    assert host.stop_action.isEnabled()

    store = QSettings(str(tmp_path / "guest.ini"), QSettings.Format.IniFormat)
    guest_settings = Settings(store)
    guest_settings.name = "Invité"
    guest = MainWindow(guest_settings)
    qtbot.addWidget(guest)
    with qtbot.waitSignal(host.session.peer_joined, timeout=5000):
        guest.session.join("127.0.0.1", host.session.listening_port)
    qtbot.waitUntil(lambda: guest.input.isEnabled(), timeout=5000)

    guest.input.setPlainText("<b>pas du HTML</b>")
    with qtbot.waitSignal(host.session.message_received, timeout=5000):
        qtbot.keyClick(guest.input, Qt.Key.Key_Return)
    assert guest.input.toPlainText() == ""
    assert "<b>pas du HTML</b>" in host.transcript.toPlainText()
    assert "Invité" in host.windowTitle()
    guest.session.stop()
    host.session.stop()
