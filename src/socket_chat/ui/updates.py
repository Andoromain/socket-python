"""Bandeau « nouvelle version disponible » et déroulement de la mise à jour."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..messenger import Messenger
from ..settings import Settings
from ..updater import (
    Manifest,
    ManifestError,
    UpdateChecker,
    UpdateDownloader,
    can_self_update,
    cleanup_downloads,
    install_update,
    platform_key,
)
from .theme import current_theme, human_size

log = logging.getLogger(__name__)

MANUAL_UPDATE_HELP = (
    "Cette copie de Socket Chat a été installée avec Python (pip ou pipx) : mettez-la à "
    "jour de la même façon, par exemple :\n\npipx install --force <adresse du dépôt>"
)


class UpdateBanner(QFrame):
    """Bandeau affiché en haut de la fenêtre."""

    notes_requested = Signal()
    update_requested = Signal()
    later_requested = Signal()
    skip_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("updateBanner")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        dark = current_theme().dark
        background, border, text = (
            ("#1c3247", "#2f4f6b", "#d6e8f7") if dark else ("#e3f0fb", "#b9d6ee", "#0d3350")
        )
        self.setStyleSheet(
            f"#updateBanner {{ background: {background}; border: none;"
            f" border-bottom: 1px solid {border}; }}"
            f"#updateBanner QLabel {{ color: {text}; }}"
        )
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.progress = QProgressBar(self)
        self.progress.setMaximumWidth(220)
        self.progress.setTextVisible(True)
        self.notes_button = QPushButton("Nouveautés", self)
        self.update_button = QPushButton("Mettre à jour", self)
        self.update_button.setDefault(True)
        self.skip_button = QPushButton("Ignorer cette version", self)
        self.skip_button.setFlat(True)
        self.later_button = QPushButton("Plus tard", self)
        self.later_button.setFlat(True)
        self.cancel_button = QPushButton("Annuler", self)

        self.notes_button.clicked.connect(self.notes_requested)
        self.update_button.clicked.connect(self.update_requested)
        self.later_button.clicked.connect(self.later_requested)
        self.skip_button.clicked.connect(self.skip_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.progress)
        for button in (
            self.notes_button,
            self.update_button,
            self.cancel_button,
            self.skip_button,
            self.later_button,
        ):
            layout.addWidget(button)
        self.hide()

    def show_available(self, version: str, size: int | None, *, can_install: bool) -> None:
        detail = f" ({human_size(size)})" if size else ""
        self.label.setText(f"<b>Socket Chat {version}</b> est disponible{detail}.")
        self.update_button.setText("Mettre à jour" if can_install else "Comment mettre à jour ?")
        self._set_buttons(update=True, notes=True, skip=True, later=True)
        self.progress.hide()
        self.show()

    def show_progress(self, done: int, total: int) -> None:
        self.label.setText("Téléchargement de la mise à jour…")
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.progress.setFormat(f"{human_size(done)} / {human_size(total)}")
        self.progress.show()
        self._set_buttons(cancel=True)
        self.show()

    def show_installing(self) -> None:
        self.label.setText("Installation de la mise à jour : Socket Chat va redémarrer…")
        self.progress.setRange(0, 0)
        self._set_buttons()

    def show_error(self, message: str) -> None:
        self.label.setText(f"⚠ {message}")
        self.progress.hide()
        self.update_button.setText("Réessayer")
        self._set_buttons(update=True, later=True)
        self.show()

    def _set_buttons(self, *, update=False, notes=False, skip=False, later=False, cancel=False):
        self.update_button.setVisible(update)
        self.notes_button.setVisible(notes)
        self.skip_button.setVisible(skip)
        self.later_button.setVisible(later)
        self.cancel_button.setVisible(cancel)


class UpdateController(QObject):
    """Relie la vérification, le bandeau, le téléchargement et l'installation."""

    #: L'installation est lancée : l'application doit se fermer.
    quit_requested = Signal()
    #: Notification à afficher dans la zone de notification (titre, texte).
    notify = Signal(str, str)

    def __init__(
        self,
        settings: Settings,
        messenger: Messenger,
        banner: UpdateBanner,
        parent: QObject | None = None,
        *,
        checker: UpdateChecker | None = None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.messenger = messenger
        self.banner = banner
        self.checker = checker or UpdateChecker(settings.update_url, __version__, self)
        self.downloader = UpdateDownloader(self)
        self.manifest: Manifest | None = None
        # Points d'accroche remplaçables (tests, autres plates-formes).
        self.self_update = can_self_update()
        self.platform = platform_key()
        self.installer: Callable[[str], None] = install_update
        self.inform: Callable[[str, str], None] = self._message_box
        self.confirm: Callable[[str, str], bool] = self._confirm_box
        #: Installer sans attendre de clic (option --update).
        self.auto_install = False

        self.checker.update_available.connect(self._on_available)
        self.checker.up_to_date.connect(self._on_up_to_date)
        self.checker.failed.connect(self._on_check_failed)
        self.downloader.progress.connect(self.banner.show_progress)
        self.downloader.finished.connect(self._on_downloaded)
        self.downloader.failed.connect(self.banner.show_error)
        banner.notes_requested.connect(self.show_notes)
        banner.update_requested.connect(self.start_update)
        banner.later_requested.connect(banner.hide)
        banner.skip_requested.connect(self.skip_version)
        banner.cancel_requested.connect(self.downloader.cancel)

    # -- API -------------------------------------------------------------
    def start(self) -> None:
        if cleanup_downloads():
            log.info("Fichiers de la mise à jour précédente supprimés")
        if self.settings.update_enabled:
            self.checker.start()
        else:
            log.info("Mises à jour automatiques désactivées (update/enabled)")

    def stop(self) -> None:
        self.checker.stop()
        self.downloader.cancel()

    def check_now(self) -> None:
        self.checker.check_now(manual=True)

    def menu_action(self, parent: QWidget) -> QAction:
        action = QAction("Rechercher des mises à &jour…", parent)
        action.triggered.connect(self.check_now)
        return action

    def show_notes(self) -> None:
        if self.manifest is None:
            return
        dialog = QDialog(self.banner.window())
        dialog.setWindowTitle(f"Nouveautés de Socket Chat {self.manifest.version}")
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(self.manifest.notes or "Aucune note pour cette version.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(browser)
        layout.addWidget(buttons)
        dialog.resize(520, 420)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        self.notes_dialog = dialog

    def skip_version(self) -> None:
        if self.manifest is not None:
            self.settings.skipped_version = self.manifest.version
        self.banner.hide()

    def start_update(self) -> None:
        manifest = self.manifest
        if manifest is None or self.downloader.running:
            return
        if not self.self_update:
            self.inform("Mise à jour", MANUAL_UPDATE_HELP)
            return
        asset = manifest.asset_for(self.platform)
        if asset is None:
            self.banner.show_error("Aucun installeur de cette version pour votre système.")
            return
        busy = self.messenger.transfers.active_count
        if busy and not self.confirm(
            "Mise à jour",
            f"{busy} transfert(s) de fichier en cours seront interrompus.\n"
            "Mettre à jour maintenant ?",
        ):
            return
        self.banner.show_progress(0, asset.size)
        self.downloader.start(asset)

    # -- Réactions -------------------------------------------------------
    def _on_available(self, manifest: Manifest) -> None:
        manual = self.checker.manual
        if self.settings.skipped_version == manifest.version and not manual:
            return
        self.manifest = manifest
        asset = manifest.asset_for(self.platform)
        self.banner.show_available(
            manifest.version, asset.size if asset else None, can_install=self.self_update
        )
        if self.auto_install:
            self.auto_install = False
            self.start_update()
            return
        if self.settings.notified_version != manifest.version:
            self.settings.notified_version = manifest.version
            self.notify.emit(
                "Mise à jour disponible",
                f"Socket Chat {manifest.version} est disponible : "
                "ouvrez la fenêtre pour l'installer.",
            )

    def _on_up_to_date(self) -> None:
        if self.checker.manual:
            self.inform("Mises à jour", f"Vous utilisez la dernière version ({__version__}).")

    def _on_check_failed(self, message: str) -> None:
        if self.checker.manual:
            self.inform("Mises à jour", message)

    def _on_downloaded(self, path: str) -> None:
        self.banner.show_installing()
        try:
            self.installer(path)
        except (ManifestError, OSError) as exc:
            self.banner.show_error(str(exc))
            return
        self.quit_requested.emit()

    # -- Boîtes de dialogue par défaut -----------------------------------
    def _message_box(self, title: str, text: str) -> None:
        QMessageBox.information(self.banner.window(), title, text)

    def _confirm_box(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self.banner.window(), title, text)
        return answer == QMessageBox.StandardButton.Yes
