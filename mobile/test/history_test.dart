// Historique chiffré au repos (transposition de tests/test_store.py et test_vault.py).

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/history.dart';
import 'package:socket_chat_mobile/app/secrets.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:sqlite3/sqlite3.dart';

const a = '11111111-1111-4111-8111-111111111111';
const secret = 'Code du coffre : 4521';
final key = List<int>.generate(32, (i) => i);

MessageRecord secretMessage([int i = 1]) =>
    MessageRecord('00000000-0000-4000-8000-${i.toString().padLeft(12, '0')}', a, true, secret, i, Status.pending);

bool hasBytes(List<int> haystack, List<int> needle) {
  outer:
  for (var i = 0; i + needle.length <= haystack.length; i++) {
    for (var j = 0; j < needle.length; j++) {
      if (haystack[i + j] != needle[j]) continue outer;
    }
    return true;
  }
  return false;
}

List<int> diskBytes(Directory dir) => [for (final f in dir.listSync().whereType<File>()) ...f.readAsBytesSync()];

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('socket-chat-history-'));
  tearDown(() => tmp.deleteSync(recursive: true));

  test('historique chiffré : relu, rien en clair sur le disque', () {
    final path = '${tmp.path}/h.sqlite3';
    final store = Store(path, key);
    expect(store.encrypted, isTrue);
    store.upsertContact(a, 'Alice');
    store.addMessage(secretMessage());
    final thumb = Uint8List.fromList([0xff, 0xd8, ...utf8.encode('JPEG-APERCU-SECRET')]);
    final file = MessageRecord(
      '00000000-0000-4000-8000-000000000002',
      a,
      true,
      '',
      2,
      Status.pending,
      file: FileInfo('photo.png', 10, FileState.offered, '/x', thumb),
    );
    store.addMessage(file);
    store.updateFile(file.id, path: '/sdcard/Download/salaires-2026.xlsx');
    expect(store.messages(a).first.text, secret);
    final loaded = store.message(file.id)!.file!;
    expect([loaded.name, loaded.path], ['photo.png', '/sdcard/Download/salaires-2026.xlsx']);
    expect(loaded.thumb, thumb);
    expect(store.contacts().first.lastMessage!.file!.name, 'photo.png');
    store.close();

    final raw = diskBytes(tmp);
    for (final clear in [secret, 'photo.png', 'salaires-2026', 'JPEG-APERCU-SECRET']) {
      expect(hasBytes(raw, utf8.encode(clear)), isFalse, reason: clear);
    }
    final again = Store(path, key);
    expect(again.message(secretMessage().id)!.text, secret);
    again.close();
  });

  test('un historique existant est chiffré puis compacté', () {
    final path = '${tmp.path}/h.sqlite3';
    final plain = Store(path);
    plain.upsertContact(a, 'Alice');
    for (var i = 1; i < 40; i++) {
      plain.addMessage(secretMessage(i));
    }
    plain.close();
    expect(hasBytes(diskBytes(tmp), utf8.encode(secret)), isTrue);

    final store = Store(path, key);
    expect(store.messages(a).map((m) => m.text).toSet(), {secret});
    store.close();
    expect(hasBytes(diskBytes(tmp), utf8.encode(secret)), isFalse);
  });

  test('clé absente ou incorrecte refusée', () {
    final path = '${tmp.path}/h.sqlite3';
    Store(path, key).close();
    expect(() => Store(path, List<int>.filled(32, 0)), throwsA(isA<HistoryLockedException>()));
    expect(() => Store(path), throwsA(isA<HistoryLockedException>()));
  });

  test('valeur recopiée dans un autre message : illisible', () {
    final path = '${tmp.path}/h.sqlite3';
    final store = Store(path, key);
    store.upsertContact(a, 'Alice');
    final first = secretMessage(1);
    final second = MessageRecord('00000000-0000-4000-8000-000000000002', a, true, 'autre', 2, Status.pending);
    store.addMessage(first);
    store.addMessage(second);
    store.close();
    // Un attaquant recopie le texte chiffré d'un message dans un autre.
    final reopened = Store(path, key);
    reopened.debugExecute('UPDATE messages SET text = (SELECT text FROM messages WHERE id = ?) WHERE id = ?', [
      first.id,
      second.id,
    ]);
    expect(reopened.message(second.id)!.text, unreadable);
    expect(reopened.message(first.id)!.text, secret);
    reopened.close();
  });

  test('openHistory : chiffré, puis ancien historique mis de côté si la clé est perdue', () async {
    final secrets = MemorySecretStore();
    var (store, warning) = await openHistory(tmp.path, secrets);
    expect(store.encrypted && warning == null, isTrue);
    store.upsertContact(a, 'Alice');
    store.addMessage(secretMessage());
    store.close();

    (store, warning) = await openHistory(tmp.path, secrets);
    expect(warning, isNull);
    expect(store.messages(a), hasLength(1));
    store.close();

    secrets.values.clear(); // clé perdue (Keystore réinitialisé)
    (store, warning) = await openHistory(tmp.path, secrets);
    expect(warning, contains('history-illisible.sqlite3'));
    expect(store.contacts(), isEmpty);
    expect(File('${tmp.path}/history-illisible.sqlite3').existsSync(), isTrue);
    store.close();
  });

  test('clé de l’appareil : dans le coffre, ancienne valeur en clair déplacée', () async {
    final prefs = MemoryPreferenceStore();
    final secrets = MemorySecretStore();
    final first = await Settings.load(prefs, secrets);
    final again = await Settings.load(prefs, secrets);
    expect(again.identity.public, first.identity.public);
    expect(prefs.values.containsKey('identity_key'), isFalse);

    final legacyPrefs = MemoryPreferenceStore()..values['identity_key'] = base64.encode(List<int>.filled(32, 7));
    final moved = MemorySecretStore();
    final settings = await Settings.load(legacyPrefs, moved);
    expect(moved.values['identity_key'], base64.encode(List<int>.filled(32, 7)));
    expect(legacyPrefs.values['identity_key'], '');
    expect(settings.identity.private, List<int>.filled(32, 7));
  });

  test('colonnes manquantes réparées (régression 4.0.0)', () {
    final path = '${tmp.path}/h.sqlite3';
    final db = sqlite3.open(path);
    db.execute('''
      CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT,
                             port INTEGER, last_seen INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE messages (id TEXT PRIMARY KEY, contact_id TEXT NOT NULL,
                             outgoing INTEGER NOT NULL, text TEXT NOT NULL,
                             ts INTEGER NOT NULL, status TEXT NOT NULL);
      PRAGMA user_version = 3;
    ''');
    db.execute("INSERT INTO contacts VALUES (?, 'Alice', NULL, NULL, 0)", [a]);
    db.execute("INSERT INTO messages VALUES ('m1', ?, 0, 'ancien', 1, 'read')", [a]);
    db.close();

    final store = Store(path, key);
    final summary = store.contacts().single;
    expect(summary.contact.name, 'Alice');
    expect(summary.contact.publicKey, isNull);
    expect(summary.lastMessage!.text, 'ancien');
    expect(store.setReaction('m1', me, '👍'), isTrue);
    store.close();
  });
}
