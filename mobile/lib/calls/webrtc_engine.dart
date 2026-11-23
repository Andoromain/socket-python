// Média des appels sur Android : WebRTC (flutter_webrtc), compatible avec la page
// src/socket_chat/resources/call.html du PC (Opus, mêmes descriptions, mêmes candidats).
//
// Pas de serveur STUN/TURN : les appareils se joignent directement sur le réseau local.

import 'dart:async';

import 'package:flutter_webrtc/flutter_webrtc.dart';

import '../core/signal.dart';
import '../net/calls.dart';

class WebRtcEngine extends MediaEngine {
  RTCPeerConnection? _pc;

  /// Image de notre caméra et du correspondant (appel vidéo), affichées par l'écran d'appel.
  MediaStream? get localStream => _stream;
  MediaStream? remoteStream;
  final Signal<void> streamsChanged = Signal();
  bool _camera = true;

  /// Résolu avec la connexion une fois créée (`null` si l'appel échoue ou s'arrête) :
  /// la description du correspondant peut arriver pendant la demande d'accès au micro.
  final Completer<RTCPeerConnection?> _pcReady = Completer();
  MediaStream? _stream;
  bool _remoteSet = false;
  bool _stopped = false;
  bool _connected = false;
  bool _muted = false;
  bool _speaker = false;
  final List<RTCIceCandidate> _pendingIce = [];

  @override
  Future<void> start({required bool caller, required String media}) async {
    final MediaStream stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        'audio': {'echoCancellation': true, 'noiseSuppression': true, 'autoGainControl': true},
        'video': media == 'video' ? {'facingMode': 'user', 'width': 640, 'height': 480, 'frameRate': 24} : false,
      });
    } catch (e) {
      _resolve(null);
      if (!_stopped) {
        failed.emit(
          media == 'video'
              ? "Caméra ou micro inaccessible : autorisez-les dans les réglages d'Android."
              : "Micro inaccessible : autorisez l'accès au micro dans les réglages d'Android.",
        );
      }
      return;
    }
    if (_stopped) {
      _resolve(null);
      await _release(stream);
      return;
    }
    _stream = stream;
    _applyMute();
    _applyCamera();
    streamsChanged.emit(null);
    final pc = await createPeerConnection({'iceServers': <Map<String, Object>>[], 'sdpSemantics': 'unified-plan'});
    if (_stopped) {
      _resolve(null);
      await pc.close();
      await _release(stream);
      return;
    }
    _pc = pc;
    pc.onIceCandidate = (candidate) {
      final text = candidate.candidate;
      if (_stopped || text == null || text.isEmpty) return;
      localIce.emit((text, candidate.sdpMid ?? '', candidate.sdpMLineIndex ?? 0));
    };
    pc.onTrack = (event) {
      if (_stopped || event.streams.isEmpty || remoteStream == event.streams.first) return;
      remoteStream = event.streams.first;
      streamsChanged.emit(null);
    };
    pc.onConnectionState = (state) {
      if (_stopped) return;
      if (state == RTCPeerConnectionState.RTCPeerConnectionStateConnected && !_connected) {
        _connected = true;
        connected.emit(null);
      } else if (state == RTCPeerConnectionState.RTCPeerConnectionStateFailed) {
        failed.emit('Liaison perdue (réseau ou pare-feu).');
      }
    };
    for (final track in stream.getTracks()) {
      await pc.addTrack(track, stream);
    }
    _resolve(pc);
    await Helper.setSpeakerphoneOn(_speaker); // écouteur par défaut, comme un appel téléphonique
    if (caller) {
      final offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      if (!_stopped) localDescription.emit(('offer', offer.sdp ?? ''));
    }
  }

  @override
  Future<void> setRemoteDescription(String kind, String sdp) async {
    final pc = await _ready();
    if (pc == null) return;
    try {
      await pc.setRemoteDescription(RTCSessionDescription(sdp, kind));
      _remoteSet = true;
      for (final candidate in _pendingIce) {
        await pc.addCandidate(candidate);
      }
      _pendingIce.clear();
      if (kind == 'offer') {
        final answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        if (!_stopped) localDescription.emit(('answer', answer.sdp ?? ''));
      }
    } catch (e) {
      if (!_stopped) failed.emit('Négociation impossible : $e');
    }
  }

  @override
  Future<void> addRemoteIce(String candidate, String mid, int index) async {
    final pc = await _ready();
    if (pc == null) return;
    final ice = RTCIceCandidate(candidate, mid, index);
    if (!_remoteSet) {
      _pendingIce.add(ice);
      return;
    }
    try {
      await pc.addCandidate(ice);
    } catch (_) {
      // Candidat inutilisable : ignoré.
    }
  }

  @override
  void setMuted(bool muted) {
    _muted = muted;
    _applyMute();
  }

  @override
  void setSpeaker(bool on) {
    _speaker = on;
    if (_pc != null) Helper.setSpeakerphoneOn(on);
  }

  @override
  void setCameraEnabled(bool on) {
    _camera = on;
    _applyCamera();
  }

  @override
  Future<void> switchCamera() async {
    final tracks = _stream?.getVideoTracks() ?? const <MediaStreamTrack>[];
    if (tracks.isNotEmpty) await Helper.switchCamera(tracks.first);
  }

  void _applyCamera() {
    for (final track in _stream?.getVideoTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = _camera;
    }
  }

  @override
  Future<void> stop() async {
    if (_stopped) return;
    _stopped = true;
    _resolve(null);
    _pendingIce.clear();
    final pc = _pc;
    final stream = _stream;
    _pc = null;
    _stream = null;
    remoteStream = null;
    streamsChanged.emit(null);
    if (pc != null) {
      await pc.close();
      await pc.dispose();
    }
    if (stream != null) await _release(stream);
  }

  void _applyMute() {
    for (final track in _stream?.getAudioTracks() ?? const <MediaStreamTrack>[]) {
      track.enabled = !_muted;
    }
  }

  Future<RTCPeerConnection?> _ready() async {
    final pc = await _pcReady.future;
    return _stopped ? null : pc;
  }

  void _resolve(RTCPeerConnection? pc) {
    if (!_pcReady.isCompleted) _pcReady.complete(pc);
  }

  static Future<void> _release(MediaStream stream) async {
    for (final track in stream.getTracks()) {
      await track.stop();
    }
    await stream.dispose();
  }
}
