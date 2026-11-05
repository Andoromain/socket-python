"""Protocole d'échange entre deux instances de Socket Chat.

Chaque trame est un objet JSON encodé en UTF-8 sur une seule ligne,
terminé par ``\\n`` (format « JSON Lines »). Les retours à la ligne présents
dans les messages sont échappés par JSON, ce qui garantit qu'une trame ne
s'étend jamais sur plusieurs lignes.

Trames définies :

* ``{"type": "hello", "version": 1, "name": "Alice"}`` : envoyée par chaque
  pair dès l'ouverture de la connexion ;
* ``{"type": "chat", "text": "Bonjour"}`` : un message ;
* ``{"type": "bye", "reason": "..."}`` : fin de conversation volontaire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 1
DEFAULT_PORT = 12800

#: Taille maximale d'une trame encodée (octets), séparateur compris.
MAX_FRAME_SIZE = 64 * 1024
#: Longueur maximale d'un message texte (caractères).
MAX_TEXT_LENGTH = 10_000
#: Longueur maximale d'un nom d'utilisateur (caractères).
MAX_NAME_LENGTH = 32


class ProtocolError(Exception):
    """Trame invalide reçue ou impossible à encoder."""


class MessageType(StrEnum):
    HELLO = "hello"
    CHAT = "chat"
    BYE = "bye"


@dataclass(frozen=True)
class Message:
    type: MessageType
    name: str = ""
    text: str = ""
    reason: str = ""
    version: int = PROTOCOL_VERSION

    # -- Constructeurs ---------------------------------------------------
    @classmethod
    def hello(cls, name: str) -> Message:
        return cls(MessageType.HELLO, name=name)

    @classmethod
    def chat(cls, text: str) -> Message:
        return cls(MessageType.CHAT, text=text)

    @classmethod
    def bye(cls, reason: str = "") -> Message:
        return cls(MessageType.BYE, reason=reason)

    # -- Sérialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        if self.type is MessageType.HELLO:
            return {"type": self.type.value, "version": self.version, "name": self.name}
        if self.type is MessageType.CHAT:
            return {"type": self.type.value, "text": self.text}
        return {"type": self.type.value, "reason": self.reason}


def validate_name(name: str) -> str:
    """Normalise un nom d'utilisateur et lève ``ValueError`` s'il est invalide."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Le nom ne peut pas être vide.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Le nom ne doit pas dépasser {MAX_NAME_LENGTH} caractères.")
    return name


def encode(message: Message) -> bytes:
    """Encode un message en une trame prête à être envoyée."""
    if message.type is MessageType.CHAT and len(message.text) > MAX_TEXT_LENGTH:
        raise ProtocolError(f"Message trop long (maximum {MAX_TEXT_LENGTH} caractères).")
    data = json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":"))
    frame = data.encode("utf-8") + b"\n"
    if len(frame) > MAX_FRAME_SIZE:
        raise ProtocolError("Trame trop volumineuse.")
    return frame


def _require_str(payload: dict[str, object], key: str, *, max_length: int) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ProtocolError(f"Champ « {key} » invalide.")
    if len(value) > max_length:
        raise ProtocolError(f"Champ « {key} » trop long.")
    return value


def decode(line: bytes) -> Message:
    """Décode une trame (sans le séparateur final)."""
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Trame illisible.") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("Trame invalide.")

    try:
        kind = MessageType(payload.get("type"))
    except ValueError as exc:
        raise ProtocolError(f"Type de trame inconnu : {payload.get('type')!r}.") from exc

    if kind is MessageType.HELLO:
        version = payload.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ProtocolError("Version de protocole absente.")
        if version != PROTOCOL_VERSION:
            raise ProtocolError(
                f"Version de protocole incompatible ({version}, attendue {PROTOCOL_VERSION})."
            )
        raw_name = _require_str(payload, "name", max_length=MAX_NAME_LENGTH * 4)
        try:
            name = validate_name(raw_name)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        return Message(kind, name=name, version=version)
    if kind is MessageType.CHAT:
        return Message(kind, text=_require_str(payload, "text", max_length=MAX_TEXT_LENGTH))
    return Message(kind, reason=_require_str(payload, "reason", max_length=500))


class FrameDecoder:
    """Reconstitue les trames à partir d'un flux d'octets TCP arbitrairement découpé."""

    def __init__(self, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self._buffer = bytearray()
        self._max_frame_size = max_frame_size

    def feed(self, data: bytes) -> list[Message]:
        """Ajoute des octets reçus et renvoie les messages complets décodés."""
        self._buffer += data
        messages: list[Message] = []
        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                if len(self._buffer) >= self._max_frame_size:
                    raise ProtocolError("Trame trop volumineuse.")
                return messages
            if index + 1 > self._max_frame_size:
                raise ProtocolError("Trame trop volumineuse.")
            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            if line.strip():
                messages.append(decode(line))
