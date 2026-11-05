import json

import pytest

from socket_chat.protocol import (
    MAX_FRAME_SIZE,
    MAX_TEXT_LENGTH,
    PROTOCOL_VERSION,
    FrameDecoder,
    Message,
    MessageType,
    ProtocolError,
    decode,
    encode,
    validate_name,
)


@pytest.mark.parametrize(
    "message",
    [
        Message.hello("Élodie"),
        Message.chat("Bonjour 👋\nDeuxième ligne"),
        Message.chat(""),
        Message.bye("busy"),
    ],
)
def test_roundtrip(message):
    frame = encode(message)
    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    assert decode(frame[:-1]) == message


def test_decoder_handles_split_and_merged_frames():
    data = encode(Message.hello("Alice")) + encode(Message.chat("é" * 50)) + encode(Message.bye())
    decoder = FrameDecoder()
    received = []
    # Découpage arbitraire, y compris au milieu d'un caractère UTF-8.
    for i in range(0, len(data), 7):
        received += decoder.feed(data[i : i + 7])
    assert [m.type for m in received] == [MessageType.HELLO, MessageType.CHAT, MessageType.BYE]
    assert received[1].text == "é" * 50


def test_decoder_ignores_blank_lines():
    assert FrameDecoder().feed(b"\n\r\n" + encode(Message.chat("x"))) == [Message.chat("x")]


def test_decoder_rejects_oversized_frame_without_newline():
    decoder = FrameDecoder()
    with pytest.raises(ProtocolError):
        decoder.feed(b"a" * MAX_FRAME_SIZE)


def test_decoder_rejects_oversized_frame_with_newline():
    with pytest.raises(ProtocolError):
        FrameDecoder(max_frame_size=10).feed(b'{"type":"chat","text":"long"}\n')


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"\xff\xfe",
        b"[1, 2]",
        b'{"type": "unknown"}',
        b'{"type": "chat", "text": 3}',
        b'{"type": "hello", "name": "A"}',
        b'{"type": "hello", "version": true, "name": "A"}',
        b'{"type": "hello", "version": 999, "name": "A"}',
        b'{"type": "hello", "version": 1, "name": "   "}',
        json.dumps({"type": "chat", "text": "x" * (MAX_TEXT_LENGTH + 1)}).encode(),
    ],
)
def test_decode_rejects_invalid_frames(line):
    with pytest.raises(ProtocolError):
        decode(line)


def test_encode_rejects_too_long_text():
    with pytest.raises(ProtocolError):
        encode(Message.chat("x" * (MAX_TEXT_LENGTH + 1)))


def test_hello_carries_version():
    assert json.loads(encode(Message.hello("A")))["version"] == PROTOCOL_VERSION


def test_validate_name():
    assert validate_name("  Jean   Dupont ") == "Jean Dupont"
    with pytest.raises(ValueError):
        validate_name(" ")
    with pytest.raises(ValueError):
        validate_name("x" * 33)
