// Appels audio (et vidéo) : signalisation et déroulement, transposés de
// src/socket_chat/calls.py (mêmes trames, mêmes délais, mêmes issues).
//
// Le média circule directement entre les deux appareils (WebRTC, voir
// lib/calls/webrtc_engine.dart) ; ce module ne fait que la signalisation, par la
// connexion déjà établie avec le contact.

import 'dart:async';

import '../core/signal.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
import 'messenger.dart';

/// Délais (ms) : accusé de sonnerie, sonnerie, établissement de la liaison média.
int ringingAckTimeoutMs = 6000;
int ringTimeoutMs = 30000;
int connectTimeoutMs = 20000;

/// Motifs de fin (champ « reason » de call_end).
const String hangupReason = 'hangup';
const String declinedReason = 'declined';
const String busyReason = 'busy';
const String timeoutReason = 'timeout';
const String failedReason = 'failed';

enum CallState {
  calling, // invitation envoyée, pas encore de sonnerie chez le correspondant
  ringingOut, // le correspondant sonne
  incoming, // nous sonnons
  connecting, // décroché, liaison média en cours d'établissement
  active, // en communication
}

/// Moteur média d'un appel (WebRTC). Une instance par appel.
abstract class MediaEngine {
  /// Type (offer/answer), SDP.
  final Signal<(String, String)> localDescription = Signal();

  /// Candidat, mid, index.
  final Signal<(String, String, int)> localIce = Signal();
  final Signal<void> connected = Signal();

  /// Motif lisible.
  final Signal<String> failed = Signal();

  /// Ouvre le micro ; l'appelant produit ensuite son offre ([localDescription]).
  Future<void> start({required bool caller, required String media});
  Future<void> setRemoteDescription(String kind, String sdp);
  Future<void> addRemoteIce(String candidate, String mid, int index);
  void setMuted(bool muted);

  /// Haut-parleur (sinon écouteur) ; sans effet hors téléphone.
  void setSpeaker(bool on) {}

  /// Appel vidéo : coupe ou rétablit la caméra (image noire chez le correspondant).
  void setCameraEnabled(bool on) {}

  /// Appel vidéo : caméra avant ou arrière.
  Future<void> switchCamera() async {}
  Future<void> stop();
}

typedef EngineFactory = MediaEngine Function();

class Call {
  Call(this.id, this.contactId, this.media, this.outgoing, this.state);
  final String id;
  final String contactId;
  final String media;
  final bool outgoing;
  CallState state;
  Stopwatch? _talking;
  bool muted = false;

  /// Haut-parleur : d'office en vidéo (téléphone tenu devant soi), écouteur sinon.
  late bool speaker = video;

  /// Appel vidéo : caméra coupée.
  bool cameraOff = false;

  bool get video => media == 'video';
  MediaEngine? engine;

  /// Secondes de conversation.
  int get duration => _talking?.elapsed.inSeconds ?? 0;
}

/// Un appel à la fois : signalisation, délais, historique.
class CallManager {
  CallManager(this._messenger, this._engineFactory) {
    _messenger.callFrame.connect((frame) => _onFrame(frame.$1, frame.$2));
    _messenger.peerDisconnected.connect(_onDisconnected);
  }

  final Messenger _messenger;
  final EngineFactory _engineFactory;
  Call? call;
  Timer? _timer;

  /// Appel en cours (ou `null` une fois terminé).
  final Signal<Call?> stateChanged = Signal();

  /// Contact, média.
  final Signal<(String, String)> incomingCall = Signal();

  /// Contact, issue.
  final Signal<(String, CallOutcome)> callEnded = Signal();
  final Signal<String> error = Signal();

  // -- Actions locales ---------------------------------------------------
  Call startCall(String contactId, [String media = 'audio']) {
    if (call != null) throw StateError('Un appel est déjà en cours.');
    if (!_messenger.isConnected(contactId)) {
      final name = _messenger.store.contact(contactId)?.name ?? 'Ce contact';
      throw StateError("$name n'est pas en ligne : appel impossible.");
    }
    final current = Call(newId(), contactId, media, true, CallState.calling);
    call = current;
    _messenger.sendTo(contactId, Message.callInvite(current.id, media));
    _arm(ringingAckTimeoutMs);
    stateChanged.emit(current);
    return current;
  }

  void accept() {
    final current = call;
    if (current == null || current.state != CallState.incoming) return;
    if (!_messenger.sendTo(current.contactId, Message.callAccept(current.id))) {
      _finish(CallOutcome.failed);
      return;
    }
    _setState(CallState.connecting);
    _startEngine(caller: false);
    _arm(connectTimeoutMs);
  }

  void decline() {
    final current = call;
    if (current == null || current.state != CallState.incoming) return;
    _messenger.sendTo(current.contactId, Message.callEnd(current.id, declinedReason));
    _finish(CallOutcome.declined);
  }

  void hangup() {
    final current = call;
    if (current == null) return;
    if (current.state == CallState.incoming) {
      decline();
      return;
    }
    _messenger.sendTo(current.contactId, Message.callEnd(current.id, hangupReason));
    _finish(current.state == CallState.active ? CallOutcome.completed : CallOutcome.cancelled);
  }

  void setMuted(bool muted) {
    final current = call;
    if (current == null) return;
    current.muted = muted;
    current.engine?.setMuted(muted);
    stateChanged.emit(current);
  }

  void setSpeaker(bool on) {
    final current = call;
    if (current == null) return;
    current.speaker = on;
    current.engine?.setSpeaker(on);
    stateChanged.emit(current);
  }

  void setCameraEnabled(bool on) {
    final current = call;
    if (current == null || !current.video) return;
    current.cameraOff = !on;
    current.engine?.setCameraEnabled(on);
    stateChanged.emit(current);
  }

  Future<void> switchCamera() async => call?.engine?.switchCamera();

  /// Arrêt de l'application : raccroche proprement.
  void stop() => hangup();

  // -- Trames reçues -----------------------------------------------------
  void _onFrame(String peerId, Message message) {
    if (message.type == MessageType.callInvite) {
      _onInvite(peerId, message);
      return;
    }
    final current = call;
    if (current == null || current.id != message.id || current.contactId != peerId) return;
    switch (message.type) {
      case MessageType.callRinging when current.state == CallState.calling:
        _setState(CallState.ringingOut);
        _arm(ringTimeoutMs + 5000); // l'appelé abandonne le premier
      case MessageType.callAccept
          when current.outgoing && (current.state == CallState.calling || current.state == CallState.ringingOut):
        _setState(CallState.connecting);
        _startEngine(caller: true);
        _arm(connectTimeoutMs);
      case MessageType.callSdp:
        current.engine?.setRemoteDescription(message.sdpKind, message.sdp);
      case MessageType.callIce:
        if (message.candidate.isNotEmpty) current.engine?.addRemoteIce(message.candidate, message.mid, message.index);
      case MessageType.callEnd:
        _finish(_remoteOutcome(current, message.reason));
      default:
        break;
    }
  }

  void _onInvite(String peerId, Message message) {
    final current = call;
    if (current != null) {
      final glare =
          current.outgoing &&
          current.contactId == peerId &&
          (current.state == CallState.calling || current.state == CallState.ringingOut);
      if (glare && peerId.compareTo(_messenger.userId) < 0) {
        // Appels croisés : on garde celui de l'identifiant le plus petit (le sien).
        _messenger.sendTo(peerId, Message.callEnd(current.id, hangupReason));
        _timer?.cancel();
        call = null;
      } else {
        _messenger.sendTo(peerId, Message.callEnd(message.id, busyReason));
        return;
      }
    }
    final incoming = Call(message.id, peerId, message.media, false, CallState.incoming);
    call = incoming;
    _messenger.sendTo(peerId, Message.callRinging(incoming.id));
    _arm(ringTimeoutMs);
    stateChanged.emit(incoming);
    incomingCall.emit((peerId, message.media));
  }

  void _onDisconnected(String contactId) {
    final current = call;
    if (current == null || current.contactId != contactId) return;
    _finish(switch (current.state) {
      CallState.incoming => CallOutcome.missed,
      CallState.active => CallOutcome.completed,
      _ => CallOutcome.failed,
    });
  }

  // -- Moteur média ------------------------------------------------------
  void _startEngine({required bool caller}) {
    final current = call!;
    final engine = _engineFactory();
    current.engine = engine;
    final callId = current.id;
    final contactId = current.contactId;
    engine.localDescription.connect((d) => _sendIfCurrent(callId, contactId, Message.callSdp(callId, d.$1, d.$2)));
    engine.localIce.connect((c) => _sendIfCurrent(callId, contactId, Message.callIce(callId, c.$1, c.$2, c.$3)));
    engine.connected.connect((_) => _onMediaConnected(callId));
    engine.failed.connect((reason) => _onMediaFailed(callId, reason));
    engine.start(caller: caller, media: current.media).catchError((Object e) => _onMediaFailed(callId, '$e'));
    if (current.muted) engine.setMuted(true);
    if (current.speaker) engine.setSpeaker(true);
    if (current.cameraOff) engine.setCameraEnabled(false);
  }

  void _sendIfCurrent(String callId, String contactId, Message message) {
    if (call?.id == callId) _messenger.sendTo(contactId, message);
  }

  void _onMediaConnected(String callId) {
    final current = call;
    if (current == null || current.id != callId || current.state == CallState.active) return;
    _timer?.cancel();
    current._talking = Stopwatch()..start();
    _setState(CallState.active);
  }

  void _onMediaFailed(String callId, String reason) {
    final current = call;
    if (current == null || current.id != callId) return;
    _messenger.sendTo(current.contactId, Message.callEnd(current.id, failedReason));
    error.emit(reason);
    _finish(current.state == CallState.active ? CallOutcome.completed : CallOutcome.failed);
  }

  // -- Fin d'appel ---------------------------------------------------------
  static CallOutcome _remoteOutcome(Call current, String reason) {
    if (current.state == CallState.active) return CallOutcome.completed;
    if (current.state == CallState.connecting) {
      return reason == hangupReason ? CallOutcome.cancelled : CallOutcome.failed;
    }
    if (current.outgoing) {
      return switch (reason) {
        declinedReason => CallOutcome.declined,
        busyReason => CallOutcome.busy,
        timeoutReason => CallOutcome.noAnswer,
        _ => CallOutcome.failed,
      };
    }
    if (current.state == CallState.incoming) return CallOutcome.missed; // l'appelant a raccroché
    return CallOutcome.failed;
  }

  void _onTimeout() {
    final current = call;
    if (current == null) return;
    switch (current.state) {
      case CallState.calling:
        // Aucun accusé de sonnerie : version trop ancienne chez le correspondant.
        _messenger.sendTo(current.contactId, Message.callEnd(current.id, hangupReason));
        _finish(CallOutcome.unsupported);
      case CallState.ringingOut:
        _messenger.sendTo(current.contactId, Message.callEnd(current.id, timeoutReason));
        _finish(CallOutcome.noAnswer);
      case CallState.incoming:
        _messenger.sendTo(current.contactId, Message.callEnd(current.id, timeoutReason));
        _finish(CallOutcome.missed);
      case CallState.connecting:
        _messenger.sendTo(current.contactId, Message.callEnd(current.id, failedReason));
        error.emit("La liaison audio n'a pas pu s'établir (pare-feu ou réseau).");
        _finish(CallOutcome.failed);
      case CallState.active:
        break;
    }
  }

  void _finish(CallOutcome outcome) {
    final current = call;
    if (current == null) return;
    call = null;
    _timer?.cancel();
    final engine = current.engine;
    current.engine = null;
    engine?.stop();
    final duration = outcome == CallOutcome.completed ? current.duration : 0;
    _messenger.addCallRecord(current.contactId, current.outgoing, CallInfo(current.media, outcome, duration));
    stateChanged.emit(null);
    callEnded.emit((current.contactId, outcome));
  }

  void _setState(CallState state) {
    call!.state = state;
    stateChanged.emit(call);
  }

  void _arm(int ms) {
    _timer?.cancel();
    _timer = Timer(Duration(milliseconds: ms), _onTimeout);
  }
}
