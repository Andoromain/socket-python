"""Identité cryptographique du poste et code de sécurité des contacts.

Chaque installation possède une paire de clés X25519 créée au premier lancement et
gardée dans ``identity.key`` (profil de l'utilisateur ; protégée par DPAPI sous Windows,
lisible par l'utilisateur seul sous Linux, voir ``vault``). La clé
publique est présentée et prouvée à chaque connexion (Noise XX) ; le correspondant
l'épingle au premier contact et refuse ensuite toute autre clé.

Le **code de sécurité** de deux postes se calcule à partir de leurs deux clés
publiques : il est identique des deux côtés. Le comparer de vive voix (ou par
téléphone) garantit qu'aucun intermédiaire ne s'est glissé dans la conversation.

Module sans Qt ; mobile/lib/net/identity.dart calcule le même code.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from pathlib import Path

from . import vault
from .noise import DHLEN, KeyPair, NoiseError

log = logging.getLogger(__name__)

#: Préambule des poignées de main : distingue Socket Chat (protocole 4) de tout autre.
PROLOGUE = b"SocketChat/4"
_SAFETY_LABEL = b"SocketChat safety number v1"
SAFETY_GROUPS = 12


def encode_key(public: bytes) -> str:
    return base64.b64encode(public).decode("ascii")


def decode_key(text: str) -> bytes:
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Clé invalide.") from exc
    if len(raw) != DHLEN:
        raise ValueError("Clé invalide.")
    return raw


def load_or_create(path: str | Path) -> KeyPair:
    """Clé du poste ; créée si le fichier est absent ou illisible.

    Le fichier est protégé par ``vault`` (DPAPI sous Windows, droits 600 sous Linux).
    """
    path = Path(path)
    try:
        private = vault.read_secret(path)
        if private is not None:
            return KeyPair(private)
    except (vault.SecretUnavailableError, NoiseError) as exc:
        # Fichier abîmé ou venu d'une autre session : une nouvelle clé est créée ; les
        # contacts verront « clé changée » et devront l'accepter.
        log.warning("Clé du poste illisible (%s) : nouvelle clé créée", exc)
        vault.set_aside(path, "abime")
    keys = KeyPair.generate()
    vault.write_secret(path, keys.private)
    log.info("Nouvelle clé de poste créée : %s", fingerprint(keys.public))
    return keys


def fingerprint(public: bytes) -> str:
    """Empreinte courte d'une clé (journaux, diagnostics)."""
    return hashlib.sha256(public).hexdigest()[:16]


def safety_number(key_a: bytes, key_b: bytes) -> str:
    """Code de sécurité de deux postes : 12 groupes de 5 chiffres, même ordre des deux côtés."""
    first, second = sorted((key_a, key_b))
    digest = hashlib.sha512(_SAFETY_LABEL + first + second).digest()
    groups = [
        f"{int.from_bytes(digest[i * 5 : i * 5 + 5], 'big') % 100_000:05d}"
        for i in range(SAFETY_GROUPS)
    ]
    return " ".join(groups)
