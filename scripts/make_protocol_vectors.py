"""Génère les vecteurs de test du protocole partagés avec l'application Android.

Usage : python scripts/make_protocol_vectors.py

Écrit ``mobile/test/fixtures/protocol_vectors.json`` à partir de
``src/socket_chat/protocol.py`` : trames valides (encodage exact et champs attendus),
trames invalides à rejeter, annonces UDP, noms d'utilisateur et de fichiers.
Les tests Python et Dart lisent le même fichier ; un test Python vérifie qu'il est à jour.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from socket_chat.protocol import (  # noqa: E402
    CHUNK_SIZE,
    MAX_AVATAR_SIZE,
    MAX_TEXT_LENGTH,
    PROTOCOL_VERSION,
    AckStatus,
    Message,
    ProtocolError,
    decode,
    decode_announce,
    encode,
    encode_announce,
    encode_leave,
    safe_file_name,
    validate_name,
)

OUTPUT = ROOT / "mobile" / "test" / "fixtures" / "protocol_vectors.json"
UID = "6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _fields(message: Message) -> dict[str, object]:
    return {
        "type": message.type.value,
        "id": message.id,
        "name": message.name,
        "port": message.port,
        "text": message.text,
        "status": message.status.value if message.status else None,
        "active": message.active,
        "reason": message.reason,
        "emoji": message.emoji,
        "size": message.size,
        "thumb": _b64(message.thumb),
        "offset": message.offset,
        "data": _b64(message.data),
        "sha256": message.sha256,
    }


VALID = [
    Message.hello(UID, "Élodie", 12800),
    Message.hello(UID, "李雷", 1),
    Message.chat(UID, "Bonjour 👋\nDeuxième ligne"),
    Message.chat(UID, ""),
    Message.chat(UID, 'Guillemets " antislash \\ tab\t retour\r fin\x01'),
    Message.chat(UID, "🎉" * 50),
    Message.ack(UID, AckStatus.DELIVERED),
    Message.ack(UID, AckStatus.READ),
    Message.typing(True),
    Message.typing(False),
    Message.react(UID, "👍"),
    Message.react(UID, "❤️"),
    Message.react(UID, "🧑‍💻"),
    Message.react(UID, ""),
    Message.file_offer(UID, "rapport final.pdf", 123456),
    Message.file_offer(UID, "photo.jpg", 9, b"\xff\xd8jpeg"),
    Message.file_answer(UID, True),
    Message.file_answer(UID, False),
    Message.file_chunk(UID, 0, b""),
    Message.file_chunk(UID, CHUNK_SIZE, bytes(range(256)) * 4),
    Message.file_end(UID, "a" * 64),
    Message.file_cancel(UID, "integrity"),
    Message.avatar(b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8),
    Message.avatar(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40),
    Message.avatar(b"\xff\xd8\xff" + b"j" * (MAX_AVATAR_SIZE - 3)),
    Message.avatar(b""),
    Message.bye("au revoir"),
    Message.bye(),
]


def _hello(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "type": "hello",
        "version": PROTOCOL_VERSION,
        "id": UID,
        "name": "A",
        "port": 1,
    }
    payload.update(changes)
    return json.dumps(payload).encode()


def _json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


INVALID = [
    b"not json",
    b"\xff\xfe",
    b"[1, 2]",
    b'{"type": 3}',
    b'{"pas": "de type"}',
    _hello(version=1),
    _hello(version=True),
    _hello(id="pas-un-uuid"),
    _hello(id=UID.upper()),
    _hello(name="   "),
    _hello(name="x" * 33),
    _hello(port=0),
    _hello(port=70000),
    _hello(port="12800"),
    _hello(port=12800.0),
    _json({"type": "chat", "id": UID, "text": 3}),
    _json({"type": "chat", "text": "sans id"}),
    _json({"type": "chat", "id": UID, "text": "x" * (MAX_TEXT_LENGTH + 1)}),
    _json({"type": "chat", "id": UID, "text": "🎉" * (MAX_TEXT_LENGTH + 1)}),
    _json({"type": "ack", "id": UID, "status": "lost"}),
    _json({"type": "typing", "active": "yes"}),
    _json({"type": "typing", "active": 1}),
    _json({"type": "react", "id": UID, "emoji": "texte"}),
    _json({"type": "react", "id": UID, "emoji": "👍 "}),
    _json({"type": "react", "id": UID, "emoji": "👍" * 20}),
    _json({"type": "file_offer", "id": UID, "name": "a", "size": -1}),
    _json({"type": "file_offer", "id": UID, "name": "a", "size": 3 * 1024**3}),
    _json({"type": "file_offer", "id": UID, "name": "a", "size": 1, "thumb": "%%"}),
    _json({"type": "file_offer", "id": UID, "name": "a", "size": 1, "thumb": "abc"}),
    _json({"type": "file_offer", "id": UID, "name": 5, "size": 1}),
    _json({"type": "file_answer", "id": UID, "accept": "oui"}),
    _json({"type": "file_chunk", "id": UID, "offset": 0, "data": "pas du base64!"}),
    _json({"type": "file_chunk", "id": UID, "offset": -5, "data": ""}),
    _json({"type": "file_chunk", "id": UID, "offset": 0, "data": "-_-_"}),
    _json({"type": "file_end", "id": UID, "sha256": "zz"}),
    _json({"type": "file_end", "id": UID, "sha256": "A" * 64}),
    _json({"type": "file_cancel", "id": UID, "reason": 1}),
    _json({"type": "avatar", "data": 5}),
    _json({"type": "avatar", "data": "pas du base64!"}),
    _json({"type": "avatar", "data": _b64(b"GIF89a" + b"\x00" * 10)}),
    _json({"type": "avatar", "data": _b64(b"\xff\xd8\xff" + b"j" * MAX_AVATAR_SIZE)}),
]

UNKNOWN = [b'{"type": "sticker", "id": "x"}', b'{"type": "future"}']

NAMES = ["  Jean   Dupont ", "Élodie", " ", "", "x" * 32, "x" * 33, "a\tb\nc", "🎉" * 32, "🎉" * 33]

FILE_NAMES = [
    "rapport.pdf",
    "../../etc/passwd",
    "C:\\Users\\x\\photo.png",
    'a<b>c:d"e|f?g*.txt',
    "...",
    "",
    "nul\x00.txt",
    "..\\..\\Windows/system32/evil:.exe",
    " .cache ",
    "x" * 500 + ".docx",
    "y" * 300,
    "é" * 250 + ".txt",
]

ANNOUNCE_VALID = [
    encode_announce(UID, "Alice", 12800),
    encode_announce(UID, "Ünïcødé", 65535),
    encode_leave(UID),
]
ANNOUNCE_INVALID = [
    b"x" * 1025,
    b"bruit",
    json.dumps({"type": "announce", "version": 2, "id": UID, "name": "A", "port": 1}).encode(),
    json.dumps({"type": "hello", "version": PROTOCOL_VERSION, "id": UID}).encode(),
    json.dumps({"type": "announce", "version": PROTOCOL_VERSION, "id": UID, "port": 1}).encode(),
]


def build() -> dict[str, object]:
    valid = []
    for message in VALID:
        frame = encode(message)
        assert decode(frame[:-1]) == message
        valid.append({"frame": _b64(frame), "fields": _fields(message)})
    invalid = []
    for line in INVALID:
        try:
            decode(line)
        except ProtocolError:
            invalid.append(_b64(line))
        else:
            raise AssertionError(f"trame acceptée à tort : {line!r}")
    for line in UNKNOWN:
        assert decode(line) is None
    names = []
    for raw in NAMES:
        try:
            names.append([raw, validate_name(raw)])
        except ValueError:
            names.append([raw, None])
    announces = []
    for datagram in ANNOUNCE_VALID:
        a = decode_announce(datagram)
        announces.append({"datagram": _b64(datagram), "id": a.id, "name": a.name, "port": a.port})
    for datagram in ANNOUNCE_INVALID:
        try:
            decode_announce(datagram)
        except ProtocolError:
            continue
        raise AssertionError(f"annonce acceptée à tort : {datagram!r}")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "valid": valid,
        "invalid": invalid,
        "unknown": [_b64(line) for line in UNKNOWN],
        "names": names,
        "file_names": [[raw, safe_file_name(raw)] for raw in FILE_NAMES],
        "announce_valid": announces,
        "announce_invalid": [_b64(d) for d in ANNOUNCE_INVALID],
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"Vecteurs écrits dans {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
