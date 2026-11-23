"""Préférences utilisateur, stockées via ``QSettings``.

Emplacement : registre Windows (``HKCU\\Software\\SocketChat``) ou
``~/.config/SocketChat/SocketChat.conf`` sous Linux.
"""

from __future__ import annotations

import getpass

from PySide6.QtCore import QByteArray, QSettings

from .netpolicy import Network, parse_networks
from .protocol import DEFAULT_PORT, MAX_NAME_LENGTH, is_valid_id, new_id, validate_name
from .updater import DEFAULT_UPDATE_URL

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
    def allowed_networks(self) -> list[Network]:
        """Réseaux autorisés (clé d'administration, voir ``netpolicy``) ; vide : tous."""
        value = self._store.value("network/allowed_networks", "")
        if isinstance(value, (list, tuple)):  # liste INI ou REG_MULTI_SZ
            return parse_networks([str(v) for v in value])
        return parse_networks(str(value or ""))

    # -- Mises à jour ------------------------------------------------------
    # Ces clés peuvent aussi être fixées par l'administrateur pour tous les utilisateurs
    # (QSettings lit en repli HKLM\Software\SocketChat sous Windows, /etc/xdg sous Linux).
    @property
    def update_url(self) -> str:
        return self._store.value("update/url", "", str) or DEFAULT_UPDATE_URL

    @property
    def update_enabled(self) -> bool:
        return self._store.value("update/enabled", True, bool)

    @property
    def skipped_version(self) -> str:
        return self._store.value("update/skipped", "", str)

    @skipped_version.setter
    def skipped_version(self, value: str) -> None:
        self._store.setValue("update/skipped", value)

    @property
    def notified_version(self) -> str:
        return self._store.value("update/notified", "", str)

    @notified_version.setter
    def notified_version(self, value: str) -> None:
        self._store.setValue("update/notified", value)

    @property
    def avatar(self) -> bytes:
        """Photo de profil (JPEG) envoyée aux contacts ; vide : initiales."""
        value = self._store.value("user/avatar")
        return bytes(value.data()) if isinstance(value, QByteArray) else b""

    @avatar.setter
    def avatar(self, value: bytes) -> None:
        if value:
            self._store.setValue("user/avatar", QByteArray(value))
        else:
            self._store.remove("user/avatar")

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
