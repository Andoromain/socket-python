// Identité cryptographique de l'appareil et code de sécurité des contacts,
// transposé de src/socket_chat/identity.py (même calcul, même code des deux côtés).

import 'dart:convert';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as hashes;

import 'noise.dart';

const int safetyGroups = 12;
final List<int> _safetyLabel = utf8.encode('SocketChat safety number v1');

String encodeKey(List<int> public) => base64.encode(public);

/// Clé publique depuis sa forme base64 ; lève [FormatException] si invalide.
Uint8List decodeKey(String text) {
  final raw = base64.decode(text);
  if (raw.length != dhLen) throw const FormatException('Clé invalide.');
  return raw;
}

/// Empreinte courte d'une clé (journaux, diagnostics).
String fingerprint(List<int> public) => hashes.sha256.convert(public).toString().substring(0, 16);

int _compare(List<int> a, List<int> b) {
  for (var i = 0; i < a.length && i < b.length; i++) {
    if (a[i] != b[i]) return a[i] - b[i];
  }
  return a.length - b.length;
}

/// Code de sécurité de deux appareils : 12 groupes de 5 chiffres, même ordre des deux côtés.
String safetyNumber(List<int> keyA, List<int> keyB) {
  final ordered = _compare(keyA, keyB) <= 0 ? [keyA, keyB] : [keyB, keyA];
  final digest = hashes.sha512.convert([..._safetyLabel, ...ordered[0], ...ordered[1]]).bytes;
  final groups = <String>[];
  for (var i = 0; i < safetyGroups; i++) {
    var value = 0;
    for (final byte in digest.sublist(i * 5, i * 5 + 5)) {
      value = value * 256 + byte; // 40 bits : tient dans un int Dart
    }
    groups.add((value % 100000).toString().padLeft(5, '0'));
  }
  return groups.join(' ');
}

/// Code de sécurité sur trois lignes de quatre groupes.
String formatSafetyNumber(String number) {
  final groups = number.split(' ');
  return [
    for (var i = 0; i < groups.length; i += 4)
      groups.sublist(i, i + 4 > groups.length ? groups.length : i + 4).join(' '),
  ].join('\n');
}
