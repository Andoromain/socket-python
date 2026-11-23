"""Secrets du poste (clé de chiffrement de l'historique, clé du poste) protégés au repos.

* **Windows** : le secret est chiffré par DPAPI (``CryptProtectData``), lié à la session
  Windows de l'utilisateur. Le fichier copié sur une autre machine ou lu par un autre
  compte (ou depuis le disque retiré du poste) est inutilisable sans le mot de passe de
  la session.
* **Linux** : fichier lisible par l'utilisateur seul (droits 600). La protection contre
  le vol du disque relève alors de son chiffrement (LUKS, chiffrement du dossier
  personnel).

Format du fichier : ``dpapi:<base64>`` ou ``plain:<base64>`` (une ligne). Module sans Qt.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_ENTROPY = b"SocketChat secret v1"


class SecretUnavailableError(Exception):
    """Secret présent mais illisible (autre poste, autre compte, fichier abîmé)."""


# -- DPAPI (Windows) -------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    source, _keep = _blob(data)
    entropy, _keep_entropy = _blob(_ENTROPY)
    output = _Blob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = function(
        ctypes.byref(source),
        "Socket Chat" if protect else None,
        ctypes.byref(entropy),
        None,
        None,
        flags,
        ctypes.byref(output),
    )
    if not ok:
        raise SecretUnavailableError(f"DPAPI : erreur {ctypes.GetLastError()}")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def dpapi_available() -> bool:
    return sys.platform == "win32"


# -- Fichiers --------------------------------------------------------------------------
def protect(secret: bytes) -> str:
    """Forme enregistrée d'un secret (une ligne de texte)."""
    if dpapi_available():
        return "dpapi:" + base64.b64encode(_dpapi(secret, protect=True)).decode("ascii")
    return "plain:" + base64.b64encode(secret).decode("ascii")


def unprotect(text: str) -> bytes:
    """Secret d'origine ; lève ``SecretUnavailableError`` s'il est illisible."""
    text = text.strip()
    scheme, _, payload = text.partition(":") if ":" in text else ("plain", "", text)
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecretUnavailableError("Secret abîmé.") from exc
    if scheme == "plain":
        return raw
    if scheme == "dpapi":
        if not dpapi_available():
            raise SecretUnavailableError("Secret protégé par Windows (DPAPI) : illisible ici.")
        return _dpapi(raw, protect=False)
    raise SecretUnavailableError("Format de secret inconnu.")


def write_secret(path: str | Path, secret: bytes) -> None:
    """Enregistre le secret (droits restreints, remplacement atomique)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(protect(secret) + "\n")
    os.replace(temp, path)


def read_secret(path: str | Path) -> bytes | None:
    """Secret enregistré, ``None`` si le fichier n'existe pas.

    Lève ``SecretUnavailableError`` si le fichier existe mais ne peut pas être lu.
    """
    try:
        text = Path(path).read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretUnavailableError(str(exc)) from exc
    return unprotect(text)


def load_or_create(path: str | Path, size: int = 32) -> tuple[bytes, bool]:
    """Secret aléatoire de ``size`` octets, créé s'il n'existe pas.

    Renvoie ``(secret, créé)``. Lève ``SecretUnavailableError`` si le fichier est illisible :
    l'appelant décide alors de repartir de zéro (et de mettre de côté ce qui en dépend).
    """
    secret = read_secret(path)
    if secret is not None:
        if len(secret) != size:
            raise SecretUnavailableError("Secret de taille inattendue.")
        return secret, False
    secret = os.urandom(size)
    write_secret(path, secret)
    return secret, True


def set_aside(path: str | Path, label: str = "illisible") -> Path | None:
    """Renomme un fichier inutilisable (sans l'effacer) ; renvoie son nouveau nom."""
    path = Path(path)
    if not path.exists():
        return None
    target = path.with_name(f"{path.stem}-{label}{path.suffix}")
    index = 1
    while target.exists():
        index += 1
        target = path.with_name(f"{path.stem}-{label}-{index}{path.suffix}")
    path.replace(target)
    # Journal SQLite (-wal, -shm) de l'ancien fichier : déplacé avec lui, sinon SQLite
    # le rejouerait dans le nouveau fichier créé à la même place.
    for suffix in ("-wal", "-shm"):
        companion = path.with_name(path.name + suffix)
        if companion.exists():
            companion.replace(target.with_name(target.name + suffix))
    log.warning("Fichier inutilisable mis de côté : %s", target)
    return target
