// Captures d'écran de l'interface, à vérifier à l'œil (non comparées automatiquement).
//
// SOCKET_CHAT_SCREENSHOTS=/dossier flutter test --update-goldens test/screenshots

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:socket_chat_mobile/app/avatar.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/app.dart';

import '../ui_test.dart' show alice, bob, makeController, mid, populate;

final String? output = Platform.environment['SOCKET_CHAT_SCREENSHOTS'];

Future<void> loadFonts() async {
  final material = '${Platform.environment['FLUTTER_ROOT']}/bin/cache/artifacts/material_fonts';
  Future<ByteData> bytes(String path) async => ByteData.sublistView(File(path).readAsBytesSync());
  final roboto = FontLoader('Roboto');
  for (final weight in ['Regular', 'Medium', 'Bold', 'Italic']) {
    roboto.addFont(bytes('$material/Roboto-$weight.ttf'));
  }
  await roboto.load();
  await (FontLoader('MaterialIcons')..addFont(bytes('$material/MaterialIcons-Regular.otf'))).load();
  await (FontLoader('Noto Color Emoji')..addFont(bytes('/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'))).load();
}

ThemeData screenshotTheme(Brightness brightness) {
  final base = buildTheme(brightness);
  TextTheme withEmoji(TextTheme t) => t.apply(fontFamily: 'Roboto', fontFamilyFallback: const ['Noto Color Emoji']);
  return base.copyWith(textTheme: withEmoji(base.textTheme), primaryTextTheme: withEmoji(base.primaryTextTheme));
}

/// Capture avec de vraies ombres (les tests les dessinent sinon en noir opaque).
Future<void> capture(WidgetTester tester, Finder finder, String path) async {
  // Le décodage des images est asynchrone : on l'attend pour que les photos apparaissent.
  await tester.runAsync(() async {
    for (final element in find.byType(Image).evaluate()) {
      await precacheImage((element.widget as Image).image, element);
    }
  });
  await tester.pump();
  debugDisableShadows = false;
  try {
    for (final view in tester.binding.renderViews) {
      view.markNeedsPaint();
    }
    await tester.pump();
    await expectLater(finder, matchesGoldenFile(Uri.file(path)));
  } finally {
    debugDisableShadows = true;
    for (final view in tester.binding.renderViews) {
      view.markNeedsPaint();
    }
    await tester.pump();
  }
}

/// Portrait stylisé (dégradé, visage, épaules) pour les captures.
Uint8List portrait(img.Color top, img.Color bottom) {
  final image = img.Image(width: 400, height: 400);
  for (var y = 0; y < 400; y++) {
    final t = y / 400;
    final color = img.ColorRgb8(
      (top.r * (1 - t) + bottom.r * t).round(),
      (top.g * (1 - t) + bottom.g * t).round(),
      (top.b * (1 - t) + bottom.b * t).round(),
    );
    img.drawLine(image, x1: 0, y1: y, x2: 399, y2: y, color: color);
  }
  img.fillCircle(image, x: 200, y: 420, radius: 150, color: img.ColorRgb8(40, 60, 90));
  img.fillCircle(image, x: 200, y: 170, radius: 90, color: img.ColorRgb8(241, 194, 125));
  img.fillCircle(image, x: 200, y: 110, radius: 70, color: img.ColorRgb8(59, 42, 26));
  img.fillCircle(image, x: 200, y: 185, radius: 75, color: img.ColorRgb8(241, 194, 125));
  return makeAvatar(img.encodePng(image));
}

void richHistory(Store store) {
  populate(store);
  store.setAvatar(alice, portrait(img.ColorRgb8(109, 213, 237), img.ColorRgb8(33, 147, 176)));
  final now = DateTime.now().millisecondsSinceEpoch;
  const carol = '33333333-3333-4333-8333-333333333333';
  store.upsertContact(carol, 'Chloé Bernard', lastSeen: now - 3 * 86400000);
  store.setAvatar(carol, portrait(img.ColorRgb8(247, 151, 30), img.ColorRgb8(255, 210, 0)));
  store.addMessage(MessageRecord(mid(20), carol, true, 'Bon week-end ! 🎉', now - 3 * 86400000, Status.read));
  store.addMessage(
    MessageRecord(mid(10), alice, true, 'Je prends le rétroprojecteur 📽️', now - 3000, Status.delivered),
  );
  store.addMessage(
    MessageRecord(mid(11), alice, true, 'Et le compte rendu de la semaine dernière', now - 2000, Status.sent),
  );
  store.setReaction(mid(3), me, '👍');
  store.markIncomingRead(bob);
}

void main() {
  for (final (name, mode) in [('clair', ThemeMode.light), ('sombre', ThemeMode.dark)]) {
    testWidgets('captures $name', (tester) async {
      if (output == null) return;
      await tester.runAsync(loadFonts);
      tester.view.physicalSize = const Size(1080, 2340);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      final controller = await makeController(name: 'Jean Dupont');
      richHistory(controller.store);
      await tester.pumpWidget(SocketChatApp(controller: controller, theme: screenshotTheme, themeMode: mode));
      await tester.pumpAndSettle();
      Future<void> shot(String file) => capture(tester, find.byType(MaterialApp), '$output/$file-$name.png');
      await shot('1-contacts');
      await tester.tap(find.text('Alice Martin'));
      await tester.pumpAndSettle();
      await shot('2-conversation');
      await tester.longPress(find.text('Salut ! Réunion à 14 h ?'));
      await tester.pumpAndSettle();
      await shot('3-reactions');
      await tester.tapAt(const Offset(200, 100));
      await tester.pumpAndSettle();
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.tap(find.text('Bob Durand'));
      await tester.pumpAndSettle();
      await shot('4-fichier');
    });
  }

  testWidgets('capture bienvenue', (tester) async {
    if (output == null) return;
    await tester.runAsync(loadFonts);
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 2.75;
    addTearDown(tester.view.reset);
    final controller = await makeController(name: null);
    await tester.pumpWidget(SocketChatApp(controller: controller, theme: screenshotTheme, themeMode: ThemeMode.light));
    await tester.pumpAndSettle();
    await capture(tester, find.byType(MaterialApp), '$output/0-bienvenue.png');
  });

  testWidgets('capture préférences avec photo', (tester) async {
    if (output == null) return;
    await tester.runAsync(loadFonts);
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 2.75;
    addTearDown(tester.view.reset);
    final controller = await makeController(name: 'Jean Dupont');
    controller.settings.avatar = portrait(img.ColorRgb8(168, 224, 99), img.ColorRgb8(86, 171, 47));
    controller.messenger.setAvatar(controller.settings.avatar);
    await tester.pumpWidget(
      MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: screenshotTheme(Brightness.light),
        home: SettingsScreen(controller: controller),
      ),
    );
    await tester.pumpAndSettle();
    await capture(tester, find.byType(MaterialApp), '$output/5-preferences.png');
  });
}
