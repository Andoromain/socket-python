import os
import sys

import pytest

from socket_chat import identity
from socket_chat.noise import KeyPair


def test_load_or_create_persists_the_key(tmp_path):
    path = tmp_path / "profil" / "identity.key"
    first = identity.load_or_create(path)
    again = identity.load_or_create(path)
    assert first.public == again.public and first.private == again.private
    if sys.platform != "win32":
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_damaged_key_file_is_replaced(tmp_path):
    path = tmp_path / "identity.key"
    path.write_text("abîmé")
    keys = identity.load_or_create(path)
    assert identity.load_or_create(path).public == keys.public
    assert (tmp_path / "identity.key.abime").read_text() == "abîmé"


def test_key_encoding():
    keys = KeyPair.generate()
    assert identity.decode_key(identity.encode_key(keys.public)) == keys.public
    for bad in ("", "pas du base64", identity.encode_key(b"court")):
        with pytest.raises(ValueError):
            identity.decode_key(bad)


def test_safety_number_is_symmetric_and_specific():
    a, b, c = (KeyPair.generate().public for _ in range(3))
    number = identity.safety_number(a, b)
    assert number == identity.safety_number(b, a)
    groups = number.split(" ")
    assert len(groups) == 12 and all(len(g) == 5 and g.isdigit() for g in groups)
    assert identity.safety_number(a, c) != number


def test_safety_number_reference():
    """Valeur figée : la version Android doit calculer exactement la même."""
    a = bytes(range(32))
    b = bytes(range(32, 64))
    assert identity.safety_number(a, b) == identity.safety_number(b, a)
    assert identity.safety_number(a, b) == REFERENCE


REFERENCE = "87624 42352 67435 40402 88089 81683 62017 44235 17188 02176 17326 20500"
