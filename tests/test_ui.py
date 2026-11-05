from datetime import date, datetime, timedelta

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QIcon

from socket_chat.discovery import Discovery
from socket_chat.protocol import new_id
from socket_chat.settings import Settings
from socket_chat.store import MessageRecord, Status, Store
from socket_chat.ui.main_window import MainWindow
from socket_chat.ui.models import DayItem, MessageItem, MessageListModel, Role
from socket_chat.ui.theme import day_label, initials, short_time
from socket_chat.ui.tray import icon_with_badge

TIMEOUT = 5000
CONTACT = "11111111-1111-4111-8111-111111111111"


def record(i, *, outgoing=True, ts):
    return MessageRecord(
        f"00000000-0000-4000-8000-{i:012d}", CONTACT, outgoing, f"m{i}", ts, Status.SENT
    )


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@pytest.fixture
def settings(tmp_path):
    def make(name):
        store = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
        s = Settings(store)
        s.name = name
        return s

    return make


# -- Préférences ----------------------------------------------------------
def test_settings_roundtrip(settings):
    s = settings("Testeur")
    s.port = 4000
    s.remember_host("a")
    s.remember_host("b")
    s.remember_host("a")
    assert s.port == 4000
    assert s.recent_hosts == ["a", "b"]
    assert s.user_id == s.user_id  # stable une fois créé
    assert s.close_to_tray
    with pytest.raises(ValueError):
        s.name = ""


# -- Mise en forme --------------------------------------------------------
def test_initials_and_labels():
    assert initials("Alice Martin") == "AM"
    assert initials("jean-pierre durand") == "JD"
    assert initials("Zoé") == "ZO"
    assert initials("  ") == "?"
    today = date(2026, 11, 5)
    assert day_label(today, today) == "Aujourd'hui"
    assert day_label(today - timedelta(days=1), today) == "Hier"
    assert day_label(date(2026, 11, 2), today) == "Lundi 2 novembre"
    assert day_label(date(2025, 3, 1), today) == "1 mars 2025"
    now = datetime(2026, 11, 5, 15, 0)
    assert short_time(ms(datetime(2026, 11, 5, 9, 7)), now) == "09:07"
    assert short_time(ms(datetime(2026, 11, 4, 9, 7)), now) == "Hier"
    assert short_time(ms(datetime(2026, 10, 1, 9, 7)), now) == "01/10/2026"


def test_tray_badge_icon(qapp):
    from socket_chat.app import icon_path

    base = QIcon(icon_path())
    assert icon_with_badge(base, 0) is base
    assert not icon_with_badge(base, 3).pixmap(32, 32).isNull()


# -- Modèle des messages --------------------------------------------------
def test_message_model_groups_and_day_separators(qapp):
    model = MessageListModel()
    day1 = datetime(2026, 11, 4, 10, 0)
    day2 = datetime(2026, 11, 5, 10, 0)
    model.load(
        CONTACT,
        [
            record(1, ts=ms(day1)),
            record(2, ts=ms(day1 + timedelta(minutes=1))),  # groupé avec 1
            record(3, outgoing=False, ts=ms(day1 + timedelta(minutes=2))),  # autre expéditeur
            record(
                4, outgoing=False, ts=ms(day1 + timedelta(minutes=30))
            ),  # trop tard : nouveau groupe
            record(5, ts=ms(day2)),  # nouveau jour
        ],
    )
    items = [model.item(row) for row in range(model.rowCount())]
    kinds = ["day" if isinstance(i, DayItem) else i.record.text for i in items]
    assert kinds == ["day", "m1", "m2", "m3", "m4", "day", "m5"]
    flags = {
        i.record.text: (i.first_in_group, i.last_in_group)
        for i in items
        if isinstance(i, MessageItem)
    }
    assert flags == {
        "m1": (True, False),
        "m2": (False, True),
        "m3": (True, True),
        "m4": (True, True),
        "m5": (True, True),
    }

    # Ajout en direct : regroupé avec le précédent, qui perd son statut de dernier.
    model.append(record(6, ts=ms(day2 + timedelta(minutes=1))))
    assert model.item(model.rowCount() - 2).last_in_group is False
    assert model.item(model.rowCount() - 1).first_in_group is False
    model.append(record(6, ts=ms(day2)))  # doublon ignoré
    assert model.rowCount() == 8

    model.update_status(record(6, ts=0).id, "read")
    assert model.item(7).record.status is Status.READ
    assert model.index(7).data(Role.ID) == record(6, ts=0).id


# -- Fenêtre complète -----------------------------------------------------
@pytest.fixture
def window_pair(qtbot, settings, make_messenger):
    windows = []
    discoveries = []

    def make(name):
        messenger = make_messenger(name)
        discovery = Discovery(messenger.user_id, name, messenger.port, udp_port=0, targets=[])
        assert discovery.start()
        messenger.attach_discovery(discovery)
        window = MainWindow(settings(name), messenger, discovery)
        qtbot.addWidget(window)
        window.show()
        windows.append(window)
        discoveries.append(discovery)
        return window

    alice, bob = make("Alice"), make("Bob")
    alice.discovery.targets = [("127.0.0.1", bob.discovery.local_port)]
    bob.discovery.targets = [("127.0.0.1", alice.discovery.local_port)]
    yield alice, bob
    for d in discoveries:
        d.stop()
        d.deleteLater()


def test_empty_state(qtbot, window_pair):
    alice, _bob = window_pair
    assert alice.empty_contacts.isVisible()
    assert not alice.contact_list.isVisible()
    assert alice.conversation.stack.currentIndex() == 0


def test_two_windows_discover_and_chat(qtbot, window_pair):
    alice, bob = window_pair
    with qtbot.waitSignals(
        [alice.messenger.presence_changed, bob.messenger.presence_changed], timeout=TIMEOUT
    ):
        alice.discovery.announce()
    qtbot.waitUntil(lambda: alice.contacts.rowCount() == 1, timeout=TIMEOUT)
    assert alice.contact_list.isVisible()
    assert alice.contacts.index(0).data(Qt.ItemDataRole.DisplayRole) == "Bob"
    assert alice.contacts.index(0).data(Role.ONLINE)

    alice.open_conversation(bob.messenger.user_id)
    assert alice.conversation.contact_id == bob.messenger.user_id
    assert alice.conversation.name_label.text() == "Bob"

    # Envoi au clavier ; le texte n'est jamais interprété comme du HTML.
    alice.conversation.input.setPlainText("<b>pas du HTML</b>")
    notified = []
    bob.notify.connect(lambda *args: notified.append(args))
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT):
        qtbot.keyClick(alice.conversation.input, Qt.Key.Key_Return)
    assert alice.conversation.input.toPlainText() == ""
    assert alice.conversation.model.rowCount() == 2  # séparateur du jour + message

    # Bob n'a pas la conversation ouverte : notification et compteur de non-lus.
    qtbot.waitUntil(lambda: bob.windowTitle() == "(1) Socket Chat", timeout=TIMEOUT)
    assert notified and notified[0][1:] == ("Alice", "<b>pas du HTML</b>")
    summary = bob.contacts.index(0).data(Role.SUMMARY)
    assert summary.unread == 1 and summary.last_message.text == "<b>pas du HTML</b>"

    # En ouvrant la conversation (fenêtre active), Bob lit : Alice voit « lu ».
    bob.open_conversation(alice.messenger.user_id)
    bob.messenger.mark_read(alice.messenger.user_id)
    message_id = summary.last_message.id
    qtbot.waitUntil(
        lambda: alice.messenger.store.message(message_id).status is Status.READ,
        timeout=TIMEOUT,
    )
    qtbot.waitUntil(lambda: bob.windowTitle() == "Socket Chat", timeout=TIMEOUT)
    alice_item = alice.conversation.model.item(1)
    assert alice_item.record.status is Status.READ

    # « écrit… » apparaît dans l'en-tête d'Alice quand Bob tape.
    bob.conversation.input.setPlainText("je réponds")
    qtbot.waitUntil(lambda: alice.conversation.status_label.text() == "écrit…", timeout=TIMEOUT)


def test_close_to_tray_hides_instead_of_closing(qtbot, window_pair):
    alice, _bob = window_pair
    alice.close_to_tray = True
    hidden = []
    alice.hidden_to_tray.connect(lambda: hidden.append(True))
    alice.close()
    assert hidden and not alice.isVisible()
    quit_signals = []
    alice.quit_requested.connect(lambda: quit_signals.append(True))
    alice.show()
    alice.request_quit()
    assert quit_signals and not alice.isVisible()


def test_history_is_reloaded(qtbot, settings, make_messenger, tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    store.upsert_contact(CONTACT, "Claire")
    store.add_message(record(1, outgoing=False, ts=ms(datetime.now())))
    messenger = make_messenger("Alice", user_id=new_id(), store=store)
    window = MainWindow(settings("Alice"), messenger)
    qtbot.addWidget(window)
    assert window.contacts.rowCount() == 1
    assert window.windowTitle() == "(1) Socket Chat"
    window.open_conversation(CONTACT)
    assert window.conversation.model.rowCount() == 2
