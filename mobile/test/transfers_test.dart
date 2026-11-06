// Transposition de tests/test_transfers.py (hors aperçus d'image : PR suivante).

import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/net/transfers.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';

import 'helpers.dart';

final Random _random = Random(42);

Uint8List randomBytes(int size) => Uint8List.fromList(List.generate(size, (_) => _random.nextInt(256)));

FileState state(Messenger m, String messageId) => m.store.message(messageId)!.file!.state;

Future<void> waitState(Messenger m, String messageId, FileState expected) =>
    waitUntil(() => state(m, messageId) == expected);

Future<MessageRecord> offer(Messenger alice, Messenger bob, String path) async {
  final added = next(bob.messageAdded);
  final record = alice.sendFile(bob.userId, path);
  final received = await added;
  expect(received.id, record.id);
  expect(received.file!.state, FileState.offered);
  return record;
}

void main() {
  late Messengers messengers;
  late Directory tmp;
  late Messenger alice;
  late Messenger bob;

  setUp(() async {
    messengers = Messengers();
    tmp = Directory.systemTemp.createTempSync('socket-chat-transfers-');
    alice = await messengers.make('Alice');
    bob = await messengers.make('Bob');
    await link(alice, bob);
  });
  tearDown(() async {
    progressStep = 256 * 1024;
    await messengers.dispose();
    tmp.deleteSync(recursive: true);
  });

  String write(String name, List<int> content) {
    final file = File('${tmp.path}/$name')..writeAsBytesSync(content);
    return file.path;
  }

  for (final size in [0, 10, 3 * 1024 * 1024 + 123]) {
    test('transfert de fichier ($size octets)', () async {
      final content = randomBytes(size);
      final source = write('rapport final.bin', content);
      final record = await offer(alice, bob, source);
      expect(record.file!.name, 'rapport final.bin');
      expect(record.file!.size, size);
      await waitUntil(() => alice.store.message(record.id)!.status == Status.delivered);

      final progress = <(int, int)>[];
      bob.fileProgress.connect((p) => progress.add((p.$2, p.$3)));
      final destination = '${tmp.path}/reçu/rapport.bin';
      bob.acceptFile(record.id, destination);
      expect([FileState.accepted, FileState.transferring, FileState.done], contains(state(bob, record.id)));
      await waitState(bob, record.id, FileState.done);
      await waitState(alice, record.id, FileState.done);

      expect(File(destination).readAsBytesSync(), content);
      expect(File('$destination.part').existsSync(), isFalse);
      expect(bob.store.message(record.id)!.file!.path, destination);
      expect(progress.last, (size, size));
      if (size > progressStep) expect(progress.length, greaterThan(2));
    });
  }

  test('refus', () async {
    final record = await offer(alice, bob, write('a.txt', [120]));
    bob.declineFile(record.id);
    expect(state(bob, record.id), FileState.declined);
    await waitState(alice, record.id, FileState.declined);
    expect(() => bob.acceptFile(record.id, '${tmp.path}/b.txt'), throwsArgumentError);
  });

  for (final canceller in ['expéditeur', 'destinataire']) {
    test('annulation en cours de transfert ($canceller)', () async {
      progressStep = 1;
      final record = await offer(alice, bob, write('gros.bin', randomBytes(40 * 1024 * 1024)));
      final destination = '${tmp.path}/gros-recu.bin';
      final started = next(bob.fileProgress);
      bob.acceptFile(record.id, destination);
      await started;
      (canceller == 'expéditeur' ? alice : bob).cancelFile(record.id);
      await waitState(alice, record.id, FileState.cancelled);
      await waitState(bob, record.id, FileState.cancelled);
      expect(File(destination).existsSync(), isFalse);
      expect(File('$destination.part').existsSync(), isFalse);
    });
  }

  test("l'expéditeur peut retirer sa proposition", () async {
    final record = await offer(alice, bob, write('a.txt', [120]));
    alice.cancelFile(record.id);
    expect(state(alice, record.id), FileState.cancelled);
    await waitState(bob, record.id, FileState.cancelled);
  });

  test('fichier source disparu', () async {
    final source = write('parti.txt', 'bientôt supprimé'.codeUnits);
    final record = await offer(alice, bob, source);
    File(source).deleteSync();
    bob.acceptFile(record.id, '${tmp.path}/x.txt');
    await waitState(alice, record.id, FileState.failed);
    await waitState(bob, record.id, FileState.cancelled);
    expect(File('${tmp.path}/x.txt.part').existsSync(), isFalse);
  });

  test('corruption détectée', () async {
    final record = await offer(alice, bob, write('a.bin', randomBytes(5000)));
    bob.acceptFile(record.id, '${tmp.path}/b.bin');
    // Bloc incohérent injecté avant les vrais : le destinataire doit tout rejeter.
    final conn = bob.connections[alice.userId]!;
    bob.transfers.handle(conn, alice.userId, Message.fileChunk(record.id, 999, Uint8List.fromList([120])));
    expect(state(bob, record.id), FileState.failed);
    await waitState(alice, record.id, FileState.failed);
    expect(File('${tmp.path}/b.bin').existsSync(), isFalse);
  });

  test('transfert interrompu puis repris', () async {
    progressStep = 1;
    final bobStore = Store('${tmp.path}/bob.sqlite3');
    final carol = await messengers.make('Carol', store: bobStore);
    final carolId = carol.userId;
    await link(alice, carol);
    final content = randomBytes(40 * 1024 * 1024);
    final record = await offer(alice, carol, write('gros.bin', content));
    final destination = '${tmp.path}/recu.bin';
    final started = next(carol.fileProgress);
    carol.acceptFile(record.id, destination);
    await started;

    carol.stop(); // coupure en plein transfert
    expect(state(carol, record.id), FileState.failed);
    await waitState(alice, record.id, FileState.failed);

    final carol2 = await messengers.make('Carol', userId: carolId, store: bobStore);
    final back = next(carol2.presenceChanged);
    alice.onPeerSeen(carolId, 'Carol', '127.0.0.1', carol2.port!);
    alice.connectTo(carolId);
    await back;
    carol2.acceptFile(record.id, destination);
    await waitState(carol2, record.id, FileState.done);
    expect(File(destination).readAsBytesSync(), content);
  });

  test("accepter exige que l'expéditeur soit en ligne", () async {
    final record = await offer(alice, bob, write('a.txt', [120]));
    final gone = next(bob.presenceChanged);
    alice.stop();
    await gone;
    expect(
      () => bob.acceptFile(record.id, '${tmp.path}/b.txt'),
      throwsA(isA<ArgumentError>().having((e) => e.message, 'message', contains('pas connecté'))),
    );
  });

  test("validation de l'envoi de fichier", () async {
    const other = '11111111-1111-4111-8111-111111111111';
    alice.store.upsertContact(other, 'Dan');
    expect(() => alice.sendFile(other, '${tmp.path}/absent.txt'), throwsArgumentError);
    expect(() => alice.sendFile(other, tmp.path), throwsArgumentError); // un dossier
    expect(() => alice.sendFile('inconnu', '${tmp.path}/absent.txt'), throwsArgumentError);
  });

  test('nom de fichier envoyé nettoyé', () async {
    final source = write('photo.jpg', [1, 2, 3]);
    final added = next(bob.messageAdded);
    alice.sendFile(bob.userId, source, name: '../../etc/passwd');
    expect((await added).file!.name, 'passwd');
  });

  test('réactions partagées', () async {
    final added = next(bob.messageAdded);
    final record = alice.send(bob.userId, 'Réunion à 14 h ?');
    await added;

    var changed = next(alice.messageChanged);
    bob.react(record.id, '👍');
    expect(await changed, record.id);
    expect(alice.store.message(record.id)!.reactionOf(bob.userId), '👍');
    expect(bob.store.message(record.id)!.reactionOf(me), '👍');

    changed = next(alice.messageChanged);
    bob.react(record.id, '');
    await changed;
    expect(alice.store.message(record.id)!.reactions, isEmpty);
  });

  test('réaction hors ligne envoyée à la reconnexion', () async {
    final added = next(bob.messageAdded);
    final record = alice.send(bob.userId, 'Coucou');
    await added;
    final gone = next(bob.presenceChanged);
    alice.stop();
    await gone;
    await alice.start(0);

    bob.react(record.id, '❤️'); // Alice est injoignable : réaction mise de côté
    final changed = next(alice.messageChanged);
    bob.onPeerSeen(alice.userId, 'Alice', '127.0.0.1', alice.port!);
    bob.connectTo(alice.userId);
    await changed;
    expect(alice.store.message(record.id)!.reactionOf(bob.userId), '❤️');
  });

  test('refus hors ligne envoyé à la reconnexion', () async {
    final record = await offer(alice, bob, write('a.txt', [120]));
    final gone = next(bob.presenceChanged);
    alice.stop();
    await gone;
    await alice.start(0);
    bob.declineFile(record.id);
    bob.onPeerSeen(alice.userId, 'Alice', '127.0.0.1', alice.port!);
    bob.connectTo(alice.userId);
    await waitState(alice, record.id, FileState.declined);
  });

  test('checkSendable', () {
    expect(checkSendable(write('a.txt', [1, 2])), 2);
    expect(() => checkSendable(tmp.path), throwsArgumentError);
  });
}
