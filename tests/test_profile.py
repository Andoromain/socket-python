"""Photo de profil : préparation, envoi aux contacts, préférences, affichage."""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QMessageBox

from socket_chat.protocol import JPEG_MAGIC, MAX_AVATAR_SIZE
from socket_chat.settings import Settings
from socket_chat.ui.main_window import MainWindow
from socket_chat.ui.profile import AVATAR_SIDE, load_avatar, make_avatar
from socket_chat.ui.theme import avatar_image, avatar_pixmap

TIMEOUT = 5000


def sample_image(width=1200, height=800, alpha=False) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0) if alpha else QColor("#3366cc"))
    painter = QPainter(image)
    for i in range(0, width, 7):  # du détail pour que le JPEG ne soit pas trivial
        painter.setPen(QColor((i * 37) % 256, (i * 91) % 256, (i * 53) % 256))
        painter.drawLine(i, 0, width - i, height)
    painter.end()
    return image


def test_make_avatar_is_a_small_square_jpeg(tmp_path):
    data = make_avatar(sample_image())
    assert data.startswith(JPEG_MAGIC) and 0 < len(data) <= MAX_AVATAR_SIZE
    image = QImage.fromData(data)
    assert image.width() == image.height() <= AVATAR_SIDE
    assert make_avatar(QImage()) == b""
    path = tmp_path / "photo.png"
    assert sample_image(400, 900, alpha=True).save(str(path))
    assert load_avatar(str(path)).startswith(JPEG_MAGIC)
    assert load_avatar(str(tmp_path / "absente.png")) == b""


def test_avatar_is_sent_and_updated(qtbot, make_messenger):
    alice, bob = make_messenger("Alice"), make_messenger("Bob")
    photo = make_avatar(sample_image())
    alice.set_avatar(photo)  # avant la connexion : envoyée à la présentation
    with qtbot.waitSignals([alice.presence_changed, bob.presence_changed], timeout=TIMEOUT):
        alice.add_contact("127.0.0.1", bob.port)
    qtbot.waitUntil(lambda: bob.store.contact(alice.user_id).avatar == photo, timeout=TIMEOUT)
    assert alice.store.contact(bob.user_id).avatar == b""  # Bob n'en a pas

    other = make_avatar(sample_image(300, 300))
    with qtbot.waitSignal(bob.contacts_changed, timeout=TIMEOUT):
        alice.set_avatar(other)  # pendant la connexion
    qtbot.waitUntil(lambda: bob.store.contact(alice.user_id).avatar == other, timeout=TIMEOUT)
    alice.set_avatar(b"")  # retirée
    qtbot.waitUntil(lambda: bob.store.contact(alice.user_id).avatar == b"", timeout=TIMEOUT)


def test_settings_avatar_roundtrip(tmp_path):
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert settings.avatar == b""
    photo = make_avatar(sample_image())
    settings.avatar = photo
    assert Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)).avatar == photo
    settings.avatar = b""
    assert settings.avatar == b""


def test_preferences_choose_and_remove_photo(qtbot, tmp_path, make_messenger, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.name = "Alice"
    window = MainWindow(settings, make_messenger("Alice"))
    qtbot.addWidget(window)
    path = tmp_path / "moi.jpg"
    assert sample_image().save(str(path))

    def choose(dialog):
        picker = dialog.avatar_picker
        assert not picker.remove_button.isEnabled()
        picker.ask_path = lambda: str(path)
        picker.choose_button.click()
        assert picker.remove_button.isEnabled()
        return dialog.DialogCode.Accepted

    window.exec_dialog = choose
    assert window.edit_settings()
    assert settings.avatar.startswith(JPEG_MAGIC)
    assert window.messenger.avatar == settings.avatar

    def remove(dialog):
        dialog.avatar_picker.remove_button.click()
        return dialog.DialogCode.Accepted

    window.exec_dialog = remove
    assert window.edit_settings()
    assert settings.avatar == b"" and window.messenger.avatar == b""


def test_unreadable_photo_is_reported(qtbot, tmp_path):
    from socket_chat.ui.profile import AvatarPicker

    picker = AvatarPicker(b"", "Alice", "id")
    qtbot.addWidget(picker)
    bad = tmp_path / "faux.jpg"
    bad.write_bytes(b"pas une image")
    picker.ask_path = lambda: str(bad)
    picker.choose()
    assert picker.avatar == b"" and picker.error.text() == "Image illisible."


def test_avatar_pixmap_draws_the_photo(qapp):
    red = QImage(64, 64, QImage.Format.Format_RGB32)
    red.fill(QColor("#ff0000"))
    photo = make_avatar(red)
    assert avatar_image(photo) is not None and avatar_image(b"\xff\xd8\xffxx") is None
    pixmap = avatar_pixmap("Alice", "id", 40, 1.0, photo).toImage()
    center = QColor(pixmap.pixel(20, 20))
    assert center.red() > 200 and center.green() < 60  # la photo, pas les initiales
    assert QColor.fromRgba(pixmap.pixel(0, 0)).alpha() == 0  # coin hors du cercle : transparent
