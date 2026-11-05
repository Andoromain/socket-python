"""Régénère icon.png et icon.ico à partir de icon.svg (PySide6 + Pillow requis)."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "socket_chat" / "resources"


def main() -> None:
    app = QGuiApplication([])  # noqa: F841 - requis par QPainter
    renderer = QSvgRenderer(str(RESOURCES / "icon.svg"))
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    png = RESOURCES / "icon.png"
    image.save(str(png))
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    Image.open(png).save(RESOURCES / "icon.ico", sizes=sizes)
    print("Icônes générées dans", RESOURCES)


if __name__ == "__main__":
    main()
