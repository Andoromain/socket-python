// Mise à jour intégrée : logique (AppUpdater) et interface (bandeau, préférences).

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/app.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';
import 'package:socket_chat_mobile/updates/app_updater.dart';
import 'package:socket_chat_mobile/updates/installer.dart';
import 'package:socket_chat_mobile/updates/updater.dart';

Manifest manifest(String version, {bool android = true, String notes = '### Ajouté\n- **Mise à jour** intégrée.'}) =>
    Manifest(version, '2026-11-06', notes, {
      if (android)
        'android': Asset(
          'socket-chat-$version-android.apk',
          3,
          'a' * 64,
          Uri.parse('https://exemple.fr/socket-chat-$version-android.apk'),
        ),
    });

/// Serveur de mise à jour simulé.
class FakeClient extends UpdateClient {
  FakeClient(this.next);
  Manifest Function() next;
  int fetches = 0;
  int downloads = 0;
  UpdateException? downloadError;

  @override
  Future<Manifest> fetchManifest([String url = defaultUpdateUrl]) async {
    fetches++;
    return next();
  }

  @override
  Future<File> download(Asset asset, Directory directory, {void Function(int, int)? onProgress}) async {
    downloads++;
    onProgress?.call(1, 3);
    onProgress?.call(3, 3);
    if (downloadError case final error?) throw error;
    directory.createSync(recursive: true);
    return File('${directory.path}/${asset.name}')..writeAsBytesSync([1, 2, 3]);
  }
}

class FakeInstaller implements ApkInstaller {
  bool allowed = true;
  final installed = <String>[];
  int settingsOpened = 0;
  @override
  Future<bool> canInstall() async => allowed;
  @override
  Future<void> openInstallSettings() async => settingsOpened++;
  @override
  Future<bool> install(String path) async {
    installed.add(path);
    return true;
  }
}

void main() {
  late Directory tmp;
  setUp(() => tmp = Directory.systemTemp.createTempSync('socket-chat-app-maj-'));
  tearDown(() => tmp.deleteSync(recursive: true));

  AppUpdater makeUpdater(FakeClient client, FakeInstaller installer, {String current = '3.5.0'}) => AppUpdater(
    currentVersion: current,
    client: client,
    installer: installer,
    downloadDirectory: () async => Directory('${tmp.path}/updates'),
  );

  test('version plus récente proposée, sinon rien', () async {
    final client = FakeClient(() => manifest('3.6.0'));
    final updater = makeUpdater(client, FakeInstaller());
    expect(await updater.check(), isTrue);
    expect(updater.available!.version, '3.6.0');

    client.next = () => manifest('3.5.0');
    expect(await updater.check(manual: true), isFalse);
    expect(updater.available, isNull);

    client.next = () => manifest('3.7.0', android: false); // version PC seulement
    expect(await updater.check(manual: true), isFalse);
  });

  test('vérification automatique au plus toutes les 6 h, erreurs silencieuses', () async {
    final client = FakeClient(() => throw UpdateException('Serveur injoignable'));
    final updater = makeUpdater(client, FakeInstaller());
    await updater.check();
    expect(updater.error, isNull); // automatique : silencieuse
    await updater.check();
    expect(client.fetches, 1); // pas avant 6 h
    await updater.check(manual: true);
    expect(client.fetches, 2);
    expect(updater.error, 'Serveur injoignable'); // à la demande : affichée
  });

  test('« Plus tard » masque la version, une version suivante réapparaît', () async {
    final client = FakeClient(() => manifest('3.6.0'));
    final updater = makeUpdater(client, FakeInstaller());
    await updater.check();
    updater.dismiss();
    expect(updater.showBanner, isFalse);
    await updater.check(manual: true); // demande explicite : de nouveau proposée
    expect(updater.showBanner, isTrue);
    updater.dismiss();
    client.next = () => manifest('3.7.0');
    await updater.check(manual: true);
    expect(updater.showBanner, isTrue);
    expect(updater.available!.version, '3.7.0');
  });

  test('téléchargement puis installation', () async {
    final client = FakeClient(() => manifest('3.6.0'));
    final installer = FakeInstaller();
    final updater = makeUpdater(client, installer);
    await updater.check();
    final progress = <double?>[];
    updater.addListener(() => progress.add(updater.progress));
    await updater.install();
    expect(progress, containsAllInOrder([0, closeTo(1 / 3, 0.01), 1.0]));
    expect(installer.installed.single, endsWith('/updates/socket-chat-3.6.0-android.apk'));
    expect(updater.progress, isNull);
    expect(updater.error, isNull);
  });

  test('autorisation manquante : réglages ouverts, puis installation sans retélécharger', () async {
    final client = FakeClient(() => manifest('3.6.0'));
    final installer = FakeInstaller()..allowed = false;
    final updater = makeUpdater(client, installer);
    await updater.check();
    await updater.install();
    expect(installer.settingsOpened, 1);
    expect(updater.needsPermission, isTrue);
    expect(installer.installed, isEmpty);

    installer.allowed = true; // l'utilisateur a donné l'autorisation
    await updater.install();
    expect(client.downloads, 1);
    expect(installer.installed, hasLength(1));
    expect(updater.needsPermission, isFalse);
  });

  test('téléchargement refusé : erreur affichée, rien installé', () async {
    final client = FakeClient(() => manifest('3.6.0'))
      ..downloadError = UpdateException('Empreinte du fichier incorrecte : téléchargement refusé.');
    final installer = FakeInstaller();
    final updater = makeUpdater(client, installer);
    await updater.check();
    await updater.install();
    expect(updater.error, contains('Empreinte'));
    expect(installer.installed, isEmpty);
    expect(updater.busy, isFalse);
  });

  group('interface', () {
    Future<(ChatController, AppUpdater, FakeInstaller)> setup() async {
      final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
      final installer = FakeInstaller();
      final updater = makeUpdater(FakeClient(() => manifest('3.6.0')), installer);
      final controller = ChatController(
        Store(),
        await Settings.load(prefs),
        useNetwork: false,
        useDiscovery: false,
        updater: updater,
      );
      return (controller, updater, installer);
    }

    testWidgets('bandeau, nouveautés, plus tard', (tester) async {
      final (controller, updater, _) = await setup();
      await tester.pumpWidget(SocketChatApp(controller: controller));
      expect(find.textContaining('est disponible'), findsNothing);

      await tester.runAsync(updater.check);
      await tester.pump();
      expect(find.text('Socket Chat 3.6.0 est disponible'), findsOneWidget);

      await tester.tap(find.text('Nouveautés'));
      await tester.pumpAndSettle();
      expect(find.text('Nouveautés de la 3.6.0'), findsOneWidget);
      expect(find.text('AJOUTÉ\n• Mise à jour intégrée.'), findsOneWidget);
      await tester.tap(find.text('Fermer'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Plus tard'));
      await tester.pump();
      expect(find.text('Socket Chat 3.6.0 est disponible'), findsNothing);
    });

    testWidgets('« Mettre à jour » télécharge et lance l\'installation', (tester) async {
      final (controller, updater, installer) = await setup();
      await tester.pumpWidget(SocketChatApp(controller: controller));
      await tester.runAsync(updater.check);
      await tester.pump();
      await tester.tap(find.text('Mettre à jour'));
      await tester.runAsync(() async {
        while (installer.installed.isEmpty) {
          await Future<void>.delayed(const Duration(milliseconds: 10));
        }
      });
      await tester.pump();
      expect(installer.installed, hasLength(1));
    });

    testWidgets('préférences : rechercher une mise à jour', (tester) async {
      final (controller, updater, _) = await setup();
      await tester.pumpWidget(MaterialApp(home: SettingsScreen(controller: controller)));
      await tester.scrollUntilVisible(
        find.text('Rechercher une mise à jour'),
        200,
        scrollable: find.descendant(of: find.byType(ListView), matching: find.byType(Scrollable)).first,
      );
      await tester.tap(find.text('Rechercher une mise à jour'));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump();
      expect(find.textContaining('Socket Chat 3.6.0 est disponible'), findsOneWidget); // message
      expect(updater.available!.version, '3.6.0');
    });
  });
}
