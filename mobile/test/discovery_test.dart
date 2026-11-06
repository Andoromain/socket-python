// Transposition de tests/test_discovery.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/net/discovery.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';

import 'helpers.dart';

void main() {
  final created = <Discovery>[];

  /// Deux instances qui s'annoncent l'une à l'autre en unicast sur 127.0.0.1.
  Future<(Discovery, Discovery)> pair() async {
    Future<Discovery> make(String name, int tcpPort) async {
      final d = Discovery(newId(), name, tcpPort, udpPort: 0, targets: const []);
      expect(await d.start(), isTrue);
      created.add(d);
      return d;
    }

    final alice = await make('Alice', 1111);
    final bob = await make('Bob', 2222);
    alice.targets = [('127.0.0.1', bob.localPort)];
    bob.targets = [('127.0.0.1', alice.localPort)];
    return (alice, bob);
  }

  tearDown(() async {
    for (final d in created) {
      d.stop();
    }
    created.clear();
    peerTimeoutMs = 15000;
    await pause(20);
  });

  test('les pairs se découvrent mutuellement', () async {
    final (alice, bob) = await pair();
    final seen = Future.wait([next(alice.peerSeen), next(bob.peerSeen)]);
    alice.announce();
    await seen;
    expect(alice.peers, [bob.userId]);
    expect(bob.peers, [alice.userId]);
  });

  test("l'annonce porte l'identité et l'adresse", () async {
    final (alice, bob) = await pair();
    final seen = next(bob.peerSeen);
    alice.announce();
    expect(await seen, (alice.userId, 'Alice', '127.0.0.1', 1111));
  });

  test('le départ est signalé', () async {
    final (alice, bob) = await pair();
    final seen = next(bob.peerSeen);
    alice.announce();
    await seen;
    final lost = next(bob.peerLost);
    alice.stop();
    expect(await lost, alice.userId);
  });

  test('un pair silencieux est oublié', () async {
    peerTimeoutMs = 100;
    final (alice, bob) = await pair();
    final seen = next(alice.peerSeen);
    bob.announce();
    await seen;
    bob.silence(); // Bob se tait sans annoncer son départ
    expect(await next(alice.peerLost), bob.userId);
  });

  test('datagrammes invalides et les nôtres ignorés', () async {
    final (_, bob) = await pair();
    final seen = <Object>[];
    bob.peerSeen.connect(seen.add);
    final sender = await RawDatagramSocket.bind(InternetAddress.loopbackIPv4, 0);
    for (final datagram in [
      utf8.encode('bruit'),
      utf8.encode(jsonEncode({'type': 'announce', 'version': 1, 'id': newId(), 'name': 'V1', 'port': 1})),
      utf8.encode(jsonEncode({'type': 'announce', 'version': 3, 'id': bob.userId, 'name': 'Moi', 'port': 1})),
      List.filled(2000, 32),
    ]) {
      sender.send(datagram, InternetAddress.loopbackIPv4, bob.localPort);
    }
    await pause();
    expect(seen, isEmpty);
    sender.close();
  });

  test('adresses de diffusion', () async {
    final targets = await broadcastAddresses(extra: ['192.168.1.255']);
    expect(targets, contains('255.255.255.255'));
    expect(targets, contains('192.168.1.255'));
    expect(targets.where((t) => !t.endsWith('.255')), isEmpty);
  });

  test('la détection alimente la messagerie', () async {
    final messengers = Messengers();
    addTearDown(messengers.dispose);
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    final aliceDisc = Discovery(alice.userId, 'Alice', alice.port!, udpPort: 0, targets: const []);
    final bobDisc = Discovery(bob.userId, 'Bob', bob.port!, udpPort: 0, targets: const []);
    expect(await aliceDisc.start() && await bobDisc.start(), isTrue);
    created.addAll([aliceDisc, bobDisc]);
    aliceDisc.targets = [('127.0.0.1', bobDisc.localPort)];
    bobDisc.targets = [('127.0.0.1', aliceDisc.localPort)];
    alice.attachDiscovery(aliceDisc);
    bob.attachDiscovery(bobDisc);

    final online = Future.wait([next(alice.presenceChanged), next(bob.presenceChanged)]);
    aliceDisc.announce();
    await online;
    expect(alice.store.contacts().map((s) => s.contact.name), ['Bob']);
    // Le contact détecté est joignable directement : le message part sans saisie d'adresse.
    final added = next(bob.messageAdded);
    alice.send(bob.userId, 'Trouvé tout seul !');
    await added;
  });
}
