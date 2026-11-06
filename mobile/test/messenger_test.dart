// Transposition de tests/test_messenger.py et tests/test_network_order.py.

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/net/connection.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
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
    final bob2 = await messengers.make('Bob', userId: bobId, store: bobStore);
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
      final conn = PeerConnection.outgoing(Message.hello(alice.userId, 'Alice', 1));
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

  group('ordre de la présentation', () {
    late ServerSocket server;
    late Socket client;
    late RawSocket accepted;
    final frames = <Message>[];

    setUp(() async {
      frames.clear();
      server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      // Le serveur de test est un RawServerSocket, comme Messenger.
      await server.close();
      final raw = await RawServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final incoming = raw.first;
      client = await Socket.connect('127.0.0.1', raw.port);
      accepted = await incoming;
      await raw.close();
      final decoder = FrameDecoder();
      client.listen((data) => frames.addAll(decoder.feed(data)));
    });
    tearDown(() => client.destroy());

    test('notre hello précède les trames envoyées dès que le pair est prêt', () async {
      handshakeTimeoutMs = 300;
      addTearDown(() => handshakeTimeoutMs = 10000);
      final myId = newId();
      final conn = PeerConnection.accepted(accepted, Message.hello(myId, 'Moi', 1));
      final closed = <String>[];
      conn.closed.connect(closed.add);
      // Comme Messenger, on envoie quelque chose dès que le pair est identifié.
      conn.ready.connect((_) => conn.send(Message.typing(true)));

      // Le « hello » du pair arrive avant notre présentation différée.
      client.add(encode(Message.hello(newId(), 'Pair', 2)));
      await waitUntil(() => frames.length >= 2);
      expect(frames[0].type, MessageType.hello);
      expect(frames[0].id, myId);
      expect(frames[1], Message.typing(true));

      // Le délai de présentation ne doit pas couper une connexion identifiée.
      await pause(600);
      expect(closed, isEmpty);
      expect(conn.isReady, isTrue);
      expect(frames.where((f) => f.type == MessageType.hello), hasLength(1));
      conn.abort();
    });

    test('un pair muet est coupé au bout du délai de présentation', () async {
      handshakeTimeoutMs = 200;
      addTearDown(() => handshakeTimeoutMs = 10000);
      final conn = PeerConnection.accepted(accepted, Message.hello(newId(), 'Moi', 1));
      final closed = next(conn.closed);
      expect(await closed, contains('présenté'));
    });

    test('une trame avant la présentation coupe la connexion', () async {
      final conn = PeerConnection.accepted(accepted, Message.hello(newId(), 'Moi', 1));
      final closed = next(conn.closed);
      client.add(encode(Message.typing(true)));
      expect(await closed, contains('avant la présentation'));
    });

    test('des données invalides coupent la connexion', () async {
      final conn = PeerConnection.accepted(accepted, Message.hello(newId(), 'Moi', 1));
      final closed = next(conn.closed);
      client.add('pas du json\n'.codeUnits);
      expect(await closed, contains('Données invalides'));
    });

    test('fermeture polie : bye puis fin de connexion', () async {
      final conn = PeerConnection.accepted(accepted, Message.hello(newId(), 'Moi', 1));
      client.add(encode(Message.hello(newId(), 'Pair', 2)));
      await waitUntil(() => conn.isReady);
      final closed = next(conn.closed);
      conn.close('au revoir');
      await waitUntil(() => frames.any((f) => f.type == MessageType.bye));
      expect(frames.last, Message.bye('au revoir'));
      client.destroy(); // le pair ferme à son tour
      expect(await closed, 'au revoir');
    });

    test('bye reçu : la connexion se termine avec son motif', () async {
      final conn = PeerConnection.accepted(accepted, Message.hello(newId(), 'Moi', 1));
      client.add(encode(Message.hello(newId(), 'Pair', 2)));
      await waitUntil(() => conn.isReady);
      final closed = next(conn.closed);
      client.add(encode(Message.bye('à demain')));
      await client.flush();
      await client.close();
      expect(await closed, 'à demain');
    });
  });

  test('photos de profil échangées, changées et retirées', () async {
    final jpeg = Uint8List.fromList([0xff, 0xd8, 0xff, 0xe0, ...List.filled(500, 7)]);
    final alice = await messengers.make('Alice');
    final bob = await messengers.make('Bob');
    alice.setAvatar(jpeg); // avant la connexion : envoyée dès qu'elle est prête
    var changed = next(bob.avatarChanged);
    alice.addContact('127.0.0.1', bob.port!);
    expect(await changed, alice.userId);
    expect(bob.store.contact(alice.userId)!.avatar, jpeg);
    expect(alice.store.contact(bob.userId)!.avatar, isEmpty);

    final png = Uint8List.fromList([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0]);
    changed = next(alice.avatarChanged);
    bob.setAvatar(png);
    await changed;
    expect(alice.store.contact(bob.userId)!.avatar, png);

    changed = next(bob.avatarChanged);
    alice.setAvatar(Uint8List(0));
    await changed;
    expect(bob.store.contact(alice.userId)!.avatar, isEmpty);
  });

  test('photo de profil invalide refusée', () async {
    final alice = await messengers.make('Alice', start: false);
    expect(() => alice.setAvatar(Uint8List.fromList('GIF89a'.codeUnits)), throwsArgumentError);
    expect(alice.avatar, isEmpty);
  });
}
