"""Chiffrement des connexions : Noise_XX_25519_ChaChaPoly_SHA256.

Implémentation du cadre Noise (https://noiseprotocol.org/noise.html, révision 34)
limitée au motif XX : chaque poste prouve qu'il détient sa clé statique X25519, les
deux clés éphémères assurent la confidentialité persistante (une clé volée plus tard
ne permet pas de déchiffrer les conversations enregistrées).

    -> e
    <- e, ee, s, es
    -> s, se

Les primitives viennent de ``cryptography`` : X25519, ChaCha20-Poly1305 (IETF), SHA-256.
Vérifiée avec les vecteurs de test officiels (tests/fixtures/noise_xx_vector.json).
Module sans Qt ; la version Android (mobile/lib/net/noise.dart) suit pas à pas.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

PROTOCOL_NAME = b"Noise_XX_25519_ChaChaPoly_SHA256"
DHLEN = 32
HASHLEN = 32
TAGLEN = 16
#: Taille maximale d'un message Noise (octets, étiquette d'authentification comprise).
MAX_MESSAGE_SIZE = 65535
#: Données utiles maximales par message de transport.
MAX_PAYLOAD_SIZE = MAX_MESSAGE_SIZE - TAGLEN
_MAX_NONCE = 2**64 - 1  # valeur réservée par la spécification


class NoiseError(Exception):
    """Poignée de main ou message chiffré invalide."""


# -- Primitives -------------------------------------------------------------------
class KeyPair:
    """Paire de clés X25519 (32 octets bruts chacune)."""

    def __init__(self, private: bytes) -> None:
        if len(private) != DHLEN:
            raise NoiseError("Clé privée invalide.")
        self._key = X25519PrivateKey.from_private_bytes(private)
        self.private = private
        self.public = self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    @classmethod
    def generate(cls) -> KeyPair:
        key = X25519PrivateKey.generate()
        return cls(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))

    def dh(self, public: bytes) -> bytes:
        try:
            return self._key.exchange(X25519PublicKey.from_public_bytes(public))
        except ValueError as exc:  # clé publique de petit ordre (secret nul)
            raise NoiseError("Clé publique du correspondant invalide.") from exc


def _hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _hkdf(chaining_key: bytes, ikm: bytes) -> tuple[bytes, bytes]:
    temp = _hmac(chaining_key, ikm)
    out1 = _hmac(temp, b"\x01")
    return out1, _hmac(temp, out1 + b"\x02")


# -- États de la spécification ------------------------------------------------------
class CipherState:
    def __init__(self, key: bytes | None = None) -> None:
        self._aead = ChaCha20Poly1305(key) if key is not None else None
        self.n = 0

    @property
    def has_key(self) -> bool:
        return self._aead is not None

    def _nonce(self) -> bytes:
        if self.n >= _MAX_NONCE:
            raise NoiseError("Trop de messages sur cette connexion.")
        return b"\x00\x00\x00\x00" + struct.pack("<Q", self.n)

    def encrypt(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._aead is None:
            return plaintext
        ciphertext = self._aead.encrypt(self._nonce(), plaintext, ad)
        self.n += 1
        return ciphertext

    def decrypt(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self._aead is None:
            return ciphertext
        try:
            plaintext = self._aead.decrypt(self._nonce(), ciphertext, ad)
        except InvalidTag as exc:
            raise NoiseError("Message chiffré altéré ou clé incorrecte.") from exc
        self.n += 1
        return plaintext


class SymmetricState:
    def __init__(self, protocol_name: bytes) -> None:
        if len(protocol_name) <= HASHLEN:
            self.h = protocol_name.ljust(HASHLEN, b"\x00")
        else:
            self.h = _hash(protocol_name)
        self.ck = self.h
        self.cipher = CipherState()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, temp_k = _hkdf(self.ck, ikm)
        self.cipher = CipherState(temp_k)

    def mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ciphertext = self.cipher.encrypt(self.h, plaintext)
        self.mix_hash(ciphertext)
        return ciphertext

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        plaintext = self.cipher.decrypt(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return plaintext

    def split(self) -> tuple[CipherState, CipherState]:
        k1, k2 = _hkdf(self.ck, b"")
        return CipherState(k1), CipherState(k2)


class HandshakeState:
    """Poignée de main XX ; ``write_message`` / ``read_message`` à tour de rôle.

    L'initiateur écrit les messages 1 et 3, le répondeur le message 2. Une fois
    ``complete``, ``remote_static`` est la clé publique prouvée du correspondant et
    ``send`` / ``receive`` chiffrent le transport.
    """

    def __init__(
        self,
        initiator: bool,
        static: KeyPair,
        prologue: bytes = b"",
        *,
        ephemeral: KeyPair | None = None,  # imposée par les vecteurs de test
    ) -> None:
        self.initiator = initiator
        self._s = static
        self._e: KeyPair | None = None
        self._fixed_e = ephemeral
        self._re: bytes | None = None
        self.remote_static: bytes | None = None
        self._ss = SymmetricState(PROTOCOL_NAME)
        self._ss.mix_hash(prologue)
        self._step = 0  # nombre de messages de la poignée de main déjà traités
        self.send: CipherState | None = None
        self.receive: CipherState | None = None

    @property
    def complete(self) -> bool:
        return self.send is not None

    @property
    def handshake_hash(self) -> bytes:
        return self._ss.h

    @property
    def my_turn(self) -> bool:
        return not self.complete and (self._step % 2 == 0) == self.initiator

    def _ephemeral(self) -> KeyPair:
        self._e = self._fixed_e or KeyPair.generate()
        return self._e

    def write_message(self, payload: bytes = b"") -> bytes:
        if not self.my_turn:
            raise NoiseError("Ce n'est pas à ce poste d'écrire.")
        ss, out = self._ss, bytearray()
        if self._step == 0:  # -> e
            e = self._ephemeral()
            out += e.public
            ss.mix_hash(e.public)
        elif self._step == 1:  # <- e, ee, s, es
            assert self._re is not None
            e = self._ephemeral()
            out += e.public
            ss.mix_hash(e.public)
            ss.mix_key(e.dh(self._re))
            out += ss.encrypt_and_hash(self._s.public)
            ss.mix_key(self._s.dh(self._re))
        else:  # -> s, se
            assert self._re is not None
            out += ss.encrypt_and_hash(self._s.public)
            ss.mix_key(self._s.dh(self._re))
        out += ss.encrypt_and_hash(payload)
        self._advance()
        if len(out) > MAX_MESSAGE_SIZE:
            raise NoiseError("Message de poignée de main trop long.")
        return bytes(out)

    def read_message(self, message: bytes) -> bytes:
        if self.complete or self.my_turn:
            raise NoiseError("Message de poignée de main inattendu.")
        if len(message) > MAX_MESSAGE_SIZE:
            raise NoiseError("Message de poignée de main trop long.")
        ss = self._ss
        rest = message
        if self._step == 0:  # -> e
            self._re, rest = self._take(rest, DHLEN)
            ss.mix_hash(self._re)
        elif self._step == 1:  # <- e, ee, s, es
            assert self._e is not None
            self._re, rest = self._take(rest, DHLEN)
            ss.mix_hash(self._re)
            ss.mix_key(self._e.dh(self._re))
            encrypted_s, rest = self._take(rest, DHLEN + TAGLEN)
            self.remote_static = ss.decrypt_and_hash(encrypted_s)
            ss.mix_key(self._e.dh(self.remote_static))
        else:  # -> s, se
            assert self._e is not None
            encrypted_s, rest = self._take(rest, DHLEN + TAGLEN)
            self.remote_static = ss.decrypt_and_hash(encrypted_s)
            ss.mix_key(self._e.dh(self.remote_static))
        payload = ss.decrypt_and_hash(rest)
        self._advance()
        return payload

    @staticmethod
    def _take(data: bytes, size: int) -> tuple[bytes, bytes]:
        if len(data) < size:
            raise NoiseError("Message de poignée de main tronqué.")
        return data[:size], data[size:]

    def _advance(self) -> None:
        self._step += 1
        if self._step == 3:
            first, second = self._ss.split()
            self.send, self.receive = (first, second) if self.initiator else (second, first)


# -- Flux chiffré ---------------------------------------------------------------------
class FrameReader:
    """Découpe un flux TCP en messages Noise préfixés par leur longueur (2 octets)."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer += data
        messages = []
        while len(self._buffer) >= 2:
            size = int.from_bytes(self._buffer[:2], "big")
            if len(self._buffer) < 2 + size:
                break
            messages.append(bytes(self._buffer[2 : 2 + size]))
            del self._buffer[: 2 + size]
        return messages

    def take_rest(self) -> bytes:
        rest, self._buffer = bytes(self._buffer), bytearray()
        return rest


def frame(message: bytes) -> bytes:
    if len(message) > MAX_MESSAGE_SIZE:
        raise NoiseError("Message trop long.")
    return len(message).to_bytes(2, "big") + message


def encrypt_stream(cipher: CipherState, data: bytes) -> bytes:
    """Chiffre ``data`` en un ou plusieurs messages de transport prêts à envoyer."""
    out = bytearray()
    for start in range(0, len(data), MAX_PAYLOAD_SIZE):
        out += frame(cipher.encrypt(b"", data[start : start + MAX_PAYLOAD_SIZE]))
    return bytes(out)
