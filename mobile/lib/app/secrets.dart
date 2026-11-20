// Secrets de l'appareil (clé de l'appareil, clé de l'historique), transposé de
// src/socket_chat/vault.py.
//
// Sur Android, ils sont chiffrés par flutter_secure_storage avec une clé d'Android
// Keystore : elle ne quitte jamais le composant sécurisé du téléphone, si bien qu'une
// copie des données de l'application (sauvegarde, téléphone rooté, extraction) ne
// permet pas de lire l'historique.

import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Stockage clé-valeur protégé.
abstract interface class SecretStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

/// Android Keystore (flutter_secure_storage).
class KeystoreSecretStore implements SecretStore {
  const KeystoreSecretStore();

  static const FlutterSecureStorage _storage = FlutterSecureStorage(aOptions: AndroidOptions());

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) => _storage.write(key: key, value: value);
}

/// Secrets en mémoire (tests).
class MemorySecretStore implements SecretStore {
  final Map<String, String> values = {};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;
}

/// Secret aléatoire de [size] octets enregistré sous [name] ; renvoie (secret, créé).
///
/// Une valeur illisible (abîmée, d'une autre taille) est remplacée : l'appelant met alors
/// de côté ce qui en dépendait.
Future<(Uint8List, bool)> loadOrCreateSecret(SecretStore store, String name, {int size = 32}) async {
  final stored = await store.read(name);
  if (stored != null && stored.isNotEmpty) {
    try {
      final secret = base64.decode(stored);
      if (secret.length == size) return (secret, false);
    } on FormatException {
      // Valeur abîmée : remplacée ci-dessous.
    }
  }
  final random = Random.secure();
  final secret = Uint8List.fromList(List<int>.generate(size, (_) => random.nextInt(256)));
  await store.write(name, base64.encode(secret));
  return (secret, true);
}

/// Renomme un fichier inutilisable (sans l'effacer) ; renvoie son nouveau chemin.
String? setAside(String path, [String label = 'illisible']) {
  final file = File(path);
  if (!file.existsSync()) return null;
  final dot = path.lastIndexOf('.');
  final stem = dot > path.lastIndexOf('/') ? path.substring(0, dot) : path;
  final ext = dot > path.lastIndexOf('/') ? path.substring(dot) : '';
  var target = '$stem-$label$ext';
  for (var index = 2; File(target).existsSync(); index++) {
    target = '$stem-$label-$index$ext';
  }
  file.renameSync(target);
  for (final suffix in ['-wal', '-shm']) {
    final companion = File('$path$suffix');
    if (companion.existsSync()) companion.deleteSync();
  }
  return target;
}
