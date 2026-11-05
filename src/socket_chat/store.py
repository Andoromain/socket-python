"""Historique local des contacts et des messages (SQLite, sans dépendance Qt)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

SCHEMA_VERSION = 1

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


@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    host: str | None = None
    port: int | None = None
    last_seen: int = 0


@dataclass(frozen=True)
class MessageRecord:
    id: str
    contact_id: str
    outgoing: bool
    text: str
    ts: int
    status: Status

    @property
    def unread(self) -> bool:
        return not self.outgoing and self.status is not Status.READ


@dataclass(frozen=True)
class ContactSummary:
    contact: Contact
    last_message: MessageRecord | None
    unread: int

    @property
    def last_activity(self) -> int:
        return max(self.contact.last_seen, self.last_message.ts if self.last_message else 0)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Historique créé par une version plus récente de Socket Chat.")
        with self._db:
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._db.close()

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

    def contact(self, contact_id: str) -> Contact | None:
        row = self._db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return _contact(row) if row else None

    def contacts(self) -> list[ContactSummary]:
        """Contacts avec leur dernier message, du plus récemment actif au plus ancien."""
        rows = self._db.execute(
            """
            SELECT c.*,
                   m.id AS m_id, m.outgoing AS m_outgoing, m.text AS m_text,
                   m.ts AS m_ts, m.status AS m_status,
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
                    row["m_text"],
                    row["m_ts"],
                    Status(row["m_status"]),
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
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.contact_id,
                    int(record.outgoing),
                    record.text,
                    record.ts,
                    record.status.value,
                ),
            )
        return cursor.rowcount == 1

    def message(self, message_id: str) -> MessageRecord | None:
        row = self._db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return _message(row) if row else None

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
        return [_message(row) for row in rows]

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
        return [_message(row) for row in rows]

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

    def total_unread(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE outgoing = 0 AND status != 'read'"
        ).fetchone()[0]


def _contact(row: sqlite3.Row) -> Contact:
    return Contact(row["id"], row["name"], row["host"], row["port"], row["last_seen"])


def _message(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        row["id"],
        row["contact_id"],
        bool(row["outgoing"]),
        row["text"],
        row["ts"],
        Status(row["status"]),
    )
