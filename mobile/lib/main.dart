import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'app/controller.dart';
import 'app/settings.dart';
import 'store/store.dart';
import 'ui/app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await Settings.load(SharedPreferenceStore());
  final directory = await getApplicationSupportDirectory();
  final store = Store('${directory.path}/history.sqlite3');
  final controller = ChatController(store, settings);
  runApp(SocketChatApp(controller: controller));
  await controller.start();
}
