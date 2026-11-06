import 'dart:async';

import 'package:flutter/material.dart';

import '../app/controller.dart';
import '../net/calls.dart';
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
    CallState.incoming => 'Appel audio entrant',
    CallState.connecting => 'Connexion…',
    CallState.active => _clock(call.duration) + (call.muted ? ' · micro coupé' : ''),
  };

  @override
  Widget build(BuildContext context) {
    final call = calls.call ?? _last;
    final scheme = Theme.of(context).colorScheme;
    if (call == null) return Scaffold(backgroundColor: scheme.surfaceContainerHighest);
    final contact = widget.controller.store.contact(call.contactId);
    final name = contact?.name ?? 'Contact';
    final ended = calls.call == null;
    return PopScope(
      canPop: ended, // pendant l'appel, « retour » ne raccroche pas par mégarde
      child: Scaffold(
        backgroundColor: scheme.surfaceContainerHighest,
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
            child: Column(
              children: [
                const Spacer(),
                Avatar(name: name, contactId: call.contactId, size: 128, photo: contact?.avatar),
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
                        icon: Icons.call,
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
  });

  final IconData icon;
  final String label;
  final VoidCallback onPressed;
  final Color? background;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = background ?? (selected ? scheme.onSurface : scheme.surface);
    final iconColor = background != null ? Colors.white : (selected ? scheme.surface : scheme.onSurface);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox.square(
          dimension: 72,
          child: IconButton.filled(
            tooltip: label,
            style: IconButton.styleFrom(backgroundColor: color, foregroundColor: iconColor),
            iconSize: 32,
            icon: Icon(icon),
            onPressed: onPressed,
          ),
        ),
        const SizedBox(height: 8),
        Text(label, style: Theme.of(context).textTheme.labelLarge),
      ],
    );
  }
}
