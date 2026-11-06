import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../store/store.dart';
import 'format.dart';

/// Avatar rond : photo de profil si le contact en a une, sinon initiales sur la même
/// couleur que sur PC ; point vert si en ligne.
class Avatar extends StatelessWidget {
  const Avatar({
    super.key,
    required this.name,
    required this.contactId,
    this.online = false,
    this.size = 48,
    this.photo,
  });

  final String name;
  final String contactId;
  final bool online;
  final double size;

  /// Photo de profil (JPEG ou PNG) ; `null` ou vide : initiales.
  final Uint8List? photo;

  @override
  Widget build(BuildContext context) {
    final dot = size * 0.3;
    final initialsCircle = CircleAvatar(
      radius: size / 2,
      backgroundColor: avatarColor(contactId),
      child: Text(
        initials(name),
        style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: size * 0.38),
      ),
    );
    final image = photo;
    return SizedBox.square(
      dimension: size,
      child: Stack(
        children: [
          if (image != null && image.isNotEmpty)
            ClipOval(
              child: Image.memory(
                image,
                width: size,
                height: size,
                fit: BoxFit.cover,
                gaplessPlayback: true,
                semanticLabel: 'Photo de $name',
                errorBuilder: (_, _, _) => initialsCircle, // image illisible : initiales
              ),
            )
          else
            initialsCircle,
          if (online)
            Positioned(
              right: 0,
              bottom: 0,
              child: Semantics(
                label: 'en ligne',
                child: Container(
                  width: dot,
                  height: dot,
                  decoration: BoxDecoration(
                    color: onlineColor,
                    shape: BoxShape.circle,
                    border: Border.all(color: Theme.of(context).colorScheme.surface, width: dot * 0.18),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// État d'un message envoyé : horloge, ✓ (envoyé), ✓✓ (remis), ✓✓ colorées (lu).
class StatusTicks extends StatelessWidget {
  const StatusTicks({super.key, required this.status, required this.color, required this.readColor, this.size = 16});

  final Status status;
  final Color color;
  final Color readColor;
  final double size;

  @override
  Widget build(BuildContext context) {
    final (icon, label) = switch (status) {
      Status.pending => (Icons.schedule, 'en attente'),
      Status.sent => (Icons.done, 'envoyé'),
      Status.delivered => (Icons.done_all, 'remis'),
      Status.read => (Icons.done_all, 'lu'),
    };
    return Icon(icon, size: size, color: status == Status.read ? readColor : color, semanticLabel: label);
  }
}
