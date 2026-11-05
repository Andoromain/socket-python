"""Préférences utilisateur, stockées via ``QSettings``.

Emplacement : registre Windows (``HKCU\\Software\\SocketChat``) ou
``~/.config/SocketChat/SocketChat.conf`` sous Linux.
"""

from __future__ import annotations

import getpass

from PySide6.QtCore import QByteArray, QSettings

from .protocol import DEFAULT_PORT, MAX_NAME_LENGTH, is_valid_id, new_id, validate_name

MAX_RECENT_HOSTS = 10
MAX_RECENT_EMOJIS = 24


def default_name() -> str:
    try:
        return validate_name(getpass.getuser()[:MAX_NAME_LENGTH])
    except Exception:
        return "Utilisateur"


class Settings:
    def __init__(self, store: QSettings | None = None) -> None:
        self._store = store if store is not None else QSettings()

    @property
    def user_id(self) -> str:
        """Identifiant stable de l'utilisateur sur ce poste (créé au premier lancement)."""
        value = self._store.value("user/id", "", str)
        if not is_valid_id(value):
            value = new_id()
            self._store.setValue("user/id", value)
        return value

    @property
    def has_name(self) -> bool:
        return bool(self._store.value("user/name", "", str))

    @property
    def name(self) -> str:
        return self._store.value("user/name", "", str) or default_name()

    @name.setter
    def name(self, value: str) -> None:
        self._store.setValue("user/name", validate_name(value))

    @property
    def port(self) -> int:
        port = self._store.value("network/port", DEFAULT_PORT, int)
        return port if 1 <= port <= 65535 else DEFAULT_PORT

    @port.setter
    def port(self, value: int) -> None:
        if not 1 <= value <= 65535:
            raise ValueError("Port invalide.")
        self._store.setValue("network/port", value)

    @property
    def recent_hosts(self) -> list[str]:
        value = self._store.value("network/recent_hosts", [])
        if isinstance(value, str):  # QSettings renvoie une chaîne pour une liste à 1 élément
            value = [value]
        return [str(host) for host in value or [] if str(host).strip()]

    def remember_host(self, host: str) -> None:
        hosts = [h for h in self.recent_hosts if h != host]
        self._store.setValue("network/recent_hosts", [host, *hosts][:MAX_RECENT_HOSTS])

    @property
    def recent_emojis(self) -> list[str]:
        value = self._store.value("emoji/recent", [])
        if isinstance(value, str):
            value = [value]
        return [str(e) for e in value or [] if str(e)]

    def remember_emoji(self, emoji: str) -> None:
        recent = [e for e in self.recent_emojis if e != emoji]
        self._store.setValue("emoji/recent", [emoji, *recent][:MAX_RECENT_EMOJIS])

    @property
    def close_to_tray(self) -> bool:
        return self._store.value("window/close_to_tray", True, bool)

    @close_to_tray.setter
    def close_to_tray(self, value: bool) -> None:
        self._store.setValue("window/close_to_tray", bool(value))

    @property
    def tray_hint_shown(self) -> bool:
        return self._store.value("window/tray_hint_shown", False, bool)

    @tray_hint_shown.setter
    def tray_hint_shown(self, value: bool) -> None:
        self._store.setValue("window/tray_hint_shown", bool(value))

    @property
    def splitter_state(self) -> QByteArray | None:
        value = self._store.value("window/splitter")
        return value if isinstance(value, QByteArray) else None

    @splitter_state.setter
    def splitter_state(self, value: QByteArray) -> None:
        self._store.setValue("window/splitter", value)

    @property
    def window_geometry(self) -> QByteArray | None:
        value = self._store.value("window/geometry")
        return value if isinstance(value, QByteArray) else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._store.setValue("window/geometry", value)

    def sync(self) -> None:
        self._store.sync()
