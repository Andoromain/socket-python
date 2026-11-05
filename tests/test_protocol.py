import json

import pytest

from socket_chat.protocol import (
    MAX_DATAGRAM_SIZE,
    MAX_FRAME_SIZE,
    MAX_TEXT_LENGTH,
    PROTOCOL_VERSION,
    AckStatus,
    FrameDecoder,
    Message,
    MessageType,
    ProtocolError,
    decode,
    decode_announce,
    encode,
    encode_announce,
    encode_leave,
    is_valid_id,
    new_id,
    validate_name,
)

UID = "6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10"


@pytest.mark.parametrize(
    "message",
    [
        Message.hello(UID, "Élodie", 12800),
        Message.chat(UID, "Bonjour 👋\nDeuxième ligne"),
        Message.chat(UID, ""),
        Message.ack(UID, AckStatus.DELIVERED),
        Message.ack(UID, AckStatus.READ),
        Message.typing(True),
        Message.typing(False),
        Message.bye("au revoir"),
    ],
)
def test_roundtrip(message):
    frame = encode(message)
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    assert decode(frame[:-1]) == message


def test_decoder_handles_split_and_merged_frames():
    data = (
        encode(Message.hello(UID, "Alice", 1))
        + encode(Message.chat(UID, "é" * 50))
        + encode(Message.bye())
    )
    decoder = FrameDecoder()
    received = []
    # Découpage arbitraire, y compris au milieu d'un caractère UTF-8.
    for i in range(0, len(data), 7):
        received += decoder.feed(data[i : i + 7])
    assert [m.type for m in received] == [MessageType.HELLO, MessageType.CHAT, MessageType.BYE]
    assert received[1].text == "é" * 50


def test_decoder_ignores_blank_lines():
    assert FrameDecoder().feed(b"\n\r\n" + encode(Message.typing(True))) == [Message.typing(True)]


def test_decoder_rejects_oversized_frames():
    with pytest.raises(ProtocolError):
        FrameDecoder().feed(b"a" * MAX_FRAME_SIZE)
    with pytest.raises(ProtocolError):
        FrameDecoder(max_frame_size=10).feed(b'{"type":"typing","active":true}\n')


def _hello(**changes):
    payload = {"type": "hello", "version": PROTOCOL_VERSION, "id": UID, "name": "A", "port": 1}
    payload.update(changes)
    return json.dumps(payload).encode()


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"\xff\xfe",
        b"[1, 2]",
        b'{"type": "unknown"}',
        _hello(version=1),
        _hello(version=True),
        _hello(id="pas-un-uuid"),
        _hello(id=UID.upper()),
        _hello(name="   "),
        _hello(port=0),
        _hello(port=70000),
        _hello(port="12800"),
        json.dumps({"type": "chat", "id": UID, "text": 3}).encode(),
        json.dumps({"type": "chat", "text": "sans id"}).encode(),
        json.dumps({"type": "chat", "id": UID, "text": "x" * (MAX_TEXT_LENGTH + 1)}).encode(),
        json.dumps({"type": "ack", "id": UID, "status": "lost"}).encode(),
        json.dumps({"type": "typing", "active": "yes"}).encode(),
    ],
)
def test_decode_rejects_invalid_frames(line):
    with pytest.raises(ProtocolError):
        decode(line)


def test_version_mismatch_message_is_helpful():
    with pytest.raises(ProtocolError, match="mettez Socket Chat à jour"):
        decode(_hello(version=1))


def test_encode_rejects_too_long_text():
    with pytest.raises(ProtocolError):
        encode(Message.chat(UID, "x" * (MAX_TEXT_LENGTH + 1)))


def test_announcements():
    announce = decode_announce(encode_announce(UID, "Alice", 12800))
    assert (announce.id, announce.name, announce.port, announce.leaving) == (
        UID,
        "Alice",
        12800,
        False,
    )
    assert decode_announce(encode_leave(UID)).leaving
    with pytest.raises(ProtocolError):
        decode_announce(b"x" * (MAX_DATAGRAM_SIZE + 1))
    with pytest.raises(ProtocolError):
        decode_announce(json.dumps({"type": "hello", "version": 2, "id": UID}).encode())


def test_ids():
    assert is_valid_id(new_id())
    assert new_id() != new_id()
    assert not is_valid_id(None)
    assert not is_valid_id("x" * 36)


def test_validate_name():
    assert validate_name("  Jean   Dupont ") == "Jean Dupont"
    with pytest.raises(ValueError):
        validate_name(" ")
    with pytest.raises(ValueError):
        validate_name("x" * 33)
