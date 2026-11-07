// Historique local des contacts et des messages (SQLite), transposé de
// src/socket_chat/store.py : même schéma, mêmes règles.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:sqlite3/sqlite3.dart';

const int schemaVersion = 3;

const Set<String> imageExtensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'};

const String _schema = '''
CREATE TABLE IF NOT EXISTS contacts (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    host      TEXT,
    port      INTEGER,
    last_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id         TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    outgoing   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    status     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_contact ON messages(contact_id, ts);
''';

/// Migrations successives : la clé est la version atteinte après application.
const Map<int, String> _migrations = {
  2: '''
    ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text';
    ALTER TABLE messages ADD COLUMN file_name TEXT;
    ALTER TABLE messages ADD COLUMN file_size INTEGER;
    ALTER TABLE messages ADD COLUMN file_state TEXT;
    ALTER TABLE messages ADD COLUMN file_path TEXT;
    ALTER TABLE messages ADD COLUMN thumb BLOB;
    CREATE TABLE reactions (
        message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        author     TEXT NOT NULL,
        emoji      TEXT NOT NULL,
        PRIMARY KEY (message_id, author)
    );
  ''',
  3: '''
    ALTER TABLE contacts ADD COLUMN avatar BLOB;
  ''',
};

/// Auteur des réactions de l'utilisateur local (les autres sont des identifiants de contact).
const String me = 'me';

int nowMs() => DateTime.now().millisecondsSinceEpoch;

/// État d'un message.
///
/// Messages envoyés : `pending` → `sent` → `delivered` → `read`.
/// Messages reçus : `delivered` (non lu) → `read`.
enum Status {
  pending('pending'),
  sent('sent'),
  delivered('delivered'),
  read('read');

  const Status(this.value);
  final String value;

  int get rank => index;

  static Status parse(String value) => values.firstWhere((s) => s.value == value);
}

/// Étape d'un transfert de fichier (du point de vue de ce poste).
enum FileState {
  offered('offered'), // proposé, en attente de la réponse du destinataire
  accepted('accepted'), // accepté, en attente du premier bloc
  transferring('transferring'),
  done('done'),
  declined('declined'),
  cancelled('cancelled'),
  failed('failed');

  const FileState(this.value);
  final String value;

  bool get active => this == accepted || this == transferring;

  static FileState parse(String value) => values.firstWhere((s) => s.value == value);
}

class FileInfo {
  FileInfo(this.name, this.size, this.state, [this.path, Uint8List? thumb]) : thumb = thumb ?? Uint8List(0);

  final String name;
  final int size;
  final FileState state;
  final String? path; // source (envoi) ou destination (réception)
  final Uint8List thumb; // aperçu JPEG pour les images

  String get extension {
    final dot = name.lastIndexOf('.');
    return dot >= 0 ? name.substring(dot + 1).toLowerCase() : '';
  }

  bool get isImage => imageExtensions.contains(extension) && thumb.isNotEmpty;
}

/// Issue d'un appel, du point de vue de cet appareil (mêmes valeurs que sur PC).
enum CallOutcome {
  completed('completed'),
  missed('missed'),
  noAnswer('no_answer'),
  declined('declined'),
  busy('busy'),
  cancelled('cancelled'),
  failed('failed'),
  unsupported('unsupported');

  const CallOutcome(this.value);
  final String value;

  static CallOutcome parse(String value) => values.firstWhere((o) => o.value == value, orElse: () => failed);
}

/// Appel noté dans la conversation (localement, jamais transmis).
class CallInfo {
  const CallInfo(this.media, this.outcome, [this.duration = 0]);
  final String media; // « audio » ou « video »
  final CallOutcome outcome;
  final int duration; // secondes de conversation

  String get icon => media == 'video' ? '🎥' : '📞';

  String label(bool outgoing) {
    final kind = media == 'video' ? 'vidéo' : 'audio';
    return switch (outcome) {
      CallOutcome.completed => 'Appel $kind · ${formatDuration(duration)}',
      CallOutcome.missed => 'Appel $kind manqué',
      CallOutcome.noAnswer => 'Pas de réponse',
      CallOutcome.declined => 'Appel refusé',
      CallOutcome.busy => 'Correspondant occupé',
      CallOutcome.cancelled => outgoing ? 'Appel annulé' : 'Appel $kind manqué',
      CallOutcome.unsupported => 'Appel impossible : le correspondant doit mettre Socket Chat à jour',
      CallOutcome.failed => "Échec de l'appel",
    };
  }

  String toJson() => jsonEncode({'media': media, 'outcome': outcome.value, 'duration': duration});

  static CallInfo fromJson(String text) {
    try {
      final raw = jsonDecode(text) as Map<String, Object?>;
      return CallInfo(
        raw['media']! as String,
        CallOutcome.parse(raw['outcome']! as String),
        raw['duration'] as int? ?? 0,
      );
    } on Object {
      return const CallInfo('audio', CallOutcome.failed);
    }
  }
}

/// « 42 s », « 3 min 05 », « 1 h 02 min ».
String formatDuration(int seconds) {
  if (seconds < 60) return '$seconds s';
  final minutes = seconds ~/ 60;
  final secs = (seconds % 60).toString().padLeft(2, '0');
  if (minutes < 60) return '$minutes min $secs';
  return '${minutes ~/ 60} h ${(minutes % 60).toString().padLeft(2, '0')} min';
}

class Contact {
  Contact(this.id, this.name, {this.host, this.port, this.lastSeen = 0, Uint8List? avatar})
    : avatar = avatar ?? Uint8List(0);
  final String id;
  final String name;
  final String? host;
  final int? port;
  final int lastSeen;

  /// Photo de profil (JPEG ou PNG) reçue du contact ; vide s'il n'en a pas.
  final Uint8List avatar;
}

class MessageRecord {
  const MessageRecord(
    this.id,
    this.contactId,
    this.outgoing,
    this.text,
    this.ts,
    this.status, {
    this.file,
    this.reactions = const [],
    this.call,
  });

  final String id;
  final String contactId;
  final bool outgoing;
  final String text;
  final int ts;
  final Status status;
  final FileInfo? file;

  /// Réactions : paires (auteur, emoji), l'auteur étant [me] ou l'identifiant du contact.
  final List<(String, String)> reactions;

  /// Appel noté dans la conversation ([text] est alors vide).
  final CallInfo? call;

  bool get unread => !outgoing && status != Status.read;

  /// Texte court pour la liste des contacts et les notifications.
  String get preview {
    final f = file;
    if (f != null) return f.isImage ? '🖼 Image' : '📎 ${f.name}';
    final c = call;
    if (c != null) return '${c.icon} ${c.label(outgoing)}';
    return text.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty).join(' ');
  }

  /// Texte affiché dans la bulle (libellé de l'appel pour un appel noté).
  String get displayText {
    final c = call;
    return c == null ? text : '${c.icon} ${c.label(outgoing)}';
  }

  String reactionOf(String author) {
    for (final (who, emoji) in reactions) {
      if (who == author) return emoji;
    }
    return '';
  }

  MessageRecord withReactions(List<(String, String)> value) =>
      MessageRecord(id, contactId, outgoing, text, ts, status, file: file, reactions: value, call: call);
}

class ContactSummary {
  const ContactSummary(this.contact, this.lastMessage, this.unread);
  final Contact contact;
  final MessageRecord? lastMessage;
  final int unread;

  int get lastActivity {
    final ts = lastMessage?.ts ?? 0;
    return contact.lastSeen > ts ? contact.lastSeen : ts;
  }
}

class Store {
  /// Ouvre (ou crée) l'historique ; sans [path], il reste en mémoire.
  Store([String? path]) : _db = _open(path) {
    final version = _db.select('PRAGMA user_version').first.columnAt(0) as int;
    if (version > schemaVersion) {
      _db.close();
      throw StateError('Historique créé par une version plus récente de Socket Chat.');
    }
    _db.execute('PRAGMA foreign_keys = ON');
    _db.execute(_schema);
    for (var target = (version > 1 ? version : 1) + 1; target <= schemaVersion; target++) {
      _db.execute('BEGIN; ${_migrations[target]} COMMIT;');
    }
    _db.execute('PRAGMA user_version = $schemaVersion');
    // Un transfert ne survit pas à l'arrêt de l'application.
    _db.execute("UPDATE messages SET file_state = 'failed' WHERE file_state IN ('accepted', 'transferring')");
  }

  final Database _db;

  static Database _open(String? path) {
    if (path == null) return sqlite3.openInMemory();
    File(path).parent.createSync(recursive: true);
    final db = sqlite3.open(path);
    db.execute('PRAGMA journal_mode = WAL');
    return db;
  }

  void close() => _db.close();

  // -- Contacts --------------------------------------------------------
  /// Crée ou met à jour un contact ; les champs `null` sont conservés.
  void upsertContact(String contactId, String name, {String? host, int? port, int? lastSeen}) {
    _db.execute(
      '''
      INSERT INTO contacts (id, name, host, port, last_seen)
      VALUES (?1, ?2, ?3, ?4, COALESCE(?5, 0))
      ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          host = COALESCE(?3, contacts.host),
          port = COALESCE(?4, contacts.port),
          last_seen = MAX(contacts.last_seen, COALESCE(?5, 0))
      ''',
      [contactId, name, host, port, lastSeen],
    );
  }

  Contact? contact(String contactId) {
    final rows = _db.select('SELECT * FROM contacts WHERE id = ?', [contactId]);
    return rows.isEmpty ? null : _contact(rows.first);
  }

  /// Contacts avec leur dernier message, du plus récemment actif au plus ancien.
  List<ContactSummary> contacts() {
    final rows = _db.select('''
      SELECT c.*,
             m.id AS m_id, m.outgoing AS m_outgoing, m.text AS m_text,
             m.ts AS m_ts, m.status AS m_status, m.kind AS m_kind,
             m.file_name AS m_file_name, m.file_size AS m_file_size,
             m.file_state AS m_file_state, m.file_path AS m_file_path,
             m.thumb AS m_thumb,
             (SELECT COUNT(*) FROM messages u
               WHERE u.contact_id = c.id AND u.outgoing = 0 AND u.status != 'read')
               AS unread
      FROM contacts c
      LEFT JOIN messages m ON m.id = (
          SELECT id FROM messages WHERE contact_id = c.id
          ORDER BY ts DESC, rowid DESC LIMIT 1)
    ''');
    final summaries = <ContactSummary>[];
    for (final row in rows) {
      MessageRecord? last;
      if (row['m_id'] != null) {
        last = _message(row, 'm_', 'id');
      }
      summaries.add(ContactSummary(_contact(row), last, row['unread'] as int));
    }
    summaries.sort((a, b) {
      final byActivity = b.lastActivity.compareTo(a.lastActivity);
      if (byActivity != 0) return byActivity;
      return a.contact.name.toLowerCase().compareTo(b.contact.name.toLowerCase());
    });
    return summaries;
  }

  /// Enregistre (ou retire avec une liste vide) la photo d'un contact ; `true` si modifiée.
  bool setAvatar(String contactId, Uint8List data) {
    final current = contact(contactId);
    if (current == null || _sameBytes(current.avatar, data)) return false;
    _db.execute('UPDATE contacts SET avatar = ? WHERE id = ?', [data.isEmpty ? null : data, contactId]);
    return true;
  }

  void deleteContact(String contactId) {
    _db.execute('DELETE FROM contacts WHERE id = ?', [contactId]);
  }

  // -- Messages --------------------------------------------------------
  /// Enregistre un message ; renvoie `false` s'il existait déjà (doublon).
  bool addMessage(MessageRecord record) {
    final f = record.file;
    final c = record.call;
    _db.execute(
      '''
      INSERT OR IGNORE INTO messages
          (id, contact_id, outgoing, text, ts, status,
           kind, file_name, file_size, file_state, file_path, thumb)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ''',
      [
        record.id,
        record.contactId,
        record.outgoing ? 1 : 0,
        c != null ? c.toJson() : record.text,
        record.ts,
        record.status.value,
        f != null ? 'file' : (c != null ? 'call' : 'text'),
        f?.name,
        f?.size,
        f?.state.value,
        f?.path,
        f != null && f.thumb.isNotEmpty ? f.thumb : null,
      ],
    );
    return _db.updatedRows == 1;
  }

  MessageRecord? message(String messageId) {
    final rows = _db.select('SELECT * FROM messages WHERE id = ?', [messageId]);
    return rows.isEmpty ? null : _withReactions([_message(rows.first)]).first;
  }

  List<MessageRecord> _withReactions(List<MessageRecord> records) {
    if (records.isEmpty) return records;
    final byMessage = <String, List<(String, String)>>{};
    final ids = [for (final r in records) r.id];
    for (var start = 0; start < ids.length; start += 500) {
      final chunk = ids.sublist(start, start + 500 > ids.length ? ids.length : start + 500);
      final marks = List.filled(chunk.length, '?').join(',');
      final rows = _db.select(
        'SELECT message_id, author, emoji FROM reactions '
        'WHERE message_id IN ($marks) ORDER BY rowid',
        chunk,
      );
      for (final row in rows) {
        byMessage.putIfAbsent(row['message_id'] as String, () => []).add((
          row['author'] as String,
          row['emoji'] as String,
        ));
      }
    }
    return [
      for (final r in records)
        if (byMessage[r.id] case final reactions?) r.withReactions(reactions) else r,
    ];
  }

  List<MessageRecord> messages(String contactId, {int limit = 1000}) {
    final rows = _db.select(
      '''
      SELECT * FROM (
          SELECT *, rowid AS r FROM messages WHERE contact_id = ?
          ORDER BY ts DESC, rowid DESC LIMIT ?)
      ORDER BY ts, r
      ''',
      [contactId, limit],
    );
    return _withReactions([for (final row in rows) _message(row)]);
  }

  /// Fait progresser l'état d'un message (jamais de retour en arrière).
  bool updateStatus(String messageId, Status status) {
    final record = message(messageId);
    if (record == null || status.rank <= record.status.rank) return false;
    _db.execute('UPDATE messages SET status = ? WHERE id = ?', [status.value, messageId]);
    return true;
  }

  /// Messages envoyés pas encore confirmés par le destinataire (à (re)transmettre).
  List<MessageRecord> undeliveredOutgoing(String contactId) {
    final rows = _db.select(
      '''
      SELECT * FROM messages
      WHERE contact_id = ? AND outgoing = 1 AND status IN ('pending', 'sent')
      ORDER BY ts, rowid
      ''',
      [contactId],
    );
    return [for (final row in rows) _message(row)];
  }

  List<String> contactsWithUndelivered() {
    final rows = _db.select(
      "SELECT DISTINCT contact_id FROM messages WHERE outgoing = 1 AND status IN ('pending', 'sent')",
    );
    return [for (final row in rows) row.columnAt(0) as String];
  }

  /// Marque comme lus les messages reçus ; renvoie les identifiants modifiés.
  List<String> markIncomingRead(String contactId) {
    final rows = _db.select("SELECT id FROM messages WHERE contact_id = ? AND outgoing = 0 AND status != 'read'", [
      contactId,
    ]);
    final ids = [for (final row in rows) row.columnAt(0) as String];
    if (ids.isNotEmpty) {
      _transaction(() {
        final statement = _db.prepare("UPDATE messages SET status = 'read' WHERE id = ?");
        try {
          for (final id in ids) {
            statement.execute([id]);
          }
        } finally {
          statement.close();
        }
      });
    }
    return ids;
  }

  // -- Fichiers et réactions --------------------------------------------
  /// Met à jour l'état ou le chemin d'un fichier ; renvoie `true` si modifié.
  bool updateFile(String messageId, {FileState? state, String? path}) {
    final record = message(messageId);
    final file = record?.file;
    if (file == null) return false;
    final newState = state ?? file.state;
    final newPath = path ?? file.path;
    if (newState == file.state && newPath == file.path) return false;
    _db.execute('UPDATE messages SET file_state = ?, file_path = ? WHERE id = ?', [newState.value, newPath, messageId]);
    return true;
  }

  /// Pose (ou retire avec `""`) la réaction d'un auteur ; `true` si modifiée.
  bool setReaction(String messageId, String author, String emoji) {
    final record = message(messageId);
    if (record == null || record.reactionOf(author) == emoji) return false;
    if (emoji.isNotEmpty) {
      _db.execute(
        'INSERT INTO reactions VALUES (?, ?, ?) '
        'ON CONFLICT(message_id, author) DO UPDATE SET emoji = excluded.emoji',
        [messageId, author, emoji],
      );
    } else {
      _db.execute('DELETE FROM reactions WHERE message_id = ? AND author = ?', [messageId, author]);
    }
    return true;
  }

  int totalUnread() =>
      _db.select("SELECT COUNT(*) FROM messages WHERE outgoing = 0 AND status != 'read'").first.columnAt(0) as int;

  void _transaction(void Function() body) {
    _db.execute('BEGIN');
    try {
      body();
      _db.execute('COMMIT');
    } catch (_) {
      _db.execute('ROLLBACK');
      rethrow;
    }
  }
}

bool _sameBytes(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

Contact _contact(Row row) => Contact(
  row['id'] as String,
  row['name'] as String,
  host: row['host'] as String?,
  port: row['port'] as int?,
  lastSeen: row['last_seen'] as int,
  avatar: row['avatar'] as Uint8List?,
);

FileInfo? _file(Row row, [String prefix = '']) {
  if (row['${prefix}kind'] != 'file') return null;
  return FileInfo(
    row['${prefix}file_name'] as String,
    row['${prefix}file_size'] as int,
    FileState.parse(row['${prefix}file_state'] as String),
    row['${prefix}file_path'] as String?,
    row['${prefix}thumb'] as Uint8List?,
  );
}

MessageRecord _message(Row row, [String prefix = '', String contactKey = 'contact_id']) {
  final isCall = row['${prefix}kind'] == 'call';
  final text = row['${prefix}text'] as String;
  return MessageRecord(
    row['${prefix}id'] as String,
    row[contactKey] as String,
    row['${prefix}outgoing'] == 1,
    isCall ? '' : text,
    row['${prefix}ts'] as int,
    Status.parse(row['${prefix}status'] as String),
    file: _file(row, prefix),
    call: isCall ? CallInfo.fromJson(text) : null,
  );
}
