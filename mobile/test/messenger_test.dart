// Transposition de tests/test_messenger.py et tests/test_network_order.py.

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import 'dart:typed_data';

import 'package:socket_chat_mobile/net/connection.dart';
import 'package:socket_chat_mobile/net/identity.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/net/noise.dart' as noise;
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';

import 'helpers.dart';

Status status(Messenger m, String messageId) => m.store.message(messageId)!.status;

void main() {
  late Messengers messengers;
  late Directory tmp;
  setUp(() {
    messengers = Messengers();
    tmp = Directory.systemTemp.createTempSync('socket-chat-messenger-');
  });
  tearDown(() async {
    await messengers.dispose();
    tmp.deleteSync(recursive: true);
  });

  test("l'ajout par adresse crée le contact des deux côtés", () async {
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    await link(alice, bob);
    expect(alice.store.contacts().map((s) => s.contact.name), ['Bob']);
    expect(bob.store.contacts().map((s) => s.contact.name), ['Alice']);
    // Le port mémorisé est le port d'écoute, pas le port éphémère de la connexion.
    expect(bob.store.contact(alice.userId)!.port, alice.port);
    expect(bob.store.contact(alice.userId)!.host, '127.0.0.1');
  });

  test('remise et accusés de lecture', () async {
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    await link(alice, bob);

    final added = next(bob.messageAdded);
    final record = alice.send(bob.userId, '  Salut !\nÇa va ?  ');
    final received = await added;
    expect([received.id, received.text, received.outgoing], [record.id, 'Salut !\nÇa va ?', false]);
    expect(received.unread, isTrue);
    expect(bob.store.totalUnread(), 1);

    await waitUntil(() => status(alice, record.id) == Status.delivered);
    final read = next(alice.messageStatusChanged, where: (v) => v.$1 == record.id && v.$2 == Status.read);
    bob.markRead(alice.userId);
    await read;
    expect(bob.store.totalUnread(), 0);
  });

  test('indication « écrit… »', () async {
    typingTimeoutMs = 200;
    addTearDown(() => typingTimeoutMs = 6000);
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    await link(alice, bob);

    var change = next(bob.typingChanged);
    alice.setTyping(bob.userId, true);
    expect(await change, (alice.userId, true));
    expect(bob.isTyping(alice.userId), isTrue);
    // Sans nouvelle indication, « écrit… » disparaît tout seul.
    expect(await next(bob.typingChanged), (alice.userId, false));

    // Un message reçu efface aussi l'indication.
    alice.setTyping(bob.userId, false);
    change = next(bob.typingChanged);
    alice.setTyping(bob.userId, true);
    await change;
    change = next(bob.typingChanged);
    alice.send(bob.userId, 'fini');
    expect(await change, (alice.userId, false));
  });

  test('messages hors ligne mis en attente puis remis', () async {
    final bobStore = Store('${tmp.path}/bob.sqlite3');
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob', store: bobStore);
    final bobId = bob.userId;
    await link(alice, bob);

    final gone = next(alice.presenceChanged);
    bob.stop();
    expect(await gone, (bobId, false));

    final record = alice.send(bobId, 'Tu verras ça en revenant');
    await pause(100);
    expect(status(alice, record.id), Status.pending);

    // Bob revient (même identité, même historique, nouveau port) et est détecté.
    final bob2 = await messengers.make('Bob', userId: bobId, store: bobStore, identity: bob.identity);
    final added = next(bob2.messageAdded);
    alice.onPeerSeen(bobId, 'Bob', '127.0.0.1', bob2.port!);
    expect((await added).text, 'Tu verras ça en revenant');
    await waitUntil(() => status(alice, record.id) == Status.delivered);
  });

  test("un message retransmis n'est pas dupliqué", () async {
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    await link(alice, bob);
    final added = next(bob.messageAdded);
    final record = alice.send(bob.userId, 'une seule fois');
    await added;
    bob.markRead(alice.userId);
    await waitUntil(() => status(alice, record.id) == Status.read);

    // Retransmission (accusé perdu) : Bob ne l'affiche pas deux fois
    // et renvoie l'état réel du message.
    final duplicates = <MessageRecord>[];
    bob.messageAdded.connect(duplicates.add);
    alice.transmit(alice.connections[bob.userId]!, record);
    await pause();
    expect(duplicates, isEmpty);
    expect(bob.store.messages(alice.userId), hasLength(1));
  });

  test('les connexions croisées convergent', () async {
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    alice.addContact('127.0.0.1', bob.port!);
    bob.addContact('127.0.0.1', alice.port!);
    await waitUntil(() => alice.isOnline(bob.userId) && bob.isOnline(alice.userId));
    await pause(300); // laisse se fermer la connexion en double
    expect(alice.connections.keys, [bob.userId]);
    expect(bob.connections.keys, [alice.userId]);
    expect(alice.connections[bob.userId]!.isReady, isTrue);
    var added = next(alice.messageAdded);
    bob.send(alice.userId, 'ping');
    await added;
    added = next(bob.messageAdded);
    alice.send(bob.userId, 'pong');
    await added;
  });

  test('adresse injoignable signalée', () async {
    final probe = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final port = probe.port;
    await probe.close();

    final alice = await messengers.make('Alice');
    final error = next(alice.error);
    alice.addContact('127.0.0.1', port);
    expect(await error, contains('Impossible de joindre'));
  });

  test('connexion à soi-même expliquée', () async {
    final alice = await messengers.make('Alice');
    final errors = <String>[];
    alice.error.connect(errors.add);
    alice.addContact('127.0.0.1', alice.port!);
    await pause(300);
    expect(alice.store.contacts(), isEmpty);
    expect(errors, [selfError]);
  });

  for (final byeFirst in [false, true]) {
    test("connexion à soi-même expliquée quel que soit l'ordre (bye d'abord : $byeFirst)", () async {
      final alice = await messengers.make('Alice', start: false);
      final errors = <String>[];
      alice.error.connect(errors.add);
      final conn = PeerConnection.outgoing(Message.hello(alice.userId, 'Alice', 1), alice.identity);
      alice.debugWatch(conn, manual: true);
      if (!byeFirst) alice.debugOnReady(conn, alice.userId, 'Alice', 1);
      alice.debugOnClosed(conn, selfReason);
      expect(errors, [selfError]);
    });
  }

  test('port déjà utilisé signalé', () async {
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob', start: false);
    final error = next(bob.error);
    expect(await bob.start(alice.port!), isFalse);
    expect(await error, contains('Impossible d\'écouter'));
  });

  test('validation des messages', () async {
    final alice = await messengers.make('Alice');
    expect(() => alice.send('inconnu', 'x'), throwsArgumentError);
    alice.store.upsertContact('11111111-1111-4111-8111-111111111111', 'Bob');
    expect(() => alice.send('11111111-1111-4111-8111-111111111111', '   '), throwsArgumentError);
    expect(() => alice.send('11111111-1111-4111-8111-111111111111', '🎉' * (maxTextLength + 1)), throwsArgumentError);
  });

  test("une nouvelle adresse remplace une tentative vers l'ancienne", () async {
    // Un serveur qui accepte sans jamais répondre simule une tentative qui traîne.
    final blackHole = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final held = <Socket>[];
    blackHole.listen(held.add);
    addTearDown(() async {
      for (final s in held) {
        s.destroy();
      }
      await blackHole.close();
    });
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    alice.store.upsertContact(bob.userId, 'Bob', host: '127.0.0.1', port: blackHole.port);
    final record = alice.send(bob.userId, 'Tu es là ?');
    expect(alice.dialing.containsKey(bob.userId), isTrue);

    final added = next(bob.messageAdded, limit: const Duration(seconds: 2));
    alice.onPeerSeen(bob.userId, 'Bob', '127.0.0.1', bob.port!);
    expect((await added).id, record.id);
  });

  group('connexion chiffrée vue sur le fil', () {
    late Socket client;
    late RawSocket accepted;
    late _NoiseClient peer;

    setUp(() async {
      final raw = await RawServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final incoming = raw.first;
      client = await Socket.connect('127.0.0.1', raw.port);
      accepted = await incoming;
      await raw.close();
      peer = _NoiseClient(client);
    });
    tearDown(() => client.destroy());

    PeerConnection serve([String? id]) =>
        PeerConnection.accepted(accepted, Message.hello(id ?? newId(), 'Moi', 1), noise.KeyPair.generate());

    test('poignée de main, puis notre hello, puis les trames', () async {
      handshakeTimeoutMs = 300;
      addTearDown(() => handshakeTimeoutMs = 10000);
      final myId = newId();
      final myKeys = noise.KeyPair.generate();
      final conn = PeerConnection.accepted(accepted, Message.hello(myId, 'Moi', 1), myKeys);
      final closed = <String>[];
      conn.closed.connect(closed.add);
      final ready = <(String, String, int)>[];
      conn.ready.connect(ready.add);
      // Comme Messenger, on envoie quelque chose dès que le pair est identifié.
      conn.ready.connect((_) => conn.send(Message.typing(true)));

      await peer.handshake();
      await waitUntil(() => peer.frames.isNotEmpty);
      expect(peer.frames[0].type, MessageType.hello);
      expect(peer.frames[0].id, myId);
      expect(peer.state.remoteStatic, myKeys.public);

      final peerId = newId();
      peer.send(Message.hello(peerId, 'Pair', 2));
      await waitUntil(() => peer.frames.length >= 2);
      expect(peer.frames[1], Message.typing(true));
      expect(ready, [(peerId, 'Pair', 2)]);
      expect(conn.peerKey, peer.keys.public);

      // Le délai de présentation ne doit pas couper une connexion identifiée.
      await pause(600);
      expect(closed, isEmpty);
      expect(conn.isReady, isTrue);
      expect(peer.frames.where((f) => f.type == MessageType.hello), hasLength(1));
      conn.abort();
    });

    test('un pair muet est coupé au bout du délai', () async {
      handshakeTimeoutMs = 200;
      addTearDown(() => handshakeTimeoutMs = 10000);
      final closed = next(serve().closed);
      expect(await closed, contains('présenté'));
    });

    test('un pair en protocole 3 (JSON en clair) est reconnu', () async {
      final closed = next(serve().closed);
      client.add(encode(Message.hello(newId(), 'Vieux', 1)));
      expect(await closed, oldVersionError);
    });

    test('une poignée de main altérée coupe la connexion', () async {
      final closed = next(serve().closed);
      client.add(noise.frame(peer.state.writeMessage()));
      final msg2 = await peer.nextRaw();
      peer.state.readMessage(msg2);
      final msg3 = peer.state.writeMessage();
      msg3[5] ^= 0xff;
      client.add(noise.frame(msg3));
      expect(await closed, startsWith('Connexion chiffrée impossible'));
    });

    test('une trame avant la présentation coupe la connexion', () async {
      final closed = next(serve().closed);
      await peer.handshake();
      peer.send(Message.typing(true));
      expect(await closed, contains('avant la présentation'));
    });

    test('des données invalides coupent la connexion', () async {
      final closed = next(serve().closed);
      await peer.handshake();
      peer.sendRaw('pas du json\n'.codeUnits);
      expect(await closed, contains('Données invalides'));
    });

    test('fermeture polie : bye puis fin de connexion', () async {
      final conn = serve();
      await peer.handshake();
      peer.send(Message.hello(newId(), 'Pair', 2));
      await waitUntil(() => conn.isReady);
      final closed = next(conn.closed);
      conn.close('au revoir');
      await waitUntil(() => peer.frames.any((f) => f.type == MessageType.bye));
      expect(peer.frames.last, Message.bye('au revoir'));
      client.destroy(); // le pair ferme à son tour
      expect(await closed, 'au revoir');
    });

    test('bye reçu : la connexion se termine avec son motif', () async {
      final conn = serve();
      await peer.handshake();
      peer.send(Message.hello(newId(), 'Pair', 2));
      await waitUntil(() => conn.isReady);
      final closed = next(conn.closed);
      peer.send(Message.bye('à demain'));
      await client.flush();
      await client.close();
      expect(await closed, 'à demain');
    });
  });

  group('clés de sécurité', () {
    test('le premier contact épingle les deux clés', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      await link(alice, bob);
      expect(alice.store.contact(bob.userId)!.publicKey, encodeKey(bob.identity.public));
      expect(bob.store.contact(alice.userId)!.publicKey, encodeKey(alice.identity.public));
      expect(alice.contactKey(bob.userId), bob.identity.public);
    });

    test('une clé changée est refusée jusqu’à acceptation', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      final bobId = bob.userId;
      await link(alice, bob);
      final gone = next(alice.presenceChanged);
      bob.stop();
      await gone;

      final impostorKeys = noise.KeyPair.generate();
      final impostor = await messengers.make('Bob', userId: bobId, identity: impostorKeys);
      final record = alice.send(bobId, 'secret');
      final refused = <(String, bool)>[];
      impostor.keyRefused.connect(refused.add);
      final errors = <String>[];
      impostor.error.connect(errors.add);
      final changed = next(alice.keyChanged);
      impostor.addContact('127.0.0.1', alice.port!);
      expect(await changed, bobId);
      await waitUntil(() => refused.isNotEmpty);
      expect(refused, [(alice.userId, true)]);
      expect(errors, [keyRefusedError]);
      final contact = alice.store.contact(bobId)!;
      expect(contact.publicKey, encodeKey(bob.identity.public));
      expect(contact.pendingKey, encodeKey(impostorKeys.public));
      expect(impostor.store.messages(alice.userId), isEmpty); // rien n'a fui
      expect(status(alice, record.id), Status.pending);

      alice.store.upsertContact(bobId, 'Bob', host: '127.0.0.1', port: impostor.port);
      final added = next(impostor.messageAdded);
      alice.trustNewKey(bobId);
      expect((await added).text, 'secret');
      await waitUntil(() => refused.last == (alice.userId, false));
      expect(alice.store.contact(bobId)!.publicKey, encodeKey(impostorKeys.public));
      expect(alice.store.contact(bobId)!.pendingKey, isNull);
    });

    test('une annonce ne renomme pas un contact vérifié', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      await link(alice, bob);
      alice.onPeerSeen(bob.userId, 'Usurpateur', '127.0.0.1', bob.port!);
      expect(alice.store.contact(bob.userId)!.name, 'Bob');
    });

    test('code de sécurité identique à Python (vecteurs partagés)', () {
      final vectors =
          jsonDecode(File('test/fixtures/protocol_vectors.json').readAsStringSync()) as Map<String, dynamic>;
      expect(base64.decode(vectors['prologue'] as String), noise.prologue);
      for (final raw in vectors['safety_numbers'] as List<dynamic>) {
        final entry = raw as Map<String, dynamic>;
        expect(safetyNumber(base64.decode(entry['a'] as String), base64.decode(entry['b'] as String)), entry['number']);
      }
    });
  });
}

/// Initiateur Noise minimal au-dessus d'une [Socket] de test.
class _NoiseClient {
  _NoiseClient(this.socket) {
    socket.listen((data) {
      for (final message in reader.feed(data)) {
        if (state.complete) {
          frames.addAll(decoder.feed(state.receive!.decrypt(const [], message)));
        } else {
          raw.add(message);
        }
      }
    });
  }

  final Socket socket;
  final noise.KeyPair keys = noise.KeyPair.generate();
  late final noise.HandshakeState state = noise.HandshakeState(true, keys, prologue: noise.prologue);
  final noise.FrameReader reader = noise.FrameReader();
  final FrameDecoder decoder = FrameDecoder();
  final List<Uint8List> raw = [];
  final List<Message> frames = [];

  Future<Uint8List> nextRaw() async {
    await waitUntil(() => raw.isNotEmpty);
    return raw.removeAt(0);
  }

  Future<void> handshake() async {
    socket.add(noise.frame(state.writeMessage()));
    state.readMessage(await nextRaw());
    socket.add(noise.frame(state.writeMessage()));
  }

  void send(Message message) => sendRaw(encode(message));

  void sendRaw(List<int> plaintext) => socket.add(noise.encryptStream(state.send!, plaintext));
}
