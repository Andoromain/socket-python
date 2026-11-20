import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'app/controller.dart';
import 'app/history.dart';
import 'app/secrets.dart';
import 'app/settings.dart';
import 'ui/app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  const secrets = KeystoreSecretStore();
  final settings = await Settings.load(SharedPreferenceStore(), secrets);
  final directory = await getApplicationSupportDirectory();
  final (store, warning) = await openHistory(directory.path, secrets);
  final controller = ChatController(store, settings)..notice = warning;
  runApp(SocketChatApp(controller: controller));
  await controller.start();
}
