import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QTextBrowser
from update_keys import sign_manifest

from socket_chat import __version__
from socket_chat.settings import Settings
from socket_chat.ui.main_window import MainWindow
from socket_chat.ui.updates import MANUAL_UPDATE_HELP, UpdateBanner, UpdateController
from socket_chat.updater import DEFAULT_UPDATE_URL, UpdateChecker

TIMEOUT = 10_000


def make_repo(root: Path, version: str, payload: bytes = b"archive") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    name = f"socket-chat-{version}-linux-x86_64.tar.gz"
    (root / name).write_bytes(payload)
    manifest = {
        "version": version,
        "published": "2026-11-05",
        "notes": "### Ajouté\n- Mise à jour en un clic",
        "assets": {
            "linux-x86_64": {
                "name": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    (root / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sign_manifest(root / "latest.json")
    return root


@pytest.fixture
def setup(qtbot, tmp_path, make_messenger, monkeypatch):
    # Téléchargements dans le dossier du test plutôt que dans le /tmp du système.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.name = "Alice"
    window = MainWindow(settings, make_messenger("Alice"))
    qtbot.addWidget(window)
    banner = UpdateBanner(window)
    window.add_banner(banner)
    repo = make_repo(tmp_path / "depot", "99.0.0")
    checker = UpdateChecker(str(repo), __version__)
    controller = UpdateController(settings, window.messenger, banner, window, checker=checker)
    controller.self_update = True
    controller.platform = "linux-x86_64"
    messages = []
    controller.inform = lambda title, text: messages.append(text)
    controller.confirm = lambda title, text: True
    installed = []
    controller.installer = installed.append
    window.show()
    return window, banner, controller, messages, installed, repo


def check(qtbot, controller, *, manual=False, signal="update_available"):
    with qtbot.waitSignal(getattr(controller.checker, signal), timeout=TIMEOUT):
        controller.checker.check_now(manual=manual)


def test_default_source_and_settings(tmp_path):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert settings.update_url == DEFAULT_UPDATE_URL
    assert settings.update_enabled


def test_banner_shows_new_version_and_notifies_once(qtbot, setup):
    window, banner, controller, _messages, _installed, _repo = setup
    assert banner.isHidden()
    notified = []
    controller.notify.connect(lambda title, text: notified.append(text))
    check(qtbot, controller)
    assert banner.isVisible() and "99.0.0" in banner.label.text()
    assert banner.update_button.text() == "Mettre à jour"
    # Le bandeau est au-dessus des panneaux de la fenêtre.
    assert window.banner_area.indexOf(banner) == 0
    check(qtbot, controller)
    assert len(notified) == 1  # une seule notification par version


def test_later_and_skip(qtbot, setup):
    _window, banner, controller, _messages, _installed, _repo = setup
    check(qtbot, controller)
    banner.later_button.click()
    assert banner.isHidden()
    check(qtbot, controller)
    assert banner.isVisible()  # « Plus tard » : reproposée à la vérification suivante

    banner.skip_button.click()
    assert banner.isHidden() and controller.settings.skipped_version == "99.0.0"
    check(qtbot, controller)
    assert banner.isHidden()  # version ignorée lors des vérifications automatiques
    check(qtbot, controller, manual=True)
    assert banner.isVisible()  # mais une recherche manuelle la montre


def test_update_downloads_verifies_installs_and_quits(qtbot, setup):
    _window, banner, controller, _messages, installed, _repo = setup
    check(qtbot, controller)
    with qtbot.waitSignal(controller.quit_requested, timeout=TIMEOUT):
        banner.update_button.click()
    assert len(installed) == 1
    path = Path(installed[0])
    assert path.name == "socket-chat-99.0.0-linux-x86_64.tar.gz"
    assert path.read_bytes() == b"archive"
    assert "redémarrer" in banner.label.text()


def test_install_failure_is_shown(qtbot, setup):
    _window, banner, controller, _messages, _installed, _repo = setup

    def broken(path):
        raise OSError("droits insuffisants")

    controller.installer = broken
    check(qtbot, controller)
    quit_signals = []
    controller.quit_requested.connect(lambda: quit_signals.append(True))
    with qtbot.waitSignal(controller.downloader.finished, timeout=TIMEOUT):
        banner.update_button.click()
    assert "droits insuffisants" in banner.label.text()
    assert banner.update_button.text() == "Réessayer" and not quit_signals


def test_corrupted_download_is_refused(qtbot, setup):
    _window, banner, controller, _messages, installed, repo = setup
    check(qtbot, controller)
    # Même taille que l'original : seule l'empreinte permet de détecter la substitution.
    (repo / "socket-chat-99.0.0-linux-x86_64.tar.gz").write_bytes(b"ARCHIVE")
    with qtbot.waitSignal(controller.downloader.failed, timeout=TIMEOUT):
        banner.update_button.click()
    assert installed == [] and "⚠" in banner.label.text()


def test_pip_installs_get_instructions(qtbot, setup):
    _window, banner, controller, messages, installed, _repo = setup
    controller.self_update = False
    check(qtbot, controller)
    assert banner.update_button.text() == "Comment mettre à jour ?"
    banner.update_button.click()
    assert messages == [MANUAL_UPDATE_HELP] and installed == []


def test_unsupported_platform(qtbot, setup):
    _window, banner, controller, _messages, installed, _repo = setup
    controller.platform = "windows-x64"  # absent du manifeste de test
    check(qtbot, controller)
    banner.update_button.click()
    assert "votre système" in banner.label.text() and installed == []


def test_busy_transfers_ask_for_confirmation(qtbot, setup, monkeypatch):
    _window, banner, controller, _messages, installed, _repo = setup
    monkeypatch.setattr(type(controller.messenger.transfers), "active_count", property(lambda s: 2))
    asked = []
    controller.confirm = lambda title, text: asked.append(text) or False
    check(qtbot, controller)
    banner.update_button.click()
    assert asked and "2 transfert(s)" in asked[0]
    assert not controller.downloader.running and installed == []


def test_manual_check_feedback(qtbot, setup, tmp_path):
    _window, _banner, controller, messages, _installed, _repo = setup
    old_repo = make_repo(tmp_path / "ancien", "0.0.1")
    controller.checker.url = QUrl.fromLocalFile(str(old_repo / "latest.json")).toString()
    check(qtbot, controller, manual=True, signal="up_to_date")
    assert messages and "dernière version" in messages[-1]
    controller.checker.url = QUrl.fromLocalFile(str(tmp_path / "absent" / "latest.json")).toString()
    check(qtbot, controller, manual=True, signal="failed")
    assert "Impossible de vérifier" in messages[-1]
    check(qtbot, controller, manual=False, signal="failed")
    assert len(messages) == 2  # silencieux lors des vérifications automatiques


def test_notes_dialog(qtbot, setup):
    _window, banner, controller, _messages, _installed, _repo = setup
    check(qtbot, controller)
    banner.notes_button.click()
    browser = controller.notes_dialog.findChild(QTextBrowser)
    assert "Mise à jour en un clic" in browser.toPlainText()
    controller.notes_dialog.close()


def test_auto_install_option(qtbot, setup):
    """socket-chat --update : installation sans clic dès qu'une version est trouvée."""
    _window, _banner, controller, _messages, installed, _repo = setup
    controller.auto_install = True
    with qtbot.waitSignal(controller.quit_requested, timeout=TIMEOUT):
        controller.checker.check_now()
    assert len(installed) == 1 and not controller.auto_install
