import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'app/controller.dart';
import 'app/settings.dart';
import 'store/store.dart';
import 'ui/app.dart';
import 'updates/app_updater.dart';
import 'updates/installer.dart';
import 'updates/updater.dart';
import 'version.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await Settings.load(SharedPreferenceStore());
  final directory = await getApplicationSupportDirectory();
  final store = Store('${directory.path}/history.sqlite3');
  final updater = AppUpdater(
    currentVersion: appVersion,
    client: UpdateClient(),
    installer: const PlatformApkInstaller(),
    // cache/updates : seul dossier partagé avec l'installateur (res/xml/update_paths.xml).
    downloadDirectory: () async => Directory('${(await getTemporaryDirectory()).path}/updates'),
  );
  final controller = ChatController(store, settings, updater: updater);
  // Le moteur démarre avec le processus, souvent sans écran (application fermée, démarrage
  // du téléphone) : rien n'est « vu » tant qu'Android n'a pas affiché l'écran.
  controller.setVisible(false);
  runApp(SocketChatApp(controller: controller));
  await controller.start();
}
