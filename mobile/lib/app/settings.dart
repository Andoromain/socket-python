import 'dart:convert';
import 'dart:typed_data';

import 'package:shared_preferences/shared_preferences.dart';

import '../protocol/protocol.dart';

/// Stockage clé-valeur des préférences (shared_preferences sur l'appareil).
abstract interface class PreferenceStore {
  Future<String?> getString(String key);
  Future<int?> getInt(String key);
  Future<void> setString(String key, String value);
  Future<void> setInt(String key, int value);
  Future<void> remove(String key);
}

class SharedPreferenceStore implements PreferenceStore {
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();
  @override
  Future<String?> getString(String key) => _prefs.getString(key);
  @override
  Future<int?> getInt(String key) => _prefs.getInt(key);
  @override
  Future<void> setString(String key, String value) => _prefs.setString(key, value);
  @override
  Future<void> setInt(String key, int value) => _prefs.setInt(key, value);
  @override
  Future<void> remove(String key) => _prefs.remove(key);
}

/// Préférences en mémoire (tests).
class MemoryPreferenceStore implements PreferenceStore {
  final Map<String, Object> values = {};
  @override
  Future<String?> getString(String key) async => values[key] as String?;
  @override
  Future<int?> getInt(String key) async => values[key] as int?;
  @override
  Future<void> setString(String key, String value) async => values[key] = value;
  @override
  Future<void> setInt(String key, int value) async => values[key] = value;
  @override
  Future<void> remove(String key) async => values.remove(key);
}

/// Préférences de l'appareil : identifiant stable, nom affiché, port d'écoute.
class Settings {
  Settings._(this._prefs, this.userId, this.name, this.port, this.avatar);

  static const _userIdKey = 'user_id';
  static const _nameKey = 'name';
  static const _portKey = 'port';
  static const _avatarKey = 'avatar';

  final PreferenceStore _prefs;

  /// Identifiant de cet appareil auprès des contacts (ne change jamais).
  final String userId;

  /// `null` tant que l'utilisateur n'a pas choisi son nom (premier lancement).
  String? name;
  int port;

  /// Notre photo de profil (JPEG) ; vide si aucune.
  Uint8List avatar;

  static Future<Settings> load(PreferenceStore prefs) async {
    var userId = await prefs.getString(_userIdKey);
    if (!isValidId(userId)) {
      userId = newId();
      await prefs.setString(_userIdKey, userId);
    }
    final port = await prefs.getInt(_portKey);
    return Settings._(
      prefs,
      userId!,
      await prefs.getString(_nameKey),
      port != null && port > 0 && port < 65536 ? port : defaultPort,
      _decodeAvatar(await prefs.getString(_avatarKey)),
    );
  }

  Future<void> save() async {
    final value = name;
    if (value != null) await _prefs.setString(_nameKey, value);
    await _prefs.setInt(_portKey, port);
    if (avatar.isEmpty) {
      await _prefs.remove(_avatarKey);
    } else {
      await _prefs.setString(_avatarKey, base64Encode(avatar));
    }
  }

  static Uint8List _decodeAvatar(String? raw) {
    if (raw == null || raw.isEmpty) return Uint8List(0);
    try {
      final data = base64Decode(raw);
      return isAvatarImage(data) ? data : Uint8List(0);
    } on FormatException {
      return Uint8List(0);
    }
  }
}
