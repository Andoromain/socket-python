// Noise XX : vecteur officiel (le même que tests/test_noise.py), poignée de main,
// altérations, flux chiffré.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/net/noise.dart';

Uint8List hex(String text) =>
    Uint8List.fromList([for (var i = 0; i < text.length; i += 2) int.parse(text.substring(i, i + 2), radix: 16)]);

String toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

(HandshakeState, HandshakeState, KeyPair, KeyPair) handshake() {
  final alice = KeyPair.generate();
  final bob = KeyPair.generate();
  final a = HandshakeState(true, alice, prologue: prologue);
  final b = HandshakeState(false, bob, prologue: prologue);
  b.readMessage(a.writeMessage());
  a.readMessage(b.writeMessage());
  b.readMessage(a.writeMessage());
  return (a, b, alice, bob);
}

void main() {
  final vector = jsonDecode(File('test/fixtures/noise_xx_vector.json').readAsStringSync()) as Map<String, dynamic>;
  KeyPair key(String name) => KeyPair(hex(vector[name] as String));

  test('vecteur officiel (cacophony)', () {
    final initiator = HandshakeState(
      true,
      key('init_static'),
      prologue: hex(vector['init_prologue'] as String),
      ephemeral: key('init_ephemeral'),
    );
    final responder = HandshakeState(
      false,
      key('resp_static'),
      prologue: hex(vector['resp_prologue'] as String),
      ephemeral: key('resp_ephemeral'),
    );
    var writer = initiator;
    var reader = responder;
    final messages = vector['messages'] as List<dynamic>;
    for (var i = 0; i < messages.length; i++) {
      final step = messages[i] as Map<String, dynamic>;
      final payload = hex(step['payload'] as String);
      final Uint8List ciphertext;
      if (i < 3) {
        ciphertext = writer.writeMessage(payload);
        expect(reader.readMessage(ciphertext), payload);
      } else {
        ciphertext = writer.send!.encrypt(const [], payload);
        expect(reader.receive!.decrypt(const [], ciphertext), payload);
      }
      expect(toHex(ciphertext), step['ciphertext'], reason: 'message $i');
      final swap = writer;
      writer = reader;
      reader = swap;
    }
    expect(toHex(initiator.handshakeHash), vector['handshake_hash']);
    expect(toHex(responder.handshakeHash), vector['handshake_hash']);
    expect(initiator.remoteStatic, key('resp_static').public);
    expect(responder.remoteStatic, key('init_static').public);
  });

  test('poignée de main aléatoire et transport', () {
    final (a, b, alice, bob) = handshake();
    expect(a.complete && b.complete, isTrue);
    expect(a.remoteStatic, bob.public);
    expect(b.remoteStatic, alice.public);
    expect(a.handshakeHash, b.handshakeHash);
    for (var i = 0; i < 3; i++) {
      final text = utf8.encode('bonjour $i');
      expect(b.receive!.decrypt(const [], a.send!.encrypt(const [], text)), text);
      expect(a.receive!.decrypt(const [], b.send!.encrypt(const [], [1, 2])), [1, 2]);
    }
  });

  test('préambule différent : échec', () {
    final a = HandshakeState(true, KeyPair.generate(), prologue: utf8.encode('SocketChat/4'));
    final b = HandshakeState(false, KeyPair.generate(), prologue: utf8.encode('SocketChat/5'));
    b.readMessage(a.writeMessage());
    expect(() => a.readMessage(b.writeMessage()), throwsA(isA<NoiseException>()));
  });

  test('altération et rejeu détectés', () {
    var (a, b, _, _) = handshake();
    final ciphertext = a.send!.encrypt(const [], utf8.encode('message secret'));
    ciphertext[3] ^= 1;
    expect(() => b.receive!.decrypt(const [], ciphertext), throwsA(isA<NoiseException>()));

    (a, b, _, _) = handshake();
    final first = a.send!.encrypt(const [], [1]);
    expect(b.receive!.decrypt(const [], first), [1]);
    expect(() => b.receive!.decrypt(const [], first), throwsA(isA<NoiseException>()));
  });

  test('messages hors tour ou tronqués', () {
    final b = HandshakeState(false, KeyPair.generate());
    expect(() => b.writeMessage(), throwsA(isA<NoiseException>()));
    expect(() => b.readMessage([1, 2, 3]), throwsA(isA<NoiseException>()));
    final a2 = HandshakeState(true, KeyPair.generate());
    final b2 = HandshakeState(false, KeyPair.generate());
    b2.readMessage(a2.writeMessage());
    expect(() => a2.readMessage(b2.writeMessage().sublist(0, 40)), throwsA(isA<NoiseException>()));
  });

  test('clé publique de petit ordre refusée', () {
    final b = HandshakeState(false, KeyPair.generate());
    expect(() {
      b.readMessage(Uint8List(32));
      b.writeMessage();
    }, throwsA(isA<NoiseException>()));
  });

  test('flux découpé et données volumineuses', () {
    final (a, b, _, _) = handshake();
    final data = Uint8List.fromList([for (var i = 0; i < 256 * 600; i++) i % 256]);
    final wire = encryptStream(a.send!, data);
    final reader = FrameReader();
    final received = BytesBuilder();
    for (var start = 0; start < wire.length; start += 777) {
      final end = start + 777 < wire.length ? start + 777 : wire.length;
      for (final message in reader.feed(wire.sublist(start, end))) {
        expect(message.length <= maxPayloadSize + 16, isTrue);
        received.add(b.receive!.decrypt(const [], message));
      }
    }
    expect(received.takeBytes(), data);
    expect(() => frame(Uint8List(70000)), throwsA(isA<NoiseException>()));
  });
}
