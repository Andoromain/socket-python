import os

import pytest

# Tests sans affichage (CI, serveurs).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def make_session(qtbot):
    """Crée des ChatSession et les détruit proprement en fin de test.

    Sans cela, des objets Qt créés par Python survivent au test et sont détruits
    après la QApplication, ce qui fait planter l'interpréteur à la sortie.
    """
    from socket_chat.network import ChatSession

    sessions: list[ChatSession] = []

    def factory(name: str) -> ChatSession:
        session = ChatSession(name)
        sessions.append(session)
        return session

    yield factory

    for session in sessions:
        session.stop()
    for session in sessions:
        with qtbot.waitSignal(session.destroyed, timeout=1000):
            session.deleteLater()
