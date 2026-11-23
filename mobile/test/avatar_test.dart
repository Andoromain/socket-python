// Photo de profil (transposition de tests/test_profile.py).

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:socket_chat_mobile/app/avatar.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';

import 'helpers.dart';

Uint8List sampleImage({int width = 1200, int height = 800}) {
  final image = img.Image(width: width, height: height, numChannels: 4);
  for (var x = 0; x < width; x += 7) {
    img.drawLine(image, x1: x, y1: 0, x2: width - x, y2: height, color: img.ColorRgb8(x % 256, (x * 3) % 256, 90));
  }
  return img.encodePng(image);
}

void main() {
  test('photo carrée, JPEG de moins de 32 Kio', () {
    final avatar = makeAvatar(sampleImage());
    expect(isJpeg(avatar), isTrue);
    expect(avatar.length, inInclusiveRange(1, maxAvatarSize));
    final decoded = img.decodeJpg(avatar)!;
    expect(decoded.width, decoded.height);
    expect(decoded.width, lessThanOrEqualTo(avatarSide));
    expect(makeAvatar(sampleImage(width: 300, height: 900)), isNotEmpty);
    expect(makeAvatar([1, 2, 3]), isEmpty);
  });

  test('stockage de la photo d’un contact', () {
    final store = Store();
    const id = '11111111-1111-4111-8111-111111111111';
    store.upsertContact(id, 'Alice');
    expect(store.contact(id)!.avatar, isNull);
    expect(store.setAvatar(id, [0xff, 0xd8, 0xff, 1]), isTrue);
    expect(store.setAvatar(id, [0xff, 0xd8, 0xff, 1]), isFalse);
    expect(store.contacts().single.contact.avatar, [0xff, 0xd8, 0xff, 1]);
    expect(store.setAvatar(id, const []), isTrue);
    expect(store.contact(id)!.avatar, isNull);
  });

  group('envoi aux contacts', () {
    late Messengers messengers;
    setUp(() => messengers = Messengers());
    tearDown(() => messengers.dispose());

    test('envoyée à la connexion, mise à jour, retirée', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      final photo = makeAvatar(sampleImage());
      alice.setAvatar(photo);
      await link(alice, bob);
      await waitUntil(() => bob.store.contact(alice.userId)?.avatar != null);
      expect(bob.store.contact(alice.userId)!.avatar, photo);

      final other = makeAvatar(sampleImage(width: 300, height: 300));
      alice.setAvatar(other);
      await waitUntil(() => _same(bob.store.contact(alice.userId)?.avatar, other));
      alice.setAvatar(const []);
      await waitUntil(() => bob.store.contact(alice.userId)!.avatar == null);
    });
  });

  test('préférences : photo enregistrée', () async {
    final prefs = MemoryPreferenceStore();
    final settings = await Settings.load(prefs);
    expect(settings.avatar, isEmpty);
    settings.avatar = Uint8List.fromList([0xff, 0xd8, 0xff, 7]);
    await settings.save();
    expect((await Settings.load(prefs)).avatar, [0xff, 0xd8, 0xff, 7]);
  });

  testWidgets('écran Préférences : choisir puis retirer la photo', (tester) async {
    final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
    final settings = await tester.runAsync(() => Settings.load(prefs));
    final controller = ChatController(Store(), settings!, useNetwork: false, useDiscovery: false);
    final picked = sampleImage(width: 200, height: 200);
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(controller: controller, pickPhoto: () async => picked),
      ),
    );
    expect(find.text('Retirer'), findsNothing);
    await tester.runAsync(() async {
      await tester.tap(find.text('Choisir une photo'));
      await Future<void>.delayed(const Duration(milliseconds: 300));
    });
    await tester.pump();
    expect(isJpeg(controller.settings.avatar), isTrue);
    expect(controller.messenger.avatar, controller.settings.avatar);
    expect(find.text('Retirer'), findsOneWidget);

    await tester.runAsync(() async {
      await tester.tap(find.text('Retirer'));
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });
    await tester.pump();
    expect(controller.settings.avatar, isEmpty);
    expect(controller.messenger.avatar, isEmpty);
  });

  testWidgets('image illisible signalée', (tester) async {
    final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
    final settings = await tester.runAsync(() => Settings.load(prefs));
    final controller = ChatController(Store(), settings!, useNetwork: false, useDiscovery: false);
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(controller: controller, pickPhoto: () async => [1, 2, 3]),
      ),
    );
    await tester.tap(find.text('Choisir une photo'));
    await tester.pump();
    await tester.pump();
    expect(find.text('Image illisible.'), findsOneWidget);
    expect(controller.settings.avatar, isEmpty);
  });
}

bool _same(List<int>? a, List<int> b) {
  if (a == null || a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}
