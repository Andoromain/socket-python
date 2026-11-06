"""Point d'entrée de l'application."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from importlib.resources import files
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__

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
    # Vérification de l'exécutable construit (CI) : le moteur des appels démarre-t-il ?
    parser.add_argument("--check-calls", action="store_true", help=argparse.SUPPRESS)
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


def prepare_webengine() -> None:
    """Réglages de Qt WebEngine (appels), à faire avant de créer l'application."""
    from PySide6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    # Ubuntu 24.04 et d'autres interdisent les espaces de noms utilisateur au programme
    # qui n'a pas de profil AppArmor : le bac à sable de Chrome ne peut alors pas
    # démarrer et WebEngine s'arrête. La page des appels n'affiche que notre propre
    # contenu local (aucun site web) : on se passe du bac à sable dans ce cas.
    if sys.platform.startswith("linux") and "QTWEBENGINE_DISABLE_SANDBOX" not in os.environ:
        try:
            flag = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").read_text()
        except OSError:
            flag = "0"
        if flag.strip() == "1" or os.geteuid() == 0:
            os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"


def check_calls(timeout_ms: int = 60_000) -> int:
    """Charge la page des appels (WebEngine) puis quitte : 0 si elle répond."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .call_engine import CallHost

    prepare_webengine()
    app = QApplication.instance() or QApplication([sys.argv[0]])
    host = CallHost()
    result = {"code": 1}

    def on_ready() -> None:
        result["code"] = 0
        app.quit()

    host.bridge.page_ready.connect(on_ready)
    QTimer.singleShot(timeout_ms, app.quit)
    host.ensure_page()
    if not host.ready:
        app.exec()
    if sys.stdout is not None:
        print("Moteur des appels :", "prêt" if result["code"] == 0 else "ne répond pas")
    return result["code"]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.check_calls:
        return check_calls()

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .discovery import Discovery
    from .messenger import Messenger
    from .settings import Settings
    from .store import Store
    from .ui.main_window import MainWindow
    from .ui.tray import Tray
    from .ui.updates import UpdateBanner, UpdateController

    QApplication.setOrganizationName(ORGANIZATION)
    QApplication.setApplicationName(APPLICATION)
    QApplication.setApplicationDisplayName("Socket Chat")
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName("socket-chat")

    prepare_webengine()
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
    store = Store(data_dir / "history.sqlite3")
    messenger = Messenger(store, settings.user_id, settings.name, avatar=settings.avatar)
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
    if discovery.start():
        messenger.attach_discovery(discovery)
    else:
        log.warning("Détection automatique indisponible : ajout par adresse uniquement")

    calls = None
    ring = None
    try:
        from .call_engine import CallHost, WebEngineCallEngine
        from .calls import CallManager

        call_host = CallHost(messenger)
        calls = CallManager(messenger, lambda: WebEngineCallEngine(call_host), messenger)
        ring = call_host.ring
    except ImportError as exc:  # installation sans Qt WebEngine : pas d'appels
        log.warning("Appels indisponibles : %s", exc)

    window = MainWindow(settings, messenger, discovery, calls=calls, ring=ring)
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
        if calls is not None:
            calls.stop()
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
