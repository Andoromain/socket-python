"""Clé de signature des mises à jour utilisée par les tests (voir conftest.py)."""

from pathlib import Path

from socket_chat import signing

PRIVATE_KEY = signing.generate_private_key()
PUBLIC_KEY = signing.public_key_of(PRIVATE_KEY)


def sign_manifest(manifest: Path, private_key: str = PRIVATE_KEY) -> Path:
    """Écrit ``<manifeste>.sig`` à côté du manifeste."""
    output = manifest.with_name(manifest.name + signing.SIGNATURE_SUFFIX)
    output.write_text(signing.sign(manifest.read_bytes(), private_key), encoding="ascii")
    return output
