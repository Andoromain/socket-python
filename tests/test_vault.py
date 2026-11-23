import os
import sys

import pytest

from socket_chat import vault


def test_secret_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "profil" / "secret.key"
    secret, created = vault.load_or_create(path)
    assert created and len(secret) == 32
    again, created = vault.load_or_create(path)
    assert again == secret and not created
    stored = path.read_text()
    assert secret.hex() not in stored
    if sys.platform == "win32":
        assert stored.startswith("dpapi:")
    else:
        assert stored.startswith("plain:")
        assert os.stat(path).st_mode & 0o777 == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI : Windows seulement")
def test_dpapi_protects_the_secret():
    import base64

    secret = os.urandom(32)
    text = vault.protect(secret)
    assert text.startswith("dpapi:")
    assert secret not in base64.b64decode(text.split(":", 1)[1])
    assert vault.unprotect(text) == secret
    tampered = "dpapi:" + base64.b64encode(b"\x00" * 64).decode()
    with pytest.raises(vault.SecretUnavailableError):
        vault.unprotect(tampered)


def test_unreadable_secrets():
    with pytest.raises(vault.SecretUnavailableError):
        vault.unprotect("plain:pas du base64 !")
    with pytest.raises(vault.SecretUnavailableError):
        vault.unprotect("inconnu:AAAA")
    if sys.platform != "win32":
        with pytest.raises(vault.SecretUnavailableError):
            vault.unprotect("dpapi:AAAA")  # fichier venu d'un poste Windows
    assert vault.unprotect("AAAA") == b"\x00\x00\x00"  # ancien format sans préfixe


def test_wrong_size_and_set_aside(tmp_path):
    path = tmp_path / "history.key"
    vault.write_secret(path, b"court")
    with pytest.raises(vault.SecretUnavailableError):
        vault.load_or_create(path)
    assert vault.set_aside(path) == tmp_path / "history-illisible.key"
    vault.write_secret(path, b"x")
    assert vault.set_aside(path) == tmp_path / "history-illisible-2.key"
    assert vault.set_aside(path) is None


def test_open_history_encrypts_and_recovers_from_lost_key(tmp_path):
    from socket_chat.app import open_history
    from socket_chat.store import MessageRecord, Status

    store, warning = open_history(tmp_path)
    assert store.encrypted and warning is None
    store.upsert_contact("11111111-1111-4111-8111-111111111111", "Alice")
    store.add_message(
        MessageRecord(
            "00000000-0000-4000-8000-000000000001",
            "11111111-1111-4111-8111-111111111111",
            True,
            "bonjour",
            1,
            Status.SENT,
        )
    )
    store.close()

    store, warning = open_history(tmp_path)  # même clé : historique relu
    assert warning is None and len(store.messages("11111111-1111-4111-8111-111111111111")) == 1
    store.close()

    (tmp_path / "history.key").unlink()  # clé perdue
    store, warning = open_history(tmp_path)
    assert warning is not None and "history-illisible.sqlite3" in warning
    assert store.contacts() == []
    assert (tmp_path / "history-illisible.sqlite3").exists()
    store.close()

    (tmp_path / "history.key").write_text("dpapi:AAAA" if sys.platform != "win32" else "x:y")
    store, warning = open_history(tmp_path)  # clé illisible (autre poste)
    assert warning is not None and (tmp_path / "history-illisible.key").exists()
    store.close()


def test_set_aside_moves_sqlite_journal(tmp_path):
    """Le journal WAL de l'historique mis de côté ne doit pas être rejoué dans le nouveau."""
    for name in ("history.sqlite3", "history.sqlite3-wal", "history.sqlite3-shm"):
        (tmp_path / name).write_text(name)
    target = vault.set_aside(tmp_path / "history.sqlite3")
    assert target == tmp_path / "history-illisible.sqlite3"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "history-illisible.sqlite3",
        "history-illisible.sqlite3-shm",
        "history-illisible.sqlite3-wal",
    ]
    assert (tmp_path / "history-illisible.sqlite3-wal").read_text() == "history.sqlite3-wal"
