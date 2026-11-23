import sqlite3

from socket_chat.store import (
    ME,
    SCHEMA_VERSION,
    FileInfo,
    FileState,
    MessageRecord,
    Status,
    Store,
)

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def msg(i, contact=A, outgoing=True, ts=None, status=Status.PENDING):
    return MessageRecord(
        f"00000000-0000-4000-8000-{i:012d}", contact, outgoing, f"m{i}", ts or i, status
    )


def test_contacts_upsert_keeps_known_address(tmp_path):
    store = Store(tmp_path / "h.sqlite3")
    store.upsert_contact(A, "Alice", host="10.0.0.1", port=12800, last_seen=5)
    store.upsert_contact(A, "Alice Martin")  # nom mis à jour, adresse conservée
    contact = store.contact(A)
    assert (contact.name, contact.host, contact.port, contact.last_seen) == (
        "Alice Martin",
        "10.0.0.1",
        12800,
        5,
    )
    store.upsert_contact(A, "Alice Martin", last_seen=1)
    assert store.contact(A).last_seen == 5  # jamais de retour en arrière
    assert store.contact(B) is None


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "h.sqlite3"
    store = Store(path)
    store.upsert_contact(A, "Alice")
    store.add_message(msg(1))
    store.close()
    reopened = Store(path)
    assert [m.text for m in reopened.messages(A)] == ["m1"]


def test_messages_order_duplicates_and_limit():
    store = Store()
    store.upsert_contact(A, "Alice")
    assert store.add_message(msg(2, ts=20))
    assert store.add_message(msg(1, ts=10))
    assert not store.add_message(msg(1, ts=10))  # doublon ignoré
    assert [m.text for m in store.messages(A)] == ["m1", "m2"]
    assert [m.text for m in store.messages(A, limit=1)] == ["m2"]


def test_status_only_moves_forward():
    store = Store()
    store.upsert_contact(A, "Alice")
    record = msg(1)
    store.add_message(record)
    assert store.update_status(record.id, Status.DELIVERED)
    assert not store.update_status(record.id, Status.SENT)
    assert store.update_status(record.id, Status.READ)
    assert store.message(record.id).status is Status.READ
    assert not store.update_status("inconnu", Status.READ)


def test_undelivered_and_unread():
    store = Store()
    store.upsert_contact(A, "Alice")
    store.upsert_contact(B, "Bob")
    store.add_message(msg(1, status=Status.PENDING))
    store.add_message(msg(2, status=Status.SENT))
    store.add_message(msg(3, status=Status.DELIVERED))
    store.add_message(msg(4, contact=B, outgoing=False, status=Status.DELIVERED))
    store.add_message(msg(5, contact=B, outgoing=False, status=Status.DELIVERED))
    assert [m.text for m in store.undelivered_outgoing(A)] == ["m1", "m2"]
    assert store.contacts_with_undelivered() == [A]
    assert store.total_unread() == 2
    assert len(store.mark_incoming_read(B)) == 2
    assert store.mark_incoming_read(B) == []
    assert store.total_unread() == 0


def test_contact_summaries_sorted_by_activity():
    store = Store()
    store.upsert_contact(A, "Alice", last_seen=1)
    store.upsert_contact(B, "Bob", last_seen=1)
    store.add_message(msg(1, contact=B, outgoing=False, ts=100, status=Status.DELIVERED))
    store.add_message(msg(2, contact=A, ts=50))
    summaries = store.contacts()
    assert [s.contact.name for s in summaries] == ["Bob", "Alice"]
    assert summaries[0].unread == 1 and summaries[0].last_message.text == "m1"
    assert summaries[1].unread == 0 and summaries[1].last_message.outgoing
    store.delete_contact(B)
    assert [s.contact.name for s in store.contacts()] == ["Alice"]


def file_msg(i, *, state=FileState.OFFERED, outgoing=True, thumb=b""):
    return MessageRecord(
        f"00000000-0000-4000-8000-{i:012d}",
        A,
        outgoing,
        "",
        i,
        Status.PENDING,
        FileInfo("photo.png" if thumb else "doc.pdf", 1234, state, "/tmp/x", thumb),
    )


def test_file_messages_roundtrip_and_updates():
    store = Store()
    store.upsert_contact(A, "Alice")
    record = file_msg(1, thumb=b"jpeg")
    store.add_message(record)
    loaded = store.message(record.id)
    assert loaded.file == record.file and loaded.file.is_image
    assert loaded.preview == "🖼 Image"
    assert store.update_file(record.id, state=FileState.TRANSFERRING, path="/dl/photo.png")
    assert not store.update_file(record.id, state=FileState.TRANSFERRING)
    assert store.message(record.id).file.path == "/dl/photo.png"
    assert not store.update_file(msg(9).id, state=FileState.DONE)  # message inconnu
    summary = store.contacts()[0]
    assert summary.last_message.file.state is FileState.TRANSFERRING
    plain = file_msg(2)
    assert plain.preview == "📎 doc.pdf" and not plain.file.is_image


def test_interrupted_transfers_are_failed_on_reopen(tmp_path):
    path = tmp_path / "h.sqlite3"
    store = Store(path)
    store.upsert_contact(A, "Alice")
    store.add_message(file_msg(1, state=FileState.TRANSFERRING))
    store.add_message(file_msg(2, state=FileState.ACCEPTED))
    store.add_message(file_msg(3, state=FileState.DONE))
    store.close()
    reopened = Store(path)
    states = [m.file.state for m in reopened.messages(A)]
    assert states == [FileState.FAILED, FileState.FAILED, FileState.DONE]


def test_reactions():
    store = Store()
    store.upsert_contact(A, "Alice")
    record = msg(1)
    store.add_message(record)
    assert store.set_reaction(record.id, ME, "👍")
    assert not store.set_reaction(record.id, ME, "👍")
    assert store.set_reaction(record.id, A, "😂")
    assert store.set_reaction(record.id, ME, "❤️")  # remplace la précédente
    loaded = store.message(record.id)
    assert loaded.reaction_of(ME) == "❤️" and loaded.reaction_of(A) == "😂"
    assert store.messages(A)[0].reactions == loaded.reactions
    assert store.set_reaction(record.id, ME, "")
    assert store.message(record.id).reactions == ((A, "😂"),)
    assert not store.set_reaction(msg(5).id, ME, "👍")  # message inconnu


def test_migration_from_schema_1(tmp_path):
    """Une base créée par la 3.0 (schéma 1) est mise à niveau sans perte."""
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT,
                               port INTEGER, last_seen INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE messages (id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
                               outgoing INTEGER NOT NULL, text TEXT NOT NULL,
                               ts INTEGER NOT NULL, status TEXT NOT NULL);
        PRAGMA user_version = 1;
        """
    )
    db.execute("INSERT INTO contacts VALUES (?, 'Alice', NULL, NULL, 0)", (A,))
    db.execute("INSERT INTO messages VALUES ('m1', ?, 0, 'ancien', 1, 'read')", (A,))
    db.commit()
    db.close()

    store = Store(path)
    assert [m.text for m in store.messages(A)] == ["ancien"]
    assert store.messages(A)[0].file is None
    assert store.set_reaction("m1", ME, "👍")
    store.add_message(file_msg(2))
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_migration_from_schema_1_adds_key_columns(tmp_path):
    test_migration_from_schema_1(tmp_path)
    store = Store(tmp_path / "old.sqlite3")
    contact = store.contact(A)
    assert contact.public_key is None and contact.pending_key is None


def test_key_pinning():
    store = Store()
    store.upsert_contact(A, "Alice")
    store.pin_key(A, "clé1")
    assert store.contact(A).public_key == "clé1"
    assert store.set_pending_key(A, "clé2")
    assert not store.set_pending_key(A, "clé2")  # inchangée
    assert store.contact(A).pending_key == "clé2"
    store.upsert_contact(A, "Alice Martin")  # le reste du contact ne touche pas aux clés
    assert store.contact(A).public_key == "clé1"
    store.pin_key(A, "clé2")
    contact = store.contact(A)
    assert contact.public_key == "clé2" and contact.pending_key is None
    assert not store.set_pending_key(A, None)
    assert [s.contact.public_key for s in store.contacts()] == ["clé2"]


# -- Chiffrement au repos ------------------------------------------------------------
KEY = bytes(range(32))
SECRET = "Code du coffre : 4521"


def disk_bytes(directory):
    """Tout ce que SQLite a écrit sur le disque (base, journal WAL)."""
    return b"".join(p.read_bytes() for p in directory.iterdir() if p.is_file())


def secret_message(i=1):
    return MessageRecord(f"00000000-0000-4000-8000-{i:012d}", A, True, SECRET, i, Status.PENDING)


def test_encrypted_history_roundtrip_and_nothing_in_clear(tmp_path):
    store = Store(tmp_path / "h.sqlite3", KEY)
    assert store.encrypted
    store.upsert_contact(A, "Alice")
    store.add_message(secret_message())
    thumb = b"\xff\xd8JPEG-APERCU-SECRET"
    record = file_msg(2, thumb=thumb)
    store.add_message(record)
    store.update_file(record.id, path="/home/alice/Documents/salaires-2026.xlsx")
    assert store.messages(A)[0].text == SECRET
    loaded = store.message(record.id).file
    assert (loaded.name, loaded.thumb) == ("photo.png", thumb)
    assert loaded.path == "/home/alice/Documents/salaires-2026.xlsx"
    assert store.contacts()[0].last_message.file.name == "photo.png"
    store.close()

    raw = disk_bytes(tmp_path)
    for clear in (SECRET.encode(), b"photo.png", b"salaires-2026", b"JPEG-APERCU-SECRET"):
        assert clear not in raw

    again = Store(tmp_path / "h.sqlite3", KEY)
    assert again.message(secret_message().id).text == SECRET
    again.close()


def test_existing_history_is_encrypted_and_compacted(tmp_path):
    path = tmp_path / "h.sqlite3"
    plain = Store(path)
    plain.upsert_contact(A, "Alice")
    for i in range(1, 40):
        plain.add_message(secret_message(i))
    plain.close()
    assert SECRET.encode() in disk_bytes(tmp_path)

    store = Store(path, KEY)
    assert [m.text for m in store.messages(A)] == [SECRET] * 39
    store.close()
    assert SECRET.encode() not in disk_bytes(tmp_path)  # anciennes pages effacées


def test_wrong_or_missing_key_is_refused(tmp_path):
    import pytest

    from socket_chat.store import HistoryLockedError

    path = tmp_path / "h.sqlite3"
    Store(path, KEY).close()
    with pytest.raises(HistoryLockedError):
        Store(path, bytes(32))
    with pytest.raises(HistoryLockedError):
        Store(path)


def test_tampered_or_swapped_values_are_unreadable(tmp_path):
    from socket_chat.store import UNREADABLE

    path = tmp_path / "h.sqlite3"
    store = Store(path, KEY)
    store.upsert_contact(A, "Alice")
    first, second = secret_message(1), msg(2)
    store.add_message(first)
    store.add_message(second)
    db = store._db
    # Un attaquant recopie le texte chiffré d'un message dans un autre : refusé.
    stolen = db.execute("SELECT text FROM messages WHERE id = ?", (first.id,)).fetchone()[0]
    db.execute("UPDATE messages SET text = ? WHERE id = ?", (stolen, second.id))
    db.commit()
    assert store.message(second.id).text == UNREADABLE
    assert store.message(first.id).text == SECRET


def test_missing_columns_are_repaired(tmp_path):
    """Régression 4.0.0 : historique en version 3 auquel il manque des colonnes."""
    path = tmp_path / "h.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT,
                               port INTEGER, last_seen INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE messages (id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
                               outgoing INTEGER NOT NULL, text TEXT NOT NULL,
                               ts INTEGER NOT NULL, status TEXT NOT NULL);
        PRAGMA user_version = 3;
        """
    )
    db.execute("INSERT INTO contacts VALUES (?, 'Alice', NULL, NULL, 0)", (A,))
    db.execute("INSERT INTO messages VALUES ('m1', ?, 0, 'ancien', 1, 'read')", (A,))
    db.commit()
    db.close()

    store = Store(path, KEY)
    summary = store.contacts()[0]
    assert summary.contact.name == "Alice" and summary.contact.public_key is None
    assert summary.last_message.text == "ancien"
    assert store.set_reaction("m1", ME, "👍")
    store.pin_key(A, "clé")
    assert store.contact(A).public_key == "clé"


JPEG = b"\xff\xd8\xff\xe0photo"


def test_contact_avatar():
    store = Store()
    store.upsert_contact(A, "Alice")
    assert store.contact(A).avatar == b""
    assert store.set_avatar(A, JPEG)
    assert not store.set_avatar(A, JPEG)
    assert store.contact(A).avatar == JPEG
    store.upsert_contact(A, "Alice Martin", host="10.0.0.1")  # la photo est conservée
    assert store.contacts()[0].contact.avatar == JPEG
    assert store.set_avatar(A, b"")
    assert store.contact(A).avatar == b""
    assert not store.set_avatar(B, JPEG)  # contact inconnu


def test_migration_from_schema_2(tmp_path):
    """Une base de la 3.1 à la 3.3 (schéma 2) reçoit la colonne des photos."""
    path = tmp_path / "v2.sqlite3"
    old = Store(path)
    old.upsert_contact(A, "Alice", host="10.0.0.1", port=12800)
    old.add_message(msg(1))
    old.close()
    db = sqlite3.connect(path)
    db.executescript("ALTER TABLE contacts DROP COLUMN avatar; PRAGMA user_version = 2;")
    db.close()

    store = Store(path)
    assert store.contact(A).host == "10.0.0.1"
    assert [m.text for m in store.messages(A)] == ["m1"]
    assert store.set_avatar(A, JPEG)
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4


def test_history_from_fork_3_8_is_upgraded(tmp_path):
    """Historique d'une 3.4 à 3.8 (schéma 3 = photo de profil, sans les clés de la 4.0)."""
    path = tmp_path / "h.sqlite3"
    old = Store(path)
    old.upsert_contact(A, "Alice")
    old.set_avatar(A, JPEG)
    old.add_message(msg(1))
    old.close()
    db = sqlite3.connect(path)
    db.executescript(
        "ALTER TABLE contacts DROP COLUMN public_key; ALTER TABLE contacts DROP COLUMN "
        "pending_key; DROP TABLE meta; PRAGMA user_version = 3;"
    )
    db.close()

    store = Store(path, KEY)
    summary = store.contacts()[0]
    assert summary.contact.avatar == JPEG and summary.contact.public_key is None
    assert summary.last_message.text == "m1"
    store.pin_key(A, "clé")
    assert store.contact(A).public_key == "clé"
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
