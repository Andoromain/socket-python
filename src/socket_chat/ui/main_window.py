"""Fenêtre principale de Socket Chat."""

from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeyEvent, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..network import ChatSession, SessionState, local_ipv4_addresses
from ..protocol import MAX_TEXT_LENGTH
from ..settings import Settings
from .dialogs import ConnectDialog, SettingsDialog

APP_TITLE = "Socket Chat"


class MessageInput(QPlainTextEdit):
    """Zone de saisie : Entrée envoie, Maj+Entrée insère un retour à la ligne."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - API Qt
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings or Settings()
        self.session = ChatSession(self.settings.name, self)

        self._build_ui()
        self._build_actions()
        self._connect_session()
        self._on_state_changed(SessionState.IDLE)

        geometry = self.settings.window_geometry
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(520, 600)

    # -- Construction ----------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle(APP_TITLE)

        self.transcript = QTextBrowser(self)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setPlaceholderText(
            "Hébergez une conversation ou rejoignez celle d'un collègue pour commencer."
        )

        self.input = MessageInput(self)
        self.input.setPlaceholderText("Écrivez un message… (Maj+Entrée pour aller à la ligne)")
        self.input.setFixedHeight(self.fontMetrics().lineSpacing() * 4)
        self.input.submitted.connect(self.send_message)

        self.send_button = QPushButton("&Envoyer", self)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.send_message)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.addWidget(self.transcript, 1)
        layout.addLayout(input_row)
        self.setCentralWidget(central)

        self.status_label = QLabel(self)
        self.statusBar().addWidget(self.status_label, 1)

    def _build_actions(self) -> None:
        style = self.style()
        self.host_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon), "&Héberger", self
        )
        self.host_action.setShortcut(QKeySequence("Ctrl+H"))
        self.host_action.setStatusTip("Attendre qu'un collègue se connecte à ce poste")
        self.host_action.triggered.connect(self.host)

        self.join_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "&Rejoindre…", self
        )
        self.join_action.setShortcut(QKeySequence("Ctrl+J"))
        self.join_action.setStatusTip("Se connecter au poste d'un collègue")
        self.join_action.triggered.connect(self.join)

        self.stop_action = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserStop), "&Déconnecter", self
        )
        self.stop_action.setShortcut(QKeySequence("Ctrl+D"))
        self.stop_action.triggered.connect(self.stop)

        self.settings_action = QAction("&Préférences…", self)
        self.settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self.settings_action.triggered.connect(self.edit_settings)

        self.clear_action = QAction("&Effacer l'historique", self)
        self.clear_action.triggered.connect(self.transcript.clear)

        quit_action = QAction("&Quitter", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)

        about_action = QAction("À &propos", self)
        about_action.triggered.connect(self.about)

        menu = self.menuBar().addMenu("&Conversation")
        menu.addActions([self.host_action, self.join_action, self.stop_action])
        menu.addSeparator()
        menu.addAction(self.clear_action)
        menu.addAction(self.settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menuBar().addMenu("&Aide").addAction(about_action)

        toolbar = self.addToolBar("Conversation")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addActions([self.host_action, self.join_action, self.stop_action])

    def _connect_session(self) -> None:
        self.session.state_changed.connect(self._on_state_changed)
        self.session.peer_joined.connect(self._on_peer_joined)
        self.session.peer_left.connect(self._on_peer_left)
        self.session.message_received.connect(self._on_message_received)
        self.session.error.connect(self._on_error)

    # -- Actions ---------------------------------------------------------
    def host(self) -> None:
        self.session.host(self.settings.port)

    def join(self) -> None:
        dialog = ConnectDialog(self.settings.recent_hosts, self.settings.port, self)
        if dialog.exec() != ConnectDialog.DialogCode.Accepted:
            return
        self.settings.remember_host(dialog.host)
        self._append_info(f"Connexion à {dialog.host}:{dialog.port}…")
        self.session.join(dialog.host, dialog.port)

    def stop(self) -> None:
        self.session.stop()

    def send_message(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.session.state != SessionState.CONNECTED:
            return
        if len(text) > MAX_TEXT_LENGTH:
            QMessageBox.warning(
                self,
                "Message trop long",
                f"Un message ne peut pas dépasser {MAX_TEXT_LENGTH} caractères.",
            )
            return
        self.session.send(text)
        self._append_message(self.session.local_name, text, own=True)
        self.input.clear()

    def edit_settings(self, *, first_run: bool = False) -> bool:
        dialog = SettingsDialog(self.settings.name, self.settings.port, self, first_run=first_run)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return False
        self.settings.name = dialog.name
        self.settings.port = dialog.port
        self.session.local_name = dialog.name
        if self.session.state != SessionState.IDLE:
            self.statusBar().showMessage(
                "Les changements s'appliqueront à la prochaine conversation.", 5000
            )
        self._refresh_status()
        return True

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"À propos de {APP_TITLE}",
            f"<h3>{APP_TITLE} {__version__}</h3>"
            "<p>Messagerie instantanée de poste à poste pour le réseau interne.</p>"
            f"<p>{_qt_version()}</p>",
        )

    # -- Réactions à la session -------------------------------------------
    def _on_state_changed(self, state: SessionState) -> None:
        connected = state == SessionState.CONNECTED
        self.host_action.setEnabled(state == SessionState.IDLE)
        self.join_action.setEnabled(state == SessionState.IDLE)
        self.stop_action.setEnabled(state != SessionState.IDLE)
        self.input.setEnabled(connected)
        self.send_button.setEnabled(connected)
        if connected:
            self.input.setFocus()
        if state == SessionState.LISTENING and not self.session.peer_name:
            self._append_info(f"En attente d'un correspondant ({self._listening_text()}).")
        self._refresh_status()

    def _on_peer_joined(self, name: str) -> None:
        self._append_info(f"{name} a rejoint la conversation.")
        QApplication.alert(self)

    def _on_peer_left(self, name: str, reason: str) -> None:
        text = f"{name} a quitté la conversation."
        if reason:
            text = f"{text} ({reason})"
        self._append_info(text)

    def _on_message_received(self, name: str, text: str) -> None:
        self._append_message(name, text, own=False)
        QApplication.alert(self)

    def _on_error(self, message: str) -> None:
        self._append_info(message, error=True)
        QMessageBox.warning(self, APP_TITLE, message)

    # -- Affichage -------------------------------------------------------
    def _listening_text(self) -> str:
        port = self.session.listening_port
        addresses = local_ipv4_addresses() or ["localhost"]
        return ", ".join(f"{address}:{port}" for address in addresses)

    def _refresh_status(self) -> None:
        state = self.session.state
        name = self.session.local_name
        if state == SessionState.CONNECTED:
            text = f"Connecté à {self.session.peer_name}"
        elif state == SessionState.LISTENING:
            text = f"En écoute sur {self._listening_text()}"
        elif state == SessionState.CONNECTING:
            text = "Connexion en cours…"
        else:
            text = "Non connecté"
        self.status_label.setText(f"{text}  —  vous : {name}")
        if state == SessionState.CONNECTED:
            self.setWindowTitle(f"{self.session.peer_name} — {APP_TITLE}")
        else:
            self.setWindowTitle(APP_TITLE)

    def _append_message(self, name: str, text: str, *, own: bool) -> None:
        role = QPalette.ColorRole.Highlight if own else QPalette.ColorRole.Link
        color = self.palette().color(role).name()
        body = html.escape(text).replace("\n", "<br>")
        self.transcript.append(
            f'<p><span style="color:gray">{_now()}</span> '
            f'<b style="color:{color}">{html.escape(name)}</b><br>{body}</p>'
        )

    def _append_info(self, text: str, *, error: bool = False) -> None:
        color = "#c62828" if error else "gray"
        self.transcript.append(
            f'<p style="color:{color}"><i>{_now()} — {html.escape(text)}</i></p>'
        )

    # -- Événements ------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        if self.session.state == SessionState.CONNECTED:
            answer = QMessageBox.question(
                self,
                APP_TITLE,
                f"Une conversation avec {self.session.peer_name} est en cours.\n"
                "Quitter quand même ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.session.stop()
        self.settings.window_geometry = self.saveGeometry()
        self.settings.sync()
        super().closeEvent(event)


def _now() -> str:
    return datetime.now().strftime("%H:%M")


def _qt_version() -> str:
    from PySide6 import QtCore

    return f"PySide6 {QtCore.__version__} (Qt {QtCore.qVersion()})"
