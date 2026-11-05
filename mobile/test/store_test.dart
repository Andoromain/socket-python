// Transposition de tests/test_store.py.

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:sqlite3/sqlite3.dart';

const a = '11111111-1111-4111-8111-111111111111';
const b = '22222222-2222-4222-8222-222222222222';

String mid(int i) => '00000000-0000-4000-8000-${i.toString().padLeft(12, '0')}';

MessageRecord msg(int i, {String contact = a, bool outgoing = true, int? ts, Status status = Status.pending}) =>
    MessageRecord(mid(i), contact, outgoing, 'm$i', ts ?? i, status);

MessageRecord fileMsg(int i, {FileState state = FileState.offered, String thumb = ''}) => MessageRecord(
  mid(i),
  a,
  true,
  '',
  i,
  Status.pending,
  file: FileInfo(
    thumb.isNotEmpty ? 'photo.png' : 'doc.pdf',
    1234,
    state,
    '/tmp/x',
    Uint8List.fromList(thumb.codeUnits),
  ),
);

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('socket-chat-store-'));
  tearDown(() => tmp.deleteSync(recursive: true));

  test("mise à jour d'un contact sans perdre son adresse", () {
    final store = Store('${tmp.path}/h.sqlite3');
    store.upsertContact(a, 'Alice', host: '10.0.0.1', port: 12800, lastSeen: 5);
    store.upsertContact(a, 'Alice Martin');
    final contact = store.contact(a)!;
    expect([contact.name, contact.host, contact.port, contact.lastSeen], ['Alice Martin', '10.0.0.1', 12800, 5]);
    store.upsertContact(a, 'Alice Martin', lastSeen: 1);
    expect(store.contact(a)!.lastSeen, 5);
    expect(store.contact(b), isNull);
    store.close();
  });

  test('persistance après réouverture', () {
    final path = '${tmp.path}/sous/dossier/h.sqlite3';
    final store = Store(path);
    store.upsertContact(a, 'Alice');
    store.addMessage(msg(1));
    store.close();
    final reopened = Store(path);
    expect(reopened.messages(a).map((m) => m.text), ['m1']);
    reopened.close();
  });

  test('ordre, doublons et limite', () {
    final store = Store();
    store.upsertContact(a, 'Alice');
    expect(store.addMessage(msg(2, ts: 20)), isTrue);
    expect(store.addMessage(msg(1, ts: 10)), isTrue);
    expect(store.addMessage(msg(1, ts: 10)), isFalse);
    expect(store.messages(a).map((m) => m.text), ['m1', 'm2']);
    expect(store.messages(a, limit: 1).map((m) => m.text), ['m2']);
  });

  test("l'état ne fait que progresser", () {
    final store = Store();
    store.upsertContact(a, 'Alice');
    final record = msg(1);
    store.addMessage(record);
    expect(store.updateStatus(record.id, Status.delivered), isTrue);
    expect(store.updateStatus(record.id, Status.sent), isFalse);
    expect(store.updateStatus(record.id, Status.read), isTrue);
    expect(store.message(record.id)!.status, Status.read);
    expect(store.updateStatus('inconnu', Status.read), isFalse);
  });

  test('messages en attente et non lus', () {
    final store = Store();
    store.upsertContact(a, 'Alice');
    store.upsertContact(b, 'Bob');
    store.addMessage(msg(1, status: Status.pending));
    store.addMessage(msg(2, status: Status.sent));
    store.addMessage(msg(3, status: Status.delivered));
    store.addMessage(msg(4, contact: b, outgoing: false, status: Status.delivered));
    store.addMessage(msg(5, contact: b, outgoing: false, status: Status.delivered));
    expect(store.undeliveredOutgoing(a).map((m) => m.text), ['m1', 'm2']);
    expect(store.contactsWithUndelivered(), [a]);
    expect(store.totalUnread(), 2);
    expect(store.markIncomingRead(b), hasLength(2));
    expect(store.markIncomingRead(b), isEmpty);
    expect(store.totalUnread(), 0);
  });

  test('contacts triés par activité', () {
    final store = Store();
    store.upsertContact(a, 'Alice', lastSeen: 1);
    store.upsertContact(b, 'Bob', lastSeen: 1);
    store.addMessage(msg(1, contact: b, outgoing: false, ts: 100, status: Status.delivered));
    store.addMessage(msg(2, contact: a, ts: 50));
    final summaries = store.contacts();
    expect(summaries.map((s) => s.contact.name), ['Bob', 'Alice']);
    expect(summaries[0].unread, 1);
    expect(summaries[0].lastMessage!.text, 'm1');
    expect(summaries[1].unread, 0);
    expect(summaries[1].lastMessage!.outgoing, isTrue);
    store.deleteContact(b);
    expect(store.contacts().map((s) => s.contact.name), ['Alice']);
  });

  test('messages fichier : aller-retour et mises à jour', () {
    final store = Store();
    store.upsertContact(a, 'Alice');
    final record = fileMsg(1, thumb: 'jpeg');
    store.addMessage(record);
    final loaded = store.message(record.id)!;
    expect(loaded.file!.name, 'photo.png');
    expect(loaded.file!.thumb, record.file!.thumb);
    expect(loaded.file!.isImage, isTrue);
    expect(loaded.preview, '🖼 Image');
    expect(store.updateFile(record.id, state: FileState.transferring, path: '/dl/photo.png'), isTrue);
    expect(store.updateFile(record.id, state: FileState.transferring), isFalse);
    expect(store.message(record.id)!.file!.path, '/dl/photo.png');
    expect(store.updateFile(mid(9), state: FileState.done), isFalse);
    expect(store.contacts()[0].lastMessage!.file!.state, FileState.transferring);
    final plain = fileMsg(2);
    expect(plain.preview, '📎 doc.pdf');
    expect(plain.file!.isImage, isFalse);
  });

  test('transferts interrompus marqués en échec à la réouverture', () {
    final path = '${tmp.path}/h.sqlite3';
    final store = Store(path);
    store.upsertContact(a, 'Alice');
    store.addMessage(fileMsg(1, state: FileState.transferring));
    store.addMessage(fileMsg(2, state: FileState.accepted));
    store.addMessage(fileMsg(3, state: FileState.done));
    store.close();
    final reopened = Store(path);
    expect(reopened.messages(a).map((m) => m.file!.state), [FileState.failed, FileState.failed, FileState.done]);
    reopened.close();
  });

  test('réactions', () {
    final store = Store();
    store.upsertContact(a, 'Alice');
    final record = msg(1);
    store.addMessage(record);
    expect(store.setReaction(record.id, me, '👍'), isTrue);
    expect(store.setReaction(record.id, me, '👍'), isFalse);
    expect(store.setReaction(record.id, a, '😂'), isTrue);
    expect(store.setReaction(record.id, me, '❤️'), isTrue);
    final loaded = store.message(record.id)!;
    expect(loaded.reactionOf(me), '❤️');
    expect(loaded.reactionOf(a), '😂');
    expect(store.messages(a)[0].reactions, loaded.reactions);
    expect(store.setReaction(record.id, me, ''), isTrue);
    expect(store.message(record.id)!.reactions, [(a, '😂')]);
    expect(store.setReaction(mid(5), me, '👍'), isFalse);
  });

  test('migration depuis le schéma 1', () {
    final path = '${tmp.path}/old.sqlite3';
    final db = sqlite3.open(path);
    db.execute('''
      CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT,
                             port INTEGER, last_seen INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE messages (id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
                             outgoing INTEGER NOT NULL, text TEXT NOT NULL,
                             ts INTEGER NOT NULL, status TEXT NOT NULL);
      PRAGMA user_version = 1;
    ''');
    db.execute("INSERT INTO contacts VALUES (?, 'Alice', NULL, NULL, 0)", [a]);
    db.execute("INSERT INTO messages VALUES ('m1', ?, 0, 'ancien', 1, 'read')", [a]);
    db.close();

    final store = Store(path);
    expect(store.messages(a).map((m) => m.text), ['ancien']);
    expect(store.messages(a)[0].file, isNull);
    expect(store.setReaction('m1', me, '👍'), isTrue);
    store.addMessage(fileMsg(2));
    store.close();
    final check = sqlite3.open(path);
    expect(check.select('PRAGMA user_version').first.columnAt(0), schemaVersion);
    check.close();
  });

  test('historique plus récent refusé', () {
    final path = '${tmp.path}/new.sqlite3';
    final db = sqlite3.open(path);
    db.execute('PRAGMA user_version = ${schemaVersion + 1}');
    db.close();
    expect(() => Store(path), throwsStateError);
  });
}
