import 'package:flutter/material.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';

import '../calls/webrtc_engine.dart';

/// Image d'un appel vidéo : le correspondant en plein écran, notre caméra en incrustation.
/// Tant que l'image du correspondant n'arrive pas, [placeholder] est affiché.
class VideoViews extends StatefulWidget {
  const VideoViews({
    super.key,
    required this.engine,
    required this.placeholder,
    this.cameraOff = false,
    this.mirror = true,
  });

  final WebRtcEngine engine;
  final Widget placeholder;

  /// Caméra coupée : l'incrustation disparaît.
  final bool cameraOff;

  /// Caméra avant : effet miroir, comme dans une glace.
  final bool mirror;

  @override
  State<VideoViews> createState() => _VideoViewsState();
}

class _VideoViewsState extends State<VideoViews> {
  final RTCVideoRenderer _remote = RTCVideoRenderer();
  final RTCVideoRenderer _local = RTCVideoRenderer();
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    widget.engine.streamsChanged.connect(_update);
    _initialize();
  }

  Future<void> _initialize() async {
    await _remote.initialize();
    await _local.initialize();
    _ready = true;
    _update(null);
  }

  void _update(void _) {
    if (!_ready || !mounted) return;
    setState(() {
      _remote.srcObject = widget.engine.remoteStream;
      _local.srcObject = widget.engine.localStream;
    });
  }

  @override
  void dispose() {
    widget.engine.streamsChanged.disconnect(_update);
    _remote.srcObject = null;
    _local.srcObject = null;
    _remote.dispose();
    _local.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final hasRemote = _ready && widget.engine.remoteStream != null;
    final hasLocal = _ready && widget.engine.localStream != null && !widget.cameraOff;
    return Stack(
      fit: StackFit.expand,
      children: [
        if (hasRemote)
          RTCVideoView(_remote, objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitCover)
        else
          widget.placeholder,
        if (hasLocal)
          Positioned(
            top: MediaQuery.paddingOf(context).top + 16,
            right: 16,
            width: 108,
            height: 152,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(12),
              child: RTCVideoView(
                _local,
                mirror: widget.mirror,
                objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitCover,
              ),
            ),
          ),
      ],
    );
  }
}
