// Service de messagerie, transposé de src/socket_chat/messenger.py : écoute,
// connexions par contact, file d'envoi, accusés, réactions et fichiers.
//
// Chaque appareil écoute en permanence et peut converser avec plusieurs contacts.
// Une seule connexion TCP est conservée par contact ; elle est ouverte à la
// demande (envoi d'un message, contact détecté avec des messages en attente).
// Les messages écrits hors ligne sont conservés et transmis au retour du contact.

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:meta/meta.dart';

import '../core/signal.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
import 'connection.dart';
import 'discovery.dart';
import 'identity.dart';
import 'netpolicy.dart';
import 'noise.dart' as noise;
import 'transfers.dart';

/// Intervalle minimal entre deux indications « écrit… » envoyées (ms).
int typingThrottleMs = 3000;

/// Durée d'affichage de « écrit… » sans nouvelle indication (ms).
int typingTimeoutMs = 6000;

/// Intervalle des nouvelles tentatives d'envoi des messages en attente (ms).
int retryIntervalMs = 30000;

const String duplicateReason = 'duplicate';
const String selfReason = 'self';
const String selfError = 'Cette adresse est celle de votre propre appareil.';
const String networkReason = 'network';
const String networkError =
    'Cet appareil est hors des réseaux autorisés (réglage « Réseaux autorisés » ou '
    'configuration de votre administrateur).';

/// Clé présentée différente de la clé épinglée : connexion refusée.
const String keyReason = 'key';
const String keyError =
    'La clé de sécurité de ce contact a changé : connexion refusée. Vérifiez avec cette '
    'personne (réinstallation ?) puis acceptez la nouvelle clé dans votre conversation.';
const String keyRefusedError =
    'Ce contact a refusé la connexion : il connaît une autre clé de sécurité pour cet '
    'appareil. Votre nouvelle clé doit être acceptée de son côté, dans votre conversation.';

/// Aperçu JPEG d'une image à envoyer (vide si ce n'en est pas une).
typedef Thumbnailer = Uint8List Function(String path);

Uint8List _noThumbnail(String path) => Uint8List(0);

class Messenger {
  Messenger(
    this.store,
    this.userId,
    String name, {
    this.thumbnailer = _noThumbnail,
    noise.KeyPair? identity,
    Uint8List? avatar,
  }) : _name = validateName(name),
       identity = identity ?? noise.KeyPair.generate(),
       _avatar = avatar != null && isAvatarImage(avatar) ? avatar : Uint8List(0),
       transfers = FileTransfers(store) {
    transfers.changed.connect(_onTransferChanged);
    transfers.progress.connect(fileProgress.emit);
  }

  final Store store;
  final String userId;

  /// Clé de l'appareil (Settings.identity) ; éphémère si absente (tests).
  final noise.KeyPair identity;
  String _name;
  final Thumbnailer thumbnailer;
  final FileTransfers transfers;

  /// Liste, aperçus ou compteurs à rafraîchir.
  final Signal<void> contactsChanged = Signal();
  final Signal<MessageRecord> messageAdded = Signal();

  /// Identifiant du message, nouvel état.
  final Signal<(String, Status)> messageStatusChanged = Signal();

  /// Réactions ou état du fichier modifiés.
  final Signal<String> messageChanged = Signal();

  /// Identifiant, octets transférés, taille.
  final Signal<(String, int, int)> fileProgress = Signal();

  /// Contact, en train d'écrire.
  final Signal<(String, bool)> typingChanged = Signal();

  /// Contact, en ligne.
  final Signal<(String, bool)> presenceChanged = Signal();

  /// Contact dont une nouvelle clé attend d'être acceptée (ou ne l'attend plus).
  final Signal<String> keyChanged = Signal();

  /// Contact, refuse notre clé (il en connaît une autre pour cet appareil).
  final Signal<(String, bool)> keyRefused = Signal();

  /// Contact dont la photo de profil a changé.
  final Signal<String> avatarChanged = Signal();

  /// Contact, trame d'appel (voir calls.dart).
  final Signal<(String, Message)> callFrame = Signal();

  /// La connexion avec ce contact vient de se fermer.
  final Signal<String> peerDisconnected = Signal();
  final Signal<String> error = Signal();

  RawServerSocket? _server;
  StreamSubscription<RawSocket>? _serverSubscription;
  final Map<String, PeerConnection> _connections = {}; // contacts identifiés
  final Set<PeerConnection> _pending = {}; // présentation pas encore reçue
  final Map<String, PeerConnection> _dialing = {}; // connexions sortantes par contact
  final Set<PeerConnection> _manual = {}; // ajouts par adresse (erreurs affichées)
  final Set<PeerConnection> _manualReady = {}; // ajouts par adresse, identifiés
  final Set<String> _refusedBy = {}; // contacts qui refusent notre clé (déjà signalés)

  /// Réseaux autorisés (netpolicy.dart) ; liste vide : aucune restriction.
  List<Network> allowedNetworks = const [];

  /// Règle d'adresse (remplaçable dans les tests, où tout passe par la boucle locale).
  @visibleForTesting
  bool Function(String address, List<Network> networks) addressPolicy = isAllowed;
  final Set<String> _discovered = {};
  final Map<String, Timer> _typingTimers = {};
  final Set<String> _typing = {}; // contacts en train d'écrire
  final Map<String, (bool, int)> _typingSent = {};
  // Envois en attente d'une connexion : réactions et refus de fichiers.
  final Map<String, Map<String, String>> _pendingReactions = {};
  final Map<String, Set<String>> _pendingDeclines = {};
  Timer? _retryTimer;

  // -- Propriétés ------------------------------------------------------
  String get name => _name;
  set name(String value) => _name = validateName(value);

  Uint8List _avatar;

  /// Notre photo de profil (vide : aucune).
  Uint8List get avatar => _avatar;

  /// Change (ou retire avec une liste vide) notre photo et l'envoie aux contacts connectés.
  void setAvatar(Uint8List data) {
    if (data.isNotEmpty && !isAvatarImage(data)) {
      throw ArgumentError('Photo de profil invalide (JPEG ou PNG de 32 Ko au plus).');
    }
    _avatar = data;
    for (final conn in _connections.values) {
      if (conn.isReady) conn.send(Message.avatar(data));
    }
  }

  int? get port => _server?.port;
  bool get running => _server != null;

  bool isOnline(String contactId) => _connections.containsKey(contactId) || _discovered.contains(contactId);

  bool isTyping(String contactId) => _typing.contains(contactId);

  /// Le contact refuse actuellement la clé de cet appareil.
  bool refusesOurKey(String contactId) => _refusedBy.contains(contactId);

  @visibleForTesting
  void debugRefusedBy(String contactId, bool refused) {
    if (refused ? _refusedBy.add(contactId) : _refusedBy.remove(contactId)) {
      keyRefused.emit((contactId, refused));
    }
  }

  /// Clé épinglée du contact (`null` s'il ne s'est jamais connecté en version 4).
  Uint8List? contactKey(String contactId) {
    final key = store.contact(contactId)?.publicKey;
    return key == null ? null : decodeKey(key);
  }

  /// L'utilisateur accepte la nouvelle clé présentée par ce contact.
  void trustNewKey(String contactId) {
    final contact = store.contact(contactId);
    final pending = contact?.pendingKey;
    if (contact == null || pending == null) return;
    store.pinKey(contactId, pending);
    keyChanged.emit(contactId);
    // Une tentative en cours (vers une adresse obsolète) empêcherait de reconnecter.
    _dialing.remove(contactId)?.abort('Nouvelle clé acceptée');
    connectTo(contactId);
  }

  /// Vrai si [key] est la clé épinglée du contact (ou s'il n'en a pas encore).
  bool _checkKey(String peerId, Uint8List key) {
    final contact = store.contact(peerId);
    if (contact == null || contact.publicKey == null) return true;
    final encoded = encodeKey(key);
    if (encoded == contact.publicKey) {
      if (contact.pendingKey != null) {
        store.setPendingKey(peerId, null); // fausse alerte : clé d'origine
        keyChanged.emit(peerId);
      }
      return true;
    }
    if (store.setPendingKey(peerId, encoded)) keyChanged.emit(peerId);
    return false;
  }

  @visibleForTesting
  Map<String, PeerConnection> get connections => _connections;

  @visibleForTesting
  Map<String, PeerConnection> get dialing => _dialing;

  // -- Cycle de vie ----------------------------------------------------
  /// Écoute les connexions entrantes ([port] 0 : choisi par le système).
  Future<bool> start(int port) async {
    if (_server != null) return true;
    RawServerSocket server;
    try {
      server = await RawServerSocket.bind(InternetAddress.anyIPv4, port);
    } on SocketException catch (exc) {
      error.emit(
        "Impossible d'écouter sur le port $port : ${describeSocketError(exc)}\n"
        'Socket Chat est peut-être déjà ouvert sur cet appareil, ou le port est '
        'utilisé par une autre application (voir Préférences).',
      );
      return false;
    }
    _server = server;
    _serverSubscription = server.listen(_onNewConnection);
    _retryTimer = Timer.periodic(Duration(milliseconds: retryIntervalMs), (_) => _retryUndelivered());
    return true;
  }

  /// Ferme toutes les connexions et arrête l'écoute.
  void stop() {
    _retryTimer?.cancel();
    _retryTimer = null;
    transfers.stop();
    final server = _server;
    if (server != null) {
      _serverSubscription?.cancel();
      server.close();
      _server = null;
    }
    final all = {..._connections.values, ..._pending, ..._dialing.values};
    _connections.clear();
    _pending.clear();
    _dialing.clear();
    _manual.clear();
    for (final conn in all) {
      conn.close();
    }
    for (final contactId in _typing.toList()) {
      _setPeerTyping(contactId, false);
    }
    final online = _discovered.toList();
    _discovered.clear();
    for (final contactId in online) {
      presenceChanged.emit((contactId, false));
    }
  }

  // -- Actions ---------------------------------------------------------
  /// Envoie (ou met en attente) un message ; il est enregistré immédiatement.
  MessageRecord send(String contactId, String text) {
    text = text.trim();
    if (text.isEmpty) throw ArgumentError('Message vide.');
    if (runeLength(text) > maxTextLength) {
      throw ArgumentError('Un message ne peut pas dépasser $maxTextLength caractères.');
    }
    if (store.contact(contactId) == null) throw ArgumentError('Contact inconnu.');
    final record = MessageRecord(newId(), contactId, true, text, nowMs(), Status.pending);
    store.addMessage(record);
    messageAdded.emit(record);
    contactsChanged.emit(null);
    _typingSent.remove(contactId);
    _transmitOrConnect(contactId, record);
    return record;
  }

  /// Propose un fichier au contact (aperçu inclus pour une image).
  MessageRecord sendFile(String contactId, String path, {String? name}) {
    if (store.contact(contactId) == null) throw ArgumentError('Contact inconnu.');
    final absolute = File(path).absolute.path;
    final size = checkSendable(absolute);
    final fileName = safeFileName(name ?? absolute.split(Platform.pathSeparator).last);
    final info = FileInfo(fileName, size, FileState.offered, absolute, thumbnailer(absolute));
    final record = MessageRecord(newId(), contactId, true, '', nowMs(), Status.pending, file: info);
    store.addMessage(record);
    messageAdded.emit(record);
    contactsChanged.emit(null);
    _transmitOrConnect(contactId, record);
    return record;
  }

  /// Accepte un fichier reçu et l'enregistre à [destination].
  void acceptFile(String messageId, String destination) {
    final record = store.message(messageId);
    final file = record?.file;
    if (record == null || file == null || record.outgoing) throw ArgumentError('Fichier inconnu.');
    if (file.state != FileState.offered && file.state != FileState.failed) {
      throw ArgumentError("Ce fichier n'est plus disponible.");
    }
    final conn = _connections[record.contactId];
    if (conn == null) {
      final name = store.contact(record.contactId)?.name ?? "L'expéditeur";
      throw ArgumentError("$name n'est pas connecté : réessayez quand il sera de nouveau en ligne.");
    }
    try {
      transfers.accept(messageId, destination, conn);
    } on FileSystemException catch (exc) {
      throw ArgumentError("Impossible d'écrire dans $destination : ${exc.message}");
    }
  }

  void declineFile(String messageId) {
    final record = store.message(messageId);
    final file = record?.file;
    if (record == null || file == null || record.outgoing) return;
    if (file.state != FileState.offered) return;
    if (store.updateFile(messageId, state: FileState.declined)) messageChanged.emit(messageId);
    final conn = _connections[record.contactId];
    if (conn != null) {
      conn.send(Message.fileAnswer(messageId, false));
    } else {
      _pendingDeclines.putIfAbsent(record.contactId, () => {}).add(messageId);
    }
  }

  /// Annule un fichier proposé ou un transfert en cours (des deux côtés).
  void cancelFile(String messageId) {
    final record = store.message(messageId);
    if (record == null || record.file == null) return;
    transfers.cancel(messageId, _connections[record.contactId]);
  }

  /// Pose une réaction sur un message (`""` la retire).
  void react(String messageId, String emoji) {
    if (runeLength(emoji) > maxEmojiLength) throw ArgumentError('Réaction invalide.');
    final record = store.message(messageId);
    if (record == null || !store.setReaction(messageId, me, emoji)) return;
    messageChanged.emit(messageId);
    final conn = _connections[record.contactId];
    if (conn != null) {
      conn.send(Message.react(messageId, emoji));
    } else {
      _pendingReactions.putIfAbsent(record.contactId, () => {})[messageId] = emoji;
    }
  }

  /// Marque la conversation comme lue et prévient l'expéditeur.
  void markRead(String contactId) {
    final ids = store.markIncomingRead(contactId);
    if (ids.isEmpty) return;
    final conn = _connections[contactId];
    if (conn != null) {
      for (final messageId in ids) {
        conn.send(Message.ack(messageId, AckStatus.read));
      }
    }
    for (final messageId in ids) {
      messageStatusChanged.emit((messageId, Status.read));
    }
    contactsChanged.emit(null);
  }

  /// Signale au contact que l'on écrit (envoi limité dans le temps).
  void setTyping(String contactId, bool active) {
    final conn = _connections[contactId];
    if (conn == null) return;
    final (lastState, lastTime) = _typingSent[contactId] ?? (false, -typingThrottleMs);
    final now = monotonicMs();
    if (active == lastState && (!active || now - lastTime < typingThrottleMs)) return;
    _typingSent[contactId] = (active, now);
    conn.send(Message.typing(active));
  }

  /// Connexion établie avec ce contact (nécessaire pour un appel).
  bool isConnected(String contactId) => _connections[contactId]?.isReady ?? false;

  /// Envoie une trame au contact s'il est connecté ; `false` sinon.
  bool sendTo(String contactId, Message message) {
    final conn = _connections[contactId];
    if (conn == null || !conn.isReady) return false;
    conn.send(message);
    return true;
  }

  /// Note un appel dans la conversation (localement).
  MessageRecord addCallRecord(String contactId, bool outgoing, CallInfo call) {
    final unread = !outgoing && (call.outcome == CallOutcome.missed || call.outcome == CallOutcome.cancelled);
    final record = MessageRecord(
      newId(),
      contactId,
      outgoing,
      '',
      nowMs(),
      unread ? Status.delivered : Status.read,
      call: call,
    );
    store.addMessage(record);
    messageAdded.emit(record);
    contactsChanged.emit(null);
    return record;
  }

  /// Se connecte à une adresse ; le contact est créé à sa présentation.
  void addContact(String host, int port) {
    _manual.add(_dial(host, port));
  }

  /// Ouvre la connexion vers un contact connu, si ce n'est pas déjà fait.
  void connectTo(String contactId) {
    if (_connections.containsKey(contactId) || _dialing.containsKey(contactId) || !running) return;
    final contact = store.contact(contactId);
    final host = contact?.host;
    final port = contact?.port;
    if (host == null || host.isEmpty || port == null || port == 0) return;
    _dialing[contactId] = _dial(host, port);
  }

  // -- Détection (reliée à Discovery) ----------------------------------
  void attachDiscovery(Discovery discovery) {
    discovery.peerSeen.connect((peer) => onPeerSeen(peer.$1, peer.$2, peer.$3, peer.$4));
    discovery.peerLost.connect(onPeerLost);
  }

  void onPeerSeen(String contactId, String name, String host, int port) {
    if (contactId == userId) return;
    final previous = store.contact(contactId);
    // Annonce non authentifiée : le nom d'un contact vérifié ne change que par sa
    // présentation chiffrée (sinon n'importe qui pourrait le renommer).
    if (previous != null && previous.publicKey != null) name = previous.name;
    store.upsertContact(contactId, name, host: host, port: port, lastSeen: nowMs());
    final moved = previous != null && (previous.host != host || previous.port != port);
    final stale = _dialing[contactId];
    if (moved && stale != null) {
      // Tentative en cours vers l'ancienne adresse (qui peut traîner plusieurs
      // secondes) : on l'abandonne pour la nouvelle.
      stale.abort('Adresse obsolète');
      connectTo(contactId);
    }
    if (!_discovered.contains(contactId)) {
      final wasOnline = isOnline(contactId);
      _discovered.add(contactId);
      if (!wasOnline) presenceChanged.emit((contactId, true));
      contactsChanged.emit(null);
      if (store.undeliveredOutgoing(contactId).isNotEmpty) connectTo(contactId);
    }
  }

  void onPeerLost(String contactId) {
    if (_discovered.remove(contactId)) {
      if (!isOnline(contactId)) {
        _setPeerTyping(contactId, false);
        presenceChanged.emit((contactId, false));
      }
      contactsChanged.emit(null);
    }
  }

  // -- Connexions ------------------------------------------------------
  Message _hello() => Message.hello(userId, _name, port ?? 0);

  PeerConnection _dial(String host, int port) {
    final conn = PeerConnection.outgoing(_hello(), identity);
    _watch(conn);
    conn.connectToHost(host, port);
    return conn;
  }

  void _onNewConnection(RawSocket socket) {
    if (!addressPolicy(socket.remoteAddress.address, allowedNetworks)) {
      socket.close();
      return;
    }
    _watch(PeerConnection.accepted(socket, _hello(), identity));
  }

  void _watch(PeerConnection conn) {
    _pending.add(conn);
    conn.ready.connect((peer) => _onReady(conn, peer.$1, peer.$2, peer.$3));
    conn.messageReceived.connect((message) => _onMessage(conn, message));
    conn.closed.connect((reason) => _onClosed(conn, reason));
    conn.bytesWritten.connect((_) => transfers.pump(conn));
  }

  void _onReady(PeerConnection conn, String peerId, String name, int port) {
    _pending.remove(conn);
    final manual = _manual.remove(conn);
    if (_dialing[peerId] == conn) _dialing.remove(peerId);
    if (!addressPolicy(conn.peerAddress, allowedNetworks)) {
      // Connexion sortante vers un nom d'hôte résolu hors des réseaux autorisés.
      conn.abort(networkReason);
      if (manual) error.emit(networkError);
      return;
    }
    final key = conn.peerKey;
    if (peerId == userId || key == null || _sameBytes(key, identity.public)) {
      conn.close(selfReason);
      if (manual) error.emit(selfError);
      return;
    }
    if (!_checkKey(peerId, key)) {
      conn.close(keyReason);
      if (manual) error.emit(keyError);
      return;
    }
    if (manual) _manualReady.add(conn);

    final existing = _connections[peerId];
    if (existing != null && existing != conn) {
      // Connexions croisées : les deux postes gardent celle ouverte par le plus
      // petit identifiant, pour converger vers la même connexion.
      final keeperInitiator = userId.compareTo(peerId) <= 0 ? userId : peerId;
      final initiator = conn.outgoing ? userId : peerId;
      if (initiator != keeperInitiator) {
        conn.close(duplicateReason);
        return;
      }
      _connections[peerId] = conn;
      existing.close(duplicateReason);
    } else {
      _connections[peerId] = conn;
    }

    final wasPinned = store.contact(peerId)?.publicKey != null;
    store.upsertContact(peerId, name, host: conn.peerAddress, port: port, lastSeen: nowMs());
    // Premier contact chiffré : la clé est épinglée (confiance au premier usage).
    if (!wasPinned) store.pinKey(peerId, encodeKey(key));
    if (existing == null && !_discovered.contains(peerId)) presenceChanged.emit((peerId, true));
    contactsChanged.emit(null);
    for (final record in store.undeliveredOutgoing(peerId)) {
      transmit(conn, record);
    }
    for (final MapEntry(key: messageId, value: emoji) in (_pendingReactions.remove(peerId) ?? {}).entries) {
      conn.send(Message.react(messageId, emoji));
    }
    for (final messageId in _pendingDeclines.remove(peerId) ?? <String>{}) {
      conn.send(Message.fileAnswer(messageId, false));
    }
    if (_avatar.isNotEmpty) conn.send(Message.avatar(_avatar));
  }

  void _onMessage(PeerConnection conn, Message message) {
    final peerId = conn.peerId;
    if (peerId == null || _connections[peerId] != conn) return;
    if (_refusedBy.remove(peerId)) {
      // Le contact nous parle : il a accepté notre nouvelle clé.
      keyRefused.emit((peerId, false));
    }
    switch (message.type) {
      case MessageType.chat || MessageType.fileOffer:
        _receiveChat(conn, peerId, message);
      case MessageType.react:
        final record = store.message(message.id);
        if (record != null && record.contactId == peerId && store.setReaction(message.id, peerId, message.emoji)) {
          messageChanged.emit(message.id);
        }
      case MessageType.fileAnswer || MessageType.fileChunk || MessageType.fileEnd || MessageType.fileCancel:
        transfers.handle(conn, peerId, message);
      case MessageType.ack:
        final record = store.message(message.id);
        if (record == null || !record.outgoing || record.contactId != peerId) return;
        final status = message.status == AckStatus.read ? Status.read : Status.delivered;
        if (store.updateStatus(message.id, status)) {
          messageStatusChanged.emit((message.id, status));
          contactsChanged.emit(null);
        }
      case MessageType.typing:
        _setPeerTyping(peerId, message.active);
      case MessageType.avatar:
        if (store.setAvatar(peerId, message.data ?? Uint8List(0))) {
          avatarChanged.emit(peerId);
          contactsChanged.emit(null);
        }
      case MessageType.callInvite ||
          MessageType.callRinging ||
          MessageType.callAccept ||
          MessageType.callSdp ||
          MessageType.callIce ||
          MessageType.callEnd:
        callFrame.emit((peerId, message));
      case MessageType.hello || MessageType.bye:
        break;
    }
  }

  void _receiveChat(PeerConnection conn, String peerId, Message message) {
    FileInfo? info;
    if (message.type == MessageType.fileOffer) {
      info = FileInfo(message.name, message.size, FileState.offered, null, message.thumb);
    }
    final record = MessageRecord(message.id, peerId, false, message.text, nowMs(), Status.delivered, file: info);
    bool inserted;
    try {
      inserted = store.addMessage(record);
    } on Object {
      return; // impossible d'enregistrer : pas d'accusé, l'expéditeur réessaiera
    }
    conn.send(Message.ack(message.id, AckStatus.delivered));
    _setPeerTyping(peerId, false);
    if (inserted) {
      messageAdded.emit(record);
      contactsChanged.emit(null);
    } else {
      // Retransmission d'un message déjà reçu : renvoyer son état réel.
      if (store.message(message.id)?.status == Status.read) {
        conn.send(Message.ack(message.id, AckStatus.read));
      }
    }
  }

  void _onClosed(PeerConnection conn, String reason) {
    transfers.connectionClosed(conn);
    _pending.remove(conn);
    _dialing.removeWhere((_, dialing) => dialing == conn);
    if (_manual.remove(conn)) {
      // Selon l'ordre des événements, l'autre extrémité d'une connexion à soi-même
      // peut fermer avant d'avoir reçu notre présentation.
      if (reason == selfReason) {
        error.emit(selfError);
      } else if (reason == keyReason) {
        error.emit(keyRefusedError);
      } else {
        error.emit('Impossible de joindre cet appareil : ${reason.isEmpty ? 'connexion fermée.' : reason}');
      }
    }
    final refusedBy = conn.peerId;
    if (reason == keyReason && refusedBy != null) {
      if (_manualReady.contains(conn)) error.emit(keyRefusedError);
      if (_refusedBy.add(refusedBy)) keyRefused.emit((refusedBy, true));
    }
    _manualReady.remove(conn);
    final peerId = conn.peerId;
    if (peerId != null && _connections[peerId] == conn) {
      _connections.remove(peerId);
      _typingSent.remove(peerId);
      peerDisconnected.emit(peerId);
      if (!isOnline(peerId)) {
        _setPeerTyping(peerId, false);
        presenceChanged.emit((peerId, false));
      }
      contactsChanged.emit(null);
    }
  }

  void _transmitOrConnect(String contactId, MessageRecord record) {
    final conn = _connections[contactId];
    if (conn != null) {
      transmit(conn, record);
    } else {
      connectTo(contactId);
    }
  }

  @visibleForTesting
  void transmit(PeerConnection conn, MessageRecord record) {
    final file = record.file;
    final frame = file != null
        ? Message.fileOffer(record.id, file.name, file.size, file.thumb)
        : Message.chat(record.id, record.text);
    try {
      conn.send(frame);
    } on StateError {
      return;
    } on ProtocolException {
      return;
    }
    if (store.updateStatus(record.id, Status.sent)) {
      messageStatusChanged.emit((record.id, Status.sent));
    }
  }

  void _onTransferChanged(String messageId) {
    messageChanged.emit(messageId);
    contactsChanged.emit(null);
  }

  void _retryUndelivered() {
    for (final contactId in store.contactsWithUndelivered()) {
      connectTo(contactId);
    }
  }

  void _setPeerTyping(String contactId, bool active) {
    if (active) {
      _typingTimers[contactId]?.cancel();
      _typingTimers[contactId] = Timer(Duration(milliseconds: typingTimeoutMs), () => _setPeerTyping(contactId, false));
      if (_typing.add(contactId)) typingChanged.emit((contactId, true));
    } else if (_typing.remove(contactId)) {
      _typingTimers.remove(contactId)?.cancel();
      typingChanged.emit((contactId, false));
    }
  }

  /// Test uniquement : simule les événements d'une connexion.
  @visibleForTesting
  void debugWatch(PeerConnection conn, {bool manual = false}) {
    _pending.add(conn);
    if (manual) _manual.add(conn);
  }

  @visibleForTesting
  void debugOnReady(PeerConnection conn, String peerId, String name, int port) => _onReady(conn, peerId, name, port);

  @visibleForTesting
  void debugOnClosed(PeerConnection conn, String reason) => _onClosed(conn, reason);
}

bool _sameBytes(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}
