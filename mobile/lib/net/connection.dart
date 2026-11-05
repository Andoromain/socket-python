// Connexion TCP avec un pair, transposée de src/socket_chat/network.py.
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

/// Délai maximal pour établir la connexion TCP (ms).
int connectTimeoutMs = 10000;

/// Délai maximal pour recevoir la présentation (« hello ») du pair (ms).
int handshakeTimeoutMs = 10000;

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

String _plainAddress(String address) =>
    address.startsWith('::ffff:') ? address.substring(7) : address;

/// Connexion avec un pair, au-dessus d'une [RawSocket].
///
/// Gère la présentation mutuelle (« hello »), le découpage des trames et la
/// fermeture. Le signal [closed] est émis exactement une fois.
///
/// [outgoing] indique qui a ouvert la connexion : ce poste ([PeerConnection.outgoing],
/// puis [connectToHost]) ou le pair ([PeerConnection.accepted]).
class PeerConnection {
  PeerConnection.outgoing(this._hello) : outgoing = true;

  PeerConnection.accepted(RawSocket socket, this._hello) : outgoing = false {
    _attach(socket);
    // La socket est déjà connectée. La présentation est différée pour laisser
    // l'appelant brancher ses signaux.
    Timer.run(_sendHello);
  }

  final Message _hello;
  final bool outgoing;

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
        _sendHello();
      }, onError: _onConnectError);
    }, onError: _onConnectError);
  }

  /// Termine poliment la conversation (envoie « bye » puis ferme).
  void close([String reason = '']) {
    if (_finished) return;
    if (_socket != null && !_closing) {
      _closeReason = reason;
      _write(Message.bye(reason));
      _beginClose();
    } else if (_socket == null) {
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
    final frame = encode(message);
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

  void _sendHello() {
    if (_finished || _helloSent || _socket == null) return;
    _helloSent = true;
    _connectTimer?.cancel();
    _write(_hello);
    if (_peerId == null) {
      // Délai accordé au pair pour se présenter, s'il ne l'a pas déjà fait.
      _handshakeTimer = Timer(Duration(milliseconds: handshakeTimeoutMs), () {
        _fail("Le correspondant ne s'est pas présenté à temps.");
      });
    }
  }

  void _onData(Uint8List data) {
    List<Message> messages;
    try {
      messages = _decoder.feed(data);
    } on ProtocolException catch (error) {
      _fail('Données invalides reçues : $error');
      return;
    }
    for (final message in messages) {
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
      // Sur une connexion acceptée, notre présentation est différée d'un tour de
      // boucle : si celle du pair arrive avant, on l'envoie maintenant, avant que
      // quoi que ce soit d'autre ne parte (le pair couperait la connexion).
      _sendHello();
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
