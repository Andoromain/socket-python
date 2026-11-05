"""Dessin des bulles de messages et des lignes de contacts."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QFont,
    QFontMetricsF,
    QPainter,
    QPalette,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..store import ContactSummary
from .models import DayItem, MessageItem, Role
from .theme import (
    Theme,
    clock,
    current_theme,
    day_label,
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


class _Layout:
    """Mise en page calculée d'une bulle."""

    def __init__(self, doc: QTextDocument, content: QSize, meta_inline: bool, meta_width: float):
        self.doc = doc
        self.content = content
        self.meta_inline = meta_inline
        self.meta_width = meta_width


class BubbleDelegate(QStyledItemDelegate):
    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view
        self._cache: dict[tuple[str, str, int], _Layout] = {}

    # -- Polices et mesures --------------------------------------------
    def _font(self) -> QFont:
        return self._view.font()

    def _meta_font(self) -> QFont:
        font = QFont(self._view.font())
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.78))
        return font

    def _meta_size(self, item: MessageItem) -> tuple[float, float]:
        metrics = QFontMetricsF(self._meta_font())
        width = metrics.horizontalAdvance(clock(item.record.ts))
        if item.record.outgoing:
            width += 4 + ticks_width(metrics.height() * 0.85)
        return width, metrics.height()

    def _max_text_width(self) -> int:
        available = self._view.viewport().width() - 2 * SIDE_MARGIN
        return max(80, int(max(MIN_BUBBLE_WIDTH, available * MAX_WIDTH_RATIO)) - 2 * PAD_H)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _layout(self, item: MessageItem) -> _Layout:
        max_width = self._max_text_width()
        key = (item.record.id, item.record.status.value, max_width)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        doc = QTextDocument()
        doc.setDefaultFont(self._font())
        doc.setDocumentMargin(0)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setPlainText(item.record.text)  # jamais interprété comme du HTML
        doc.setTextWidth(max_width)
        ideal = min(max_width, doc.idealWidth())
        doc.setTextWidth(ideal + 1)

        meta_width, meta_height = self._meta_size(item)
        last_block = doc.lastBlock().layout()
        last_line_width = (
            last_block.lineAt(last_block.lineCount() - 1).naturalTextWidth()
            if last_block.lineCount()
            else 0
        )
        text_height = doc.size().height()
        if last_line_width + META_GAP + meta_width <= max_width:
            width = max(ideal, last_line_width + META_GAP + meta_width)
            height = max(text_height, meta_height)
            inline = True
        else:
            width = max(ideal, meta_width)
            height = text_height + meta_height
            inline = False
        layout = _Layout(doc, QSize(int(width + 1), int(height + 0.5)), inline, meta_width)
        if len(self._cache) > 2000:
            self._cache.clear()
        self._cache[key] = layout
        return layout

    # -- API délégué ------------------------------------------------------
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        item = index.data(Role.ITEM)
        width = self._view.viewport().width()
        if isinstance(item, DayItem):
            metrics = QFontMetricsF(self._meta_font())
            return QSize(width, int(metrics.height() + 2 * PAD_V + 2 * BLOCK_GAP))
        assert isinstance(item, MessageItem)
        layout = self._layout(item)
        top = BLOCK_GAP if item.first_in_group else GROUP_GAP
        bottom = BLOCK_GAP if item.last_in_group else 0
        return QSize(width, layout.content.height() + 2 * PAD_V + top + bottom)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item = index.data(Role.ITEM)
        theme = current_theme()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if isinstance(item, DayItem):
            self._paint_day(painter, QRectF(option.rect), item, theme)
        elif isinstance(item, MessageItem):
            self._paint_message(painter, QRectF(option.rect), item, theme)
        painter.restore()

    def bubble_rect(self, rect: QRectF, item: MessageItem) -> QRectF:
        layout = self._layout(item)
        top = BLOCK_GAP if item.first_in_group else GROUP_GAP
        width = layout.content.width() + 2 * PAD_H
        height = layout.content.height() + 2 * PAD_V
        x = (
            rect.right() - SIDE_MARGIN - width
            if item.record.outgoing
            else rect.left() + SIDE_MARGIN
        )
        return QRectF(x, rect.top() + top, width, height)

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
        self, painter: QPainter, rect: QRectF, item: MessageItem, theme: Theme
    ) -> None:
        layout = self._layout(item)
        outgoing = item.record.outgoing
        bubble = self.bubble_rect(rect, item)
        tail = TAIL_RADIUS if item.last_in_group else RADIUS
        small_top = RADIUS if item.first_in_group else TAIL_RADIUS + 4
        if outgoing:
            path = rounded_path(bubble, RADIUS, small_top, tail, RADIUS)
        else:
            path = rounded_path(bubble, small_top, RADIUS, RADIUS, tail)

        if not theme.dark and not outgoing:
            shadow = path.translated(0, 1)
            painter.fillPath(shadow, theme.separator)
        painter.fillPath(path, theme.outgoing if outgoing else theme.incoming)

        # Texte
        text_color = theme.outgoing_text if outgoing else theme.incoming_text
        content = bubble.adjusted(PAD_H, PAD_V, -PAD_H, -PAD_V)
        painter.save()
        painter.translate(content.topLeft())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, text_color)
        layout.doc.documentLayout().draw(painter, context)
        painter.restore()

        # Heure et état
        meta_color = theme.meta_outgoing if outgoing else theme.meta_incoming
        painter.setFont(self._meta_font())
        metrics = QFontMetricsF(painter.font())
        meta_rect = QRectF(
            content.right() - layout.meta_width,
            content.bottom() - metrics.height(),
            layout.meta_width,
            metrics.height(),
        )
        painter.setPen(meta_color)
        painter.drawText(
            meta_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clock(item.record.ts),
        )
        if outgoing:
            tick_h = metrics.height() * 0.85
            tick_rect = QRectF(
                meta_rect.right() - ticks_width(tick_h),
                meta_rect.center().y() - tick_h / 2,
                ticks_width(tick_h),
                tick_h,
            )
            paint_ticks(painter, tick_rect, item.record.status.value, meta_color, theme.read_tick)

    def text_at(self, rect: QRectF, index: QModelIndex, pos: QPointF) -> str | None:
        """Texte du message sous le curseur (menu « Copier »)."""
        item = index.data(Role.ITEM)
        if isinstance(item, MessageItem) and self.bubble_rect(rect, item).contains(pos):
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
            text = " ".join(summary.last_message.text.split())
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
