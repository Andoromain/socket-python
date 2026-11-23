import 'package:flutter/services.dart';

/// Fonctions réseau propres à Android (voir MainActivity.kt).
///
/// Android filtre le broadcast UDP reçu tant qu'aucune application ne détient un
/// verrou multicast Wi-Fi : sans lui, les postes du réseau resteraient invisibles.
/// Hors Android (tests), les appels sont sans effet.
class NetworkPlatform {
  const NetworkPlatform();

  static const MethodChannel _channel = MethodChannel('socket_chat/network');

  Future<void> acquireMulticastLock() => _call('acquireMulticastLock');

  Future<void> releaseMulticastLock() => _call('releaseMulticastLock');

  /// Adresses de diffusion réelles des réseaux IPv4 de l'appareil.
  Future<List<String>> broadcastAddresses() async {
    try {
      final result = await _channel.invokeListMethod<String>('broadcastAddresses');
      return result ?? const [];
    } on MissingPluginException {
      return const [];
    } on PlatformException {
      return const [];
    }
  }

  /// Réseaux autorisés imposés par l'administrateur (configuration gérée par MDM,
  /// clé « allowed_networks ») ; `null` si aucune.
  Future<String?> managedAllowedNetworks() async {
    try {
      final value = await _channel.invokeMethod<String>('managedAllowedNetworks');
      return value == null || value.trim().isEmpty ? null : value;
    } on MissingPluginException {
      return null;
    } on PlatformException {
      return null;
    }
  }

  Future<void> _call(String method) async {
    try {
      await _channel.invokeMethod<void>(method);
    } on MissingPluginException {
      // Pas d'Android (tests) : rien à faire.
    } on PlatformException {
      // Verrou indisponible : la détection fonctionnera peut-être quand même.
    }
  }
}
