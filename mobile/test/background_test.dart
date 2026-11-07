// Application fermée : notifications des messages et des appels, actions des
// notifications, service au premier plan et Préférences (plate-forme Android simulée).

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/background.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/net/calls.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/app.dart';
import 'package:socket_chat_mobile/ui/settings_screen.dart';

import 'helpers.dart';

/// Plate-forme Android simulée : note les appels reçus.
class FakeBackground extends BackgroundPlatform {
  final calls = <String>[];
  final notified = <(String, String, List<String>, int)>[];
  BackgroundStatus current = const BackgroundStatus();
  void Function(BackgroundAction)? onAction;

  @override
  void listen(void Function(BackgroundAction action) onAction) => this.onAction = onAction;
  @override
  Future<void> setKeepRunning(bool on) async => calls.add('keepRunning:$on');
  @override
  Future<void> setInCall(bool on, {bool video = false}) async => calls.add('inCall:$on${video ? ':video' : ''}');
  @override
  Future<void> setCallMode(bool on) async => calls.add('callMode:$on');
  @override
  Future<void> notifyMessages(String contactId, String title, List<String> lines, int count) async =>
      notified.add((contactId, title, lines, count));
  @override
  Future<void> cancelMessages(String contactId) async => calls.add('cancelMessages');
  @override
  Future<void> showIncomingCall(String callId, String contactId, String name, {bool video = false}) async =>
      calls.add('incoming:$name${video ? ':video' : ''}');
  @override
  Future<void> cancelIncomingCall() async => calls.add('cancelIncoming');
  @override
  Future<BackgroundStatus> status() async => current;
  @override
  Future<void> requestNotificationPermission() async => calls.add('askNotifications');
  @override
  Future<void> openNotificationSettings() async => calls.add('openNotifications');
  @override
  Future<void> openFullScreenSettings() async => calls.add('openFullScreen');
  @override
  Future<void> requestBatteryUnrestricted() async => calls.add('battery');
}

Future<int> freePort() async {
  final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
  final port = server.port;
  await server.close();
  return port;
}

void main() {
  late Messengers messengers;
  late ChatController controller;
  late FakeBackground background;
  late Messenger peer;
  late CallManager peerCalls;

  Future<void> setUpPeer(WidgetTester tester, {bool start = true}) async {
    FakeEngine.instances.clear();
    messengers = Messengers();
    background = FakeBackground();
    final prefs = MemoryPreferenceStore()
      ..values['name'] = 'Moi'
      ..values['port'] = await tester.runAsync(freePort) as int;
    controller = ChatController(
      Store(),
      await Settings.load(prefs),
      useDiscovery: false,
      engineFactory: FakeEngine.new,
      background: background,
    );
    await tester.runAsync(() async {
      await controller.start();
      expect(controller.running, isTrue);
      peer = await messengers.make('Bob Durand');
      peerCalls = CallManager(peer, FakeEngine.new);
      await link(controller.messenger, peer);
    });
  }

  Future<void> tearDownPeer(WidgetTester tester) async {
    await tester.runAsync(() async {
      controller.messenger.stop();
      await messengers.dispose();
    });
  }

  Future<void> network(WidgetTester tester, bool Function() condition) async {
    await tester.runAsync(() => waitUntil(condition));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
  }

  testWidgets('démarrage : service au premier plan et autorisation des notifications', (tester) async {
    await setUpPeer(tester);
    expect(background.calls, ['keepRunning:true', 'askNotifications']);
    await tearDownPeer(tester);
  });

  testWidgets('message reçu application fermée : notifié, puis retiré à la lecture', (tester) async {
    await setUpPeer(tester);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    controller.setVisible(false);
    await tester.runAsync(() async {
      peer.send(controller.messenger.userId, 'Salut !');
      peer.send(controller.messenger.userId, 'Tu es là ?');
      await waitUntil(() => background.notified.length == 2);
    });
    final (contact, title, lines, count) = background.notified.last;
    expect(
      [contact, title, lines, count],
      [
        peer.userId,
        'Bob Durand',
        ['Salut !', 'Tu es là ?'],
        2,
      ],
    );

    // Toucher la notification : la conversation s'ouvre et les messages sont lus.
    controller.setVisible(true);
    background.onAction!(OpenConversation(peer.userId));
    await tester.pumpAndSettle();
    expect(find.text('Tu es là ?'), findsOneWidget);
    expect(controller.store.totalUnread(), 0);
    expect(background.calls, contains('cancelMessages'));

    // Écran affiché sur cette conversation : pas de notification.
    background.notified.clear();
    await tester.runAsync(() async {
      peer.send(controller.messenger.userId, 'Parfait');
      await waitUntil(() => controller.store.messages(peer.userId).length == 3);
      await pause(50);
    });
    expect(background.notified, isEmpty);
    await tearDownPeer(tester);
  });

  testWidgets('appel entrant application fermée : notification, « Répondre », fin', (tester) async {
    await setUpPeer(tester);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    controller.setVisible(false);
    background.calls.clear();
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId));
    await network(tester, () => controller.calls.call?.state == CallState.incoming);
    expect(background.calls, ['incoming:Bob Durand', 'callMode:true']);

    // « Répondre » ouvre l'écran (Android) puis transmet l'action.
    controller.setVisible(true);
    background.onAction!(AnswerCall(controller.calls.call!.id));
    await network(tester, () => controller.calls.call?.state == CallState.active);
    expect(background.calls, containsAllInOrder(['cancelIncoming', 'inCall:true']));
    expect(find.byKey(const ValueKey('call-hangup')), findsOneWidget);

    await tester.runAsync(() async => peerCalls.hangup());
    await network(tester, () => controller.calls.call == null);
    await tester.pumpAndSettle();
    expect(background.calls, containsAllInOrder(['inCall:false', 'callMode:false']));
    await tearDownPeer(tester);
  });

  testWidgets('« Refuser » sur la notification, puis appel manqué notifié', (tester) async {
    await setUpPeer(tester);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    controller.setVisible(false);
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId));
    await network(tester, () => controller.calls.call?.state == CallState.incoming);
    background.onAction!(DeclineCall(controller.calls.call!.id));
    await network(tester, () => peerCalls.call == null);
    expect(peer.store.messages(controller.messenger.userId).last.call?.outcome, CallOutcome.declined);
    expect(background.calls, contains('cancelIncoming'));

    // L'appelant raccroche avant toute réponse : appel manqué, notifié comme un message.
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId));
    await network(tester, () => controller.calls.call?.state == CallState.incoming);
    await tester.runAsync(() async => peerCalls.hangup());
    await network(tester, () => controller.calls.call == null);
    expect(background.notified.last.$3, ['📞 Appel audio manqué']);
    await tester.pumpAndSettle();
    await tearDownPeer(tester);
  });

  testWidgets('Préférences : rester joignable et autorisations manquantes', (tester) async {
    final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
    final settings = await Settings.load(prefs);
    expect(settings.keepRunning, isTrue); // activé par défaut
    final fake = FakeBackground()
      ..current = const BackgroundStatus(notifications: false, fullScreen: false, battery: false);
    final offline = ChatController(Store(), settings, useNetwork: false, useDiscovery: false, background: fake);
    await tester.pumpWidget(MaterialApp(home: SettingsScreen(controller: offline)));
    await tester.pumpAndSettle();

    for (final key in ['allow-notifications', 'allow-full-screen', 'allow-battery']) {
      await tester.scrollUntilVisible(find.byKey(ValueKey(key)), 200, scrollable: find.byType(Scrollable).first);
      await tester.tap(find.descendant(of: find.byKey(ValueKey(key)), matching: find.byType(TextButton)));
      await tester.pump();
    }
    expect(fake.calls, ['openNotifications', 'openFullScreen', 'battery']);

    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('keep-running')),
      -200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const ValueKey('keep-running')));
    await tester.pumpAndSettle();
    expect(settings.keepRunning, isFalse);
    expect(prefs.values['keep_running'], 0);
    expect(find.byKey(const ValueKey('allow-battery')), findsNothing); // inutile désactivé
  });
}
