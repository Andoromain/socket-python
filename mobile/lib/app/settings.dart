import 'package:shared_preferences/shared_preferences.dart';

import '../net/noise.dart';
import '../protocol/protocol.dart';
import 'secrets.dart';

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
  Settings._(this._prefs, this.userId, this.identity, this.name, this.port, this.allowedNetworks);

  static const _userIdKey = 'user_id';
  static const _nameKey = 'name';
  static const _portKey = 'port';
  static const _identityKey = 'identity_key';
  static const _networksKey = 'allowed_networks';

  final PreferenceStore _prefs;

  /// Identifiant de cet appareil auprès des contacts (ne change jamais).
  final String userId;

  /// Clé X25519 de l'appareil (protocole 4), créée au premier lancement et gardée dans
  /// le coffre de l'appareil (Android Keystore, voir secrets.dart).
  final KeyPair identity;

  /// `null` tant que l'utilisateur n'a pas choisi son nom (premier lancement).
  String? name;
  int port;

  /// Réseaux autorisés choisis sur l'appareil (CIDR séparés par des virgules ; vide :
  /// aucune restriction). Une configuration gérée par l'administrateur l'emporte.
  String allowedNetworks;

  static Future<Settings> load(PreferenceStore prefs, [SecretStore? secrets]) async {
    var userId = await prefs.getString(_userIdKey);
    if (!isValidId(userId)) {
      userId = newId();
      await prefs.setString(_userIdKey, userId);
    }
    final port = await prefs.getInt(_portKey);
    return Settings._(
      prefs,
      userId!,
      await _loadIdentity(prefs, secrets ?? MemorySecretStore()),
      await prefs.getString(_nameKey),
      port != null && port > 0 && port < 65536 ? port : defaultPort,
      await prefs.getString(_networksKey) ?? '',
    );
  }

  static Future<KeyPair> _loadIdentity(PreferenceStore prefs, SecretStore secrets) async {
    // Clé enregistrée en clair dans les préférences par une préversion : déplacée.
    final legacy = await prefs.getString(_identityKey);
    if (legacy != null && legacy.isNotEmpty && await secrets.read(_identityKey) == null) {
      await secrets.write(_identityKey, legacy);
    }
    if (legacy != null) await prefs.setString(_identityKey, '');
    final (private, _) = await loadOrCreateSecret(secrets, _identityKey);
    return KeyPair(private);
  }

  Future<void> save() async {
    final value = name;
    if (value != null) await _prefs.setString(_nameKey, value);
    await _prefs.setInt(_portKey, port);
    await _prefs.setString(_networksKey, allowedNetworks);
  }
}
