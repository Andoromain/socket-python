// Le protocole Dart se comporte exactement comme protocol.py (vecteurs partagés).
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';

Map<String, dynamic> loadVectors() =>
    jsonDecode(File('test/fixtures/protocol_vectors.json').readAsStringSync()) as Map<String, dynamic>;

Map<String, Object?> fieldsOf(Message m) => {
  'type': m.type.wire,
  'id': m.id,
  'name': m.name,
  'port': m.port,
  'text': m.text,
  'status': m.status?.wire,
  'active': m.active,
  'reason': m.reason,
  'emoji': m.emoji,
  'size': m.size,
  'thumb': base64Encode(m.thumb ?? Uint8List(0)),
  'offset': m.offset,
  'data': base64Encode(m.data ?? Uint8List(0)),
  'sha256': m.sha256,
  'media': m.media,
  'sdp_kind': m.sdpKind,
  'sdp': m.sdp,
  'candidate': m.candidate,
  'mid': m.mid,
  'index': m.index,
};

Message messageFromFields(Map<String, dynamic> f) {
  Uint8List? bytes(String key) {
    final raw = base64Decode(f[key] as String);
    return raw.isEmpty ? null : raw;
  }

  return Message(
    MessageType.fromWire(f['type'] as String)!,
    id: f['id'] as String,
    name: f['name'] as String,
    port: f['port'] as int,
    text: f['text'] as String,
    status: AckStatus.fromWire(f['status']),
    active: f['active'] as bool,
    reason: f['reason'] as String,
    emoji: f['emoji'] as String,
    size: f['size'] as int,
    thumb: bytes('thumb'),
    offset: f['offset'] as int,
    data: f['type'] == 'file_chunk' ? base64Decode(f['data'] as String) : bytes('data'),
    sha256: f['sha256'] as String,
    media: f['media'] as String,
    sdpKind: f['sdp_kind'] as String,
    sdp: f['sdp'] as String,
    candidate: f['candidate'] as String,
    mid: f['mid'] as String,
    index: f['index'] as int,
  );
}

void main() {
  final vectors = loadVectors();

  test('même version de protocole', () {
    expect(vectors['protocol_version'], protocolVersion);
  });

  test('trames valides : décodage et encodage identiques à Python', () {
    for (final entry in vectors['valid'] as List) {
      final frame = base64Decode(entry['frame'] as String);
      final fields = (entry['fields'] as Map).cast<String, dynamic>();
      final decoded = decode(frame.sublist(0, frame.length - 1));
      expect(decoded, isNotNull, reason: utf8.decode(frame));
      expect(fieldsOf(decoded!), fields, reason: utf8.decode(frame));
      // Encodage octet pour octet identique à celui de Python.
      expect(encode(messageFromFields(fields)), frame, reason: utf8.decode(frame));
    }
  });

  test('trames invalides rejetées', () {
    for (final raw in vectors['invalid'] as List) {
      final line = base64Decode(raw as String);
      expect(() => decode(line), throwsA(isA<ProtocolException>()), reason: String.fromCharCodes(line));
    }
  });

  test('types inconnus ignorés', () {
    for (final raw in vectors['unknown'] as List) {
      expect(decode(base64Decode(raw as String)), isNull);
    }
  });

  test('noms d\'utilisateur', () {
    for (final pair in vectors['names'] as List) {
      final raw = pair[0] as String;
      final expected = pair[1] as String?;
      if (expected == null) {
        expect(() => validateName(raw), throwsArgumentError, reason: raw);
      } else {
        expect(validateName(raw), expected, reason: raw);
      }
    }
  });

  test('noms de fichiers nettoyés', () {
    for (final pair in vectors['file_names'] as List) {
      expect(safeFileName(pair[0] as String), pair[1], reason: pair[0] as String);
    }
  });

  test('annonces UDP', () {
    for (final entry in vectors['announce_valid'] as List) {
      final a = decodeAnnounce(base64Decode(entry['datagram'] as String));
      expect([a.id, a.name, a.port], [entry['id'], entry['name'], entry['port']]);
    }
    for (final raw in vectors['announce_invalid'] as List) {
      expect(() => decodeAnnounce(base64Decode(raw as String)), throwsA(isA<ProtocolException>()));
    }
    final announce = decodeAnnounce(encodeAnnounce('6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10', 'Bob', 12800));
    expect(announce.port, 12800);
    expect(decodeAnnounce(encodeLeave('6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10')).leaving, isTrue);
  });

  test('décodeur de flux : trames découpées et regroupées', () {
    const uid = '6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10';
    final data = <int>[
      ...encode(Message.hello(uid, 'Alice', 1)),
      ...encode(Message.chat(uid, 'é' * 50)),
      ...'\n\r\n'.codeUnits,
      ...'{"type":"future"}\n'.codeUnits,
      ...encode(Message.bye()),
    ];
    final decoder = FrameDecoder();
    final received = <Message>[];
    for (var i = 0; i < data.length; i += 7) {
      received.addAll(decoder.feed(data.sublist(i, i + 7 > data.length ? data.length : i + 7)));
    }
    expect(received.map((m) => m.type), [MessageType.hello, MessageType.chat, MessageType.bye]);
    expect(received[1].text, 'é' * 50);
    expect(() => FrameDecoder().feed(Uint8List(maxFrameSize)), throwsA(isA<ProtocolException>()));
  });

  test('un bloc de fichier tient dans une trame', () {
    final frame = encode(
      Message.fileChunk('6f1c2c1e-6a51-4f6a-9d3e-0c8b8a3c9b10', 0, Uint8List(chunkSize)..fillRange(0, chunkSize, 255)),
    );
    expect(frame.length, lessThan(maxFrameSize));
  });
}
