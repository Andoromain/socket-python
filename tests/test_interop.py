"""Interopérabilité PC ↔ Android : la version PC converse avec le pair Dart de l'application.

Le pair Dart (``mobile/tool/peer.dart``) est le cœur de l'application Android sans son
interface ; il est piloté par des commandes JSON sur son entrée standard. Test ignoré si
Dart n'est pas installé (variable ``SOCKET_CHAT_DART`` ou ``dart`` dans le PATH) ou si
les dépendances Flutter n'ont pas été récupérées (``flutter pub get`` dans ``mobile/``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from socket_chat.discovery import Discovery
from socket_chat.store import ME, FileState, Status

MOBILE = Path(__file__).resolve().parents[1] / "mobile"
DART = os.environ.get("SOCKET_CHAT_DART") or shutil.which("dart")
TIMEOUT = 20_000

_AVAILABLE = bool(DART) and (MOBILE / ".dart_tool" / "package_config.json").is_file()
if not _AVAILABLE and os.environ.get("SOCKET_CHAT_REQUIRE_INTEROP"):
    raise RuntimeError("Test d'interopérabilité exigé, mais Dart ou mobile/.dart_tool est absent.")

pytestmark = pytest.mark.skipif(
    not _AVAILABLE, reason="Dart ou les dépendances de mobile/ ne sont pas installés"
)


class DartPeer:
    """Processus ``dart run tool/peer.dart`` et ses événements."""

    def __init__(self, name: str) -> None:
        self.proc = subprocess.Popen(
            [DART, "run", "tool/peer.dart", "--name", name],
            cwd=MOBILE,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
        self.events: list[dict] = []
        self.output: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.output.append(line)
            # Les messages de construction de Dart peuvent précéder le JSON sur la ligne.
            if "{" in line:
                try:
                    event = json.loads(line[line.index("{") :])
                except ValueError:
                    continue
                with self._lock:
                    self.events.append(event)

    def command(self, cmd: str, **fields: object) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"cmd": cmd, **fields}) + "\n")
        self.proc.stdin.flush()

    def find(self, event: str, **match: object) -> dict | None:
        with self._lock:
            for item in self.events:
                if item.get("event") == event and all(item.get(k) == v for k, v in match.items()):
                    return item
        return None

    def wait(self, qtbot, event: str, timeout: int = TIMEOUT, **match: object) -> dict:
        try:
            qtbot.waitUntil(lambda: self.find(event, **match) is not None, timeout=timeout)
        except AssertionError:
            pytest.fail(
                f"Événement {event} {match} non reçu. Sortie du pair :\n{''.join(self.output)}"
            )
        result = self.find(event, **match)
        assert result is not None
        return result

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.command("quit")
                self.proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait()


@pytest.fixture
def phone(qtbot):
    peer = DartPeer("Téléphone")
    started = peer.wait(qtbot, "started", timeout=180_000)  # première compilation
    peer.id, peer.port, peer.key = started["id"], started["port"], started["key"]
    yield peer
    peer.close()


def connected(qtbot, make_messenger, phone):
    pc = make_messenger("Poste PC")
    with qtbot.waitSignal(pc.presence_changed, timeout=TIMEOUT):
        phone.command("connect", host="127.0.0.1", port=pc.port)
    phone.wait(qtbot, "presence", contact=pc.user_id, online=True)
    return pc


def test_encrypted_connection_pins_keys_and_agrees_on_safety_number(qtbot, make_messenger, phone):
    """Les deux implémentations de Noise s'entendent : clés prouvées, même code."""
    from socket_chat.identity import decode_key, encode_key, safety_number

    pc = connected(qtbot, make_messenger, phone)
    assert pc.store.contact(phone.id).public_key == phone.key
    phone.command("safety", contact=pc.user_id)
    answer = phone.wait(qtbot, "safety", contact=pc.user_id)
    assert answer["key"] == encode_key(pc.identity.public)
    assert answer["number"] == safety_number(pc.identity.public, decode_key(phone.key))


def test_messages_receipts_typing_and_reactions(qtbot, make_messenger, phone):
    pc = connected(qtbot, make_messenger, phone)
    assert [s.contact.name for s in pc.store.contacts()] == ["Téléphone"]

    # PC → téléphone, avec accusés de remise et de lecture.
    record = pc.send(phone.id, "Bonjour du PC 👋\nDeuxième ligne")
    received = phone.wait(qtbot, "message", id=record.id)
    assert received["text"] == "Bonjour du PC 👋\nDeuxième ligne" and not received["outgoing"]
    qtbot.waitUntil(lambda: pc.store.message(record.id).status is Status.DELIVERED, timeout=TIMEOUT)
    phone.command("read", contact=pc.user_id)
    qtbot.waitUntil(lambda: pc.store.message(record.id).status is Status.READ, timeout=TIMEOUT)

    # Téléphone → PC.
    with qtbot.waitSignal(pc.message_added, timeout=TIMEOUT) as blocker:
        phone.command("send", to=pc.user_id, text="Réponse du téléphone 📱")
    reply = blocker.args[0]
    assert reply.text == "Réponse du téléphone 📱" and reply.contact_id == phone.id
    phone.wait(qtbot, "status", id=reply.id, status="delivered")
    pc.mark_read(phone.id)
    phone.wait(qtbot, "status", id=reply.id, status="read")

    # « écrit… » dans les deux sens.
    with qtbot.waitSignal(pc.typing_changed, timeout=TIMEOUT) as blocker:
        phone.command("typing", to=pc.user_id, active=True)
    assert blocker.args == [phone.id, True]
    pc.set_typing(phone.id, True)
    phone.wait(qtbot, "typing", contact=pc.user_id, active=True)

    # Réactions dans les deux sens.
    with qtbot.waitSignal(pc.message_changed, timeout=TIMEOUT):
        phone.command("react", id=record.id, emoji="👍")
    assert pc.store.message(record.id).reaction_of(phone.id) == "👍"
    assert pc.store.message(record.id).reaction_of(ME) == ""
    pc.react(reply.id, "❤️")
    phone.wait(qtbot, "changed", id=reply.id, reactions={pc.user_id: "❤️"})


def test_files_in_both_directions(qtbot, make_messenger, phone, tmp_path):
    pc = connected(qtbot, make_messenger, phone)

    # PC → téléphone : plusieurs Mo, empreinte vérifiée par le destinataire.
    content = os.urandom(3 * 1024 * 1024 + 4321)
    source = tmp_path / "rapport annuel.pdf"
    source.write_bytes(content)
    record = pc.send_file(phone.id, source)
    offer = phone.wait(qtbot, "message", id=record.id)
    assert offer["file"] == {"name": "rapport annuel.pdf", "size": len(content), "state": "offered"}
    destination = tmp_path / "téléphone" / "rapport.pdf"
    phone.command("accept", id=record.id, path=str(destination))
    phone.wait(qtbot, "changed", id=record.id, file={**offer["file"], "state": "done"})
    qtbot.waitUntil(
        lambda: pc.store.message(record.id).file.state is FileState.DONE, timeout=TIMEOUT
    )
    assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(content).digest()

    # Téléphone → PC.
    photo = os.urandom(2 * 1024 * 1024 + 17)
    (tmp_path / "IMG_0001.jpg").write_bytes(photo)
    with qtbot.waitSignal(pc.message_added, timeout=TIMEOUT) as blocker:
        phone.command("send_file", to=pc.user_id, path=str(tmp_path / "IMG_0001.jpg"))
    incoming = blocker.args[0]
    assert (incoming.file.name, incoming.file.size) == ("IMG_0001.jpg", len(photo))
    saved = tmp_path / "pc" / "IMG_0001.jpg"
    pc.accept_file(incoming.id, saved)
    qtbot.waitUntil(
        lambda: pc.store.message(incoming.id).file.state is FileState.DONE, timeout=TIMEOUT
    )
    phone.wait(
        qtbot,
        "changed",
        id=incoming.id,
        file={"name": "IMG_0001.jpg", "size": len(photo), "state": "done"},
    )
    assert saved.read_bytes() == photo


def test_pc_dials_phone_and_sees_it_leave(qtbot, make_messenger, phone):
    pc = make_messenger("Poste PC")
    with qtbot.waitSignal(pc.presence_changed, timeout=TIMEOUT):
        pc.add_contact("127.0.0.1", phone.port)
    phone.wait(qtbot, "presence", contact=pc.user_id, online=True)
    with qtbot.waitSignal(
        pc.presence_changed, check_params_cb=lambda cid, online: not online, timeout=TIMEOUT
    ):
        phone.close()  # « bye » puis fermeture


def test_udp_discovery_between_pc_and_phone(qtbot, make_messenger, phone):
    pc = make_messenger("Poste PC")
    disc = Discovery(pc.user_id, "Poste PC", pc.port, udp_port=0, targets=[])
    assert disc.start()
    pc.attach_discovery(disc)
    try:
        phone.command("discover", target_port=disc.local_port)
        phone_udp = phone.wait(qtbot, "discovery")["port"]
        disc.targets = [("127.0.0.1", phone_udp)]
        with qtbot.waitSignal(disc.peer_seen, timeout=TIMEOUT) as blocker:
            disc.announce()
        assert blocker.args == [phone.id, "Téléphone", "127.0.0.1", phone.port]
        phone.wait(qtbot, "presence", contact=pc.user_id, online=True)
        # Contact détecté : le message part sans saisie d'adresse.
        record = pc.send(phone.id, "Trouvé par détection")
        phone.wait(qtbot, "message", id=record.id)
    finally:
        disc.stop()
        disc.deleteLater()
