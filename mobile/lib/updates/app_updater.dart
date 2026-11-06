import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'installer.dart';
import 'updater.dart';

/// Intervalle minimal entre deux vérifications automatiques.
const Duration checkInterval = Duration(hours: 6);

/// État de la mise à jour de l'application, affiché par le bandeau et les préférences.
class AppUpdater extends ChangeNotifier {
  AppUpdater({
    required this.currentVersion,
    required this.client,
    required this.installer,
    required this.downloadDirectory,
    this.manifestUrl = defaultUpdateUrl,
  });

  final String currentVersion;
  final UpdateClient client;
  final ApkInstaller installer;

  /// Dossier des APK téléchargés (doit être `cache/updates` sur Android : FileProvider).
  final Future<Directory> Function() downloadDirectory;
  final String manifestUrl;

  /// Version plus récente disponible pour Android, ou `null`.
  Manifest? available;

  /// L'utilisateur a choisi « Plus tard » pour cette version.
  bool dismissed = false;

  /// Progression du téléchargement (0 à 1), `null` hors téléchargement.
  double? progress;

  /// Dernière erreur à afficher, ou `null`.
  String? error;

  /// Android attend l'autorisation d'installer des applications.
  bool needsPermission = false;

  bool checking = false;
  File? _downloaded;
  DateTime? _lastCheck;

  bool get busy => checking || progress != null;
  bool get showBanner => available != null && !dismissed;

  /// Vérifie la présence d'une nouvelle version ; `true` si une version est proposée.
  ///
  /// Automatique ([manual] faux) : au plus toutes les [checkInterval], erreurs silencieuses
  /// (pas de réseau, pas d'Internet sur le Wi-Fi interne…). À la demande : erreur affichée.
  Future<bool> check({bool manual = false}) async {
    final now = DateTime.now();
    if (busy) return showBanner;
    if (!manual && _lastCheck != null && now.difference(_lastCheck!) < checkInterval) return showBanner;
    _lastCheck = now;
    checking = true;
    if (manual) error = null;
    notifyListeners();
    try {
      final manifest = await client.fetchManifest(manifestUrl);
      final apk = manifest.android;
      if (apk != null && isNewer(manifest.version, currentVersion)) {
        if (available?.version != manifest.version) {
          available = manifest;
          dismissed = false;
          _downloaded = null;
        }
        if (manual) dismissed = false;
      } else {
        available = null;
      }
    } on UpdateException catch (exc) {
      if (manual) error = exc.message;
    } finally {
      checking = false;
      notifyListeners();
    }
    return showBanner;
  }

  void dismiss() {
    dismissed = true;
    notifyListeners();
  }

  /// Télécharge (si besoin) et vérifie l'APK, puis ouvre l'installateur d'Android.
  Future<void> install() async {
    final manifest = available;
    final apk = manifest?.android;
    if (apk == null || busy) return;
    error = null;
    try {
      var file = _downloaded;
      if (file == null || !file.existsSync()) {
        progress = 0;
        notifyListeners();
        final directory = await downloadDirectory();
        file = await client.download(
          apk,
          directory,
          onProgress: (done, total) {
            progress = total == 0 ? 1 : done / total;
            notifyListeners();
          },
        );
        _downloaded = file;
      }
      progress = null;
      if (!await installer.canInstall()) {
        needsPermission = true;
        notifyListeners();
        await installer.openInstallSettings();
        return;
      }
      needsPermission = false;
      if (!await installer.install(file.path)) error = "L'installation n'a pas pu être lancée.";
    } on UpdateException catch (exc) {
      error = exc.message;
    } on PlatformException catch (exc) {
      error = "L'installation n'a pas pu être lancée : ${exc.message ?? exc.code}.";
    } on MissingPluginException {
      error = "L'installation n'est possible que sur Android.";
    } finally {
      progress = null;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    client.close();
    super.dispose();
  }
}
