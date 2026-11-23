"""Signature Ed25519 du manifeste de mise à jour (``latest.json``).

Le workflow de publication signe les octets exacts de ``latest.json`` avec une clé
privée gardée dans les secrets GitHub ; la signature est publiée à côté, dans
``latest.json.sig`` (base64, une ligne). L'application ne fait confiance à un manifeste
que si sa signature correspond à l'une des clés publiques ci-dessous : un compte GitHub
piraté, un miroir ou un dossier réseau modifié ne peut donc pas proposer un faux
installeur, puisque les empreintes SHA-256 des fichiers viennent de ce manifeste.

Module sans Qt : utilisé aussi par ``scripts/sign_manifest.py``.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

#: Clés publiques acceptées (base64 des 32 octets bruts). Plusieurs clés permettent d'en
#: changer : publier une version qui connaît la nouvelle, puis signer avec celle-ci.
TRUSTED_KEYS: tuple[str, ...] = ("6tCltLIr/pXySFNykBjyDzVcp8kRJUMGkO52JodgQnY=",)

SIGNATURE_SUFFIX = ".sig"
#: Taille maximale d'un fichier de signature (octets).
MAX_SIGNATURE_SIZE = 1024


class SignatureError(ValueError):
    """Signature absente, illisible ou ne correspondant à aucune clé de confiance."""


def _decode(text: str | bytes, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureError(f"{what} illisible.") from exc
    if len(raw) != size:
        raise SignatureError(f"{what} invalide.")
    return raw


def generate_private_key() -> str:
    """Nouvelle clé privée (base64 des 32 octets bruts), à garder secrète."""
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    raw = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    return base64.b64encode(raw).decode("ascii")


def _private_key(private_b64: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_decode(private_b64, 32, "Clé privée"))


def public_key_of(private_b64: str) -> str:
    raw = _private_key(private_b64).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign(data: bytes, private_b64: str) -> str:
    """Signature de ``data`` (base64), à écrire dans ``latest.json.sig``."""
    return base64.b64encode(_private_key(private_b64).sign(data)).decode("ascii")


def verify(data: bytes, signature: str | bytes, keys: tuple[str, ...] | None = None) -> None:
    """Lève ``SignatureError`` si ``signature`` ne correspond à aucune clé de confiance."""
    if len(signature) > MAX_SIGNATURE_SIZE:
        raise SignatureError("Signature trop volumineuse.")
    raw_signature = _decode(signature, 64, "Signature")
    for key in TRUSTED_KEYS if keys is None else keys:
        public = Ed25519PublicKey.from_public_bytes(_decode(key, 32, "Clé publique"))
        try:
            public.verify(raw_signature, data)
        except InvalidSignature:
            continue
        return
    raise SignatureError("Signature non reconnue.")
