"""Noise XX : vecteurs officiels, poignée de main, altérations, flux chiffré."""

import json
from pathlib import Path

import pytest

from socket_chat.noise import (
    MAX_PAYLOAD_SIZE,
    FrameReader,
    HandshakeState,
    KeyPair,
    NoiseError,
    encrypt_stream,
    frame,
)

VECTOR = json.loads(
    (Path(__file__).parent / "fixtures" / "noise_xx_vector.json").read_text(encoding="utf-8")
)


def key(name):
    return KeyPair(bytes.fromhex(VECTOR[name]))


def test_official_vector():
    """Vecteur de cacophony : mêmes octets, même empreinte de poignée de main."""
    initiator = HandshakeState(
        True,
        key("init_static"),
        bytes.fromhex(VECTOR["init_prologue"]),
        ephemeral=key("init_ephemeral"),
    )
    responder = HandshakeState(
        False,
        key("resp_static"),
        bytes.fromhex(VECTOR["resp_prologue"]),
        ephemeral=key("resp_ephemeral"),
    )
    writer, reader = initiator, responder
    for index, step in enumerate(VECTOR["messages"]):
        payload, expected = bytes.fromhex(step["payload"]), bytes.fromhex(step["ciphertext"])
        if index < 3:
            ciphertext = writer.write_message(payload)
            assert ciphertext == expected, f"message {index}"
            assert reader.read_message(ciphertext) == payload
        else:
            ciphertext = writer.send.encrypt(b"", payload)
            assert ciphertext == expected, f"message {index}"
            assert reader.receive.decrypt(b"", ciphertext) == payload
        writer, reader = reader, writer
    assert initiator.handshake_hash.hex() == VECTOR["handshake_hash"]
    assert responder.handshake_hash.hex() == VECTOR["handshake_hash"]
    assert initiator.remote_static == key("resp_static").public
    assert responder.remote_static == key("init_static").public


def handshake(prologue_a=b"p", prologue_b=b"p"):
    alice, bob = KeyPair.generate(), KeyPair.generate()
    a = HandshakeState(True, alice, prologue_a)
    b = HandshakeState(False, bob, prologue_b)
    b.read_message(a.write_message())
    a.read_message(b.write_message())
    b.read_message(a.write_message())
    return a, b, alice, bob


def test_random_handshake_and_transport():
    a, b, alice, bob = handshake()
    assert a.complete and b.complete
    assert a.remote_static == bob.public and b.remote_static == alice.public
    assert a.handshake_hash == b.handshake_hash
    for i in range(3):
        assert b.receive.decrypt(b"", a.send.encrypt(b"", b"bonjour %d" % i)) == b"bonjour %d" % i
        assert a.receive.decrypt(b"", b.send.encrypt(b"", b"salut")) == b"salut"


def test_prologue_mismatch_fails():
    a = HandshakeState(True, KeyPair.generate(), b"SocketChat/4")
    b = HandshakeState(False, KeyPair.generate(), b"SocketChat/5")
    b.read_message(a.write_message())
    with pytest.raises(NoiseError):
        a.read_message(b.write_message())


def test_tampering_and_replay_are_detected():
    a, b, _, _ = handshake()
    ciphertext = bytearray(a.send.encrypt(b"", b"message secret"))
    ciphertext[3] ^= 1
    with pytest.raises(NoiseError):
        b.receive.decrypt(b"", bytes(ciphertext))

    a, b, _, _ = handshake()
    first = a.send.encrypt(b"", b"un")
    assert b.receive.decrypt(b"", first) == b"un"
    with pytest.raises(NoiseError):
        b.receive.decrypt(b"", first)  # rejoué : le compteur a avancé


def test_out_of_turn_and_truncated_messages():
    b = HandshakeState(False, KeyPair.generate())
    with pytest.raises(NoiseError):
        b.write_message()
    with pytest.raises(NoiseError):
        b.read_message(b"court")
    a2 = HandshakeState(True, KeyPair.generate())
    b2 = HandshakeState(False, KeyPair.generate())
    b2.read_message(a2.write_message())
    with pytest.raises(NoiseError):
        a2.read_message(b2.write_message()[:40])


def test_low_order_public_key_is_refused():
    b = HandshakeState(False, KeyPair.generate())
    with pytest.raises(NoiseError):
        b.read_message(b"\x00" * 32)
        b.write_message()


def test_stream_framing_and_large_data():
    a, b, _, _ = handshake()
    data = bytes(range(256)) * 600  # plus grand qu'un message Noise
    wire = encrypt_stream(a.send, data)
    reader = FrameReader()
    received = bytearray()
    for start in range(0, len(wire), 777):  # découpage TCP arbitraire
        for message in reader.feed(wire[start : start + 777]):
            assert len(message) <= MAX_PAYLOAD_SIZE + 16
            received += b.receive.decrypt(b"", message)
    assert bytes(received) == data
    assert reader.take_rest() == b""
    with pytest.raises(NoiseError):
        frame(b"x" * 70000)
