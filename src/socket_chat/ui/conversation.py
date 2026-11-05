"""Vue d'une conversation : en-tête, fil de bulles et zone de saisie."""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPlainTextEdit,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..messenger import Messenger
from ..protocol import MAX_TEXT_LENGTH
from ..store import MessageRecord
from .delegates import BubbleDelegate
from .models import MessageListModel
from .theme import avatar_pixmap, current_theme

MAX_INPUT_LINES = 5


def _resource(name: str) -> str:
    return str(files("socket_chat") / "resources" / name)


class MessageInput(QPlainTextEdit):
    """Zone de saisie : Entrée envoie, Maj+Entrée va à la ligne ; hauteur adaptative."""

    submitted = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("Écrivez un message…")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _fit_height(self) -> None:
        lines = max(1, min(MAX_INPUT_LINES, self.document().size().height()))
        line_height = self.fontMetrics().lineSpacing()
        margins = self.contentsMargins()
        frame = int(self.document().documentMargin() * 2) + margins.top() + margins.bottom()
        self.setFixedHeight(int(lines * line_height) + frame + 2)


class MessageView(QListView):
    """Fil de messages : défilement au pixel, relayout à chaque redimensionnement."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.verticalScrollBar().setSingleStep(24)
        self.bubbles = BubbleDelegate(self)
        self.setItemDelegate(self.bubbles)
        self.customContextMenuRequested.connect(self._context_menu)
        self.apply_theme()

    def apply_theme(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Base, current_theme().chat_background)
        self.setPalette(palette)

    def is_at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 40

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        at_bottom = self.is_at_bottom()
        super().resizeEvent(event)
        self.bubbles.clear_cache()
        self.scheduleDelayedItemsLayout()
        if at_bottom:
            QTimer.singleShot(0, self, self.scrollToBottom)

    def _context_menu(self, pos) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        text = self.bubbles.text_at(QRectF(self.visualRect(index)), index, QPointF(pos))
        if text is None:
            return
        menu = QMenu(self)
        copy = QAction("Copier le texte", menu)
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(text))
        menu.addAction(copy)
        menu.exec(self.viewport().mapToGlobal(pos))


class ConversationView(QWidget):
    """Affiche une conversation ; ne connaît le réseau qu'à travers ``Messenger``."""

    message_error = Signal(str)

    def __init__(self, messenger: Messenger, parent: QWidget | None = None):
        super().__init__(parent)
        self._messenger = messenger
        self.contact_id: str | None = None

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_empty_page())
        self.stack.addWidget(self._build_chat_page())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        messenger.message_status_changed.connect(self.model.update_status)
        messenger.typing_changed.connect(self._on_contact_state)
        messenger.presence_changed.connect(self._on_contact_state)
        messenger.contacts_changed.connect(self._refresh_header)

    # -- Construction ----------------------------------------------------
    def _build_empty_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        icon = QLabel(page)
        icon.setPixmap(QIcon(_resource("icon.svg")).pixmap(96, 96))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Sélectionnez un collègue pour discuter", page)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setBold(True)
        title.setFont(font)
        hint = QLabel(
            "Les collègues qui ont Socket Chat ouvert sur le réseau apparaissent "
            "automatiquement dans la liste.\nVous pouvez aussi en ajouter un par son adresse.",
            page,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        for widget in (icon, title, hint):
            layout.addWidget(widget)
        layout.addStretch(2)
        return page

    def _build_chat_page(self) -> QWidget:
        page = QWidget(self)

        # En-tête
        header = QFrame(page)
        header.setFrameShape(QFrame.Shape.NoFrame)
        header.setAutoFillBackground(True)
        self.avatar = QLabel(header)
        self.avatar.setFixedSize(40, 40)
        self.name_label = QLabel(header)
        name_font = self.name_label.font()
        name_font.setBold(True)
        name_font.setPointSizeF(name_font.pointSizeF() * 1.1)
        self.name_label.setFont(name_font)
        self.status_label = QLabel(header)
        self.status_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.name_label)
        titles.addWidget(self.status_label)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 14, 8)
        header_layout.addWidget(self.avatar)
        header_layout.addSpacing(6)
        header_layout.addLayout(titles, 1)

        separator = QFrame(page)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setForegroundRole(QPalette.ColorRole.Mid)

        # Fil de messages
        self.model = MessageListModel(self)
        self.list = MessageView(page)
        self.list.setModel(self.model)
        self.model.rowsInserted.connect(self._on_rows_inserted)

        # Saisie
        self.input = MessageInput(page)
        self.input.submitted.connect(self.send)
        self.input.textChanged.connect(self._on_text_changed)
        self.send_button = QToolButton(page)
        self.send_button.setIcon(QIcon(_resource("send.svg")))
        self.send_button.setIconSize(QSize(20, 20))
        self.send_button.setToolTip("Envoyer (Entrée)")
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(40, 40)
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.send)
        self.send_button.setStyleSheet(
            "#sendButton { background: #2a7ab8; border: none; border-radius: 20px; }"
            "#sendButton:hover { background: #3389cc; }"
            "#sendButton:disabled { background: #9db7cc; }"
        )
        input_row = QHBoxLayout()
        input_row.setContentsMargins(12, 8, 12, 10)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(separator)
        layout.addWidget(self.list, 1)
        layout.addLayout(input_row)
        return page

    # -- API -------------------------------------------------------------
    def set_contact(self, contact_id: str | None) -> None:
        if contact_id == self.contact_id:
            return
        if self.contact_id is not None:
            self._messenger.set_typing(self.contact_id, False)
        self.contact_id = contact_id
        self.input.clear()
        if contact_id is None:
            self.model.load(None, [])
            self.stack.setCurrentIndex(0)
            return
        self.model.load(contact_id, self._messenger.store.messages(contact_id))
        self.stack.setCurrentIndex(1)
        self._refresh_header()
        self.list.scrollToBottom()
        self.input.setFocus()

    def add_message(self, record: MessageRecord) -> None:
        self.model.append(record)

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.contact_id is None:
            return
        if len(text) > MAX_TEXT_LENGTH:
            self.message_error.emit(
                f"Un message ne peut pas dépasser {MAX_TEXT_LENGTH} caractères."
            )
            return
        self._messenger.send(self.contact_id, text)
        self.input.clear()
        self.list.scrollToBottom()

    # -- Réactions -------------------------------------------------------
    def _on_text_changed(self) -> None:
        has_text = bool(self.input.toPlainText().strip())
        self.send_button.setEnabled(has_text)
        if self.contact_id is not None:
            self._messenger.set_typing(self.contact_id, has_text)

    def _on_rows_inserted(self, _parent, first: int, last: int) -> None:
        item = self.model.item(last)
        own = getattr(getattr(item, "record", None), "outgoing", False)
        if own or self.list.is_at_bottom():
            QTimer.singleShot(0, self.list, self.list.scrollToBottom)

    def _on_contact_state(self, contact_id: str, _value: bool) -> None:
        if contact_id == self.contact_id:
            self._refresh_header()

    def _refresh_header(self) -> None:
        if self.contact_id is None:
            return
        contact = self._messenger.store.contact(self.contact_id)
        if contact is None:
            return
        self.name_label.setText(contact.name)
        self.avatar.setPixmap(avatar_pixmap(contact.name, contact.id, 40, self.devicePixelRatioF()))
        if self._messenger.is_typing(contact.id):
            status = "écrit…"
        elif self._messenger.is_online(contact.id):
            status = "en ligne"
        else:
            status = "hors ligne — vos messages lui seront remis à sa reconnexion"
        self.status_label.setText(status)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "list"):
            self.list.apply_theme()
        super().changeEvent(event)
