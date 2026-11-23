// Préparation de la photo de profil, transposée de src/socket_chat/ui/profile.py :
// image carrée recadrée au centre, 128 px, JPEG de moins de 32 Kio.

import 'dart:typed_data';

import 'package:image/image.dart' as img;

import '../protocol/protocol.dart';

/// Côté de la photo envoyée (pixels).
const int avatarSide = 128;

/// Photo prête à envoyer, ou tableau vide si l'image est illisible.
Uint8List makeAvatar(List<int> bytes) {
  img.Image? decoded;
  try {
    decoded = img.decodeImage(Uint8List.fromList(bytes));
  } on Object {
    decoded = null;
  }
  if (decoded == null) return Uint8List(0);
  final oriented = img.bakeOrientation(decoded); // photos de téléphone : orientation EXIF
  final side = oriented.width < oriented.height ? oriented.width : oriented.height;
  final square = img.copyCrop(
    oriented,
    x: (oriented.width - side) ~/ 2,
    y: (oriented.height - side) ~/ 2,
    width: side,
    height: side,
  );
  var size = avatarSide;
  for (final quality in [88, 75, 60, 45]) {
    final scaled = img.copyResize(square, width: size, height: size, interpolation: img.Interpolation.average);
    // Transparence (PNG) sur fond blanc, comme sur PC.
    final flat = img.Image(width: scaled.width, height: scaled.height)..clear(img.ColorRgb8(255, 255, 255));
    img.compositeImage(flat, scaled);
    final jpeg = img.encodeJpg(flat, quality: quality);
    if (jpeg.isNotEmpty && jpeg.length <= maxAvatarSize) return jpeg;
    size = (size * 0.85).round();
  }
  return Uint8List(0);
}
