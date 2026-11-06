"""Photo de profil côté PC : préparation de l'image, préférences, dessin et envoi."""

import pytest
from PySide6.QtCore import QRectF, QSettings
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QMessageBox

from socket_chat.protocol import MAX_AVATAR_SIZE, is_avatar_image
from socket_chat.settings import Settings
from socket_chat.transfers import AVATAR_SIDE, make_avatar
from socket_chat.ui.dialogs import SettingsDialog
from socket_chat.ui.main_window import MainWindow
from socket_chat.ui.theme import avatar_color, paint_avatar

TIMEOUT = 5000


def photo_file(tmp_path, width=1600, height=900, color="#c0392b"):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    path = tmp_path / "photo.png"
    assert image.save(str(path))
    return path


def test_make_avatar_crops_and_compresses(tmp_path):
    data = make_avatar(photo_file(tmp_path))
    assert is_avatar_image(data) and data.startswith(b"\xff\xd8\xff")
    assert len(data) <= MAX_AVATAR_SIZE
    image = QImage.fromData(data)
    assert image.width() == image.height() <= AVATAR_SIDE  # carré
    noisy = QImage(3000, 3000, QImage.Format.Format_RGB32)
    for x in range(0, 3000, 7):  # image très détaillée : la qualité baisse pour tenir
        for y in range(0, 3000, 13):
            noisy.setPixelColor(x, y, QColor((x * 7) % 256, (y * 3) % 256, (x + y) % 256))
    noisy.save(str(tmp_path / "bruit.png"))
    assert len(make_avatar(tmp_path / "bruit.png")) <= MAX_AVATAR_SIZE


def test_make_avatar_refuses_non_images(tmp_path):
    (tmp_path / "a.txt").write_text("pas une image")
    with pytest.raises(ValueError):
        make_avatar(tmp_path / "a.txt")


def test_settings_keep_the_avatar(tmp_path):
    ini = str(tmp_path / "s.ini")
    data = make_avatar(photo_file(tmp_path))
    Settings(QSettings(ini, QSettings.Format.IniFormat)).avatar = data
    assert Settings(QSettings(ini, QSettings.Format.IniFormat)).avatar == data
    Settings(QSettings(ini, QSettings.Format.IniFormat)).avatar = b""
    assert Settings(QSettings(ini, QSettings.Format.IniFormat)).avatar == b""


def center_color(photo: bytes) -> QColor:
    canvas = QImage(64, 64, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ffffff"))
    painter = QPainter(canvas)
    paint_avatar(painter, QRectF(0, 0, 64, 64), "Alice Martin", "cle", photo=photo)
    painter.end()
    # En haut du cercle : hors des initiales (centrées), quelle que soit la police disponible.
    return canvas.pixelColor(32, 6)


def test_paint_avatar_uses_the_photo(qtbot, tmp_path):
    red = center_color(make_avatar(photo_file(tmp_path, color="#c0392b")))
    assert red.red() > 150 and red.green() < 100
    assert center_color(b"") == avatar_color("cle")  # sans photo : fond coloré
    assert center_color(b"\xff\xd8\xffcorrompu") == avatar_color("cle")  # illisible


def test_settings_dialog_choose_and_remove_photo(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = SettingsDialog("Alice", 12800, user_id="cle")
    qtbot.addWidget(dialog)
    assert dialog.avatar == b"" and not dialog.remove_photo.isEnabled()
    assert dialog.set_photo_from_file(str(photo_file(tmp_path)))
    assert is_avatar_image(dialog.avatar) and dialog.remove_photo.isEnabled()
    (tmp_path / "faux.png").write_text("non")
    previous = dialog.avatar
    assert not dialog.set_photo_from_file(str(tmp_path / "faux.png"))
    assert dialog.avatar == previous  # inchangée après un échec
    dialog.remove_photo.click()
    assert dialog.avatar == b""


def test_photo_from_preferences_reaches_the_contact(qtbot, tmp_path, make_messenger):
    def window(name):
        settings = Settings(QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat))
        settings.name = name
        w = MainWindow(settings, make_messenger(name))
        qtbot.addWidget(w)
        return w

    alice, bob = window("Alice"), window("Bob")
    with qtbot.waitSignals(
        [alice.messenger.presence_changed, bob.messenger.presence_changed], timeout=TIMEOUT
    ):
        alice.messenger.add_contact("127.0.0.1", bob.messenger.port)

    data = make_avatar(photo_file(tmp_path))

    def accept_with_photo(dialog):
        dialog._avatar = data
        return SettingsDialog.DialogCode.Accepted

    import socket_chat.ui.main_window as main_window_module

    original = main_window_module.SettingsDialog.exec
    main_window_module.SettingsDialog.exec = accept_with_photo
    try:
        with qtbot.waitSignal(bob.messenger.avatar_changed, timeout=TIMEOUT):
            alice.edit_settings()
    finally:
        main_window_module.SettingsDialog.exec = original
    assert alice.settings.avatar == data
    assert bob.messenger.store.contact(alice.messenger.user_id).avatar == data
