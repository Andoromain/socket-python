import 'dart:async';

import 'package:flutter/material.dart';

import '../app/controller.dart';
import '../calls/webrtc_engine.dart';
import '../net/calls.dart';
import 'video_views.dart';
import 'widgets.dart';

/// Écran plein d'un appel : entrant (Répondre, Refuser) puis en cours (durée, micro,
/// haut-parleur, Raccrocher). Se ferme tout seul à la fin de l'appel.
class CallScreen extends StatefulWidget {
  const CallScreen({super.key, required this.controller});
  final ChatController controller;

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  Timer? _ticker;
  Call? _last;

  /// Caméra avant (miroir) ou arrière.
  bool _frontCamera = true;

  CallManager get calls => widget.controller.calls;

  @override
  void initState() {
    super.initState();
    _last = calls.call;
    calls.stateChanged.connect(_onState);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && calls.call?.state == CallState.active) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    calls.stateChanged.disconnect(_onState);
    super.dispose();
  }

  void _onState(Call? call) {
    if (!mounted) return;
    if (call == null) {
      // pop() et non maybePop() : PopScope refuse encore le retour à cet instant.
      final route = ModalRoute.of(context);
      if (route == null || !route.isActive) return;
      route.isCurrent ? Navigator.of(context).pop() : Navigator.of(context).removeRoute(route);
      return;
    }
    setState(() => _last = call);
  }

  static String _clock(int seconds) {
    final minutes = (seconds ~/ 60).toString().padLeft(2, '0');
    final secs = (seconds % 60).toString().padLeft(2, '0');
    return seconds >= 3600 ? '${seconds ~/ 3600}:$minutes:$secs' : '$minutes:$secs';
  }

  String _status(Call call) => switch (call.state) {
    CallState.calling => 'Appel en cours…',
    CallState.ringingOut => 'Sonnerie…',
    CallState.incoming => call.video ? 'Appel vidéo entrant' : 'Appel audio entrant',
    CallState.connecting => 'Connexion…',
    CallState.active =>
      _clock(call.duration) + (call.muted ? ' · micro coupé' : '') + (call.cameraOff ? ' · caméra coupée' : ''),
  };

  Future<void> _switchCamera() async {
    await calls.switchCamera();
    if (mounted) setState(() => _frontCamera = !_frontCamera);
  }

  /// Appel vidéo décroché : image du correspondant en plein écran, commandes par-dessus.
  Widget _videoCall(BuildContext context, Call call, String name, Widget avatar) {
    final engine = call.engine;
    final placeholder = ColoredBox(
      color: Colors.black,
      child: Center(child: avatar),
    );
    const shadow = [Shadow(blurRadius: 6)];
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          if (engine is WebRtcEngine)
            VideoViews(engine: engine, placeholder: placeholder, cameraOff: call.cameraOff, mirror: _frontCamera)
          else
            placeholder,
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(24, 16, 140, 0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(color: Colors.white, shadows: shadow),
                  ),
                  Text(
                    _status(call),
                    key: const ValueKey('call-status'),
                    style: const TextStyle(color: Colors.white70, shadows: shadow),
                  ),
                ],
              ),
            ),
          ),
          Align(
            alignment: Alignment.bottomCenter,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _RoundButton(
                      key: const ValueKey('call-mute'),
                      icon: call.muted ? Icons.mic_off : Icons.mic,
                      label: call.muted ? 'Micro coupé' : 'Micro',
                      selected: call.muted,
                      onImage: true,
                      onPressed: () => calls.setMuted(!call.muted),
                    ),
                    _RoundButton(
                      key: const ValueKey('call-camera'),
                      icon: call.cameraOff ? Icons.videocam_off : Icons.videocam,
                      label: call.cameraOff ? 'Caméra coupée' : 'Caméra',
                      selected: call.cameraOff,
                      onImage: true,
                      onPressed: () => calls.setCameraEnabled(call.cameraOff),
                    ),
                    _RoundButton(
                      key: const ValueKey('call-switch-camera'),
                      icon: Icons.cameraswitch,
                      label: 'Retourner',
                      onImage: true,
                      onPressed: call.cameraOff ? null : _switchCamera,
                    ),
                    _RoundButton(
                      key: const ValueKey('call-hangup'),
                      icon: Icons.call_end,
                      label: 'Raccrocher',
                      background: Colors.red.shade600,
                      onImage: true,
                      onPressed: calls.hangup,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final call = calls.call ?? _last;
    final scheme = Theme.of(context).colorScheme;
    if (call == null) return Scaffold(backgroundColor: scheme.surfaceContainerHighest);
    final contact = widget.controller.store.contact(call.contactId);
    final name = contact?.name ?? 'Contact';
    final ended = calls.call == null;
    final avatar = Avatar(name: name, contactId: call.contactId, size: 128, photo: contact?.avatar);
    final videoCall = !ended && call.video && (call.state == CallState.connecting || call.state == CallState.active);
    return PopScope(
      canPop: ended, // pendant l'appel, « retour » ne raccroche pas par mégarde
      child: videoCall
          ? _videoCall(context, call, name, avatar)
          : Scaffold(
              backgroundColor: scheme.surfaceContainerHighest,
              body: SafeArea(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
                  child: Column(
                    children: [
                      const Spacer(),
                      avatar,
                      const SizedBox(height: 24),
                      Text(
                        name,
                        textAlign: TextAlign.center,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        ended ? 'Appel terminé' : _status(call),
                        key: const ValueKey('call-status'),
                        style: Theme.of(context).textTheme.titleMedium?.copyWith(color: scheme.onSurfaceVariant),
                      ),
                      const Spacer(flex: 2),
                      if (ended)
                        const SizedBox(height: 72)
                      else if (call.state == CallState.incoming)
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                          children: [
                            _RoundButton(
                              key: const ValueKey('call-decline'),
                              icon: Icons.call_end,
                              label: 'Refuser',
                              background: Colors.red.shade600,
                              onPressed: calls.decline,
                            ),
                            _RoundButton(
                              key: const ValueKey('call-answer'),
                              icon: call.video ? Icons.videocam : Icons.call,
                              label: 'Répondre',
                              background: Colors.green.shade600,
                              onPressed: calls.accept,
                            ),
                          ],
                        )
                      else
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                          children: [
                            _RoundButton(
                              key: const ValueKey('call-mute'),
                              icon: call.muted ? Icons.mic_off : Icons.mic,
                              label: call.muted ? 'Micro coupé' : 'Micro',
                              selected: call.muted,
                              onPressed: () => calls.setMuted(!call.muted),
                            ),
                            _RoundButton(
                              key: const ValueKey('call-hangup'),
                              icon: Icons.call_end,
                              label: 'Raccrocher',
                              background: Colors.red.shade600,
                              onPressed: calls.hangup,
                            ),
                            _RoundButton(
                              key: const ValueKey('call-speaker'),
                              icon: call.speaker ? Icons.volume_up : Icons.hearing,
                              label: call.speaker ? 'Haut-parleur' : 'Écouteur',
                              selected: call.speaker,
                              onPressed: () => calls.setSpeaker(!call.speaker),
                            ),
                          ],
                        ),
                    ],
                  ),
                ),
              ),
            ),
    );
  }
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({
    super.key,
    required this.icon,
    required this.label,
    required this.onPressed,
    this.background,
    this.selected = false,
    this.onImage = false,
  });

  final IconData icon;
  final String label;
  final VoidCallback? onPressed;
  final Color? background;
  final bool selected;

  /// Posé sur l'image d'un appel vidéo : boutons translucides, texte blanc.
  final bool onImage;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final Color color;
    final Color iconColor;
    if (background != null) {
      color = background!;
      iconColor = Colors.white;
    } else if (onImage) {
      color = selected ? Colors.white : Colors.black54;
      iconColor = selected ? Colors.black : Colors.white;
    } else {
      color = selected ? scheme.onSurface : scheme.surface;
      iconColor = selected ? scheme.surface : scheme.onSurface;
    }
    final size = onImage ? 60.0 : 72.0;
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox.square(
          dimension: size,
          child: IconButton.filled(
            tooltip: label,
            style: IconButton.styleFrom(
              backgroundColor: color,
              foregroundColor: iconColor,
              disabledBackgroundColor: color.withValues(alpha: 0.4),
            ),
            iconSize: onImage ? 28 : 32,
            icon: Icon(icon),
            onPressed: onPressed,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          label,
          style: Theme.of(context).textTheme.labelLarge
              ?.copyWith(color: onImage ? Colors.white : null, shadows: onImage ? const [Shadow(blurRadius: 6)] : null),
        ),
      ],
    );
  }
}
