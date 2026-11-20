// Connexion TCP chiffrée avec un pair, transposée de src/socket_chat/network.py.
//
// Protocole 4 : poignée de main Noise XX (noise.dart), qui prouve la clé de chaque
// appareil et établit les clés de session, puis flux chiffré transportant les trames
// JSON habituelles, à commencer par la présentation (« hello »).
//
// RawSocket plutôt que Socket : on garde notre propre file d'envoi pour connaître
// le volume en attente (contrôle de flux des transferts) et être prévenu quand il
// diminue, comme QTcpSocket.bytesToWrite() / bytesWritten côté PC.

import 'dart:async';
import 'dart:collection';
import 'dart:io';
import 'dart:typed_data';

import '../core/signal.dart';
import '../protocol/protocol.dart';
import 'noise.dart' as noise;

/// Délai maximal pour établir la connexion TCP (ms).
int connectTimeoutMs = 10000;

/// Délai maximal pour la poignée de main et la présentation (« hello ») du pair (ms).
int handshakeTimeoutMs = 10000;

const String oldVersionError =
    'Ce correspondant utilise une ancienne version de Socket Chat, sans chiffrement : '
    'il doit la mettre à jour (version 4 ou plus récente).';

/// Délai maximal accordé à une fermeture polie avant de couper la connexion (ms).
int closeTimeoutMs = 3000;

/// Message lisible pour une erreur réseau.
String describeSocketError(Object error) {
  if (error is SocketException) {
    final code = error.osError?.errorCode;
    final text = '${error.message} ${error.osError?.message ?? ''}'.toLowerCase();
    if (code == 111 || code == 61 || code == 10061 || text.contains('refused')) {
      return "Connexion refusée : aucune conversation n'est hébergée à cette adresse.";
    }
    if (text.contains('host lookup') || text.contains('no address')) return 'Hôte introuvable.';
    if (code == 110 || code == 60 || code == 10060 || text.contains('timed out')) {
      return 'Délai de connexion dépassé.';
    }
    if (code == 104 || code == 54 || code == 10054 || text.contains('reset')) {
      return 'Le correspondant a fermé la connexion.';
    }
    if (code == 101 || code == 113 || code == 51 || code == 65 || text.contains('unreachable')) {
      return 'Erreur réseau.';
    }
    return error.osError?.message.isNotEmpty == true ? error.osError!.message : error.message;
  }
  return error.toString();
}

String _plainAddress(String address) => address.startsWith('::ffff:') ? address.substring(7) : address;

/// Connexion chiffrée avec un pair, au-dessus d'une [RawSocket].
///
/// Gère la poignée de main Noise, la présentation mutuelle (« hello »), le
/// chiffrement et le découpage des trames, et la fermeture. Le signal [closed] est
/// émis exactement une fois. [peerKey] est la clé publique prouvée du pair
/// (disponible dès [ready]).
///
/// [outgoing] indique qui a ouvert la connexion : ce poste ([PeerConnection.outgoing],
/// puis [connectToHost]) ou le pair ([PeerConnection.accepted]).
class PeerConnection {
  PeerConnection.outgoing(this._hello, noise.KeyPair identity)
    : outgoing = true,
      _noise = noise.HandshakeState(true, identity, prologue: noise.prologue);

  PeerConnection.accepted(RawSocket socket, this._hello, noise.KeyPair identity)
    : outgoing = false,
      _noise = noise.HandshakeState(false, identity, prologue: noise.prologue) {
    _attach(socket);
    // La socket est déjà connectée. La poignée de main est différée pour laisser
    // l'appelant brancher ses signaux.
    Timer.run(_start);
  }

  final Message _hello;
  final bool outgoing;
  final noise.HandshakeState _noise;
  final noise.FrameReader _frames = noise.FrameReader();
  bool _started = false;
  bool _receivedAny = false;

  /// Identifiant, nom et port d'écoute du pair.
  final Signal<(String, String, int)> ready = Signal();

  /// Message reçu (hors hello et bye).
  final Signal<Message> messageReceived = Signal();

  /// Motif lisible ; chaîne vide = fermeture normale.
  final Signal<String> closed = Signal();

  /// Des données ont quitté la file d'envoi (contrôle de flux).
  final Signal<void> bytesWritten = Signal();

  RawSocket? _socket;
  ConnectionTask<RawSocket>? _connecting;
  StreamSubscription<RawSocketEvent>? _subscription;
  final FrameDecoder _decoder = FrameDecoder();
  final ListQueue<Uint8List> _queue = ListQueue();
  int _queueOffset = 0; // octets déjà écrits du premier bloc de la file
  int _pendingBytes = 0;
  String? _peerId;
  String? _peerName;
  String _peerAddress = '';
  bool _finished = false;
  bool _helloSent = false;
  bool _closing = false; // fermeture polie demandée : vider la file puis fermer
  bool _shutdownSent = false;
  bool _readClosed = false;
  String _closeReason = '';
  Timer? _handshakeTimer;
  Timer? _closeTimer;
  Timer? _connectTimer;

  // -- Propriétés ------------------------------------------------------
  String? get peerId => _peerId;
  String? get peerName => _peerName;
  String get peerAddress => _peerAddress;

  /// Clé publique du pair, prouvée par la poignée de main (`null` avant).
  Uint8List? get peerKey => _noise.complete ? _noise.remoteStatic : null;
  bool get secure => _noise.complete;

  /// Octets en attente d'envoi (pour ne pas saturer la mémoire lors d'un transfert).
  int get pendingBytes => _pendingBytes;

  /// Pair identifié et connexion utilisable (ni terminée, ni en cours de fermeture).
  bool get isReady => _peerId != null && !_finished && !_closing;
  bool get isFinished => _finished;

  // -- API -------------------------------------------------------------
  /// Envoie une trame ; ignorée si la connexion est en cours de fermeture (les
  /// messages non confirmés seront retransmis à la prochaine connexion).
  void send(Message message) {
    if (_peerId == null || _finished) throw StateError("La connexion n'est pas établie.");
    if (_closing) return;
    _sendHello(); // notre présentation précède toujours toute autre trame
    _write(message);
  }

  /// Ouvre la connexion sortante.
  void connectToHost(String host, int port) {
    _connectTimer = Timer(Duration(milliseconds: connectTimeoutMs), () {
      _fail('Délai de connexion dépassé.');
    });
    RawSocket.startConnect(host, port).then((task) {
      if (_finished) {
        task.socket.then<void>((socket) => socket.close(), onError: (Object _) {});
        task.cancel();
        return;
      }
      _connecting = task;
      task.socket.then((socket) {
        _connecting = null;
        if (_finished) {
          socket.close();
          return;
        }
        _attach(socket);
        _start();
      }, onError: _onConnectError);
    }, onError: _onConnectError);
  }

  /// Termine poliment la conversation (envoie « bye » puis ferme).
  void close([String reason = '']) {
    if (_finished) return;
    if (_socket != null && !_closing && secure) {
      _closeReason = reason;
      _write(Message.bye(reason));
      _beginClose();
    } else if (_socket == null || !secure) {
      abort(reason);
    }
  }

  /// Ferme brutalement, sans prévenir le pair.
  void abort([String reason = '']) {
    _connecting?.cancel();
    _connecting = null;
    _subscription?.cancel();
    _socket?.close();
    _finish(reason);
  }

  // -- Interne ---------------------------------------------------------
  void _attach(RawSocket socket) {
    _socket = socket;
    _peerAddress = _plainAddress(socket.remoteAddress.address);
    try {
      socket.setOption(SocketOption.tcpNoDelay, true);
    } on SocketException {
      // Option facultative.
    }
    _subscription = socket.listen(
      _onEvent,
      onError: (Object error) {
        if (!_finished) abort(describeSocketError(error));
      },
      onDone: () {
        if (!_finished) abort(_closeReason);
      },
      cancelOnError: true,
    );
    if (_pendingBytes > 0) socket.writeEventsEnabled = true;
  }

  void _onConnectError(Object error) {
    _connecting = null;
    if (!_finished) _finish(describeSocketError(error));
  }

  void _write(Message message) {
    _queueBytes(noise.encryptStream(_noise.send!, encode(message)));
  }

  void _queueBytes(Uint8List frame) {
    _queue.add(frame);
    _pendingBytes += frame.length;
    // L'écriture effective a lieu au prochain tour de boucle (événement « write »),
    // comme QTcpSocket : l'appelant n'est jamais rappelé pendant send().
    _socket?.writeEventsEnabled = true;
  }

  void _flush() {
    final socket = _socket;
    if (socket == null) return;
    var written = 0;
    while (_queue.isNotEmpty) {
      final head = _queue.first;
      int count;
      try {
        count = socket.write(head, _queueOffset);
      } on SocketException catch (error) {
        abort(describeSocketError(error));
        return;
      }
      written += count;
      _queueOffset += count;
      if (_queueOffset < head.length) break; // socket pleine : on attend
      _queue.removeFirst();
      _queueOffset = 0;
    }
    _pendingBytes -= written;
    if (_queue.isNotEmpty) socket.writeEventsEnabled = true;
    if (written > 0) bytesWritten.emit(null);
    if (!_finished) _continueClose();
  }

  void _onEvent(RawSocketEvent event) {
    if (_finished) return;
    switch (event) {
      case RawSocketEvent.read:
        final data = _socket!.read();
        if (data != null && data.isNotEmpty) _onData(data);
      case RawSocketEvent.write:
        _flush();
      case RawSocketEvent.readClosed:
        // Le pair a fermé : on envoie ce qui reste puis on ferme à notre tour.
        _readClosed = true;
        _beginClose();
      case RawSocketEvent.closed:
        abort(_closeReason);
    }
  }

  /// Connexion TCP établie : l'initiateur ouvre la poignée de main.
  void _start() {
    if (_finished || _started || _socket == null) return;
    _started = true;
    _connectTimer?.cancel();
    // Délai pour la poignée de main et la présentation du pair.
    _handshakeTimer = Timer(Duration(milliseconds: handshakeTimeoutMs), () {
      _fail("Le correspondant ne s'est pas présenté à temps.");
    });
    if (outgoing) _queueBytes(noise.frame(_noise.writeMessage()));
  }

  /// Envoie notre présentation une seule fois (idempotent), une fois chiffré.
  void _sendHello() {
    if (_finished || _helloSent || !secure) return;
    _helloSent = true;
    _write(_hello);
  }

  void _onData(Uint8List data) {
    if (!secure && !_started) _start(); // données reçues avant le tour de boucle différé
    if (!_receivedAny && data.isNotEmpty) {
      _receivedAny = true;
      if (data[0] == 0x7b) {
        // « { » : présentation JSON en clair, pair en protocole 3 ou plus ancien.
        _fail(oldVersionError);
        return;
      }
    }
    try {
      for (final message in _frames.feed(data)) {
        if (_finished) return;
        if (secure) {
          _receive(_noise.receive!.decrypt(const [], message));
        } else {
          _noise.readMessage(message);
          if (_noise.myTurn) _queueBytes(noise.frame(_noise.writeMessage()));
          if (secure) _sendHello();
        }
      }
    } on noise.NoiseException catch (error) {
      _fail('Connexion chiffrée impossible : $error');
    } on ProtocolException catch (error) {
      _fail('Données invalides reçues : $error');
    }
  }

  void _receive(Uint8List plaintext) {
    for (final message in _decoder.feed(plaintext)) {
      if (_finished) return;
      _handle(message);
    }
  }

  void _handle(Message message) {
    if (message.type == MessageType.hello) {
      if (_peerId != null) return; // présentation en double : ignorée
      _handshakeTimer?.cancel();
      _peerId = message.id;
      _peerName = message.name;
      ready.emit((message.id, message.name, message.port));
    } else if (message.type == MessageType.bye) {
      _closeReason = message.reason;
      _beginClose();
    } else if (_peerId == null) {
      _fail('Message reçu avant la présentation du correspondant.');
    } else {
      messageReceived.emit(message);
    }
  }

  void _beginClose() {
    if (_finished) return;
    if (!_closing) {
      _closing = true;
      // On borne l'attente pour ne jamais rester bloqué.
      _closeTimer = Timer(Duration(milliseconds: closeTimeoutMs), () => abort(_closeReason));
    }
    _continueClose();
  }

  void _continueClose() {
    final socket = _socket;
    if (!_closing || socket == null || _pendingBytes > 0) return;
    if (_readClosed) {
      abort(_closeReason); // les deux sens sont terminés
    } else if (!_shutdownSent) {
      _shutdownSent = true;
      try {
        socket.shutdown(SocketDirection.send);
      } on SocketException {
        abort(_closeReason);
      }
    }
  }

  void _fail(String reason) {
    if (!_finished) abort(reason);
  }

  void _finish(String reason) {
    if (_finished) return;
    _finished = true;
    _handshakeTimer?.cancel();
    _closeTimer?.cancel();
    _connectTimer?.cancel();
    _queue.clear();
    _pendingBytes = 0;
    closed.emit(reason);
  }
}
