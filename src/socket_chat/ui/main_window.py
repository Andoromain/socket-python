"""Fenêtre principale : contacts à gauche, conversation à droite."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..discovery import Discovery
from ..messenger import Messenger
from ..network import local_ipv4_addresses
from ..settings import Settings
from ..store import MessageRecord
from .conversation import ConversationView
from .delegates import ContactDelegate
from .dialogs import AddContactDialog, SettingsDialog
from .models import ContactListModel, Role

APP_TITLE = "Socket Chat"


class MainWindow(QMainWindow):
    #: Nombre total de messages non lus (pour l'icône de notification).
    unread_changed = Signal(int)
    #: Message à notifier : identifiant du contact, nom, aperçu.
    notify = Signal(str, str, str)
    #: L'utilisateur a choisi « Quitter » (et non simplement fermé la fenêtre).
    quit_requested = Signal()
    #: La fenêtre a été masquée dans la zone de notification.
    hidden_to_tray = Signal()

    def __init__(
        self,
        settings: Settings,
        messenger: Messenger,
        discovery: Discovery | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.messenger = messenger
        self.discovery = discovery
        #: Fermer la fenêtre la masque (vrai seulement si une zone de notification existe).
        self.close_to_tray = False
        self.tray_available = False
        self._quitting = False
        self._refreshing = False
        self._select_new_contact = False
        self._known_ids: set[str] = set()

        self._refresh_timer = QTimer(self, singleShot=True, interval=0)
        self._refresh_timer.timeout.connect(self._refresh_contacts)

        self._build_ui()
        self._build_menus()
        self._connect_messenger()
        self._refresh_contacts()

        geometry = settings.window_geometry
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(940, 640)
        splitter = settings.splitter_state
        if splitter is not None:
            self.splitter.restoreState(splitter)

    # -- Construction ----------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)

        # Panneau des contacts
        left = QWidget(self)
        self.search = QLineEdit(left)
        self.search.setPlaceholderText("Rechercher un collègue…")
        self.search.setClearButtonEnabled(True)

        self.contacts = ContactListModel(self.messenger, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.contacts)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)

        self.contact_list = QListView(left)
        self.contact_list.setModel(self.proxy)
        self.contact_list.setItemDelegate(ContactDelegate(self.contact_list))
        self.contact_list.setMouseTracking(True)
        self.contact_list.setUniformItemSizes(True)
        self.contact_list.setFrameShape(QListView.Shape.NoFrame)
        self.contact_list.selectionModel().currentChanged.connect(self._on_current_changed)

        self.empty_contacts = QLabel(
            "Aucun collègue pour l'instant.\n\nCeux qui ont Socket Chat ouvert sur le "
            "réseau apparaîtront ici automatiquement.",
            left,
        )
        self.empty_contacts.setWordWrap(True)
        self.empty_contacts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_contacts.setForegroundRole(self.empty_contacts.foregroundRole().PlaceholderText)
        self.empty_contacts.setMargin(16)

        self.add_button = QPushButton("Ajouter par adresse…", left)
        self.add_button.clicked.connect(self.add_contact)
        self.me_label = QLabel(left)
        self.me_label.setWordWrap(True)
        self.me_label.setForegroundRole(self.me_label.foregroundRole().PlaceholderText)
        self.me_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.contact_list, 1)
        left_layout.addWidget(self.empty_contacts, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.add_button)
        bottom.addStretch(1)
        left_layout.addLayout(bottom)
        left_layout.addWidget(self.me_label)

        # Conversation
        self.conversation = ConversationView(self.messenger, self.settings, self)
        self.conversation.message_error.connect(
            lambda text: QMessageBox.warning(self, APP_TITLE, text)
        )

        self.splitter = QSplitter(self)
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.conversation)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([300, 640])
        self.splitter.setChildrenCollapsible(False)
        left.setMinimumWidth(220)
        self.setCentralWidget(self.splitter)
        self._refresh_me()

    def _build_menus(self) -> None:
        add_action = QAction("&Ajouter un collègue par adresse…", self)
        add_action.setShortcut(QKeySequence("Ctrl+N"))
        add_action.triggered.connect(self.add_contact)

        settings_action = QAction("&Préférences…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.edit_settings)

        quit_action = QAction("&Quitter", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.request_quit)

        search_action = QAction("Rechercher", self)
        search_action.setShortcut(QKeySequence.StandardKey.Find)
        search_action.triggered.connect(lambda: self.search.setFocus())
        self.addAction(search_action)

        about_action = QAction("À &propos", self)
        about_action.triggered.connect(self.about)

        menu = self.menuBar().addMenu("&Socket Chat")
        menu.addAction(add_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menuBar().addMenu("&Aide").addAction(about_action)

    def _connect_messenger(self) -> None:
        m = self.messenger
        m.contacts_changed.connect(self._refresh_timer.start)
        m.presence_changed.connect(lambda cid, _on: self.contacts.contact_changed(cid))
        m.typing_changed.connect(lambda cid, _on: self.contacts.contact_changed(cid))
        m.message_added.connect(self._on_message_added)
        m.error.connect(lambda text: QMessageBox.warning(self, APP_TITLE, text))

    # -- Actions ---------------------------------------------------------
    def add_contact(self) -> None:
        dialog = AddContactDialog(self.settings.recent_hosts, self.settings.port, self)
        if dialog.exec() != AddContactDialog.DialogCode.Accepted:
            return
        self.settings.remember_host(dialog.host)
        self._select_new_contact = True
        self.statusBar().showMessage(f"Connexion à {dialog.host}:{dialog.port}…", 5000)
        self.messenger.add_contact(dialog.host, dialog.port)

    def edit_settings(self, *, first_run: bool = False) -> bool:
        dialog = SettingsDialog(
            self.settings.name,
            self.settings.port,
            self,
            close_to_tray=self.settings.close_to_tray,
            tray_available=self.tray_available,
            first_run=first_run,
        )
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return False
        port_changed = dialog.port != self.settings.port
        self.settings.name = dialog.name
        self.settings.port = dialog.port
        self.settings.close_to_tray = dialog.close_to_tray
        self.close_to_tray = self.tray_available and dialog.close_to_tray
        self.messenger.name = dialog.name
        if self.discovery is not None:
            self.discovery.set_name(dialog.name)
        if port_changed and not first_run:
            QMessageBox.information(
                self, APP_TITLE, "Le nouveau port sera utilisé au prochain démarrage."
            )
        self._refresh_me()
        return True

    def open_conversation(self, contact_id: str) -> None:
        """Affiche la fenêtre sur la conversation demandée (clic sur une notification)."""
        self.show_and_raise()
        row = self.contacts.row_of(contact_id)
        if row is None:
            return
        self.search.clear()
        index = self.proxy.mapFromSource(self.contacts.index(row))
        self.contact_list.setCurrentIndex(index)

    def show_and_raise(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        self._quitting = True
        self.quit_requested.emit()
        self.close()

    def about(self) -> None:
        from PySide6 import QtCore

        QMessageBox.about(
            self,
            f"À propos de {APP_TITLE}",
            f"<h3>{APP_TITLE} {__version__}</h3>"
            "<p>Messagerie instantanée pour le réseau interne.</p>"
            f"<p>PySide6 {QtCore.__version__} (Qt {QtCore.qVersion()})</p>",
        )

    # -- Réactions -------------------------------------------------------
    def _on_current_changed(self, current, _previous) -> None:
        if self._refreshing:
            return
        contact_id = current.data(Role.ID) if current.isValid() else None
        self.conversation.set_contact(contact_id)
        self.mark_current_read()

    def _on_message_added(self, record: MessageRecord) -> None:
        if record.contact_id == self.conversation.contact_id:
            self.conversation.add_message(record)
        if record.outgoing:
            return
        if record.contact_id == self.conversation.contact_id and self._is_attended():
            self.messenger.mark_read(record.contact_id)
            return
        contact = self.messenger.store.contact(record.contact_id)
        name = contact.name if contact else "Nouveau message"
        preview = record.preview
        if record.file is not None:
            preview = f"vous propose {record.file.name}"
        self.notify.emit(record.contact_id, name, preview)
        QApplication.alert(self)

    def _is_attended(self) -> bool:
        return self.isVisible() and not self.isMinimized() and self.isActiveWindow()

    def mark_current_read(self) -> None:
        if self.conversation.contact_id is not None and self._is_attended():
            self.messenger.mark_read(self.conversation.contact_id)

    def _refresh_contacts(self) -> None:
        current = self.conversation.contact_id
        self._refreshing = True
        try:
            self.contacts.refresh()
            ids = self.contacts.ids()
            new_ids = [cid for cid in ids if cid not in self._known_ids]
            if self._select_new_contact and new_ids and self._known_ids is not None:
                current = new_ids[0]
                self._select_new_contact = False
            self._known_ids = set(ids)
            if current is not None and (row := self.contacts.row_of(current)) is not None:
                index = self.proxy.mapFromSource(self.contacts.index(row))
                self.contact_list.setCurrentIndex(index)
        finally:
            self._refreshing = False
        if current != self.conversation.contact_id:
            self.conversation.set_contact(current)
            self.mark_current_read()
        has_contacts = bool(self.contacts.rowCount())
        self.contact_list.setVisible(has_contacts)
        self.empty_contacts.setVisible(not has_contacts)
        self.update_unread()

    def update_unread(self) -> None:
        unread = self.messenger.store.total_unread()
        self.setWindowTitle(f"({unread}) {APP_TITLE}" if unread else APP_TITLE)
        self.unread_changed.emit(unread)

    def _refresh_me(self) -> None:
        port = self.messenger.port
        addresses = ", ".join(local_ipv4_addresses()) or "adresse inconnue"
        where = f"{addresses} · port {port}" if port else "hors ligne"
        self.me_label.setText(f"Vous : <b>{self.messenger.name}</b><br>{where}")

    # -- Événements ------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        if event.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange):
            QTimer.singleShot(0, self, self.mark_current_read)
        super().changeEvent(event)

    def _save_layout(self) -> None:
        self.settings.window_geometry = self.saveGeometry()
        self.settings.splitter_state = self.splitter.saveState()
        self.settings.sync()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        self._save_layout()
        if self.close_to_tray and not self._quitting:
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
            return
        super().closeEvent(event)
