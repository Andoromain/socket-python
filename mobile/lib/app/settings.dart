import 'package:shared_preferences/shared_preferences.dart';

import '../net/identity.dart';
import '../net/noise.dart';
import '../protocol/protocol.dart';

/// Stockage clé-valeur des préférences (shared_preferences sur l'appareil).
abstract interface class PreferenceStore {
  Future<String?> getString(String key);
  Future<int?> getInt(String key);
  Future<void> setString(String key, String value);
  Future<void> setInt(String key, int value);
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
}

/// Préférences de l'appareil : identifiant stable, clé, nom affiché, port d'écoute.
class Settings {
  Settings._(this._prefs, this.userId, this.identity, this.name, this.port);

  static const _userIdKey = 'user_id';
  static const _nameKey = 'name';
  static const _portKey = 'port';
  static const _identityKey = 'identity_key';

  final PreferenceStore _prefs;

  /// Identifiant de cet appareil auprès des contacts (ne change jamais).
  final String userId;

  /// Clé X25519 de l'appareil (protocole 4), créée au premier lancement. Les
  /// préférences sont privées à l'application (inaccessibles aux autres applications).
  final KeyPair identity;

  /// `null` tant que l'utilisateur n'a pas choisi son nom (premier lancement).
  String? name;
  int port;

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
      await _loadIdentity(prefs),
      await prefs.getString(_nameKey),
      port != null && port > 0 && port < 65536 ? port : defaultPort,
    );
  }

  static Future<KeyPair> _loadIdentity(PreferenceStore prefs) async {
    final stored = await prefs.getString(_identityKey);
    if (stored != null) {
      try {
        return KeyPair(decodeKey(stored));
      } on Object {
        // Clé abîmée : une nouvelle est créée ; les contacts verront « clé changée ».
      }
    }
    final keys = KeyPair.generate();
    await prefs.setString(_identityKey, encodeKey(keys.private));
    return keys;
  }

  Future<void> save() async {
    final value = name;
    if (value != null) await _prefs.setString(_nameKey, value);
    await _prefs.setInt(_portKey, port);
  }
}
