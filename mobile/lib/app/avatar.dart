import 'dart:typed_data';

import 'package:image/image.dart' as img;

import '../protocol/protocol.dart';

/// Côté d'une photo de profil (pixels), comme sur PC.
const int avatarSide = 256;

/// Photo de profil : image recadrée en carré centré, 256 px, JPEG de 32 Kio au plus.
///
/// Même traitement que `make_avatar` côté PC. Lève [ArgumentError] si [source]
/// n'est pas une image lisible.
Uint8List makeAvatar(Uint8List source) {
  final decoded = img.decodeImage(source);
  if (decoded == null) throw ArgumentError("Ce fichier n'est pas une image lisible.");
  final oriented = img.bakeOrientation(decoded); // photos d'appareil : sens EXIF
  final side = oriented.width < oriented.height ? oriented.width : oriented.height;
  final square = img.copyCrop(
    oriented,
    x: (oriented.width - side) ~/ 2,
    y: (oriented.height - side) ~/ 2,
    width: side,
    height: side,
  );
  var size = avatarSide;
  while (size >= 64) {
    final scaled = img.copyResize(square, width: size, height: size, interpolation: img.Interpolation.average);
    // Transparence (PNG) sur fond blanc, comme sur PC.
    final flat = img.Image(width: size, height: size)..clear(img.ColorRgb8(255, 255, 255));
    img.compositeImage(flat, scaled);
    for (final quality in const [85, 70, 55, 40]) {
      final data = img.encodeJpg(flat, quality: quality);
      if (data.isNotEmpty && data.length <= maxAvatarSize) return data;
    }
    size = (size * 0.75).floor();
  }
  throw ArgumentError('Impossible de réduire cette image.');
}
