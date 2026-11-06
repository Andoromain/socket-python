// Transferts de fichiers entre deux postes, transposés de src/socket_chat/transfers.py.
//
// 1. l'expéditeur envoie `file_offer` (nom, taille, aperçu éventuel) ;
// 2. le destinataire accepte (en choisissant où enregistrer) ou refuse : `file_answer` ;
// 3. l'expéditeur envoie des `file_chunk` de 32 Kio, au rythme où la socket se vide,
//    puis `file_end` avec l'empreinte SHA-256 ;
// 4. le destinataire vérifie taille et empreinte, puis renomme le fichier `.part`.
//
// Chaque côté peut interrompre avec `file_cancel`. Une coupure de connexion fait
// échouer le transfert ; le destinataire peut alors réessayer depuis le début.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import '../core/signal.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
import 'connection.dart';

/// Données en attente dans la socket au-delà desquelles on n'envoie plus de bloc.
const int highWater = 512 * 1024;

/// Fréquence des notifications de progression (octets).
int progressStep = 256 * 1024;

const String integrityReason = 'integrity';
const String missingReason = 'missing';
const Map<String, String> cancelMessages = {
  integrityReason: 'fichier reçu corrompu',
  missingReason: "fichier introuvable chez l'expéditeur",
};

/// Vérifie qu'un fichier peut être envoyé et renvoie sa taille ([ArgumentError] sinon).
int checkSendable(String path) {
  final name = path.split(RegExp(r'[\\/]')).where((p) => p.isNotEmpty).lastOrNull ?? path;
  if (!FileSystemEntity.isFileSync(path)) {
    throw ArgumentError("« $name » n'est pas un fichier.");
  }
  final size = File(path).lengthSync();
  if (size > maxFileSize) {
    throw ArgumentError('« $name » dépasse la taille maximale de 2 Go.');
  }
  return size;
}

/// Empreinte SHA-256 calculée au fil de l'eau.
class _Sha256 {
  _Sha256() {
    _input = sha256.startChunkedConversion(_output);
  }

  final _DigestSink _output = _DigestSink();
  late final ByteConversionSink _input;

  void update(List<int> data) => _input.add(data);

  String hexdigest() {
    _input.close();
    return _output.value.toString();
  }
}

class _DigestSink implements Sink<Digest> {
  late Digest value;
  @override
  void add(Digest data) => value = data;
  @override
  void close() {}
}

abstract class _Transfer {
  _Transfer(this.messageId, this.conn, this.size);
  final String messageId;
  final PeerConnection conn;
  final int size;
  final _Sha256 sha = _Sha256();
  int reported = 0;

  /// Octets transférés.
  int get done;
}

class _Upload extends _Transfer {
  _Upload(super.messageId, super.conn, this.file, super.size);
  final RandomAccessFile file;
  int offset = 0;
  @override
  int get done => offset;
}

class _Download extends _Transfer {
  _Download(super.messageId, super.conn, this.file, this.part, this.destination, super.size);
  final RandomAccessFile file;
  final String part;
  final String destination;
  int received = 0;
  @override
  int get done => received;
}

class FileTransfers {
  FileTransfers(this._store);

  final Store _store;
  final Map<String, _Upload> _uploads = {};
  final Map<String, _Download> _downloads = {};

  /// Identifiant du message dont l'état a changé.
  final Signal<String> changed = Signal();

  /// Identifiant, octets transférés, taille totale.
  final Signal<(String, int, int)> progress = Signal();

  // -- État ------------------------------------------------------------
  /// Nombre de transferts en cours (envois et réceptions).
  int get activeCount => _uploads.length + _downloads.length;

  (int, int)? progressOf(String messageId) {
    final up = _uploads[messageId];
    if (up != null) return (up.offset, up.size);
    final down = _downloads[messageId];
    if (down != null) return (down.received, down.size);
    return null;
  }

  // -- Actions locales -------------------------------------------------
  void accept(String messageId, String destination, PeerConnection conn) {
    _abortDownload(messageId);
    final part = '$destination.part';
    final record = _store.message(messageId)!;
    File(destination).parent.createSync(recursive: true);
    final handle = File(part).openSync(mode: FileMode.write);
    final download = _Download(messageId, conn, handle, part, destination, record.file!.size);
    _downloads[messageId] = download;
    _setState(messageId, FileState.accepted, path: destination);
    conn.send(Message.fileAnswer(messageId, true));
    if (record.file!.size == 0) _emitProgress(download, force: true);
  }

  void cancel(String messageId, PeerConnection? conn) {
    final active = _uploads.containsKey(messageId) || _downloads.containsKey(messageId);
    _abortUpload(messageId);
    _abortDownload(messageId);
    final file = _store.message(messageId)?.file;
    if (file == null) return;
    if (const {FileState.done, FileState.declined, FileState.cancelled}.contains(file.state)) return;
    if (conn != null && conn.isReady) conn.send(Message.fileCancel(messageId));
    if (active || file.state == FileState.offered) _setState(messageId, FileState.cancelled);
  }

  void connectionClosed(PeerConnection conn) {
    for (final upload in _uploads.values.where((u) => u.conn == conn).toList()) {
      _abortUpload(upload.messageId);
      _setState(upload.messageId, FileState.failed);
    }
    for (final download in _downloads.values.where((d) => d.conn == conn).toList()) {
      _abortDownload(download.messageId);
      _setState(download.messageId, FileState.failed);
    }
  }

  void stop() {
    for (final messageId in _uploads.keys.toList()) {
      _abortUpload(messageId);
      _setState(messageId, FileState.failed);
    }
    for (final messageId in _downloads.keys.toList()) {
      _abortDownload(messageId);
      _setState(messageId, FileState.failed);
    }
  }

  // -- Trames reçues ---------------------------------------------------
  void handle(PeerConnection conn, String peerId, Message message) {
    final record = _store.message(message.id);
    final file = record?.file;
    if (record == null || file == null || record.contactId != peerId) return;
    switch (message.type) {
      case MessageType.fileAnswer when record.outgoing:
        if (message.accept) {
          _startUpload(conn, message.id);
        } else if (file.state == FileState.offered) {
          _setState(message.id, FileState.declined);
        }
      case MessageType.fileChunk when !record.outgoing:
        _receiveChunk(conn, message);
      case MessageType.fileEnd when !record.outgoing:
        _finishDownload(conn, message);
      case MessageType.fileCancel:
        final wasActive = _uploads.containsKey(message.id) || _downloads.containsKey(message.id);
        _abortUpload(message.id);
        _abortDownload(message.id);
        if (message.reason == integrityReason) {
          // Même un envoi terminé échoue si le destinataire a rejeté le fichier.
          _setState(message.id, FileState.failed);
          return;
        }
        if (file.state == FileState.done || file.state == FileState.declined) return;
        if (wasActive || file.state == FileState.offered || file.state == FileState.failed) {
          _setState(message.id, FileState.cancelled);
        }
      default:
        break;
    }
  }

  /// Envoie des blocs tant que la socket n'est pas saturée.
  void pump(PeerConnection conn) {
    for (final upload in _uploads.values.where((u) => u.conn == conn).toList()) {
      _pumpOne(upload);
    }
  }

  // -- Envoi -----------------------------------------------------------
  void _startUpload(PeerConnection conn, String messageId) {
    final file = _store.message(messageId)!.file!;
    if (file.state == FileState.declined || file.state == FileState.cancelled) {
      conn.send(Message.fileCancel(messageId));
      return;
    }
    _abortUpload(messageId);
    final path = file.path;
    RandomAccessFile handle;
    try {
      if (path == null || path.isEmpty || File(path).lengthSync() != file.size) {
        throw FileSystemException('Fichier introuvable ou modifié', path);
      }
      handle = File(path).openSync();
    } on FileSystemException {
      conn.send(Message.fileCancel(messageId, missingReason));
      _setState(messageId, FileState.failed);
      return;
    }
    final upload = _Upload(messageId, conn, handle, file.size);
    _uploads[messageId] = upload;
    _setState(messageId, FileState.transferring);
    _pumpOne(upload);
  }

  void _pumpOne(_Upload upload) {
    final conn = upload.conn;
    while (upload.offset < upload.size && conn.pendingBytes < highWater) {
      if (!conn.isReady) return;
      final want = upload.size - upload.offset < chunkSize ? upload.size - upload.offset : chunkSize;
      Uint8List chunk;
      try {
        chunk = upload.file.readSync(want);
      } on FileSystemException {
        chunk = Uint8List(0);
      }
      if (chunk.isEmpty) {
        conn.send(Message.fileCancel(upload.messageId, missingReason));
        _abortUpload(upload.messageId);
        _setState(upload.messageId, FileState.failed);
        return;
      }
      upload.sha.update(chunk);
      conn.send(Message.fileChunk(upload.messageId, upload.offset, chunk));
      upload.offset += chunk.length;
      _emitProgress(upload);
    }
    if (upload.offset >= upload.size && _uploads.containsKey(upload.messageId)) {
      conn.send(Message.fileEnd(upload.messageId, upload.sha.hexdigest()));
      _emitProgress(upload, force: true);
      _abortUpload(upload.messageId);
      _setState(upload.messageId, FileState.done);
    }
  }

  // -- Réception -------------------------------------------------------
  void _receiveChunk(PeerConnection conn, Message message) {
    final download = _downloads[message.id];
    if (download == null || download.conn != conn) return;
    final data = message.data ?? Uint8List(0);
    if (message.offset != download.received || download.received + data.length > download.size) {
      _failDownload(download, integrityReason);
      return;
    }
    try {
      download.file.writeFromSync(data);
    } on FileSystemException {
      _failDownload(download, '');
      return;
    }
    download.sha.update(data);
    download.received += data.length;
    if (download.received == data.length) _setState(message.id, FileState.transferring);
    _emitProgress(download);
  }

  void _finishDownload(PeerConnection conn, Message message) {
    final download = _downloads[message.id];
    if (download == null || download.conn != conn) return;
    if (download.received != download.size || download.sha.hexdigest() != message.sha256) {
      _failDownload(download, integrityReason);
      return;
    }
    download.file.closeSync();
    _downloads.remove(message.id);
    try {
      File(download.part).renameSync(download.destination);
    } on FileSystemException {
      _setState(message.id, FileState.failed);
      return;
    }
    _emitProgress(download, force: true);
    _setState(message.id, FileState.done);
  }

  void _failDownload(_Download download, String reason) {
    if (download.conn.isReady) download.conn.send(Message.fileCancel(download.messageId, reason));
    _abortDownload(download.messageId);
    _setState(download.messageId, FileState.failed);
  }

  // -- Utilitaires -----------------------------------------------------
  void _abortUpload(String messageId) {
    _uploads.remove(messageId)?.file.closeSync();
  }

  void _abortDownload(String messageId) {
    final download = _downloads.remove(messageId);
    if (download == null) return;
    try {
      download.file.closeSync();
    } on FileSystemException {
      // Déjà fermé.
    }
    final part = File(download.part);
    if (part.existsSync()) part.deleteSync();
  }

  void _setState(String messageId, FileState state, {String? path}) {
    if (_store.updateFile(messageId, state: state, path: path)) changed.emit(messageId);
  }

  void _emitProgress(_Transfer transfer, {bool force = false}) {
    if (force || transfer.done - transfer.reported >= progressStep) {
      transfer.reported = transfer.done;
      progress.emit((transfer.messageId, transfer.done, transfer.size));
    }
  }
}
