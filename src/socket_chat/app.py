"""Point d'entrée de l'application."""

from __future__ import annotations

import argparse
import logging
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .settings import Settings
    from .ui.main_window import MainWindow

    QApplication.setOrganizationName(ORGANIZATION)
    QApplication.setApplicationName(APPLICATION)
    QApplication.setApplicationDisplayName("Socket Chat")
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName("socket-chat")

    app = QApplication([sys.argv[0], *args.qt_args])
    app.setWindowIcon(QIcon(icon_path()))

    location = QStandardPaths.StandardLocation.AppLocalDataLocation
    data_dir = Path(QStandardPaths.writableLocation(location))
    log_file = setup_logging(data_dir, args.debug)
    log.info("Socket Chat %s démarré (journal : %s)", __version__, log_file)

    settings = Settings()
    window = MainWindow(settings)
    window.show()
    if not settings.has_name and not window.edit_settings(first_run=True):
        settings.name = settings.name  # mémorise le nom proposé par défaut

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
