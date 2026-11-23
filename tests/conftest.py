import os

import pytest

# Tests sans affichage (CI, serveurs).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Appels (Qt WebEngine) : faux micro et autorisation automatique, sans bac à sable
# (indisponible dans les conteneurs de CI).
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--use-fake-device-for-media-stream --use-fake-ui-for-media-stream",
)
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")


def pytest_configure(config):
    # Qt WebEngine exige ce réglage avant la création de l'application Qt.
    from PySide6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


@pytest.fixture(autouse=True)
def update_key(monkeypatch):
    """Remplace la clé publique de l'application par celle des tests (update_keys.py)."""
    from update_keys import PUBLIC_KEY

    from socket_chat import signing

    monkeypatch.setattr(signing, "TRUSTED_KEYS", (PUBLIC_KEY,))


@pytest.fixture
def make_messenger(qtbot):
    """Crée des Messenger démarrés (port libre) et les détruit en fin de test.

    Sans destruction explicite, des objets Qt créés par Python survivent au test
    et sont détruits après la QApplication.
    """
    from socket_chat.messenger import Messenger
    from socket_chat.protocol import new_id
    from socket_chat.store import Store

    created = []

    def factory(name, *, user_id=None, store=None, start=True, identity=None):
        messenger = Messenger(store or Store(), user_id or new_id(), name, identity)
        created.append(messenger)
        if start:
            assert messenger.start(0)
        return messenger

    yield factory

    for messenger in created:
        messenger.stop()
    qtbot.wait(20)
    for messenger in created:
        with qtbot.waitSignal(messenger.destroyed, timeout=1000):
            messenger.deleteLater()
