"""Couleurs et dessins partagés (avatars, coches, formes de bulles).

Les couleurs dérivent de la palette Qt : l'application suit automatiquement le
thème clair ou sombre du système.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

from PySide6.QtCore import QLocale, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)

AVATAR_COLORS = [
    "#1e88e5",
    "#43a047",
    "#e53935",
    "#8e24aa",
    "#fb8c00",
    "#00897b",
    "#3949ab",
    "#d81b60",
    "#6d4c41",
    "#546e7a",
]

ONLINE_COLOR = QColor("#2bb673")
ACCENT = QColor("#2a7ab8")


@dataclass(frozen=True)
class Theme:
    dark: bool
    chat_background: QColor
    incoming: QColor
    incoming_text: QColor
    outgoing: QColor
    outgoing_text: QColor
    meta_incoming: QColor
    meta_outgoing: QColor
    read_tick: QColor
    separator: QColor
    separator_text: QColor
    selection: QColor
    hover: QColor
    muted_text: QColor
    accent: QColor

    @classmethod
    def from_palette(cls, palette: QPalette) -> Theme:
        dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        text = palette.color(QPalette.ColorRole.Text)
        muted = QColor(text)
        muted.setAlphaF(0.6)
        meta_in = QColor(text)
        meta_in.setAlphaF(0.55)
        meta_out = QColor("#ffffff")
        meta_out.setAlphaF(0.75)
        return cls(
            dark=dark,
            chat_background=QColor("#15191e" if dark else "#eef1f5"),
            incoming=QColor("#262c33" if dark else "#ffffff"),
            incoming_text=text,
            outgoing=QColor("#2f6fa3" if dark else "#2a7ab8"),
            outgoing_text=QColor("#ffffff"),
            meta_incoming=meta_in,
            meta_outgoing=meta_out,
            read_tick=QColor("#8fe3ff"),
            separator=QColor("#262c33" if dark else "#dde3ea"),
            separator_text=muted,
            selection=QColor("#24384c" if dark else "#dbe9f6"),
            hover=QColor("#1f252c" if dark else "#f1f4f8"),
            muted_text=muted,
            accent=QColor("#5aa9e6" if dark else "#2a7ab8"),
        )


def current_theme() -> Theme:
    return Theme.from_palette(QGuiApplication.palette())


# -- Avatars --------------------------------------------------------------
def avatar_color(key: str) -> QColor:
    """Couleur stable pour un contact (indépendante de l'exécution)."""
    digest = hashlib.md5(key.encode("utf-8"), usedforsecurity=False).digest()
    return QColor(AVATAR_COLORS[digest[0] % len(AVATAR_COLORS)])


def initials(name: str) -> str:
    words = [w for w in name.replace("-", " ").split() if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper() if len(words[0]) > 1 else words[0][0].upper()
    return (words[0][0] + words[-1][0]).upper()


_PHOTO_CACHE: dict[bytes, QImage] = {}


def avatar_image(data: bytes) -> QImage | None:
    """Photo de profil décodée (mise en cache) ; ``None`` si absente ou illisible."""
    if not data:
        return None
    image = _PHOTO_CACHE.get(data)
    if image is None:
        image = QImage.fromData(data, "JPEG")
        if len(_PHOTO_CACHE) > 200:
            _PHOTO_CACHE.clear()
        _PHOTO_CACHE[data] = image
    return None if image.isNull() else image


def paint_avatar(
    painter: QPainter,
    rect: QRectF,
    name: str,
    key: str,
    *,
    online: bool | None = None,
    ring: QColor | None = None,
    photo: bytes = b"",
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setPen(Qt.PenStyle.NoPen)
    image = avatar_image(photo)
    if image is not None:
        # Photo recadrée au centre, dans le cercle.
        clip = QPainterPath()
        clip.addEllipse(rect)
        painter.save()
        painter.setClipPath(clip)
        side = min(image.width(), image.height())
        source = QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side)
        painter.drawImage(rect, image, source)
        painter.restore()
    else:
        painter.setBrush(avatar_color(key))
        painter.drawEllipse(rect)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(max(8, int(rect.height() * 0.38)))
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials(name))
    if online:
        size = rect.height() * 0.3
        dot = QRectF(rect.right() - size, rect.bottom() - size, size, size)
        painter.setPen(QPen(ring or QColor("#ffffff"), max(1.5, size * 0.18)))
        painter.setBrush(ONLINE_COLOR)
        painter.drawEllipse(dot)
    painter.restore()


def avatar_pixmap(
    name: str, key: str, size: int, device_pixel_ratio: float = 1.0, photo: bytes = b""
) -> QPixmap:
    pixmap = QPixmap(int(size * device_pixel_ratio), int(size * device_pixel_ratio))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_avatar(painter, QRectF(0, 0, size, size), name, key, photo=photo)
    painter.end()
    return pixmap


# -- Formes ---------------------------------------------------------------
def rounded_path(rect: QRectF, tl: float, tr: float, br: float, bl: float) -> QPainterPath:
    """Rectangle aux coins arrondis de rayons différents."""
    path = QPainterPath()
    path.moveTo(rect.left() + tl, rect.top())
    path.lineTo(rect.right() - tr, rect.top())
    path.arcTo(rect.right() - 2 * tr, rect.top(), 2 * tr, 2 * tr, 90, -90)
    path.lineTo(rect.right(), rect.bottom() - br)
    path.arcTo(rect.right() - 2 * br, rect.bottom() - 2 * br, 2 * br, 2 * br, 0, -90)
    path.lineTo(rect.left() + bl, rect.bottom())
    path.arcTo(rect.left(), rect.bottom() - 2 * bl, 2 * bl, 2 * bl, -90, -90)
    path.lineTo(rect.left(), rect.top() + tl)
    path.arcTo(rect.left(), rect.top(), 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


def paint_ticks(painter: QPainter, rect: QRectF, status: str, color: QColor, read: QColor) -> None:
    """Indicateur d'état : horloge (en attente), ✓ (envoyé), ✓✓ (remis), ✓✓ colorées (lu)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    h = rect.height()
    if status == "pending":
        pen = QPen(color, max(1.0, h * 0.1))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = QPointF(rect.left() + h / 2, rect.center().y())
        r = h * 0.38
        painter.drawEllipse(c, r, r)
        painter.drawLine(c, QPointF(c.x(), c.y() - r * 0.6))
        painter.drawLine(c, QPointF(c.x() + r * 0.5, c.y()))
    else:
        pen = QPen(read if status == "read" else color, max(1.2, h * 0.13))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        count = 1 if status == "sent" else 2
        for i in range(count):
            x = rect.left() + i * h * 0.38
            path = QPainterPath(QPointF(x, rect.top() + h * 0.55))
            path.lineTo(x + h * 0.28, rect.top() + h * 0.82)
            path.lineTo(x + h * 0.78, rect.top() + h * 0.22)
            painter.drawPath(path)
    painter.restore()


def ticks_width(height: float) -> float:
    return height * 1.2


# -- Dates ----------------------------------------------------------------
_LOCALE = QLocale(QLocale.Language.French, QLocale.Country.France)


def day_label(day: date, today: date | None = None) -> str:
    today = today or date.today()
    delta = (today - day).days
    if delta == 0:
        return "Aujourd'hui"
    if delta == 1:
        return "Hier"
    fmt = "dddd d MMMM" if day.year == today.year else "d MMMM yyyy"
    text = _LOCALE.toString(day, fmt)
    return text[:1].upper() + text[1:]


def short_time(ts_ms: int, now: datetime | None = None) -> str:
    """Heure pour la liste des contacts : « 14:05 », « Hier », « lun. » ou « 03/11/2026 »."""
    moment = datetime.fromtimestamp(ts_ms / 1000)
    now = now or datetime.now()
    delta = (now.date() - moment.date()).days
    if delta == 0:
        return moment.strftime("%H:%M")
    if delta == 1:
        return "Hier"
    if 1 < delta < 7:
        return _LOCALE.toString(moment.date(), "ddd")
    return moment.strftime("%d/%m/%Y")


def clock(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")


# -- Fichiers -------------------------------------------------------------
_FILE_COLORS = {
    ("pdf",): "#d93025",
    ("doc", "docx", "odt", "rtf", "txt", "md"): "#1a73e8",
    ("xls", "xlsx", "ods", "csv"): "#188038",
    ("ppt", "pptx", "odp"): "#e8710a",
    ("zip", "7z", "rar", "tar", "gz"): "#9c6b00",
    ("png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"): "#8e24aa",
    ("mp3", "wav", "ogg", "flac", "m4a"): "#00897b",
    ("mp4", "avi", "mkv", "mov", "webm"): "#c2185b",
    ("py", "js", "ts", "java", "c", "cpp", "cs", "html", "css", "json", "xml", "sql"): "#455a64",
}


def file_color(extension: str) -> QColor:
    for extensions, color in _FILE_COLORS.items():
        if extension in extensions:
            return QColor(color)
    return QColor("#78909c")


def human_size(size: int) -> str:
    """Taille lisible à la française : « 532 o », « 12 Ko », « 1,4 Mo »."""
    if size < 1024:
        return f"{size} o"
    value = float(size)
    for unit in ("Ko", "Mo", "Go"):
        value /= 1024
        if value < 1024 or unit == "Go":
            text = f"{value:.0f}" if value >= 100 else f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text.replace('.', ',')} {unit}"
    raise AssertionError  # inatteignable
