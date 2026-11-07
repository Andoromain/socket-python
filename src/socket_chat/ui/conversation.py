"""Vue d'une conversation : en-tête, fil de bulles et zone de saisie."""

from __future__ import annotations

from datetime import datetime
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
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
from ..settings import Settings
from ..store import ME, FileState, MessageRecord
from .delegates import BubbleDelegate
from .emoji import QUICK_REACTIONS, EmojiPicker, ReactionBar, convert_shortcuts, emoji_font
from .models import MessageItem, MessageListModel, Role
from .theme import avatar_pixmap, current_theme

MAX_INPUT_LINES = 5


def _resource(name: str) -> str:
    return str(files("socket_chat") / "resources" / name)


def local_files(mime: QMimeData) -> list[str]:
    """Fichiers locaux contenus dans un glisser-déposer ou un presse-papiers."""
    if not mime.hasUrls():
        return []
    paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
    return [p for p in paths if Path(p).is_file()]


class MessageInput(QPlainTextEdit):
    """Zone de saisie : Entrée envoie, Maj+Entrée va à la ligne ; hauteur adaptative."""

    submitted = Signal()
    files_pasted = Signal(list)  # chemins de fichiers collés
    image_pasted = Signal(QImage)  # capture d'écran collée

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(emoji_font(self.font()))
        self.setPlaceholderText("Écrivez un message… (Ctrl+V pour coller une capture)")
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

    def canInsertFromMimeData(self, source: QMimeData) -> bool:  # noqa: N802 - API Qt
        return (
            bool(local_files(source)) or source.hasImage() or super().canInsertFromMimeData(source)
        )

    def insertFromMimeData(self, source: QMimeData) -> None:  # noqa: N802 - API Qt
        if paths := local_files(source):
            self.files_pasted.emit(paths)
        elif source.hasImage():
            image = QImage(source.imageData())
            if not image.isNull():
                self.image_pasted.emit(image)
        else:
            super().insertFromMimeData(source)

    def insert_emoji(self, emoji: str) -> None:
        self.insertPlainText(emoji)
        self.setFocus()

    def _fit_height(self) -> None:
        lines = max(1, min(MAX_INPUT_LINES, self.document().size().height()))
        line_height = self.fontMetrics().lineSpacing()
        margins = self.contentsMargins()
        frame = int(self.document().documentMargin() * 2) + margins.top() + margins.bottom()
        self.setFixedHeight(int(lines * line_height) + frame + 2)


class MessageView(QListView):
    """Fil de messages : défilement au pixel, relayout, clics dans les bulles."""

    action_requested = Signal(str, str)  # identifiant du message, action
    react_requested = Signal(str, QPoint)  # identifiant du message, position globale
    react_with = Signal(str, str)  # identifiant du message, emoji ("" : retirer)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.hovered_row = -1
        self.setMouseTracking(True)
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

    # -- Souris ----------------------------------------------------------
    def _hit(self, pos: QPoint):
        index = self.indexAt(pos)
        if not index.isValid():
            return index, None
        rect = QRectF(self.visualRect(index))
        hovered = index.row() == self.hovered_row
        return index, self.bubbles.hit(rect, index, QPointF(pos), hovered=hovered)

    def _set_hovered(self, row: int) -> None:
        if row == self.hovered_row:
            return
        previous, self.hovered_row = self.hovered_row, row
        for r in (previous, row):
            if r >= 0 and self.model() is not None and r < self.model().rowCount():
                self.viewport().update(self.visualRect(self.model().index(r, 0)))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        self._set_hovered(index.row() if index.isValid() else -1)
        _index, hit = self._hit(pos)
        clickable = hit is not None and hit.kind in ("action", "react", "pill")
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        self._set_hovered(-1)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index, hit = self._hit(pos)
            if hit is not None:
                message_id = index.data(Role.ID)
                if hit.kind == "action":
                    self.action_requested.emit(message_id, hit.value)
                elif hit.kind == "pill":
                    self.action_requested.emit(message_id, "pill")
                elif hit.kind == "react":
                    rect = self.bubbles.react_button_rect(
                        QRectF(self.visualRect(index)), index.data(Role.ITEM)
                    )
                    anchor = self.viewport().mapToGlobal(rect.bottomLeft().toPoint())
                    self.react_requested.emit(message_id, anchor)
        super().mouseReleaseEvent(event)

    def _context_menu(self, pos) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        item = index.data(Role.ITEM)
        if not isinstance(item, MessageItem):
            return
        record = item.record
        menu = QMenu(self)
        react_menu = menu.addMenu("Réagir")
        mine = record.reaction_of(ME)
        for emoji in QUICK_REACTIONS:
            action = react_menu.addAction(emoji)
            action.setFont(emoji_font(self.font(), 1.3))
            action.triggered.connect(lambda _=False, e=emoji: self.react_with.emit(record.id, e))
        if mine:
            react_menu.addSeparator()
            remove = react_menu.addAction(f"Retirer ma réaction {mine}")
            remove.triggered.connect(lambda: self.react_with.emit(record.id, ""))
        text = self.bubbles.text_at(QRectF(self.visualRect(index)), index, QPointF(pos))
        if text is not None:
            copy = QAction("Copier le texte", menu)
            copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(text))
            menu.addAction(copy)
        f = record.file
        if f is not None and f.path and (record.outgoing or f.state is FileState.DONE):
            menu.addSeparator()
            for action_id, label in (("open", "Ouvrir"), ("folder", "Afficher dans le dossier")):
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda _=False, a=action_id: self.action_requested.emit(record.id, a)
                )
        menu.exec(self.viewport().mapToGlobal(pos))


class ConversationView(QWidget):
    """Affiche une conversation ; ne connaît le réseau qu'à travers ``Messenger``."""

    message_error = Signal(str)
    call_requested = Signal(str)  # contact à appeler
    video_call_requested = Signal(str)  # contact à appeler en vidéo

    def __init__(
        self, messenger: Messenger, settings: Settings | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self._messenger = messenger
        self._settings = settings
        self.contact_id: str | None = None
        data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        #: Dossier où sont conservées les captures collées (pour pouvoir les renvoyer).
        self.outbox_dir = Path(data) / "envois"
        #: Choix du fichier de destination (remplaçable dans les tests).
        self.ask_save_path = self._ask_save_path
        self.setAcceptDrops(True)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_empty_page())
        self.stack.addWidget(self._build_chat_page())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        messenger.message_status_changed.connect(self.model.update_status)
        messenger.message_changed.connect(self._on_message_changed)
        messenger.file_progress.connect(self.model.set_progress)
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
        self.call_button = QToolButton(header)
        self.call_button.setText("📞")
        self.call_button.setAutoRaise(True)
        self.call_button.setToolTip("Appel audio")
        self.call_button.setVisible(False)  # affiché si les appels sont disponibles
        self.call_button.clicked.connect(
            lambda: self.contact_id and self.call_requested.emit(self.contact_id)
        )
        header_layout.addWidget(self.call_button)
        self.video_button = QToolButton(header)
        self.video_button.setText("🎥")
        self.video_button.setAutoRaise(True)
        self.video_button.setToolTip("Appel vidéo")
        self.video_button.setVisible(False)  # affiché si les appels sont disponibles
        self.video_button.clicked.connect(
            lambda: self.contact_id and self.video_call_requested.emit(self.contact_id)
        )
        header_layout.addWidget(self.video_button)

        separator = QFrame(page)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setForegroundRole(QPalette.ColorRole.Mid)

        # Fil de messages
        self.model = MessageListModel(self)
        self.list = MessageView(page)
        self.list.setModel(self.model)
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.list.action_requested.connect(self.handle_action)
        self.list.react_requested.connect(self._show_reaction_bar)
        self.list.react_with.connect(self._react)

        # Saisie
        self.input = MessageInput(page)
        self.input.submitted.connect(self.send)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.files_pasted.connect(self.send_files)
        self.input.image_pasted.connect(self.send_image)
        self.emoji_button = self._tool_button(page, "🙂", "Insérer un emoji")
        self.emoji_button.clicked.connect(self._show_emoji_picker)
        self.attach_button = self._tool_button(page, "📎", "Envoyer un fichier")
        self.attach_button.clicked.connect(self._choose_files)
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
        input_row.setContentsMargins(8, 8, 12, 10)
        input_row.setSpacing(4)
        input_row.addWidget(self.emoji_button, 0, Qt.AlignmentFlag.AlignBottom)
        input_row.addWidget(self.attach_button, 0, Qt.AlignmentFlag.AlignBottom)
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

    def _tool_button(self, parent: QWidget, text: str, tooltip: str) -> QToolButton:
        button = QToolButton(parent)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setFont(emoji_font(self.font(), 1.35))
        button.setFixedSize(38, 40)
        return button

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
        self._messenger.send(self.contact_id, convert_shortcuts(text))
        self.input.clear()
        self.list.scrollToBottom()

    def send_files(self, paths: list[str]) -> None:
        if self.contact_id is None:
            return
        errors = []
        for path in paths:
            try:
                self._messenger.send_file(self.contact_id, path)
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            self.message_error.emit("\n".join(errors))
        self.list.scrollToBottom()

    def send_image(self, image: QImage) -> None:
        """Envoie une image collée (enregistrée en PNG pour pouvoir être retransmise)."""
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.outbox_dir / f"capture-{stamp}.png"
        counter = 1
        while path.exists():
            counter += 1
            path = self.outbox_dir / f"capture-{stamp}-{counter}.png"
        if not image.save(str(path), "PNG"):
            self.message_error.emit("Impossible d'enregistrer l'image collée.")
            return
        self.send_files([str(path)])

    def handle_action(self, message_id: str, action: str) -> None:
        """Bouton cliqué dans une bulle de fichier (ou sur les réactions)."""
        record = self._messenger.store.message(message_id)
        if record is None:
            return
        try:
            match action:
                case "accept" | "retry":
                    self._accept(record)
                case "decline":
                    self._messenger.decline_file(message_id)
                case "cancel":
                    self._messenger.cancel_file(message_id)
                case "open" | "folder":
                    self._open(record, folder=action == "folder")
                case "pill":
                    mine = record.reaction_of(ME)
                    others = [e for author, e in record.reactions if author != ME]
                    self._react(message_id, "" if mine else (others[0] if others else "👍"))
        except ValueError as exc:
            self.message_error.emit(str(exc))

    # -- Fichiers et emojis ----------------------------------------------
    def _ask_save_path(self, default: Path) -> str:
        path, _filter = QFileDialog.getSaveFileName(self, "Enregistrer le fichier", str(default))
        return path

    def _accept(self, record: MessageRecord) -> None:
        assert record.file is not None
        if record.file.path:
            default = Path(record.file.path)
        else:
            downloads = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DownloadLocation
            )
            default = Path(downloads or Path.home()) / record.file.name
        path = self.ask_save_path(default)
        if path:
            self._messenger.accept_file(record.id, path)

    def _open(self, record: MessageRecord, *, folder: bool) -> None:
        path = Path(record.file.path) if record.file and record.file.path else None
        if path is None or not path.exists():
            raise ValueError("Fichier introuvable : il a peut-être été déplacé ou supprimé.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if folder else path)))

    def _choose_files(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(self, "Envoyer des fichiers")
        if paths:
            self.send_files(paths)

    def _show_emoji_picker(self) -> None:
        recent = self._settings.recent_emojis if self._settings else []
        picker = EmojiPicker(recent, self)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        picker.picked.connect(self._insert_emoji)
        picker.popup_at(self.emoji_button.mapToGlobal(QPoint(0, -4)))
        self.emoji_picker = picker

    def _insert_emoji(self, emoji: str) -> None:
        self.input.insert_emoji(emoji)
        if self._settings is not None:
            self._settings.remember_emoji(emoji)

    def _show_reaction_bar(self, message_id: str, anchor: QPoint) -> None:
        record = self._messenger.store.message(message_id)
        if record is None:
            return
        bar = ReactionBar(record.reaction_of(ME), self)
        bar.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        bar.picked.connect(lambda emoji: self._react(message_id, emoji))
        bar.more_requested.connect(lambda: self._show_full_reaction_picker(message_id, anchor))
        bar.move(anchor)
        bar.show()
        self.reaction_bar = bar

    def _show_full_reaction_picker(self, message_id: str, anchor: QPoint) -> None:
        recent = self._settings.recent_emojis if self._settings else []
        picker = EmojiPicker(recent, self)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        picker.picked.connect(lambda emoji: self._react(message_id, emoji))
        picker.move(anchor)
        picker.show()

    def _react(self, message_id: str, emoji: str) -> None:
        try:
            self._messenger.react(message_id, emoji)
        except ValueError as exc:
            self.message_error.emit(str(exc))
            return
        if emoji and self._settings is not None:
            self._settings.remember_emoji(emoji)

    def _on_message_changed(self, message_id: str) -> None:
        if self.model.row_of(message_id) is None:
            return
        record = self._messenger.store.message(message_id)
        if record is not None:
            self.model.update_record(record)

    # -- Glisser-déposer -----------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - API Qt
        if self.contact_id is not None and local_files(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.contact_id is not None and local_files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - API Qt
        paths = local_files(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.send_files(paths)

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
        self.avatar.setPixmap(
            avatar_pixmap(
                contact.name, contact.id, 40, self.devicePixelRatioF(), photo=contact.avatar
            )
        )
        if self._messenger.is_typing(contact.id):
            status = "écrit…"
        elif self._messenger.is_online(contact.id):
            status = "en ligne"
        else:
            status = "hors ligne — vos messages lui seront remis à sa reconnexion"
        self.status_label.setText(status)
        online = self._messenger.is_connected(contact.id) or self._messenger.is_online(contact.id)
        self.call_button.setEnabled(online)
        offline = "" if online else " : contact hors ligne"
        self.call_button.setToolTip("Appel audio" + offline)
        self.video_button.setEnabled(online)
        self.video_button.setToolTip("Appel vidéo" + offline)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "list"):
            self.list.apply_theme()
        super().changeEvent(event)
