// Protocole d'échange de Socket Chat (version 3), identique à
// src/socket_chat/protocol.py côté PC.
//
// Trame TCP : objet JSON UTF-8 sur une ligne, terminé par « \n ».
// Annonce UDP : objet JSON sans « \n ».
//
// Les longueurs sont comptées en caractères Unicode (runes), comme en Python,
// et non en unités UTF-16 (String.length en Dart).

import 'dart:convert';
import 'dart:typed_data';

import 'package:uuid/uuid.dart';

const int protocolVersion = 3;
const int defaultPort = 12800;
const int discoveryPort = 12801;

/// Taille maximale d'une trame encodée (octets), séparateur compris.
const int maxFrameSize = 64 * 1024;

/// Taille maximale d'un datagramme de détection (octets).
const int maxDatagramSize = 1024;

/// Longueur maximale d'un message texte (caractères).
const int maxTextLength = 10000;

/// Longueur maximale d'un nom d'utilisateur (caractères).
const int maxNameLength = 32;

/// Taille maximale d'un fichier transféré (octets).
const int maxFileSize = 2 * 1024 * 1024 * 1024;

/// Taille d'un bloc de fichier avant encodage base64 (octets).
const int chunkSize = 32 * 1024;

/// Taille maximale de l'aperçu d'une image (octets, avant base64).
const int maxThumbSize = 32 * 1024;

/// Longueur maximale d'un nom de fichier (caractères).
const int maxFileNameLength = 200;

/// Longueur maximale d'une réaction (caractères).
const int maxEmojiLength = 16;

/// Trame invalide reçue ou impossible à encoder.
class ProtocolException implements Exception {
  ProtocolException(this.message);
  final String message;
  @override
  String toString() => message;
}

enum MessageType {
  hello('hello'),
  chat('chat'),
  ack('ack'),
  typing('typing'),
  react('react'),
  fileOffer('file_offer'),
  fileAnswer('file_answer'),
  fileChunk('file_chunk'),
  fileEnd('file_end'),
  fileCancel('file_cancel'),
  bye('bye');

  const MessageType(this.wire);
  final String wire;

  static MessageType? fromWire(String value) {
    for (final type in values) {
      if (type.wire == value) return type;
    }
    return null;
  }
}

enum AckStatus {
  delivered('delivered'),
  read('read');

  const AckStatus(this.wire);
  final String wire;

  static AckStatus? fromWire(Object? value) {
    for (final status in values) {
      if (status.wire == value) return status;
    }
    return null;
  }
}

const Uuid _uuidGenerator = Uuid();

/// Nouvel identifiant (UUID v4 canonique), pour un utilisateur ou un message.
String newId() => _uuidGenerator.v4();

int runeLength(String text) => text.runes.length;

final RegExp _uuid = RegExp(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');

/// Identifiant au format UUID canonique (minuscules, avec tirets).
bool isValidId(Object? value) => value is String && _uuid.hasMatch(value);

final RegExp _whitespace = RegExp(r'\s+', unicode: true);

/// Normalise un nom d'utilisateur ; lève [ArgumentError] s'il est invalide.
String validateName(String name) {
  final normalized = name.trim().split(_whitespace).where((w) => w.isNotEmpty).join(' ');
  if (normalized.isEmpty) {
    throw ArgumentError('Le nom ne peut pas être vide.');
  }
  if (runeLength(normalized) > maxNameLength) {
    throw ArgumentError('Le nom ne doit pas dépasser $maxNameLength caractères.');
  }
  return normalized;
}

final RegExp _unsafeFileChars = RegExp(r'[<>:"/\\|?*\x00-\x1f\x7f]');

/// Nom de fichier sans chemin ni caractère interdit (Windows compris).
String safeFileName(String name) {
  var result = name.split(RegExp(r'[\\/]')).last;
  result = result.replaceAll(_unsafeFileChars, '_').trim();
  result = _stripDots(result);
  if (result.isEmpty) return 'fichier';
  final runes = result.runes.toList();
  if (runes.length > maxFileNameLength) {
    final dot = result.lastIndexOf('.');
    final ext = dot >= 0 ? result.substring(dot + 1) : '';
    if (dot > 0 && runeLength(ext) > 0 && runeLength(ext) <= 16) {
      final stemRunes = result.substring(0, dot).runes.toList();
      final keep = maxFileNameLength - runeLength(ext) - 1;
      result = '${String.fromCharCodes(stemRunes.take(keep))}.$ext';
    } else {
      result = String.fromCharCodes(runes.take(maxFileNameLength));
    }
  }
  return result;
}

String _stripDots(String value) {
  var start = 0;
  var end = value.length;
  while (start < end && value[start] == '.') {
    start++;
  }
  while (end > start && value[end - 1] == '.') {
    end--;
  }
  return value.substring(start, end);
}

/// Message échangé entre deux pairs.
class Message {
  const Message(
    this.type, {
    this.id = '',
    this.name = '',
    this.port = 0,
    this.text = '',
    this.status,
    this.active = false,
    this.reason = '',
    this.emoji = '',
    this.size = 0,
    this.thumb,
    this.offset = 0,
    this.data,
    this.sha256 = '',
  });

  final MessageType type;
  final String id; // hello : identifiant de l'utilisateur ; sinon : du message
  final String name;
  final int port;
  final String text;
  final AckStatus? status;
  final bool active; // typing, et « accept » pour file_answer
  final String reason;
  final String emoji;
  final int size;
  final Uint8List? thumb;
  final int offset;
  final Uint8List? data;
  final String sha256;

  bool get accept => active;

  factory Message.hello(String userId, String name, int port) =>
      Message(MessageType.hello, id: userId, name: name, port: port);
  factory Message.chat(String messageId, String text) => Message(MessageType.chat, id: messageId, text: text);
  factory Message.ack(String messageId, AckStatus status) => Message(MessageType.ack, id: messageId, status: status);
  factory Message.typing(bool active) => Message(MessageType.typing, active: active);
  factory Message.react(String messageId, String emoji) => Message(MessageType.react, id: messageId, emoji: emoji);
  factory Message.fileOffer(String messageId, String name, int size, [Uint8List? thumb]) =>
      Message(MessageType.fileOffer, id: messageId, name: name, size: size, thumb: thumb);
  factory Message.fileAnswer(String messageId, bool accept) =>
      Message(MessageType.fileAnswer, id: messageId, active: accept);
  factory Message.fileChunk(String messageId, int offset, Uint8List data) =>
      Message(MessageType.fileChunk, id: messageId, offset: offset, data: data);
  factory Message.fileEnd(String messageId, String sha256) =>
      Message(MessageType.fileEnd, id: messageId, sha256: sha256);
  factory Message.fileCancel(String messageId, [String reason = '']) =>
      Message(MessageType.fileCancel, id: messageId, reason: reason);
  factory Message.bye([String reason = '']) => Message(MessageType.bye, reason: reason);

  /// Représentation JSON, dans le même ordre de clés que la version Python.
  Map<String, Object?> toJson() {
    final kind = type.wire;
    switch (type) {
      case MessageType.hello:
        return {'type': kind, 'version': protocolVersion, 'id': id, 'name': name, 'port': port};
      case MessageType.chat:
        return {'type': kind, 'id': id, 'text': text};
      case MessageType.ack:
        return {'type': kind, 'id': id, 'status': status!.wire};
      case MessageType.typing:
        return {'type': kind, 'active': active};
      case MessageType.react:
        return {'type': kind, 'id': id, 'emoji': emoji};
      case MessageType.fileOffer:
        final payload = <String, Object?>{'type': kind, 'id': id, 'name': name, 'size': size};
        if (thumb != null && thumb!.isNotEmpty) payload['thumb'] = base64Encode(thumb!);
        return payload;
      case MessageType.fileAnswer:
        return {'type': kind, 'id': id, 'accept': active};
      case MessageType.fileChunk:
        return {'type': kind, 'id': id, 'offset': offset, 'data': base64Encode(data ?? Uint8List(0))};
      case MessageType.fileEnd:
        return {'type': kind, 'id': id, 'sha256': sha256};
      case MessageType.fileCancel:
        return {'type': kind, 'id': id, 'reason': reason};
      case MessageType.bye:
        return {'type': kind, 'reason': reason};
    }
  }

  @override
  bool operator ==(Object other) => other is Message && jsonEncode(other.toJson()) == jsonEncode(toJson());

  @override
  int get hashCode => jsonEncode(toJson()).hashCode;

  @override
  String toString() => 'Message(${jsonEncode(toJson())})';
}

/// Annonce de présence reçue par UDP ([port] vaut 0 pour un départ).
class Announcement {
  const Announcement(this.id, {this.name = '', this.port = 0});
  final String id;
  final String name;
  final int port;
  bool get leaving => port == 0;
}

// -- Encodage -------------------------------------------------------------
Uint8List _dumps(Map<String, Object?> payload) => utf8.encode(jsonEncode(payload));

/// Encode un message en une trame TCP prête à être envoyée.
Uint8List encode(Message message) {
  if (message.type == MessageType.chat && runeLength(message.text) > maxTextLength) {
    throw ProtocolException('Message trop long (maximum $maxTextLength caractères).');
  }
  final body = _dumps(message.toJson());
  final frame = Uint8List(body.length + 1)
    ..setAll(0, body)
    ..[body.length] = 0x0a;
  if (frame.length > maxFrameSize) {
    throw ProtocolException('Trame trop volumineuse.');
  }
  return frame;
}

Uint8List encodeAnnounce(String userId, String name, int port) =>
    _dumps({'type': 'announce', 'version': protocolVersion, 'id': userId, 'name': name, 'port': port});

Uint8List encodeLeave(String userId) => _dumps({'type': 'leave', 'version': protocolVersion, 'id': userId});

// -- Décodage -------------------------------------------------------------
Map<String, Object?> _load(List<int> data) {
  Object? payload;
  try {
    payload = jsonDecode(utf8.decode(data));
  } on FormatException {
    throw ProtocolException('Trame illisible.');
  }
  if (payload is! Map<String, Object?>) {
    throw ProtocolException('Trame invalide.');
  }
  return payload;
}

String _requireStr(Map<String, Object?> payload, String key, {required int maxLength}) {
  final value = payload.containsKey(key) ? payload[key] : '';
  if (value is! String) throw ProtocolException('Champ « $key » invalide.');
  if (runeLength(value) > maxLength) throw ProtocolException('Champ « $key » trop long.');
  return value;
}

String _requireId(Map<String, Object?> payload) {
  final value = payload['id'];
  if (!isValidId(value)) throw ProtocolException('Identifiant invalide.');
  return value as String;
}

int _requireInt(Map<String, Object?> payload, String key, {required int maximum, int minimum = 0}) {
  final value = payload[key];
  if (value is! int || value < minimum || value > maximum) {
    throw ProtocolException('Champ « $key » invalide.');
  }
  return value;
}

int _requirePort(Map<String, Object?> payload) {
  final value = payload['port'];
  if (value is! int || value < 1 || value > 65535) throw ProtocolException('Port invalide.');
  return value;
}

bool _requireBool(Map<String, Object?> payload, String key) {
  final value = payload[key];
  if (value is! bool) throw ProtocolException('Champ « $key » invalide.');
  return value;
}

final RegExp _base64 = RegExp(r'^[A-Za-z0-9+/]*={0,2}$');

Uint8List _requireBase64(Map<String, Object?> payload, String key, {required int maxSize}) {
  final raw = _requireStr(payload, key, maxLength: (maxSize + 2) ~/ 3 * 4);
  if (raw.length % 4 != 0 || !_base64.hasMatch(raw)) {
    throw ProtocolException('Champ « $key » invalide.');
  }
  try {
    return base64Decode(raw);
  } on FormatException {
    throw ProtocolException('Champ « $key » invalide.');
  }
}

String _requireName(Map<String, Object?> payload) {
  final raw = _requireStr(payload, 'name', maxLength: maxNameLength * 4);
  try {
    return validateName(raw);
  } on ArgumentError catch (exc) {
    throw ProtocolException('${exc.message}');
  }
}

void _checkVersion(Map<String, Object?> payload) {
  final version = payload['version'];
  if (version is! int) throw ProtocolException('Version de protocole absente.');
  if (version != protocolVersion) {
    throw ProtocolException(
      'Version de protocole incompatible ($version, attendue $protocolVersion) : '
      'mettez Socket Chat à jour sur les deux postes.',
    );
  }
}

final RegExp _sha256 = RegExp(r'^[0-9a-f]{64}$');
final RegExp _letterDigitSpace = RegExp(r'[\p{L}\p{N}\s]', unicode: true);

/// Décode une trame TCP (sans le séparateur) ; `null` si son type est inconnu.
Message? decode(List<int> line) {
  final payload = _load(line);
  final kindValue = payload['type'];
  if (kindValue is! String) throw ProtocolException('Type de trame absent.');
  final kind = MessageType.fromWire(kindValue);
  if (kind == null) return null; // trame d'une version plus récente : ignorée

  switch (kind) {
    case MessageType.hello:
      _checkVersion(payload);
      return Message.hello(_requireId(payload), _requireName(payload), _requirePort(payload));
    case MessageType.chat:
      final text = _requireStr(payload, 'text', maxLength: maxTextLength);
      return Message.chat(_requireId(payload), text);
    case MessageType.ack:
      final status = AckStatus.fromWire(payload['status']);
      if (status == null) throw ProtocolException('Accusé de réception invalide.');
      return Message.ack(_requireId(payload), status);
    case MessageType.typing:
      final active = payload['active'];
      if (active is! bool) throw ProtocolException('Champ « active » invalide.');
      return Message.typing(active);
    case MessageType.react:
      final emoji = _requireStr(payload, 'emoji', maxLength: maxEmojiLength);
      if (_letterDigitSpace.hasMatch(emoji)) throw ProtocolException('Réaction invalide.');
      return Message.react(_requireId(payload), emoji);
    case MessageType.fileOffer:
      final rawName = _requireStr(payload, 'name', maxLength: maxFileNameLength * 2);
      final size = _requireInt(payload, 'size', maximum: maxFileSize);
      Uint8List? thumb;
      if (payload.containsKey('thumb')) {
        thumb = _requireBase64(payload, 'thumb', maxSize: maxThumbSize);
      }
      return Message.fileOffer(_requireId(payload), safeFileName(rawName), size, thumb);
    case MessageType.fileAnswer:
      return Message.fileAnswer(_requireId(payload), _requireBool(payload, 'accept'));
    case MessageType.fileChunk:
      final offset = _requireInt(payload, 'offset', maximum: maxFileSize);
      final data = _requireBase64(payload, 'data', maxSize: chunkSize);
      return Message.fileChunk(_requireId(payload), offset, data);
    case MessageType.fileEnd:
      final digest = _requireStr(payload, 'sha256', maxLength: 64);
      if (!_sha256.hasMatch(digest)) throw ProtocolException('Empreinte invalide.');
      return Message.fileEnd(_requireId(payload), digest);
    case MessageType.fileCancel:
      final reason = _requireStr(payload, 'reason', maxLength: 500);
      return Message.fileCancel(_requireId(payload), reason);
    case MessageType.bye:
      return Message.bye(_requireStr(payload, 'reason', maxLength: 500));
  }
}

/// Décode un datagramme de détection (lève [ProtocolException] s'il est invalide).
Announcement decodeAnnounce(List<int> datagram) {
  if (datagram.length > maxDatagramSize) throw ProtocolException('Datagramme trop volumineux.');
  final payload = _load(datagram);
  _checkVersion(payload);
  switch (payload['type']) {
    case 'announce':
      return Announcement(_requireId(payload), name: _requireName(payload), port: _requirePort(payload));
    case 'leave':
      return Announcement(_requireId(payload));
    default:
      throw ProtocolException("Type d'annonce inconnu : ${payload['type']}.");
  }
}

/// Reconstitue les trames à partir d'un flux d'octets TCP arbitrairement découpé.
class FrameDecoder {
  FrameDecoder({this.limit = maxFrameSize});

  /// Taille maximale d'une trame, séparateur compris.
  final int limit;
  final BytesBuilder _buffer = BytesBuilder(copy: false);
  Uint8List _pending = Uint8List(0);

  List<Message> feed(List<int> data) {
    _buffer.add(_pending);
    _buffer.add(data);
    var bytes = _buffer.takeBytes();
    final messages = <Message>[];
    var start = 0;
    while (true) {
      final index = bytes.indexOf(0x0a, start);
      if (index < 0) {
        _pending = Uint8List.sublistView(bytes, start);
        if (_pending.length >= limit) {
          throw ProtocolException('Trame trop volumineuse.');
        }
        return messages;
      }
      if (index + 1 - start > limit) throw ProtocolException('Trame trop volumineuse.');
      final line = Uint8List.sublistView(bytes, start, index);
      start = index + 1;
      if (!_isBlank(line)) {
        final message = decode(line);
        if (message != null) messages.add(message);
      }
    }
  }

  static bool _isBlank(Uint8List line) {
    for (final byte in line) {
      if (byte != 0x20 && byte != 0x09 && byte != 0x0d && byte != 0x0a && byte != 0x0b && byte != 0x0c) {
        return false;
      }
    }
    return true;
  }
}
