"""Génère les vecteurs de test du protocole partagés avec l'application Android.

Usage : python scripts/make_protocol_vectors.py

Écrit ``mobile/test/fixtures/protocol_vectors.json`` à partir de
``src/socket_chat/protocol.py`` : trames valides (encodage exact et champs attendus),
trames invalides à rejeter, annonces UDP, noms d'utilisateur et de fichiers, codes de
sécurité. (Noise lui-même est vérifié avec le vecteur officiel noise_xx_vector.json.)
Les tests Python et Dart lisent le même fichier ; un test Python vérifie qu'il est à jour.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from socket_chat import signing  # noqa: E402
from socket_chat.identity import PROLOGUE, safety_number  # noqa: E402
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
        "media": message.media,
        "sdp_kind": message.sdp_kind,
        "sdp": message.sdp,
        "candidate": message.candidate,
        "mid": message.mid,
        "index": message.index,
    }


SDP = (
    "v=0\r\no=- 4611731400430051336 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
    "a=group:BUNDLE 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:111 opus/48000/2\r\na=fmtp:111 minptime=10;useinbandfec=1\r\n"
)
ICE = "candidate:842163049 1 udp 1677729535 192.168.1.20 61338 typ host generation 0"


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
    Message.call_invite(UID, "audio"),
    Message.call_invite(UID, "video"),
    Message.call_ringing(UID),
    Message.call_accept(UID),
    Message.call_sdp(UID, "offer", SDP),
    Message.call_sdp(UID, "answer", SDP),
    Message.call_ice(UID, ICE, "0", 0),
    Message.call_ice(UID, "", "", 0),
    Message.call_end(UID, "hangup"),
    Message.call_end(UID, "busy"),
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
    _json({"type": "call_invite", "id": UID, "media": "fax"}),
    _json({"type": "call_invite", "media": "audio"}),
    _json({"type": "call_sdp", "id": UID, "kind": "pranswer", "sdp": "v=0"}),
    _json({"type": "call_sdp", "id": UID, "kind": "offer", "sdp": ""}),
    _json({"type": "call_sdp", "id": UID, "kind": "offer", "sdp": "x" * (16 * 1024 + 1)}),
    _json({"type": "call_ice", "id": UID, "candidate": "c" * 1025, "mid": "0", "index": 0}),
    _json({"type": "call_ice", "id": UID, "candidate": "c", "mid": "0", "index": 65}),
    _json({"type": "call_ice", "id": UID, "candidate": "c", "mid": "0", "index": "0"}),
    _json({"type": "call_end", "id": UID, "reason": "r" * 33}),
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


MANIFEST_URL = "https://github.com/Andoromain/socket-python/releases/latest/download/latest.json"


def update_manifest() -> dict[str, object]:
    """Manifeste ``latest.json`` produit par le vrai ``make_latest.py`` (contenu fixe)."""
    spec = importlib.util.spec_from_file_location(
        "make_latest", ROOT / "scripts" / "make_latest.py"
    )
    assert spec is not None and spec.loader is not None
    make_latest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_latest)
    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp)
        for name, size in [
            ("socket-chat-9.8.7-windows-setup.exe", 300),
            ("socket-chat-9.8.7-linux-x86_64.tar.gz", 150),
            ("socket-chat-9.8.7-android.apk", 210),
        ]:
            (dist / name).write_bytes(bytes(i % 251 for i in range(size)))
        changelog = dist / "CHANGELOG.md"
        changelog.write_text("## 9.8.7\n\n### Ajouté\n- Une nouveauté « accentuée ».\n", "utf-8")
        manifest = make_latest.build_manifest(dist, "9.8.7", changelog, "2026-11-06")
    return {"url": MANIFEST_URL, "manifest": manifest}


def update_signature() -> dict[str, str]:
    """Manifeste signé par le vrai ``signing.sign`` avec une clé de test fixe."""
    private_key = base64.b64encode(bytes(range(100, 132))).decode("ascii")
    data = json.dumps(update_manifest()["manifest"], ensure_ascii=False, indent=2).encode()
    return {
        "public_key": signing.public_key_of(private_key),
        "manifest": _b64(data),
        "signature": signing.sign(data, private_key),
    }


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
        "prologue": _b64(PROLOGUE),
        "safety_numbers": [
            {"a": _b64(a), "b": _b64(b), "number": safety_number(a, b)} for a, b in SAFETY_KEYS
        ],
        "update_manifest": update_manifest(),
        "update_signature": update_signature(),
    }


SAFETY_KEYS = [
    (bytes(range(32)), bytes(range(32, 64))),
    (bytes(range(32, 64)), bytes(range(32))),  # même code dans l'autre sens
    (b"\xff" * 32, b"\x00" * 31 + b"\x01"),
]


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"Vecteurs écrits dans {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
