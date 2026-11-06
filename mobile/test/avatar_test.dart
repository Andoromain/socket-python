// Photo de profil côté Android : préparation, préférences, affichage, choix.

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:image_picker/image_picker.dart';
import 'package:socket_chat_mobile/app/avatar.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/ui/app.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';
import 'package:socket_chat_mobile/ui/widgets.dart';

import 'ui_test.dart' show alice, makeController, populate;

Uint8List pngPhoto({int width = 1600, int height = 900, bool transparent = false}) {
  final image = img.Image(width: width, height: height, numChannels: transparent ? 4 : 3);
  image.clear(transparent ? img.ColorRgba8(0, 0, 0, 0) : img.ColorRgb8(192, 57, 43));
  return img.encodePng(image);
}

void main() {
  test('photo recadrée en carré, réduite et compressée', () {
    final data = makeAvatar(pngPhoto());
    expect(isAvatarImage(data), isTrue);
    expect(data.sublist(0, 3), [0xff, 0xd8, 0xff]); // JPEG
    final decoded = img.decodeJpg(data)!;
    expect(decoded.width, avatarSide);
    expect(decoded.height, avatarSide);
    final center = decoded.getPixel(128, 128);
    expect(center.r, greaterThan(150)); // la couleur de la photo est conservée
    expect(center.g, lessThan(100));
  });

  test('image détaillée : la qualité baisse pour tenir dans 32 Kio', () {
    final noisy = img.Image(width: 1200, height: 1200);
    for (var y = 0; y < 1200; y++) {
      for (var x = 0; x < 1200; x++) {
        noisy.setPixelRgb(x, y, (x * 37 + y * 11) % 256, (x * y) % 256, (x ^ y) % 256);
      }
    }
    expect(makeAvatar(img.encodePng(noisy)).length, lessThanOrEqualTo(maxAvatarSize));
  });

  test('transparence posée sur fond blanc', () {
    final pixel = img.decodeJpg(makeAvatar(pngPhoto(width: 300, height: 300, transparent: true)))!.getPixel(10, 10);
    expect([pixel.r, pixel.g, pixel.b].every((c) => c > 240), isTrue);
  });

  test('fichier qui n\'est pas une image refusé', () {
    expect(() => makeAvatar(Uint8List.fromList('pas une image'.codeUnits)), throwsArgumentError);
  });

  test('la photo est conservée dans les préférences', () async {
    final prefs = MemoryPreferenceStore();
    final settings = await Settings.load(prefs);
    expect(settings.avatar, isEmpty);
    final data = makeAvatar(pngPhoto());
    settings.avatar = data;
    await settings.save();
    expect((await Settings.load(prefs)).avatar, data);
    settings.avatar = Uint8List(0);
    await settings.save();
    expect((await Settings.load(prefs)).avatar, isEmpty);
    prefs.values['avatar'] = 'pas du base64 !';
    expect((await Settings.load(prefs)).avatar, isEmpty); // valeur abîmée ignorée
  });

  testWidgets('photo du contact affichée, initiales sinon', (tester) async {
    final controller = await makeController();
    populate(controller.store);
    controller.store.setAvatar(alice, makeAvatar(pngPhoto()));
    await tester.pumpWidget(SocketChatApp(controller: controller));
    expect(find.bySemanticsLabel('Photo de Alice Martin'), findsOneWidget); // lue par TalkBack
    final avatars = tester.widgetList<Avatar>(find.byType(Avatar)).toList();
    expect(avatars.firstWhere((a) => a.name == 'Alice Martin').photo, isNotEmpty);
    expect(find.byType(Image), findsOneWidget); // seule Alice a une photo
    expect(find.text('BD'), findsOneWidget); // Bob : initiales
  });

  testWidgets('choisir puis retirer sa photo dans les préférences', (tester) async {
    final controller = await makeController();
    final photo = pngPhoto();
    ImageSource? asked;
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          controller: controller,
          pickImage: (source) async {
            asked = source;
            return photo;
          },
        ),
      ),
    );
    expect(find.byType(Image), findsNothing);
    await tester.tap(find.byIcon(Icons.photo_camera));
    await tester.pumpAndSettle();
    expect(find.text('Retirer la photo'), findsNothing);
    await tester.tap(find.text('Choisir dans la galerie'));
    await tester.pumpAndSettle();
    expect(asked, ImageSource.gallery);
    expect(isAvatarImage(controller.settings.avatar), isTrue);
    expect(controller.messenger.avatar, controller.settings.avatar);
    expect(find.byType(Image), findsOneWidget);

    await tester.tap(find.byIcon(Icons.photo_camera));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Retirer la photo'));
    await tester.pumpAndSettle();
    expect(controller.settings.avatar, isEmpty);
    expect(controller.messenger.avatar, isEmpty);
    expect(find.byType(Image), findsNothing);
  });

  testWidgets('image illisible : message, photo inchangée', (tester) async {
    final controller = await makeController();
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(controller: controller, pickImage: (_) async => Uint8List.fromList([1, 2, 3])),
      ),
    );
    await tester.tap(find.byIcon(Icons.photo_camera));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Prendre une photo'));
    await tester.pumpAndSettle();
    expect(find.text("Ce fichier n'est pas une image lisible."), findsOneWidget);
    expect(controller.settings.avatar, isEmpty);
  });
}
