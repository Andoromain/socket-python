"""Protocole d'échange entre deux instances de Socket Chat (version 2).

Connexions TCP
--------------
Chaque trame est un objet JSON encodé en UTF-8 sur une seule ligne, terminé
par ``\\n`` (format « JSON Lines »). Les retours à la ligne présents dans les
messages sont échappés par JSON : une trame ne s'étend jamais sur plusieurs
lignes.

* ``{"type": "hello", "version": 2, "id": "<uuid>", "name": "Alice", "port": 12800}``
  envoyée par chaque pair dès l'ouverture de la connexion. ``id`` identifie
  durablement l'utilisateur, ``port`` est son port d'écoute TCP ;
* ``{"type": "chat", "id": "<uuid>", "text": "Bonjour"}`` : un message ;
* ``{"type": "ack", "id": "<uuid>", "status": "delivered" | "read"}`` :
  accusé de réception ou de lecture d'un message ;
* ``{"type": "typing", "active": true}`` : le pair est en train d'écrire ;
* ``{"type": "bye", "reason": "..."}`` : fermeture volontaire.

Détection (UDP)
---------------
Datagrammes JSON (sans ``\\n``) diffusés sur le réseau local :

* ``{"type": "announce", "version": 2, "id": "<uuid>", "name": "Alice", "port": 12800}``
* ``{"type": "leave", "version": 2, "id": "<uuid>"}``
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 2
DEFAULT_PORT = 12800
DISCOVERY_PORT = 12801

#: Taille maximale d'une trame encodée (octets), séparateur compris.
MAX_FRAME_SIZE = 64 * 1024
#: Taille maximale d'un datagramme de détection (octets).
MAX_DATAGRAM_SIZE = 1024
#: Longueur maximale d'un message texte (caractères).
MAX_TEXT_LENGTH = 10_000
#: Longueur maximale d'un nom d'utilisateur (caractères).
MAX_NAME_LENGTH = 32


class ProtocolError(Exception):
    """Trame invalide reçue ou impossible à encoder."""


class MessageType(StrEnum):
    HELLO = "hello"
    CHAT = "chat"
    ACK = "ack"
    TYPING = "typing"
    BYE = "bye"


class AckStatus(StrEnum):
    DELIVERED = "delivered"
    READ = "read"


def new_id() -> str:
    """Nouvel identifiant unique (utilisateur ou message)."""
    return str(uuid.uuid4())


def is_valid_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def validate_name(name: str) -> str:
    """Normalise un nom d'utilisateur et lève ``ValueError`` s'il est invalide."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("Le nom ne peut pas être vide.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Le nom ne doit pas dépasser {MAX_NAME_LENGTH} caractères.")
    return name


@dataclass(frozen=True)
class Message:
    type: MessageType
    id: str = ""  # hello : identifiant de l'utilisateur ; chat/ack : du message
    name: str = ""
    port: int = 0
    text: str = ""
    status: AckStatus | None = None
    active: bool = False
    reason: str = ""

    # -- Constructeurs ---------------------------------------------------
    @classmethod
    def hello(cls, user_id: str, name: str, port: int) -> Message:
        return cls(MessageType.HELLO, id=user_id, name=name, port=port)

    @classmethod
    def chat(cls, message_id: str, text: str) -> Message:
        return cls(MessageType.CHAT, id=message_id, text=text)

    @classmethod
    def ack(cls, message_id: str, status: AckStatus) -> Message:
        return cls(MessageType.ACK, id=message_id, status=status)

    @classmethod
    def typing(cls, active: bool) -> Message:
        return cls(MessageType.TYPING, active=active)

    @classmethod
    def bye(cls, reason: str = "") -> Message:
        return cls(MessageType.BYE, reason=reason)

    # -- Sérialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        kind = self.type.value
        match self.type:
            case MessageType.HELLO:
                return {
                    "type": kind,
                    "version": PROTOCOL_VERSION,
                    "id": self.id,
                    "name": self.name,
                    "port": self.port,
                }
            case MessageType.CHAT:
                return {"type": kind, "id": self.id, "text": self.text}
            case MessageType.ACK:
                assert self.status is not None
                return {"type": kind, "id": self.id, "status": self.status.value}
            case MessageType.TYPING:
                return {"type": kind, "active": self.active}
            case _:
                return {"type": kind, "reason": self.reason}


@dataclass(frozen=True)
class Announcement:
    """Annonce de présence reçue par UDP (``port`` vaut 0 pour un départ)."""

    id: str
    name: str = ""
    port: int = 0

    @property
    def leaving(self) -> bool:
        return self.port == 0


# -- Encodage -------------------------------------------------------------
def _dumps(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encode(message: Message) -> bytes:
    """Encode un message en une trame TCP prête à être envoyée."""
    if message.type is MessageType.CHAT and len(message.text) > MAX_TEXT_LENGTH:
        raise ProtocolError(f"Message trop long (maximum {MAX_TEXT_LENGTH} caractères).")
    frame = _dumps(message.to_dict()) + b"\n"
    if len(frame) > MAX_FRAME_SIZE:
        raise ProtocolError("Trame trop volumineuse.")
    return frame


def encode_announce(user_id: str, name: str, port: int) -> bytes:
    payload = {"type": "announce", "version": PROTOCOL_VERSION, "id": user_id, "name": name}
    return _dumps({**payload, "port": port})


def encode_leave(user_id: str) -> bytes:
    return _dumps({"type": "leave", "version": PROTOCOL_VERSION, "id": user_id})


# -- Décodage -------------------------------------------------------------
def _load(data: bytes) -> dict[str, object]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Trame illisible.") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("Trame invalide.")
    return payload


def _require_str(payload: dict[str, object], key: str, *, max_length: int) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ProtocolError(f"Champ « {key} » invalide.")
    if len(value) > max_length:
        raise ProtocolError(f"Champ « {key} » trop long.")
    return value


def _require_id(payload: dict[str, object]) -> str:
    value = payload.get("id")
    if not is_valid_id(value):
        raise ProtocolError("Identifiant invalide.")
    assert isinstance(value, str)
    return value


def _require_port(payload: dict[str, object]) -> int:
    value = payload.get("port")
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise ProtocolError("Port invalide.")
    return value


def _require_name(payload: dict[str, object]) -> str:
    raw = _require_str(payload, "name", max_length=MAX_NAME_LENGTH * 4)
    try:
        return validate_name(raw)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc


def _check_version(payload: dict[str, object]) -> None:
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("Version de protocole absente.")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"Version de protocole incompatible ({version}, attendue {PROTOCOL_VERSION}) : "
            "mettez Socket Chat à jour sur les deux postes."
        )


def decode(line: bytes) -> Message:
    """Décode une trame TCP (sans le séparateur final)."""
    payload = _load(line)
    try:
        kind = MessageType(payload.get("type"))
    except ValueError as exc:
        raise ProtocolError(f"Type de trame inconnu : {payload.get('type')!r}.") from exc

    match kind:
        case MessageType.HELLO:
            _check_version(payload)
            return Message.hello(
                _require_id(payload), _require_name(payload), _require_port(payload)
            )
        case MessageType.CHAT:
            text = _require_str(payload, "text", max_length=MAX_TEXT_LENGTH)
            return Message.chat(_require_id(payload), text)
        case MessageType.ACK:
            try:
                status = AckStatus(payload.get("status"))
            except ValueError as exc:
                raise ProtocolError("Accusé de réception invalide.") from exc
            return Message.ack(_require_id(payload), status)
        case MessageType.TYPING:
            active = payload.get("active")
            if not isinstance(active, bool):
                raise ProtocolError("Champ « active » invalide.")
            return Message.typing(active)
        case _:
            return Message.bye(_require_str(payload, "reason", max_length=500))


def decode_announce(datagram: bytes) -> Announcement:
    """Décode un datagramme de détection (lève ``ProtocolError`` s'il est invalide)."""
    if len(datagram) > MAX_DATAGRAM_SIZE:
        raise ProtocolError("Datagramme trop volumineux.")
    payload = _load(datagram)
    _check_version(payload)
    match payload.get("type"):
        case "announce":
            return Announcement(
                _require_id(payload), _require_name(payload), _require_port(payload)
            )
        case "leave":
            return Announcement(_require_id(payload))
        case other:
            raise ProtocolError(f"Type d'annonce inconnu : {other!r}.")


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
