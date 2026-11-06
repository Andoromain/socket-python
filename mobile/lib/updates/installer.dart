import 'package:flutter/services.dart';

/// Installation de l'APK téléchargé (voir MainActivity.kt, canal « socket_chat/update »).
abstract interface class ApkInstaller {
  /// Android 8+ : Socket Chat est-elle autorisée à installer des applications ?
  Future<bool> canInstall();

  /// Ouvre l'écran des réglages où l'utilisateur donne cette autorisation.
  Future<void> openInstallSettings();

  /// Ouvre l'installateur d'Android sur [path] ; `false` si le fichier est refusé.
  Future<bool> install(String path);
}

class PlatformApkInstaller implements ApkInstaller {
  const PlatformApkInstaller();

  static const MethodChannel _channel = MethodChannel('socket_chat/update');

  @override
  Future<bool> canInstall() async => await _channel.invokeMethod<bool>('canInstall') ?? false;

  @override
  Future<void> openInstallSettings() => _channel.invokeMethod<void>('openInstallSettings');

  @override
  Future<bool> install(String path) async => await _channel.invokeMethod<bool>('installApk', {'path': path}) ?? false;
}
