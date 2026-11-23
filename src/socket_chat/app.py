"""Point d'entrée de l'application."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from importlib.resources import files
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, identity, vault

ORGANIZATION = "SocketChat"
APPLICATION = "SocketChat"

log = logging.getLogger("socket_chat")


def icon_path() -> str:
    return str(files("socket_chat") / "resources" / "icon.png")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="socket-chat", description="Messagerie instantanée de poste à poste."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="journalisation détaillée")
    parser.add_argument(
        "--update",
        action="store_true",
        help="vérifier tout de suite les mises à jour et installer sans demander",
    )
    # Les options propres à Qt (-platform, -style…) sont transmises telles quelles.
    args, qt_args = parser.parse_known_args(argv)
    args.qt_args = qt_args
    return args


def setup_logging(log_dir: Path, debug: bool) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "socket-chat.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:  # absent pour un exécutable Windows sans console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_file


def _instance_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "socket-chat-" + "".join(c for c in user if c.isalnum())


def open_history(data_dir: Path):
    """Historique chiffré du poste ; renvoie ``(store, avertissement ou None)``.

    La clé (``history.key``) est protégée par ``vault``. Si elle est perdue ou illisible
    (profil copié sur un autre poste, autre compte Windows), l'historique ne peut plus
    être lu : il est mis de côté (renommé, jamais effacé) et un nouveau commence.
    """
    from .store import HistoryLockedError, Store

    path = data_dir / "history.sqlite3"
    key_path = data_dir / "history.key"
    lost = False
    try:
        key, _created = vault.load_or_create(key_path)
    except vault.SecretUnavailableError as exc:
        log.warning("Clé de l'historique illisible (%s)", exc)
        vault.set_aside(key_path)
        key, _created = vault.load_or_create(key_path)
        lost = True
    try:
        return Store(path, key), None
    except HistoryLockedError as exc:
        log.warning("Historique illisible (%s)", exc)
        lost = True
    old = vault.set_aside(path)
    store = Store(path, key)
    if not lost or old is None:
        return store, None
    return store, (
        "L'historique des conversations de ce poste ne peut plus être déchiffré (clé "
        "perdue ou profil venu d'un autre poste). Un nouvel historique commence ; l'ancien "
        f"est conservé, chiffré, dans « {old.name} »."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .discovery import Discovery
    from .messenger import Messenger
    from .settings import Settings
    from .ui.main_window import MainWindow
    from .ui.tray import Tray
    from .ui.updates import UpdateBanner, UpdateController

    QApplication.setOrganizationName(ORGANIZATION)
    QApplication.setApplicationName(APPLICATION)
    QApplication.setApplicationDisplayName("Socket Chat")
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName("socket-chat")

    app = QApplication([sys.argv[0], *args.qt_args])
    icon = QIcon(icon_path())
    app.setWindowIcon(icon)

    # Une seule instance par utilisateur : un second lancement réaffiche la première.
    instance = _instance_name()
    probe = QLocalSocket()
    probe.connectToServer(instance)
    if probe.waitForConnected(500):
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(instance)  # serveur orphelin après un plantage (Unix)
    local_server = QLocalServer()
    local_server.listen(instance)

    location = QStandardPaths.StandardLocation.AppLocalDataLocation
    data_dir = Path(QStandardPaths.writableLocation(location))
    log_file = setup_logging(data_dir, args.debug)
    log.info("Socket Chat %s démarré (journal : %s)", __version__, log_file)

    settings = Settings()
    store, history_warning = open_history(data_dir)
    keys = identity.load_or_create(data_dir / "identity.key")
    messenger = Messenger(store, settings.user_id, settings.name, keys)
    messenger.set_avatar(settings.avatar)
    allowed_networks = settings.allowed_networks
    if allowed_networks:
        log.info("Réseaux autorisés : %s", ", ".join(map(str, allowed_networks)))
    messenger.allowed_networks = allowed_networks
    messenger.error.connect(lambda text: log.warning("%s", text))
    if not messenger.start(settings.port):
        QMessageBox.critical(
            None,
            "Socket Chat",
            f"Impossible d'utiliser le port {settings.port} : il est déjà occupé par un autre "
            "programme.\nModifiez le port dans les préférences (ou le fichier de "
            "configuration) puis relancez Socket Chat.",
        )
        store.close()
        return 1

    discovery = Discovery(settings.user_id, settings.name, messenger.port or settings.port)
    discovery.allowed_networks = allowed_networks
    if discovery.start():
        messenger.attach_discovery(discovery)
    else:
        log.warning("Détection automatique indisponible : ajout par adresse uniquement")

    window = MainWindow(settings, messenger, discovery)
    if history_warning:
        QMessageBox.warning(None, "Socket Chat", history_warning)
    banner = UpdateBanner(window)
    window.add_banner(banner)
    updates = UpdateController(settings, messenger, banner, window)
    window.help_menu.insertAction(window.help_menu.actions()[0], updates.menu_action(window))
    updates.quit_requested.connect(window.request_quit)

    tray = None
    if Tray.available():
        tray = Tray(icon)
        tray.show()
        window.tray_available = True
        window.close_to_tray = settings.close_to_tray
        app.setQuitOnLastWindowClosed(False)
        window.unread_changed.connect(tray.set_unread)
        window.notify.connect(tray.notify)
        tray.open_requested.connect(window.show_and_raise)
        tray.conversation_requested.connect(window.open_conversation)
        tray.quit_requested.connect(window.request_quit)
        window.quit_requested.connect(app.quit)

        def on_hidden() -> None:
            if not settings.tray_hint_shown:
                settings.tray_hint_shown = True
                tray.info(
                    "Socket Chat reste actif",
                    "Vous serez prévenu des nouveaux messages. Clic droit sur l'icône "
                    "pour quitter.",
                )

        window.hidden_to_tray.connect(on_hidden)
        updates.notify.connect(tray.info)
        window.update_unread()  # état initial de l'icône

    local_server.newConnection.connect(
        lambda: (local_server.nextPendingConnection().deleteLater(), window.show_and_raise())
    )

    def shutdown() -> None:
        updates.stop()
        discovery.stop()
        messenger.stop()
        if tray is not None:
            tray.hide()
        local_server.close()
        store.close()

    app.aboutToQuit.connect(shutdown)

    window.show()
    if args.update:
        updates.auto_install = True
        updates.checker.check_now()
    updates.start()
    if not settings.has_name and not window.edit_settings(first_run=True):
        settings.name = settings.name  # mémorise le nom proposé par défaut

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
