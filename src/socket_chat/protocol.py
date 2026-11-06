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
* ``{"type": "react", "id": "<uuid>", "emoji": "👍"}`` : réaction à un message
  (``""`` la retire) ;
* ``{"type": "file_offer", "id": "<uuid>", "name": "rapport.pdf", "size": 1234,
  "thumb": "<base64>"}`` : proposition de fichier (aperçu JPEG facultatif) ;
* ``{"type": "file_answer", "id": "<uuid>", "accept": true}`` : réponse du destinataire ;
* ``{"type": "file_chunk", "id": "<uuid>", "offset": 0, "data": "<base64>"}`` : un bloc ;
* ``{"type": "file_end", "id": "<uuid>", "sha256": "<hex>"}`` : fin et empreinte ;
* ``{"type": "file_cancel", "id": "<uuid>", "reason": "..."}`` : abandon (des deux côtés) ;
* ``{"type": "avatar", "data": "<base64>"}`` : photo de profil de l'expéditeur (JPEG
  ou PNG carré, 32 Kio au plus), envoyée à l'ouverture de la connexion et à chaque
  changement ; ``""`` la retire (depuis la 3.4, ignorée par les versions antérieures) ;
* ``{"type": "bye", "reason": "..."}`` : fermeture volontaire.

Les trames d'un type inconnu sont ignorées : une version plus récente peut ajouter
des fonctions sans couper la connexion avec les anciennes.

Détection (UDP)
---------------
Datagrammes JSON (sans ``\\n``) diffusés sur le réseau local :

* ``{"type": "announce", "version": 2, "id": "<uuid>", "name": "Alice", "port": 12800}``
* ``{"type": "leave", "version": 2, "id": "<uuid>"}``
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum

PROTOCOL_VERSION = 3
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
#: Taille maximale d'un fichier transféré (octets).
MAX_FILE_SIZE = 2 * 1024**3
#: Taille d'un bloc de fichier avant encodage base64 (octets).
CHUNK_SIZE = 32 * 1024
#: Taille maximale de l'aperçu d'une image (octets, avant base64).
MAX_THUMB_SIZE = 32 * 1024
#: Taille maximale d'une photo de profil (octets, avant base64).
MAX_AVATAR_SIZE = 32 * 1024
#: Longueur maximale d'un nom de fichier (caractères).
MAX_FILE_NAME_LENGTH = 200
#: Longueur maximale d'une réaction (caractères : un emoji composé peut en compter plusieurs).
MAX_EMOJI_LENGTH = 16


class ProtocolError(Exception):
    """Trame invalide reçue ou impossible à encoder."""


class MessageType(StrEnum):
    HELLO = "hello"
    CHAT = "chat"
    ACK = "ack"
    TYPING = "typing"
    REACT = "react"
    FILE_OFFER = "file_offer"
    FILE_ANSWER = "file_answer"
    FILE_CHUNK = "file_chunk"
    FILE_END = "file_end"
    FILE_CANCEL = "file_cancel"
    AVATAR = "avatar"
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


_UNSAFE_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def safe_file_name(name: str) -> str:
    """Nom de fichier sans chemin ni caractère interdit (Windows compris)."""
    name = re.split(r"[\\/]", name)[-1]
    name = _UNSAFE_FILE_CHARS.sub("_", name).strip().strip(".")
    if not name:
        return "fichier"
    if len(name) > MAX_FILE_NAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 16:
            name = stem[: MAX_FILE_NAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            name = name[:MAX_FILE_NAME_LENGTH]
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
    emoji: str = ""
    size: int = 0
    thumb: bytes = b""
    offset: int = 0
    data: bytes = b""
    sha256: str = ""

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
    def react(cls, message_id: str, emoji: str) -> Message:
        return cls(MessageType.REACT, id=message_id, emoji=emoji)

    @classmethod
    def file_offer(cls, message_id: str, name: str, size: int, thumb: bytes = b"") -> Message:
        return cls(MessageType.FILE_OFFER, id=message_id, name=name, size=size, thumb=thumb)

    @classmethod
    def file_answer(cls, message_id: str, accept: bool) -> Message:
        return cls(MessageType.FILE_ANSWER, id=message_id, active=accept)

    @classmethod
    def file_chunk(cls, message_id: str, offset: int, data: bytes) -> Message:
        return cls(MessageType.FILE_CHUNK, id=message_id, offset=offset, data=data)

    @classmethod
    def file_end(cls, message_id: str, sha256: str) -> Message:
        return cls(MessageType.FILE_END, id=message_id, sha256=sha256)

    @classmethod
    def file_cancel(cls, message_id: str, reason: str = "") -> Message:
        return cls(MessageType.FILE_CANCEL, id=message_id, reason=reason)

    @classmethod
    def avatar(cls, data: bytes) -> Message:
        """Photo de profil (``b""`` : retirée)."""
        return cls(MessageType.AVATAR, data=data)

    @classmethod
    def bye(cls, reason: str = "") -> Message:
        return cls(MessageType.BYE, reason=reason)

    @property
    def accept(self) -> bool:
        return self.active

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
            case MessageType.REACT:
                return {"type": kind, "id": self.id, "emoji": self.emoji}
            case MessageType.FILE_OFFER:
                payload: dict[str, object] = {
                    "type": kind,
                    "id": self.id,
                    "name": self.name,
                    "size": self.size,
                }
                if self.thumb:
                    payload["thumb"] = base64.b64encode(self.thumb).decode("ascii")
                return payload
            case MessageType.FILE_ANSWER:
                return {"type": kind, "id": self.id, "accept": self.active}
            case MessageType.FILE_CHUNK:
                data = base64.b64encode(self.data).decode("ascii")
                return {"type": kind, "id": self.id, "offset": self.offset, "data": data}
            case MessageType.FILE_END:
                return {"type": kind, "id": self.id, "sha256": self.sha256}
            case MessageType.FILE_CANCEL:
                return {"type": kind, "id": self.id, "reason": self.reason}
            case MessageType.AVATAR:
                return {"type": kind, "data": base64.b64encode(self.data).decode("ascii")}
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


def _require_int(payload: dict[str, object], key: str, *, maximum: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise ProtocolError(f"Champ « {key} » invalide.")
    return value


def _require_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ProtocolError(f"Champ « {key} » invalide.")
    return value


def _require_base64(payload: dict[str, object], key: str, *, max_size: int) -> bytes:
    raw = _require_str(payload, key, max_length=(max_size + 2) // 3 * 4)
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError(f"Champ « {key} » invalide.") from exc


_SHA256 = re.compile(r"[0-9a-f]{64}")

#: Signatures acceptées pour une photo de profil : JPEG et PNG.
_IMAGE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def is_avatar_image(data: bytes) -> bool:
    """Vrai si ``data`` ressemble à une photo de profil acceptable (JPEG ou PNG)."""
    return 0 < len(data) <= MAX_AVATAR_SIZE and data.startswith(_IMAGE_SIGNATURES)


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


def decode(line: bytes) -> Message | None:
    """Décode une trame TCP (sans le séparateur final) ; ``None`` si son type est inconnu."""
    payload = _load(line)
    kind_value = payload.get("type")
    if not isinstance(kind_value, str):
        raise ProtocolError("Type de trame absent.")
    try:
        kind = MessageType(kind_value)
    except ValueError:
        return None  # trame d'une version plus récente : ignorée

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
        case MessageType.REACT:
            emoji = _require_str(payload, "emoji", max_length=MAX_EMOJI_LENGTH)
            if any(ch.isspace() or ch.isalnum() for ch in emoji):
                raise ProtocolError("Réaction invalide.")
            return Message.react(_require_id(payload), emoji)
        case MessageType.FILE_OFFER:
            raw_name = _require_str(payload, "name", max_length=MAX_FILE_NAME_LENGTH * 2)
            size = _require_int(payload, "size", maximum=MAX_FILE_SIZE)
            thumb = b""
            if "thumb" in payload:
                thumb = _require_base64(payload, "thumb", max_size=MAX_THUMB_SIZE)
            return Message.file_offer(_require_id(payload), safe_file_name(raw_name), size, thumb)
        case MessageType.FILE_ANSWER:
            return Message.file_answer(_require_id(payload), _require_bool(payload, "accept"))
        case MessageType.FILE_CHUNK:
            offset = _require_int(payload, "offset", maximum=MAX_FILE_SIZE)
            data = _require_base64(payload, "data", max_size=CHUNK_SIZE)
            return Message.file_chunk(_require_id(payload), offset, data)
        case MessageType.FILE_END:
            digest = _require_str(payload, "sha256", max_length=64)
            if not _SHA256.fullmatch(digest):
                raise ProtocolError("Empreinte invalide.")
            return Message.file_end(_require_id(payload), digest)
        case MessageType.FILE_CANCEL:
            reason = _require_str(payload, "reason", max_length=500)
            return Message.file_cancel(_require_id(payload), reason)
        case MessageType.AVATAR:
            data = _require_base64(payload, "data", max_size=MAX_AVATAR_SIZE)
            if data and not is_avatar_image(data):
                raise ProtocolError("Photo de profil invalide.")
            return Message.avatar(data)
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
            if line.strip() and (message := decode(line)) is not None:
                messages.append(message)
