"""Historique local des contacts et des messages (SQLite, sans dépendance Qt).

Chiffrement au repos : avec une clé (``key``, 32 octets, voir ``vault``), le contenu
des messages (texte, nom et chemin des fichiers, aperçus d'images) est chiffré par
ChaCha20-Poly1305 avant d'être écrit. Chaque valeur est liée à son message et à sa
colonne (données associées) : elle ne peut être ni modifiée ni déplacée sans être
détectée. Restent lisibles les métadonnées nécessaires au fonctionnement : contacts,
dates, états, tailles. Un historique existant est chiffré à la première ouverture avec
une clé, puis compacté (``VACUUM``) pour effacer les anciennes données en clair.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

SCHEMA_VERSION = 3

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    host      TEXT,
    port      INTEGER,
    last_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    outgoing   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    status     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_contact ON messages(contact_id, ts);
"""

#: Migrations successives : la clé est la version atteinte après application.
_MIGRATIONS = {
    2: """
    ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';
    ALTER TABLE messages ADD COLUMN file_name TEXT;
    ALTER TABLE messages ADD COLUMN file_size INTEGER;
    ALTER TABLE messages ADD COLUMN file_state TEXT;
    ALTER TABLE messages ADD COLUMN file_path TEXT;
    ALTER TABLE messages ADD COLUMN thumb BLOB;
    CREATE TABLE reactions (
        message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        author     TEXT NOT NULL,
        emoji      TEXT NOT NULL,
        PRIMARY KEY (message_id, author)
    );
    """,
    # Protocole 4 : clé publique (Noise) de chaque contact, épinglée au premier contact
    # chiffré, et nouvelle clé présentée en attendant que l'utilisateur l'accepte.
    3: """
    ALTER TABLE contacts ADD COLUMN public_key TEXT;
    ALTER TABLE contacts ADD COLUMN pending_key TEXT;
    """,
}

#: Colonnes attendues après les migrations : ajoutées si elles manquent (historique dans
#: un état inattendu, par exemple après une coupure pendant une mise à niveau).
_REQUIRED_COLUMNS = {
    "contacts": {"public_key": "TEXT", "pending_key": "TEXT"},
    "messages": {
        "kind": "TEXT NOT NULL DEFAULT 'text'",
        "file_name": "TEXT",
        "file_size": "INTEGER",
        "file_state": "TEXT",
        "file_path": "TEXT",
        "thumb": "BLOB",
    },
}
_REACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS reactions (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    author     TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    PRIMARY KEY (message_id, author)
);
"""

#: Auteur des réactions de l'utilisateur local (les autres sont des identifiants de contact).
ME = "me"

#: Préfixe des valeurs chiffrées (BLOB) ; un aperçu JPEG en clair commence par FF D8.
_SEALED = b"\x00SCE1"
_CHECK = b"Socket Chat history"
#: Texte affiché à la place d'une valeur qui ne peut pas être déchiffrée.
UNREADABLE = "⚠ message illisible"


class HistoryLockedError(Exception):
    """Historique chiffré, mais clé absente ou incorrecte."""


class _Cipher:
    """Chiffre une valeur liée à son emplacement (``aad``) ; ``None`` reste ``None``."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Clé d'historique invalide.")
        self._aead = ChaCha20Poly1305(key)

    def seal(self, value: str | bytes | None, aad: str) -> bytes | None:
        if value is None:
            return None
        data = value.encode("utf-8") if isinstance(value, str) else value
        nonce = os.urandom(12)
        return _SEALED + nonce + self._aead.encrypt(nonce, data, aad.encode("utf-8"))

    def open_bytes(self, stored: bytes, aad: str) -> bytes:
        nonce = stored[len(_SEALED) : len(_SEALED) + 12]
        try:
            return self._aead.decrypt(nonce, stored[len(_SEALED) + 12 :], aad.encode("utf-8"))
        except InvalidTag as exc:
            raise ValueError("Valeur chiffrée altérée.") from exc


def _sealed(value: object) -> bool:
    return isinstance(value, bytes) and value.startswith(_SEALED)


def now_ms() -> int:
    return int(time.time() * 1000)


class Status(StrEnum):
    """État d'un message.

    Messages envoyés : ``pending`` → ``sent`` → ``delivered`` → ``read``.
    Messages reçus : ``delivered`` (non lu) → ``read``.
    """

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"

    @property
    def rank(self) -> int:
        return list(Status).index(self)


class FileState(StrEnum):
    """Étape d'un transfert de fichier (du point de vue de ce poste)."""

    OFFERED = "offered"  # proposé, en attente de la réponse du destinataire
    ACCEPTED = "accepted"  # accepté, en attente du premier bloc
    TRANSFERRING = "transferring"
    DONE = "done"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def active(self) -> bool:
        return self in (FileState.ACCEPTED, FileState.TRANSFERRING)


@dataclass(frozen=True)
class FileInfo:
    name: str
    size: int
    state: FileState
    path: str | None = None  # source (envoi) ou destination (réception)
    thumb: bytes = b""  # aperçu JPEG pour les images

    @property
    def extension(self) -> str:
        return self.name.rpartition(".")[2].lower() if "." in self.name else ""

    @property
    def is_image(self) -> bool:
        return self.extension in IMAGE_EXTENSIONS and bool(self.thumb)


@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    host: str | None = None
    port: int | None = None
    last_seen: int = 0
    #: Clé publique X25519 épinglée (base64) ; ``None`` tant qu'aucune connexion chiffrée.
    public_key: str | None = None
    #: Clé différente présentée par ce contact, refusée en attendant l'utilisateur.
    pending_key: str | None = None


@dataclass(frozen=True)
class MessageRecord:
    id: str
    contact_id: str
    outgoing: bool
    text: str
    ts: int
    status: Status
    file: FileInfo | None = None
    #: Réactions : paires (auteur, emoji), l'auteur étant ``ME`` ou l'identifiant du contact.
    reactions: tuple[tuple[str, str], ...] = ()

    @property
    def unread(self) -> bool:
        return not self.outgoing and self.status is not Status.READ

    @property
    def preview(self) -> str:
        """Texte court pour la liste des contacts et les notifications."""
        if self.file is not None:
            return "🖼 Image" if self.file.is_image else f"📎 {self.file.name}"
        return " ".join(self.text.split())

    def reaction_of(self, author: str) -> str:
        return dict(self.reactions).get(author, "")


@dataclass(frozen=True)
class ContactSummary:
    contact: Contact
    last_message: MessageRecord | None
    unread: int

    @property
    def last_activity(self) -> int:
        return max(self.contact.last_seen, self.last_message.ts if self.last_message else 0)


class Store:
    def __init__(self, path: str | Path = ":memory:", key: bytes | None = None) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        # Contenu supprimé ou remplacé écrasé sur le disque (pas de restes en clair).
        self._db.execute("PRAGMA secure_delete = ON")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Historique créé par une version plus récente de Socket Chat.")
        with self._db:
            self._db.executescript(_SCHEMA)
        for target in range(max(version, 1) + 1, SCHEMA_VERSION + 1):
            self._db.executescript(f"BEGIN; {_MIGRATIONS[target]} COMMIT;")
        self._repair_schema()
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._cipher: _Cipher | None = None
        try:
            self._setup_encryption(key)
        except Exception:
            self._db.close()
            raise
        # Un transfert ne survit pas à l'arrêt de l'application.
        with self._db:
            self._db.execute(
                "UPDATE messages SET file_state = 'failed' "
                "WHERE file_state IN ('accepted', 'transferring')"
            )

    def close(self) -> None:
        self._db.close()

    def _repair_schema(self) -> None:
        """Ajoute les colonnes et tables attendues qui manqueraient."""
        with self._db:
            for table, columns in _REQUIRED_COLUMNS.items():
                present = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in present:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self._db.executescript(_REACTIONS_TABLE)

    # -- Chiffrement -------------------------------------------------------------
    @property
    def encrypted(self) -> bool:
        return self._cipher is not None

    def _setup_encryption(self, key: bytes | None) -> None:
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB)")
        row = self._db.execute("SELECT value FROM meta WHERE key = 'check'").fetchone()
        if key is None:
            if row is not None:
                raise HistoryLockedError("Historique chiffré : clé absente.")
            return
        cipher = _Cipher(key)
        if row is not None:
            try:
                if cipher.open_bytes(row[0], "meta.check") != _CHECK:
                    raise ValueError
            except ValueError as exc:
                raise HistoryLockedError("Historique chiffré avec une autre clé.") from exc
            self._cipher = cipher
            return
        # Première ouverture avec une clé : tout l'historique existant est chiffré.
        self._cipher = cipher
        with self._db:
            rows = self._db.execute(
                "SELECT id, text, file_name, file_path, thumb FROM messages"
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE messages SET text = ?, file_name = ?, file_path = ?, thumb = ? "
                    "WHERE id = ?",
                    (
                        self._seal(row["text"], "text", row["id"]),
                        self._seal(row["file_name"], "file_name", row["id"]),
                        self._seal(row["file_path"], "file_path", row["id"]),
                        self._seal(row["thumb"], "thumb", row["id"]),
                        row["id"],
                    ),
                )
            self._db.execute(
                "INSERT INTO meta VALUES ('check', ?)", (cipher.seal(_CHECK, "meta.check"),)
            )
        if rows:
            # Compacte la base : les pages qui contenaient le texte en clair disparaissent.
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._db.execute("VACUUM")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _seal(self, value: str | bytes | None, column: str, message_id: str):
        if self._cipher is None or value is None or _sealed(value):
            return value
        return self._cipher.seal(value, f"messages.{column}:{message_id}")

    def _open_text(self, value, column: str, message_id: str, default: str | None = None):
        if not _sealed(value):
            return value
        if self._cipher is None:
            return default if default is not None else UNREADABLE
        try:
            return self._cipher.open_bytes(value, f"messages.{column}:{message_id}").decode()
        except (ValueError, UnicodeDecodeError):
            return default if default is not None else UNREADABLE

    def _open_bytes(self, value, column: str, message_id: str) -> bytes:
        if not _sealed(value):
            return value or b""
        try:
            assert self._cipher is not None
            return self._cipher.open_bytes(value, f"messages.{column}:{message_id}")
        except (AssertionError, ValueError):
            return b""

    def _file(self, row: sqlite3.Row, prefix: str = "") -> FileInfo | None:
        if row[f"{prefix}kind"] != "file":
            return None
        message_id = row[f"{prefix}id"]
        path = row[f"{prefix}file_path"]
        return FileInfo(
            self._open_text(row[f"{prefix}file_name"], "file_name", message_id, "fichier"),
            row[f"{prefix}file_size"],
            FileState(row[f"{prefix}file_state"]),
            None if path is None else self._open_text(path, "file_path", message_id, ""),
            self._open_bytes(row[f"{prefix}thumb"], "thumb", message_id),
        )

    def _message(self, row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            row["id"],
            row["contact_id"],
            bool(row["outgoing"]),
            self._open_text(row["text"], "text", row["id"]),
            row["ts"],
            Status(row["status"]),
            self._file(row),
        )

    # -- Contacts --------------------------------------------------------
    def upsert_contact(
        self,
        contact_id: str,
        name: str,
        *,
        host: str | None = None,
        port: int | None = None,
        last_seen: int | None = None,
    ) -> None:
        """Crée ou met à jour un contact ; les champs ``None`` sont conservés."""
        with self._db:
            self._db.execute(
                """
                INSERT INTO contacts (id, name, host, port, last_seen)
                VALUES (:id, :name, :host, :port, COALESCE(:seen, 0))
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    host = COALESCE(:host, contacts.host),
                    port = COALESCE(:port, contacts.port),
                    last_seen = MAX(contacts.last_seen, COALESCE(:seen, 0))
                """,
                {"id": contact_id, "name": name, "host": host, "port": port, "seen": last_seen},
            )

    def pin_key(self, contact_id: str, public_key: str) -> None:
        """Épingle la clé du contact (et oublie toute clé en attente)."""
        with self._db:
            self._db.execute(
                "UPDATE contacts SET public_key = ?, pending_key = NULL WHERE id = ?",
                (public_key, contact_id),
            )

    def set_pending_key(self, contact_id: str, public_key: str | None) -> bool:
        """Mémorise (ou efface) la nouvelle clé refusée ; vrai si elle a changé."""
        with self._db:
            cursor = self._db.execute(
                "UPDATE contacts SET pending_key = ? WHERE id = ? AND pending_key IS NOT ?",
                (public_key, contact_id, public_key),
            )
        return cursor.rowcount > 0

    def contact(self, contact_id: str) -> Contact | None:
        row = self._db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return _contact(row) if row else None

    def contacts(self) -> list[ContactSummary]:
        """Contacts avec leur dernier message, du plus récemment actif au plus ancien."""
        rows = self._db.execute(
            """
            SELECT c.*,
                   m.id AS m_id, m.outgoing AS m_outgoing, m.text AS m_text,
                   m.ts AS m_ts, m.status AS m_status, m.kind AS m_kind,
                   m.file_name AS m_file_name, m.file_size AS m_file_size,
                   m.file_state AS m_file_state, m.file_path AS m_file_path,
                   m.thumb AS m_thumb,
                   (SELECT COUNT(*) FROM messages u
                     WHERE u.contact_id = c.id AND u.outgoing = 0 AND u.status != 'read')
                     AS unread
            FROM contacts c
            LEFT JOIN messages m ON m.id = (
                SELECT id FROM messages WHERE contact_id = c.id
                ORDER BY ts DESC, rowid DESC LIMIT 1)
            """
        ).fetchall()
        summaries = []
        for row in rows:
            last = None
            if row["m_id"] is not None:
                last = MessageRecord(
                    row["m_id"],
                    row["id"],
                    bool(row["m_outgoing"]),
                    self._open_text(row["m_text"], "text", row["m_id"]),
                    row["m_ts"],
                    Status(row["m_status"]),
                    self._file(row, "m_"),
                )
            summaries.append(ContactSummary(_contact(row), last, row["unread"]))
        summaries.sort(key=lambda s: (-s.last_activity, s.contact.name.casefold()))
        return summaries

    def delete_contact(self, contact_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))

    # -- Messages --------------------------------------------------------
    def add_message(self, record: MessageRecord) -> bool:
        """Enregistre un message ; renvoie ``False`` s'il existait déjà (doublon)."""
        f = record.file
        with self._db:
            cursor = self._db.execute(
                """
                INSERT OR IGNORE INTO messages
                    (id, contact_id, outgoing, text, ts, status,
                     kind, file_name, file_size, file_state, file_path, thumb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.contact_id,
                    int(record.outgoing),
                    self._seal(record.text, "text", record.id),
                    record.ts,
                    record.status.value,
                    "file" if f else "text",
                    self._seal(f.name if f else None, "file_name", record.id),
                    f.size if f else None,
                    f.state.value if f else None,
                    self._seal(f.path if f else None, "file_path", record.id),
                    self._seal(f.thumb if f and f.thumb else None, "thumb", record.id),
                ),
            )
        return cursor.rowcount == 1

    def message(self, message_id: str) -> MessageRecord | None:
        row = self._db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._with_reactions([self._message(row)])[0] if row else None

    def _with_reactions(self, records: list[MessageRecord]) -> list[MessageRecord]:
        if not records:
            return records
        by_message: dict[str, list[tuple[str, str]]] = {}
        ids = [r.id for r in records]
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = self._db.execute(
                f"SELECT message_id, author, emoji FROM reactions "
                f"WHERE message_id IN ({marks}) ORDER BY rowid",
                chunk,
            ).fetchall()
            for row in rows:
                by_message.setdefault(row[0], []).append((row[1], row[2]))
        return [
            replace(r, reactions=tuple(by_message[r.id])) if r.id in by_message else r
            for r in records
        ]

    def messages(self, contact_id: str, limit: int = 1000) -> list[MessageRecord]:
        rows = self._db.execute(
            """
            SELECT * FROM (
                SELECT *, rowid AS r FROM messages WHERE contact_id = ?
                ORDER BY ts DESC, rowid DESC LIMIT ?)
            ORDER BY ts, r
            """,
            (contact_id, limit),
        ).fetchall()
        return self._with_reactions([self._message(row) for row in rows])

    def update_status(self, message_id: str, status: Status) -> bool:
        """Fait progresser l'état d'un message (jamais de retour en arrière)."""
        record = self.message(message_id)
        if record is None or status.rank <= record.status.rank:
            return False
        with self._db:
            self._db.execute(
                "UPDATE messages SET status = ? WHERE id = ?", (status.value, message_id)
            )
        return True

    def undelivered_outgoing(self, contact_id: str) -> list[MessageRecord]:
        """Messages envoyés pas encore confirmés par le destinataire (à (re)transmettre)."""
        rows = self._db.execute(
            """
            SELECT * FROM messages
            WHERE contact_id = ? AND outgoing = 1 AND status IN ('pending', 'sent')
            ORDER BY ts, rowid
            """,
            (contact_id,),
        ).fetchall()
        return [self._message(row) for row in rows]

    def contacts_with_undelivered(self) -> list[str]:
        rows = self._db.execute(
            "SELECT DISTINCT contact_id FROM messages "
            "WHERE outgoing = 1 AND status IN ('pending', 'sent')"
        ).fetchall()
        return [row[0] for row in rows]

    def mark_incoming_read(self, contact_id: str) -> list[str]:
        """Marque comme lus les messages reçus ; renvoie les identifiants modifiés."""
        rows = self._db.execute(
            "SELECT id FROM messages WHERE contact_id = ? AND outgoing = 0 AND status != 'read'",
            (contact_id,),
        ).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            with self._db:
                self._db.executemany(
                    "UPDATE messages SET status = 'read' WHERE id = ?", [(i,) for i in ids]
                )
        return ids

    # -- Fichiers et réactions --------------------------------------------
    def update_file(
        self, message_id: str, *, state: FileState | None = None, path: str | None = None
    ) -> bool:
        """Met à jour l'état ou le chemin d'un fichier ; renvoie ``True`` si modifié."""
        record = self.message(message_id)
        if record is None or record.file is None:
            return False
        new_state = state or record.file.state
        new_path = path if path is not None else record.file.path
        if (new_state, new_path) == (record.file.state, record.file.path):
            return False
        with self._db:
            self._db.execute(
                "UPDATE messages SET file_state = ?, file_path = ? WHERE id = ?",
                (new_state.value, self._seal(new_path, "file_path", message_id), message_id),
            )
        return True

    def set_reaction(self, message_id: str, author: str, emoji: str) -> bool:
        """Pose (ou retire avec ``""``) la réaction d'un auteur ; ``True`` si modifiée."""
        record = self.message(message_id)
        if record is None or record.reaction_of(author) == emoji:
            return False
        with self._db:
            if emoji:
                self._db.execute(
                    "INSERT INTO reactions VALUES (?, ?, ?) "
                    "ON CONFLICT(message_id, author) DO UPDATE SET emoji = excluded.emoji",
                    (message_id, author, emoji),
                )
            else:
                self._db.execute(
                    "DELETE FROM reactions WHERE message_id = ? AND author = ?",
                    (message_id, author),
                )
        return True

    def total_unread(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE outgoing = 0 AND status != 'read'"
        ).fetchone()[0]


def _contact(row: sqlite3.Row) -> Contact:
    return Contact(
        row["id"],
        row["name"],
        row["host"],
        row["port"],
        row["last_seen"],
        row["public_key"],
        row["pending_key"],
    )
