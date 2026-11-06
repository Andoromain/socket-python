import 'package:flutter/services.dart';

/// Sonnerie et écran pendant les appels (voir MainActivity.kt, canal « socket_chat/calls »).
/// Hors Android (tests), les appels sont sans effet.
class CallPlatform {
  const CallPlatform();

  static const MethodChannel _channel = MethodChannel('socket_chat/calls');

  /// Sonnerie et vibration du téléphone (selon son mode : normal, vibreur, silencieux).
  Future<void> startRinging() => _call('startRinging');

  Future<void> stopRinging() => _call('stopRinging');

  /// Éteint l'écran contre l'oreille (appel sur l'écouteur).
  Future<void> setProximity(bool on) => _call('setProximity', {'on': on});

  Future<void> _call(String method, [Map<String, Object?>? arguments]) async {
    try {
      await _channel.invokeMethod<void>(method, arguments);
    } on MissingPluginException {
      // Pas d'Android (tests).
    } on PlatformException {
      // Fonction indisponible sur cet appareil : l'appel continue sans elle.
    }
  }
}
