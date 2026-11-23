"""Emojis, réactions et fichiers dans l'interface."""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, QRectF, QSettings, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from socket_chat.settings import Settings
from socket_chat.store import ME, FileInfo, FileState, MessageRecord, Status
from socket_chat.ui.delegates import file_actions, file_status
from socket_chat.ui.emoji import EmojiPicker, ReactionBar, convert_shortcuts, emoji_count, search
from socket_chat.ui.main_window import MainWindow
from socket_chat.ui.models import MessageItem, Role
from socket_chat.ui.theme import human_size

TIMEOUT = 10_000
C = "11111111-1111-4111-8111-111111111111"


# -- Fonctions pures ----------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Merci :)", "Merci 🙂"),
        (":D génial <3", "😃 génial ❤️"),
        ("http://exemple.fr:8080/", "http://exemple.fr:8080/"),
        ("a:)b", "a:)b"),
        ("(y) ok ;)", "👍 ok 😉"),
        ("Rien à convertir", "Rien à convertir"),
    ],
)
def test_convert_shortcuts(text, expected):
    assert convert_shortcuts(text) == expected


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("🎉", 1),
        ("👍🏽", 1),  # avec teinte de peau
        ("❤️ ❤️", 2),
        ("🧑‍💻", 1),  # séquence ZWJ
        ("🇫🇷", 1),  # drapeau
        ("😀😃😄😁", 4),
        ("ok 👍", 0),
        ("", 0),
        ("123", 0),
    ],
)
def test_emoji_count(text, count):
    assert emoji_count(text) == count


def test_search_is_accent_insensitive():
    assert "☕" in search("café")
    assert "🙏" in search("MERCI")
    assert "👍" in search("d'accord") or "👍" in search("accord")
    assert search("zzzzzz") == []


def test_human_size():
    assert human_size(0) == "0 o"
    assert human_size(1023) == "1023 o"
    assert human_size(1536) == "1,5 Ko"
    assert human_size(80_800) == "78,9 Ko"
    assert human_size(2_400_000) == "2,3 Mo"
    assert human_size(250 * 1024**2) == "250 Mo"
    assert human_size(3 * 1024**3) == "3 Go"


def _file_record(outgoing, state):
    return MessageRecord(
        "m", C, outgoing, "", 0, Status.SENT, FileInfo("a.pdf", 2048, state, "/x/a.pdf")
    )


def test_file_actions_and_status():
    incoming_offer = _file_record(False, FileState.OFFERED)
    assert [a for a, _ in file_actions(incoming_offer)] == ["accept", "decline"]
    assert file_status(incoming_offer, None) == "2 Ko · à accepter"
    transferring = _file_record(False, FileState.TRANSFERRING)
    assert file_status(transferring, (1024, 2048)) == "50 % · 1 Ko sur 2 Ko"
    assert [a for a, _ in file_actions(_file_record(False, FileState.DONE))] == ["open", "folder"]
    assert [a for a, _ in file_actions(_file_record(False, FileState.FAILED))] == ["retry"]
    assert [a for a, _ in file_actions(_file_record(True, FileState.DONE))] == ["open"]
    assert file_actions(_file_record(True, FileState.DECLINED)) == []
    assert file_status(_file_record(True, FileState.DECLINED), None) == "Refusé par le destinataire"


def test_recent_emojis(tmp_path):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    for emoji in ["👍", "❤️", "👍"]:
        settings.remember_emoji(emoji)
    assert settings.recent_emojis == ["👍", "❤️"]


def test_emoji_picker_and_reaction_bar(qtbot):
    picker = EmojiPicker(["🔥"])
    qtbot.addWidget(picker)
    assert picker.grid.item(0).text() == "🔥"  # les récents d'abord
    picker.search_edit.setText("cafe")
    assert "☕" in [picker.grid.item(i).text() for i in range(picker.grid.count())]
    with qtbot.waitSignal(picker.picked) as blocker:
        picker._on_item_clicked(picker.grid.item(0))
    assert blocker.args == ["☕"]

    bar = ReactionBar(current="👍")
    qtbot.addWidget(bar)
    picked = []
    bar.picked.connect(picked.append)
    bar._pick("")  # recliquer sur sa réaction la retire
    assert picked == [""]


# -- Fenêtres réelles -----------------------------------------------------------
@pytest.fixture
def windows(qtbot, tmp_path, make_messenger, monkeypatch):
    # Les erreurs s'affichent dans une boîte modale qui bloquerait le test.
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)

    def make(name):
        settings = Settings(QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat))
        settings.name = name
        window = MainWindow(settings, make_messenger(name))
        window.conversation.outbox_dir = tmp_path / f"envois-{name}"
        qtbot.addWidget(window)
        window.resize(900, 700)
        window.show()
        return window

    alice, bob = make("Alice"), make("Bob")
    with qtbot.waitSignals(
        [alice.messenger.presence_changed, bob.messenger.presence_changed], timeout=TIMEOUT
    ):
        alice.messenger.add_contact("127.0.0.1", bob.messenger.port)
    qtbot.waitUntil(lambda: alice.contacts.rowCount() == 1 and bob.contacts.rowCount() == 1)
    alice.open_conversation(bob.messenger.user_id)
    bob.open_conversation(alice.messenger.user_id)
    return alice, bob


def last_record(window):
    model = window.conversation.model
    return model.item(model.rowCount() - 1).record


def test_shortcuts_converted_and_emoji_inserted(qtbot, windows):
    alice, bob = windows
    alice.conversation._insert_emoji("🎉")
    assert alice.conversation.input.toPlainText() == "🎉"
    assert alice.settings.recent_emojis == ["🎉"]
    alice.conversation.input.setPlainText("Bravo :D")
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT) as blocker:
        alice.conversation.send()
    assert blocker.args[0].text == "Bravo 😃"


def test_pasted_screenshot_is_sent_as_image(qtbot, windows):
    alice, bob = windows
    image = QImage(800, 500, QImage.Format.Format_RGB32)
    image.fill(QColor("orange"))
    mime = QMimeData()
    mime.setImageData(image)
    assert alice.conversation.input.canInsertFromMimeData(mime)
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT) as blocker:
        alice.conversation.input.insertFromMimeData(mime)
    received = blocker.args[0]
    assert received.file.name.startswith("capture-") and received.file.is_image
    assert alice.conversation.input.toPlainText() == ""  # rien de collé dans le texte
    assert last_record(bob).file.is_image


def test_dropped_files_offer_then_accept_from_bubble(qtbot, windows, tmp_path):
    alice, bob = windows
    source = tmp_path / "notes.txt"
    source.write_text("contenu des notes")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT) as blocker:
        alice.conversation.input.insertFromMimeData(mime)
    message_id = blocker.args[0].id

    # Le bouton « Accepter » est bien dessiné là où le clic est testé.
    view = bob.conversation.list
    row = bob.conversation.model.row_of(message_id)
    index = bob.conversation.model.index(row)
    rect = QRectF(view.visualRect(index))
    item = index.data(Role.ITEM)
    assert isinstance(item, MessageItem)
    layout = view.bubbles._layout(item.record)
    accept_rect = next(r for action, _l, r in layout.buttons if action == "accept")
    origin = view.bubbles._content_origin(view.bubbles.bubble_rect(rect, item))
    hit = view.bubbles.hit(rect, index, origin + accept_rect.center(), hovered=False)
    assert (hit.kind, hit.value) == ("action", "accept")

    destination = tmp_path / "recu" / "notes.txt"
    bob.conversation.ask_save_path = lambda default: str(destination)
    with qtbot.waitSignal(view.action_requested, timeout=TIMEOUT):
        view.action_requested.emit(message_id, "accept")
    qtbot.waitUntil(
        lambda: bob.messenger.store.message(message_id).file.state is FileState.DONE,
        timeout=TIMEOUT,
    )
    assert destination.read_text() == "contenu des notes"
    qtbot.waitUntil(lambda: last_record(bob).file.state is FileState.DONE, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: last_record(alice).file.state is FileState.DONE, timeout=TIMEOUT)


def test_reactions_from_the_interface(qtbot, windows):
    alice, bob = windows
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT):
        alice.messenger.send(bob.messenger.user_id, "On déjeune ensemble ?")
    record = last_record(bob)

    with qtbot.waitSignal(alice.messenger.message_changed, timeout=TIMEOUT):
        bob.conversation.list.react_with.emit(record.id, "👍")
    assert last_record(bob).reaction_of(ME) == "👍"
    qtbot.waitUntil(
        lambda: last_record(alice).reaction_of(bob.messenger.user_id) == "👍", timeout=TIMEOUT
    )
    # La pastille apparaît sous la bulle et agrandit la ligne.
    view = alice.conversation.list
    index = alice.conversation.model.index(alice.conversation.model.rowCount() - 1)
    assert view.bubbles.pill_rect(QRectF(view.visualRect(index)), index.data(Role.ITEM))

    # Cliquer sur la pastille d'une réaction reçue ajoute la même réaction.
    with qtbot.waitSignal(bob.messenger.message_changed, timeout=TIMEOUT):
        alice.conversation.handle_action(record.id, "pill")
    assert last_record(alice).reaction_of(ME) == "👍"
    # Et la recliquer la retire.
    alice.conversation.handle_action(record.id, "pill")
    assert last_record(alice).reaction_of(ME) == ""


def test_hover_shows_react_button(qtbot, windows):
    alice, bob = windows
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT):
        alice.messenger.send(bob.messenger.user_id, "Survole-moi")
    view = bob.conversation.list
    index = bob.conversation.model.index(bob.conversation.model.rowCount() - 1)
    rect = QRectF(view.visualRect(index))
    button = view.bubbles.react_button_rect(rect, index.data(Role.ITEM))
    assert view.bubbles.hit(rect, index, button.center(), hovered=False) is None
    view.hovered_row = index.row()
    assert view.bubbles.hit(rect, index, button.center(), hovered=True).kind == "react"
    assert view.bubbles.hit(rect, index, QPointF(-50, -50), hovered=True) is None


def test_open_missing_file_reports_error(qtbot, windows, tmp_path):
    alice, bob = windows
    source = tmp_path / "temp.txt"
    source.write_text("x")
    with qtbot.waitSignal(bob.messenger.message_added, timeout=TIMEOUT):
        record = alice.messenger.send_file(bob.messenger.user_id, source)
    Path(source).unlink()
    errors = []
    alice.conversation.message_error.connect(errors.append)
    alice.conversation.handle_action(record.id, "open")
    assert errors and "introuvable" in errors[0]


# -- Sécurité -----------------------------------------------------------------
def test_safety_number_is_the_same_on_both_sides(qtbot, windows):
    alice, bob = windows
    dialogs = []
    for window in (alice, bob):
        window.conversation.open_dialog = dialogs.append
        window.conversation.lock_button.click()
    assert len(dialogs) == 2
    assert dialogs[0].number is not None and dialogs[0].number == dialogs[1].number
    assert len(dialogs[0].number.split()) == 12


def test_changed_key_banner_then_accept(qtbot, windows, make_messenger):
    from socket_chat.noise import KeyPair

    alice, bob = windows
    conv = alice.conversation
    bob_id = bob.messenger.user_id
    assert conv.changed_banner.isHidden()
    with qtbot.waitSignal(alice.messenger.presence_changed, timeout=TIMEOUT):
        bob.messenger.stop()
    other = make_messenger("Bob", user_id=bob_id, identity=KeyPair.generate())
    with qtbot.waitSignal(alice.messenger.key_changed, timeout=TIMEOUT):
        other.add_contact("127.0.0.1", alice.messenger.port)
    qtbot.waitUntil(lambda: conv.changed_banner.isVisible(), timeout=TIMEOUT)
    assert "a changé" in conv.changed_banner.label.text()

    questions = []
    conv.confirm = lambda title, text: questions.append(text) or False
    conv.changed_banner.accept_button.click()
    assert len(questions) == 1 and "Nouveau code de sécurité" in questions[0]
    assert alice.messenger.store.contact(bob_id).pending_key is not None  # refusé

    conv.confirm = lambda title, text: True
    alice.messenger.store.upsert_contact(bob_id, "Bob", host="127.0.0.1", port=other.port)
    with qtbot.waitSignal(alice.messenger.presence_changed, timeout=TIMEOUT):
        conv.changed_banner.accept_button.click()
    qtbot.waitUntil(lambda: conv.changed_banner.isHidden(), timeout=TIMEOUT)
    assert alice.messenger.contact_key(bob_id) == other.identity.public


def test_refused_key_banner(qtbot, windows):
    alice, _bob = windows
    conv = alice.conversation
    contact = conv.contact_id
    alice.messenger.key_refused.emit(contact, True)
    assert conv.refused_banner.isVisible()
    assert "ne reconnaît pas encore" in conv.refused_banner.label.text()
    alice.messenger.key_refused.emit(contact, False)
    assert conv.refused_banner.isHidden()
