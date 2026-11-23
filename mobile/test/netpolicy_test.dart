// Réseaux autorisés : règles (transposition de tests/test_netpolicy.py), filtrage des
// connexions et des annonces, réglage de l'appareil et configuration de l'administrateur.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/platform.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/net/discovery.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/net/netpolicy.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';

import 'helpers.dart';

/// Toute adresse est « extérieure » dès qu'une liste est fixée (la boucle locale étant
/// toujours permise, on simule des postes hors des réseaux autorisés).
bool strict(String address, List<Network> networks) => networks.isEmpty;

class ManagedPlatform extends NetworkPlatform {
  const ManagedPlatform(this.value);
  final String? value;

  @override
  Future<String?> managedAllowedNetworks() async => value;
}

void main() {
  test('analyse des réseaux', () {
    final networks = parseNetworks(' 10.20.0.0/16 ; 192.168.1.7, fd00::/8,, pas-un-réseau, 10.1.2.3/8, 1.2.3.4/40');
    expect(networks.map((n) => n.toString()), ['10.20.0.0/16', '192.168.1.7/32', 'fd00::/8', '10.0.0.0/8']);
  });

  test('appartenance', () {
    final networks = parseNetworks('10.20.0.0/16, fd00::/8');
    expect(isAllowed('10.20.3.4', networks), isTrue);
    expect(isAllowed('::ffff:10.20.3.4', networks), isTrue);
    expect(isAllowed('fd12::1', networks), isTrue);
    expect(isAllowed('10.21.0.1', networks), isFalse);
    expect(isAllowed('192.168.1.10', networks), isFalse);
    expect(isAllowed('::ffff:192.168.1.10', networks), isFalse);
    expect(isAllowed('2001:db8::1', networks), isFalse);
    expect(isAllowed('fe80::1%wlan0', networks), isFalse);
    expect(isAllowed('nom-de-poste', networks), isFalse);
    expect(isAllowed('127.0.0.1', networks), isTrue);
    expect(isAllowed('::1', networks), isTrue);
    expect(isAllowed('8.8.8.8', const []), isTrue);
    expect(isAllowed('192.168.1.200', parseNetworks('192.168.1.0/25')), isFalse);
    expect(isAllowed('192.168.1.100', parseNetworks('192.168.1.0/25')), isTrue);
  });

  group('filtrage', () {
    late Messengers messengers;
    setUp(() => messengers = Messengers());
    tearDown(() => messengers.dispose());

    test('connexion entrante hors réseaux refusée', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      bob
        ..addressPolicy = strict
        ..allowedNetworks = parseNetworks('10.20.0.0/16');
      final error = next(alice.error);
      alice.addContact('127.0.0.1', bob.port!);
      expect(await error, contains('Impossible de joindre'));
      expect(alice.store.contacts(), isEmpty);
      expect(bob.store.contacts(), isEmpty);
    });

    test('connexion sortante hors réseaux fermée', () async {
      final alice = await messengers.make('Alice');
      final bob = await messengers.make('Bob');
      alice
        ..addressPolicy = strict
        ..allowedNetworks = parseNetworks('10.20.0.0/16');
      final error = next(alice.error);
      alice.addContact('127.0.0.1', bob.port!);
      expect(await error, networkError);
      await pause(200);
      expect(alice.store.contacts(), isEmpty);
    });

    test('annonces hors réseaux ignorées', () async {
      Future<Discovery> make(String name) async {
        final d = Discovery(newId(), name, 1, udpPort: 0, targets: const []);
        expect(await d.start(), isTrue);
        return d;
      }

      final alice = await make('Alice');
      final bob = await make('Bob');
      addTearDown(() {
        alice.stop();
        bob.stop();
      });
      alice.targets = [('127.0.0.1', bob.localPort)];
      bob.targets = [('127.0.0.1', alice.localPort)];
      bob
        ..addressPolicy = strict
        ..allowedNetworks = parseNetworks('10.20.0.0/16');
      final seenByBob = <String>[];
      bob.peerSeen.connect((p) => seenByBob.add(p.$1));
      alice.announce();
      final seenByAlice = next(alice.peerSeen);
      bob.announce(); // témoin : les annonces circulent bien (Alice ne filtre pas)
      await seenByAlice;
      await pause(100);
      expect(seenByBob, isEmpty);
    });
  });

  group('réglage', () {
    Future<ChatController> controller({String? managed}) async {
      final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
      final settings = await Settings.load(prefs);
      return ChatController(Store(), settings, useDiscovery: false, platform: ManagedPlatform(managed));
    }

    test('préférences de l’appareil enregistrées et appliquées', () async {
      final c = await controller();
      await c.start();
      addTearDown(c.stop);
      expect(c.messenger.allowedNetworks, isEmpty);
      await c.applySettings(name: 'Moi', port: c.settings.port, allowedNetworks: ' 10.20.0.0/16 ');
      expect(c.settings.allowedNetworks, '10.20.0.0/16');
      expect(c.messenger.allowedNetworks.map((n) => '$n'), ['10.20.0.0/16']);
    });

    test("la configuration de l'administrateur l'emporte", () async {
      final c = await controller(managed: '192.168.1.0/24');
      c.settings.allowedNetworks = '10.0.0.0/8';
      await c.start();
      addTearDown(c.stop);
      expect(c.allowedNetworks, '192.168.1.0/24');
      expect(c.messenger.allowedNetworks.map((n) => '$n'), ['192.168.1.0/24']);
    });

    testWidgets('champ des préférences : validation, verrouillé par l’administrateur', (tester) async {
      expect(networksError('10.20.0.0/16, 192.168.1.0/24'), isNull);
      expect(networksError(''), isNull);
      expect(networksError('10.20.0.0/16, n’importe quoi'), isNotNull);

      final c = await tester.runAsync(() => controller(managed: '192.168.1.0/24'));
      c!.managedNetworks = '192.168.1.0/24';
      await tester.pumpWidget(MaterialApp(home: SettingsScreen(controller: c)));
      final field = tester.widget<TextField>(
        find.descendant(of: find.byKey(const Key('allowed-networks')), matching: find.byType(TextField)),
      );
      expect(field.enabled, isFalse);
      expect(find.text('Fixé par votre administrateur.'), findsOneWidget);
    });
  });
}
