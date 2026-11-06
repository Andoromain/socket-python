"""Dessin des bulles de messages et des lignes de contacts."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..store import ME, ContactSummary, FileInfo, FileState, MessageRecord
from .emoji import emoji_count, emoji_font
from .models import DayItem, MessageItem, Role
from .theme import (
    Theme,
    clock,
    current_theme,
    day_label,
    file_color,
    human_size,
    paint_avatar,
    paint_ticks,
    rounded_path,
    short_time,
    ticks_width,
)

# Dimensions des bulles (pixels logiques).
SIDE_MARGIN = 14
PAD_H = 11
PAD_V = 7
RADIUS = 15
TAIL_RADIUS = 4
GROUP_GAP = 2
BLOCK_GAP = 9
META_GAP = 10
MAX_WIDTH_RATIO = 0.72
MIN_BUBBLE_WIDTH = 220
FILE_WIDTH = 290
IMAGE_MAX_HEIGHT = 240
ICON_SIZE = 40
BUTTON_HEIGHT = 28
PILL_HEIGHT = 24
PILL_SPACE = 20  # hauteur ajoutée sous une bulle portant des réactions
REACT_BUTTON = 28

#: Actions proposées selon le sens et l'état du fichier.
_ACTIONS = {
    (False, FileState.OFFERED): [("accept", "Accepter"), ("decline", "Refuser")],
    (False, FileState.ACCEPTED): [("cancel", "Annuler")],
    (False, FileState.TRANSFERRING): [("cancel", "Annuler")],
    (False, FileState.DONE): [("open", "Ouvrir"), ("folder", "Dossier")],
    (False, FileState.FAILED): [("retry", "Réessayer")],
    (True, FileState.OFFERED): [("cancel", "Annuler")],
    (True, FileState.ACCEPTED): [("cancel", "Annuler")],
    (True, FileState.TRANSFERRING): [("cancel", "Annuler")],
}
_PRIMARY = {"accept", "open", "retry"}


def file_status(record: MessageRecord, progress: tuple[int, int] | None) -> str:
    """Ligne d'état sous le nom d'un fichier."""
    f = record.file
    assert f is not None
    size = human_size(f.size)
    out = record.outgoing
    match f.state:
        case FileState.OFFERED:
            return f"{size} · en attente d'acceptation" if out else f"{size} · à accepter"
        case FileState.ACCEPTED:
            return f"{size} · accepté, démarrage…"
        case FileState.TRANSFERRING:
            done, total = progress or (0, f.size)
            percent = int(done * 100 / total) if total else 100
            return f"{percent} % · {human_size(done)} sur {size}"
        case FileState.DONE:
            return f"{size} · envoyé" if out else f"{size} · reçu"
        case FileState.DECLINED:
            return "Refusé par le destinataire" if out else "Refusé"
        case FileState.CANCELLED:
            return "Annulé"
        case _:
            return "Échec du transfert"


def file_actions(record: MessageRecord) -> list[tuple[str, str]]:
    f = record.file
    if f is None:
        return []
    actions = _ACTIONS.get((record.outgoing, f.state), [])
    if record.outgoing and f.state is FileState.DONE and f.path:
        actions = [("open", "Ouvrir")]
    return actions


@dataclass
class _Layout:
    """Mise en page d'une bulle ; rectangles relatifs au coin de la zone de contenu."""

    width: float
    height: float
    doc: QTextDocument | None = None
    meta_width: float = 0.0
    jumbo: bool = False
    image: QRectF | None = None
    card: QRectF | None = None
    progress: QRectF | None = None
    buttons: list[tuple[str, str, QRectF]] = field(default_factory=list)


@dataclass(frozen=True)
class Hit:
    kind: str  # "action", "react", "pill" ou "bubble"
    value: str = ""


class BubbleDelegate(QStyledItemDelegate):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        self._cache: dict[tuple, _Layout] = {}
        self._thumbs: dict[str, QImage] = {}

    # -- Polices et mesures --------------------------------------------
    def _font(self) -> QFont:
        return emoji_font(self._view.font())

    def _meta_font(self) -> QFont:
        font = QFont(self._view.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.78))
        return font

    def _small_font(self) -> QFont:
        font = QFont(self._view.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        return font

    def _meta_size(self, record: MessageRecord) -> tuple[float, float]:
        metrics = QFontMetricsF(self._meta_font())
        width = metrics.horizontalAdvance(clock(record.ts))
        if record.outgoing:
            width += 4 + ticks_width(metrics.height() * 0.85)
        return width, metrics.height()

    def _max_text_width(self) -> int:
        available = self._view.viewport().width() - 2 * SIDE_MARGIN
        return max(80, int(max(MIN_BUBBLE_WIDTH, available * MAX_WIDTH_RATIO)) - 2 * PAD_H)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _thumb(self, record: MessageRecord) -> QImage | None:
        if record.file is None or not record.file.is_image:
            return None
        image = self._thumbs.get(record.id)
        if image is None:
            image = QImage.fromData(record.file.thumb)
            if image.isNull():
                return None
            self._thumbs[record.id] = image
        return image

    # -- Mise en page ------------------------------------------------------
    def _layout(self, record: MessageRecord) -> _Layout:
        max_width = self._max_text_width()
        f = record.file
        key = (
            record.id,
            record.status.value,
            f.state.value if f else "",
            max_width,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        layout = self._file_layout(record, max_width) if f else self._text_layout(record, max_width)
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[key] = layout
        return layout

    def _text_layout(self, record: MessageRecord, max_width: int) -> _Layout:
        count = emoji_count(record.text)
        jumbo = 0 < count <= 3
        doc = QTextDocument()
        doc.setDefaultFont(
            emoji_font(self._view.font(), 2.4 if count == 1 else 1.9) if jumbo else self._font()
        )
        doc.setDocumentMargin(0)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setPlainText(record.text)  # jamais interprété comme du HTML
        doc.setTextWidth(max_width)
        ideal = min(max_width, doc.idealWidth())
        doc.setTextWidth(ideal + 1)

        meta_width, meta_height = self._meta_size(record)
        last_block = doc.lastBlock().layout()
        last_line = (
            last_block.lineAt(last_block.lineCount() - 1).naturalTextWidth()
            if last_block.lineCount()
            else 0
        )
        text_height = doc.size().height()
        if not jumbo and last_line + META_GAP + meta_width <= max_width:
            width = max(ideal, last_line + META_GAP + meta_width)
            height = max(text_height, meta_height)
        else:
            width = max(ideal, meta_width)
            height = text_height + meta_height
        return _Layout(width + 1, height, doc, meta_width, jumbo)

    def _file_layout(self, record: MessageRecord, max_width: int) -> _Layout:
        f = record.file
        assert f is not None
        width = float(min(max_width, FILE_WIDTH))
        y = 0.0
        layout = _Layout(width, 0.0)
        thumb = self._thumb(record)
        if thumb is not None:
            scale = min(width / thumb.width(), IMAGE_MAX_HEIGHT / thumb.height(), 1.6)
            w, h = thumb.width() * scale, thumb.height() * scale
            layout.image = QRectF(0, 0, w, h)
            y = h + 8
        layout.card = QRectF(0, y, width, ICON_SIZE)
        y += ICON_SIZE + 6
        if f.state.active:
            layout.progress = QRectF(0, y, width, 4)
            y += 10
        metrics = QFontMetricsF(self._small_font())
        x = 0.0
        actions = file_actions(record)
        for action, label in actions:
            w = metrics.horizontalAdvance(label) + 26
            layout.buttons.append((action, label, QRectF(x, y, w, BUTTON_HEIGHT)))
            x += w + 8
        if actions:
            y += BUTTON_HEIGHT + 4
        meta_width, meta_height = self._meta_size(record)
        layout.meta_width = meta_width
        layout.height = y + meta_height
        return layout

    # -- API délégué ------------------------------------------------------
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        item = index.data(Role.ITEM)
        width = self._view.viewport().width()
        if isinstance(item, DayItem):
            metrics = QFontMetricsF(self._meta_font())
            return QSize(width, int(metrics.height() + 2 * PAD_V + 2 * BLOCK_GAP))
        assert isinstance(item, MessageItem)
        layout = self._layout(item.record)
        top = BLOCK_GAP if item.first_in_group else GROUP_GAP
        bottom = BLOCK_GAP if item.last_in_group else 0
        if item.record.reactions:
            bottom += PILL_SPACE
        return QSize(width, int(layout.height + 2 * PAD_V + top + bottom + 0.5))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item = index.data(Role.ITEM)
        theme = current_theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(option.rect)
        if isinstance(item, DayItem):
            self._paint_day(painter, rect, item, theme)
        elif isinstance(item, MessageItem):
            progress = index.data(Role.PROGRESS)
            self._paint_message(painter, rect, item, theme, progress)
            if getattr(self._view, "hovered_row", -1) == index.row():
                self._paint_react_button(painter, rect, item, theme)
        painter.restore()

    def bubble_rect(self, rect: QRectF, item: MessageItem) -> QRectF:
        layout = self._layout(item.record)
        top = BLOCK_GAP if item.first_in_group else GROUP_GAP
        width = layout.width + 2 * PAD_H
        height = layout.height + 2 * PAD_V
        if item.record.outgoing:
            x = rect.right() - SIDE_MARGIN - width
        else:
            x = rect.left() + SIDE_MARGIN
        return QRectF(x, rect.top() + top, width, height)

    def _content_origin(self, bubble: QRectF) -> QPointF:
        return QPointF(bubble.left() + PAD_H, bubble.top() + PAD_V)

    def react_button_rect(self, rect: QRectF, item: MessageItem) -> QRectF:
        bubble = self.bubble_rect(rect, item)
        y = bubble.center().y() - REACT_BUTTON / 2
        if item.record.outgoing:
            return QRectF(bubble.left() - REACT_BUTTON - 6, y, REACT_BUTTON, REACT_BUTTON)
        return QRectF(bubble.right() + 6, y, REACT_BUTTON, REACT_BUTTON)

    def pill_rect(self, rect: QRectF, item: MessageItem) -> QRectF | None:
        if not item.record.reactions:
            return None
        text = self._pill_text(item.record)
        metrics = QFontMetricsF(emoji_font(self._view.font(), 0.95))
        width = metrics.horizontalAdvance(text) + 16
        bubble = self.bubble_rect(rect, item)
        y = bubble.bottom() - 4
        if item.record.outgoing:
            return QRectF(bubble.right() - 8 - width, y, width, PILL_HEIGHT)
        return QRectF(bubble.left() + 8, y, width, PILL_HEIGHT)

    @staticmethod
    def _pill_text(record: MessageRecord) -> str:
        emojis = [emoji for _author, emoji in record.reactions]
        unique = list(dict.fromkeys(emojis))
        if len(unique) == 1 and len(emojis) > 1:
            return f"{unique[0]} {len(emojis)}"
        return "".join(unique)

    # -- Dessin ------------------------------------------------------------
    def _paint_day(self, painter: QPainter, rect: QRectF, item: DayItem, theme: Theme) -> None:
        painter.setFont(self._meta_font())
        metrics = QFontMetricsF(painter.font())
        text = day_label(item.day)
        width = metrics.horizontalAdvance(text) + 2 * PAD_H
        height = metrics.height() + 2 * PAD_V - 4
        pill = QRectF(rect.center().x() - width / 2, rect.center().y() - height / 2, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.separator)
        painter.drawRoundedRect(pill, height / 2, height / 2)
        painter.setPen(theme.separator_text)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_message(
        self,
        painter: QPainter,
        rect: QRectF,
        item: MessageItem,
        theme: Theme,
        progress: tuple[int, int] | None,
    ) -> None:
        record = item.record
        layout = self._layout(record)
        outgoing = record.outgoing
        bubble = self.bubble_rect(rect, item)
        tail = TAIL_RADIUS if item.last_in_group else RADIUS
        small_top = RADIUS if item.first_in_group else TAIL_RADIUS + 4
        if outgoing:
            path = rounded_path(bubble, RADIUS, small_top, tail, RADIUS)
        else:
            path = rounded_path(bubble, small_top, RADIUS, RADIUS, tail)
        if not theme.dark and not outgoing:
            painter.fillPath(path.translated(0, 1), theme.separator)
        painter.fillPath(path, theme.outgoing if outgoing else theme.incoming)

        origin = self._content_origin(bubble)
        text_color = theme.outgoing_text if outgoing else theme.incoming_text
        if layout.doc is not None:
            painter.save()
            painter.translate(origin)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QPalette.ColorRole.Text, text_color)
            layout.doc.documentLayout().draw(painter, context)
            painter.restore()
        else:
            self._paint_file(painter, origin, record, layout, theme, progress)

        # Heure et état
        meta_color = theme.meta_outgoing if outgoing else theme.meta_incoming
        painter.setFont(self._meta_font())
        metrics = QFontMetricsF(painter.font())
        content_right = origin.x() + layout.width
        content_bottom = origin.y() + layout.height
        meta_rect = QRectF(
            content_right - layout.meta_width,
            content_bottom - metrics.height(),
            layout.meta_width,
            metrics.height(),
        )
        painter.setPen(meta_color)
        painter.drawText(
            meta_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clock(record.ts)
        )
        if outgoing:
            tick_h = metrics.height() * 0.85
            tick_rect = QRectF(
                meta_rect.right() - ticks_width(tick_h),
                meta_rect.center().y() - tick_h / 2,
                ticks_width(tick_h),
                tick_h,
            )
            paint_ticks(painter, tick_rect, record.status.value, meta_color, theme.read_tick)

        pill = self.pill_rect(rect, item)
        if pill is not None:
            self._paint_pill(painter, pill, record, theme)

    def _paint_file(
        self,
        painter: QPainter,
        origin: QPointF,
        record: MessageRecord,
        layout: _Layout,
        theme: Theme,
        progress: tuple[int, int] | None,
    ) -> None:
        f: FileInfo = record.file  # type: ignore[assignment]
        outgoing = record.outgoing
        text_color = theme.outgoing_text if outgoing else theme.incoming_text
        muted = theme.meta_outgoing if outgoing else theme.meta_incoming
        painter.save()
        painter.translate(origin)

        if layout.image is not None and (thumb := self._thumb(record)) is not None:
            clip = QPainterPath()
            clip.addRoundedRect(layout.image, 10, 10)
            painter.save()
            painter.setClipPath(clip)
            painter.drawImage(layout.image, thumb)
            painter.restore()

        card = layout.card
        assert card is not None
        icon = QRectF(card.left(), card.top(), ICON_SIZE, ICON_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(file_color(f.extension))
        painter.drawRoundedRect(icon, 8, 8)
        label_font = QFont(self._view.font())
        label_font.setBold(True)
        label_font.setPixelSize(11 if len(f.extension) > 3 else 12)
        painter.setFont(label_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(icon, Qt.AlignmentFlag.AlignCenter, (f.extension or "?")[:4].upper())

        text_left = icon.right() + 10
        text_width = card.width() - ICON_SIZE - 10
        name_font = QFont(self._view.font())
        name_font.setBold(True)
        name_metrics = QFontMetricsF(name_font)
        painter.setFont(name_font)
        painter.setPen(text_color)
        name = name_metrics.elidedText(f.name, Qt.TextElideMode.ElideMiddle, text_width)
        painter.drawText(
            QRectF(text_left, card.top(), text_width, card.height() / 2 + 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            name,
        )
        small = self._small_font()
        painter.setFont(small)
        painter.setPen(muted)
        status = QFontMetricsF(small).elidedText(
            file_status(record, progress), Qt.TextElideMode.ElideRight, text_width
        )
        painter.drawText(
            QRectF(text_left, card.center().y() + 2, text_width, card.height() / 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            status,
        )

        if layout.progress is not None:
            track = QColor(text_color)
            track.setAlphaF(0.2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(track)
            painter.drawRoundedRect(layout.progress, 2, 2)
            done, total = progress or (0, f.size)
            ratio = done / total if total else 1.0
            filled = QRectF(layout.progress)
            filled.setWidth(max(4.0, layout.progress.width() * min(1.0, ratio)))
            painter.setBrush(theme.read_tick if outgoing else theme.accent)
            painter.drawRoundedRect(filled, 2, 2)

        for action, label, button in layout.buttons:
            primary = action in _PRIMARY
            if primary:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#ffffff") if outgoing else theme.accent)
            else:
                outline = QColor(text_color)
                outline.setAlphaF(0.45)
                painter.setPen(QPen(outline, 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(button.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
            if primary:
                painter.setPen(theme.outgoing if outgoing else QColor("#ffffff"))
            else:
                painter.setPen(text_color)
            painter.setFont(small)
            painter.drawText(button, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def _paint_pill(
        self, painter: QPainter, pill: QRectF, record: MessageRecord, theme: Theme
    ) -> None:
        mine = bool(record.reaction_of(ME))
        painter.setPen(QPen(theme.accent if mine else theme.separator, 1.2))
        painter.setBrush(theme.incoming)
        painter.drawRoundedRect(pill, PILL_HEIGHT / 2, PILL_HEIGHT / 2)
        painter.setFont(emoji_font(self._view.font(), 0.95))
        painter.setPen(theme.incoming_text)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self._pill_text(record))

    def _paint_react_button(
        self, painter: QPainter, rect: QRectF, item: MessageItem, theme: Theme
    ) -> None:
        button = self.react_button_rect(rect, item)
        painter.setPen(QPen(theme.separator, 1))
        painter.setBrush(theme.incoming)
        painter.drawEllipse(button)
        painter.setFont(emoji_font(self._view.font(), 1.0))
        painter.setPen(theme.muted_text)
        painter.drawText(button, Qt.AlignmentFlag.AlignCenter, "🙂")

    # -- Interaction ------------------------------------------------------
    def hit(self, rect: QRectF, index: QModelIndex, pos: QPointF, *, hovered: bool) -> Hit | None:
        """Élément cliquable sous ``pos`` (coordonnées de la vue)."""
        item = index.data(Role.ITEM)
        if not isinstance(item, MessageItem):
            return None
        record = item.record
        if hovered and self.react_button_rect(rect, item).contains(pos):
            return Hit("react")
        pill = self.pill_rect(rect, item)
        if pill is not None and pill.contains(pos):
            return Hit("pill")
        bubble = self.bubble_rect(rect, item)
        if not bubble.contains(pos):
            return None
        layout = self._layout(record)
        local = pos - self._content_origin(bubble)
        for action, _label, button in layout.buttons:
            if button.contains(local):
                return Hit("action", action)
        f = record.file
        openable = f is not None and f.path and (record.outgoing or f.state is FileState.DONE)
        if openable and (
            (layout.image is not None and layout.image.contains(local))
            or (layout.card is not None and layout.card.contains(local))
        ):
            return Hit("action", "open")
        return Hit("bubble")

    def text_at(self, rect: QRectF, index: QModelIndex, pos: QPointF) -> str | None:
        """Texte du message sous le curseur (menu « Copier »)."""
        item = index.data(Role.ITEM)
        if (
            isinstance(item, MessageItem)
            and item.record.file is None
            and self.bubble_rect(rect, item).contains(pos)
        ):
            return item.record.text
        return None


class ContactDelegate(QStyledItemDelegate):
    HEIGHT = 66
    AVATAR = 44

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        summary: ContactSummary = index.data(Role.SUMMARY)
        online: bool = index.data(Role.ONLINE)
        typing: bool = index.data(Role.TYPING)
        theme = current_theme()
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        background = None
        if option.state & QStyle.StateFlag.State_Selected:
            background = theme.selection
        elif option.state & QStyle.StateFlag.State_MouseOver:
            background = theme.hover
        if background is not None:
            painter.fillRect(rect, background)

        ring = background or option.palette.color(QPalette.ColorRole.Base)
        avatar = QRectF(
            rect.left() + 12, rect.center().y() - self.AVATAR / 2, self.AVATAR, self.AVATAR
        )
        paint_avatar(
            painter, avatar, summary.contact.name, summary.contact.id, online=online, ring=ring
        )

        left = avatar.right() + 12
        right = rect.right() - 12
        base_font = QFont(option.font)
        name_font = QFont(base_font)
        name_font.setBold(True)
        small_font = QFont(base_font)
        small_font.setPointSizeF(max(6.0, base_font.pointSizeF() * 0.85))
        name_metrics = QFontMetricsF(name_font)
        small_metrics = QFontMetricsF(small_font)
        line1 = rect.center().y() - 2
        line2 = rect.center().y() + 3

        # Heure du dernier message
        text_color = option.palette.color(QPalette.ColorRole.Text)
        time_text = ""
        if summary.last_message is not None:
            time_text = short_time(summary.last_message.ts)
        time_width = small_metrics.horizontalAdvance(time_text)
        painter.setFont(small_font)
        painter.setPen(theme.accent if summary.unread else theme.muted_text)
        painter.drawText(
            QRectF(
                right - time_width,
                line1 - small_metrics.height(),
                time_width,
                small_metrics.height(),
            ),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            time_text,
        )

        # Nom
        painter.setFont(name_font)
        painter.setPen(text_color)
        name_width = right - left - time_width - 8
        name = name_metrics.elidedText(
            summary.contact.name, Qt.TextElideMode.ElideRight, name_width
        )
        painter.drawText(
            QRectF(left, line1 - name_metrics.height(), name_width, name_metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            name,
        )

        # Pastille des non-lus
        badge_width = 0.0
        if summary.unread:
            label = str(summary.unread) if summary.unread < 100 else "99+"
            badge_h = small_metrics.height() + 4
            badge_width = max(badge_h, small_metrics.horizontalAdvance(label) + 12)
            badge = QRectF(right - badge_width, line2, badge_width, badge_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.accent)
            painter.drawRoundedRect(badge, badge_h / 2, badge_h / 2)
            painter.setFont(small_font)
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)

        # Aperçu
        preview_font = QFont(small_font)
        if typing:
            preview, color = "écrit…", theme.accent
            preview_font.setItalic(True)
        elif summary.last_message is not None:
            text = summary.last_message.preview
            preview = f"Vous : {text}" if summary.last_message.outgoing else text
            color = theme.muted_text
        else:
            preview = "En ligne" if online else "Hors ligne"
            color = theme.muted_text
        painter.setFont(preview_font)
        painter.setPen(color)
        preview_width = right - left - (badge_width + 8 if badge_width else 0)
        preview = QFontMetricsF(preview_font).elidedText(
            preview, Qt.TextElideMode.ElideRight, preview_width
        )
        painter.drawText(
            QRectF(left, line2, preview_width, small_metrics.height() + 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            preview,
        )
        painter.restore()
