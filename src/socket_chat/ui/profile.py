"""Photo de profil : préparation de l'image et choix dans les préférences."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QWidget

from ..protocol import MAX_AVATAR_SIZE
from .theme import avatar_pixmap

#: Côté de la photo envoyée (pixels) : nette jusqu'à 2× sur un avatar de 64 px.
AVATAR_SIDE = 128
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


def make_avatar(image: QImage) -> bytes:
    """Photo carrée (recadrée au centre), JPEG de moins de 32 Kio ; vide si impossible."""
    if image.isNull():
        return b""
    side = min(image.width(), image.height())
    square = image.copy(
        QRect((image.width() - side) // 2, (image.height() - side) // 2, side, side)
    )
    size = AVATAR_SIDE
    for quality in (88, 75, 60, 45):
        scaled = square.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        flat = QImage(scaled.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)  # transparence (PNG) sur fond blanc
        painter = QPainter(flat)
        painter.drawImage(0, 0, scaled)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        flat.save(buffer, "JPEG", quality)
        buffer.close()
        if 0 < data.size() <= MAX_AVATAR_SIZE:
            return bytes(data.data())
        size = int(size * 0.85)
    return b""


def load_avatar(path: str) -> bytes:
    reader = QImageReader(path)
    reader.setAutoTransform(True)  # photos de téléphone : orientation EXIF respectée
    return make_avatar(reader.read())


class AvatarPicker(QWidget):
    """Aperçu de la photo de profil, avec « Choisir une photo… » et « Retirer »."""

    changed = Signal()
    PREVIEW = 64

    def __init__(self, avatar: bytes, name: str, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.avatar = avatar
        self._name, self._key = name, key
        #: Choix du fichier (remplaçable dans les tests).
        self.ask_path = self._ask_path
        self.preview = QLabel(self)
        self.preview.setFixedSize(self.PREVIEW, self.PREVIEW)
        self.choose_button = QPushButton("Choisir une photo…", self)
        self.choose_button.clicked.connect(self.choose)
        self.remove_button = QPushButton("Retirer", self)
        self.remove_button.clicked.connect(self.remove)
        self.error = QLabel(self)
        self.error.setStyleSheet("color: #c62828;")
        self.error.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview)
        layout.addSpacing(8)
        layout.addWidget(self.choose_button)
        layout.addWidget(self.remove_button)
        layout.addWidget(self.error)
        layout.addStretch(1)
        self._refresh()

    def set_name(self, name: str) -> None:
        self._name = name or self._name
        self._refresh()

    def choose(self) -> None:
        path = self.ask_path()
        if not path:
            return
        avatar = load_avatar(path)
        if not avatar:
            self.error.setText("Image illisible.")
            self.error.show()
            return
        self.error.hide()
        self.avatar = avatar
        self._refresh()
        self.changed.emit()

    def remove(self) -> None:
        self.avatar = b""
        self._refresh()
        self.changed.emit()

    def _ask_path(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Photo de profil", "", IMAGE_FILTER)
        return path

    def _refresh(self) -> None:
        self.preview.setPixmap(
            avatar_pixmap(
                self._name, self._key, self.PREVIEW, self.devicePixelRatioF(), self.avatar
            )
        )
        self.remove_button.setEnabled(bool(self.avatar))
