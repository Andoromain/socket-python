// Interface des appels : écran d'appel et bouton de la conversation, face à un vrai
// correspondant sur 127.0.0.1 (média simulé : FakeEngine).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/net/calls.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/app.dart';

import 'helpers.dart';

void main() {
  late Messengers messengers;
  late ChatController controller;
  late Messenger peer;
  late CallManager peerCalls;

  Future<void> setUpCall(WidgetTester tester) async {
    FakeEngine.instances.clear();
    messengers = Messengers();
    final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
    final settings = await Settings.load(prefs);
    controller = ChatController(
      Store(),
      settings,
      useNetwork: false,
      useDiscovery: false,
      engineFactory: FakeEngine.new,
    );
    await tester.runAsync(() async {
      expect(await controller.messenger.start(0), isTrue);
      peer = await messengers.make('Bob Durand');
      peerCalls = CallManager(peer, FakeEngine.new);
      await link(controller.messenger, peer);
    });
    await tester.pumpWidget(SocketChatApp(controller: controller));
    await tester.pumpAndSettle();
  }

  Future<void> tearDownCall(WidgetTester tester) async {
    await tester.runAsync(() async {
      controller.messenger.stop();
      await messengers.dispose();
    });
  }

  /// Laisse passer le réseau (temps réel), puis redessine.
  Future<void> waitFor(WidgetTester tester, bool Function() condition) async {
    await tester.runAsync(() => waitUntil(condition));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400)); // animation de l'écran d'appel
  }

  testWidgets('appel sortant : bouton de la conversation, micro, haut-parleur, raccrocher', (tester) async {
    await setUpCall(tester);
    await tester.tap(find.text('Bob Durand'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('call-button')));
    await waitFor(tester, () => peerCalls.call?.state == CallState.incoming);
    await waitFor(tester, () => controller.calls.call?.state == CallState.ringingOut);
    expect(find.text('Sonnerie…'), findsOneWidget);
    expect(find.text('Bob Durand'), findsWidgets);

    peerCalls.accept();
    await waitFor(tester, () => controller.calls.call?.state == CallState.active);
    expect(find.text('00:00'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('call-mute')));
    await tester.pump();
    expect(FakeEngine.instances.any((e) => e.muted), isTrue);
    expect(find.text('Micro coupé'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('call-speaker')));
    await tester.pump();
    expect(controller.calls.call!.speaker, isTrue);
    expect(find.text('Haut-parleur'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('call-hangup')));
    await waitFor(tester, () => peerCalls.call == null);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('call-status')), findsNothing); // écran refermé
    expect(find.text('📞 Appel audio · 0 s'), findsOneWidget); // noté dans la conversation
    expect(find.byIcon(Icons.done_all), findsNothing); // pas de coches pour un appel
    await tearDownCall(tester);
  });

  testWidgets('appel vidéo : bouton de la conversation, caméra, retourner, raccrocher', (tester) async {
    await setUpCall(tester);
    await tester.tap(find.text('Bob Durand'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('video-button')));
    await waitFor(tester, () => peerCalls.call?.state == CallState.incoming);
    expect(peerCalls.call!.media, 'video');
    peerCalls.accept();
    await waitFor(tester, () => controller.calls.call?.state == CallState.active);
    // Image en plein écran : commandes vidéo (pas d'écouteur, haut-parleur d'office).
    expect(find.byKey(const ValueKey('call-camera')), findsOneWidget);
    expect(find.byKey(const ValueKey('call-speaker')), findsNothing);
    final engine = controller.calls.call!.engine! as FakeEngine;
    expect([engine.media, engine.speaker], ['video', true]);

    await tester.tap(find.byKey(const ValueKey('call-camera')));
    await tester.pump();
    expect([engine.camera, controller.calls.call!.cameraOff], [false, true]);
    expect(find.text('Caméra coupée'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('call-camera')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('call-switch-camera')));
    await tester.pump();
    expect([engine.camera, engine.switches], [true, 1]);

    await tester.tap(find.byKey(const ValueKey('call-hangup')));
    await waitFor(tester, () => peerCalls.call == null);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('call-camera')), findsNothing);
    expect(find.text('🎥 Appel vidéo · 0 s'), findsOneWidget);
    await tearDownCall(tester);
  });

  testWidgets('appel vidéo entrant : « Appel vidéo entrant »', (tester) async {
    await setUpCall(tester);
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId, 'video'));
    await waitFor(tester, () => controller.calls.call?.state == CallState.incoming);
    expect(find.text('Appel vidéo entrant'), findsOneWidget);
    expect(find.byIcon(Icons.videocam), findsOneWidget); // bouton Répondre
    await tester.tap(find.byKey(const ValueKey('call-decline')));
    await waitFor(tester, () => peerCalls.call == null);
    await tester.pumpAndSettle();
    await tearDownCall(tester);
  });

  testWidgets('appel entrant : répondre puis le correspondant raccroche', (tester) async {
    await setUpCall(tester);
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId));
    await waitFor(tester, () => controller.calls.call?.state == CallState.incoming);
    expect(find.text('Appel audio entrant'), findsOneWidget);
    expect(find.text('Bob Durand'), findsWidgets);

    await tester.tap(find.byKey(const ValueKey('call-answer')));
    await waitFor(tester, () => controller.calls.call?.state == CallState.active);
    expect(find.byKey(const ValueKey('call-hangup')), findsOneWidget);

    await tester.runAsync(() async => peerCalls.hangup());
    await waitFor(tester, () => controller.calls.call == null);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('call-status')), findsNothing);
    final record = controller.store.messages(peer.userId).last;
    expect([record.call?.outcome, record.outgoing], [CallOutcome.completed, false]);
    await tearDownCall(tester);
  });

  testWidgets('appel entrant refusé : noté « Appel refusé » des deux côtés', (tester) async {
    await setUpCall(tester);
    await tester.runAsync(() async => peerCalls.startCall(controller.messenger.userId));
    await waitFor(tester, () => controller.calls.call?.state == CallState.incoming);

    await tester.tap(find.byKey(const ValueKey('call-decline')));
    await waitFor(tester, () => peerCalls.call == null);
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('call-status')), findsNothing);
    final record = peer.store.messages(controller.messenger.userId).last;
    expect(record.call?.outcome, CallOutcome.declined);
    expect(controller.store.messages(peer.userId).last.call?.outcome, CallOutcome.declined);
    await tearDownCall(tester);
  });

  testWidgets('bouton d\'appel désactivé hors ligne', (tester) async {
    final prefs = MemoryPreferenceStore()..values['name'] = 'Moi';
    final settings = await Settings.load(prefs);
    final offline = ChatController(Store(), settings, useNetwork: false, useDiscovery: false);
    offline.store.upsertContact(newId(), 'Alice Martin', host: '10.0.0.2', port: 12800);
    await tester.pumpWidget(SocketChatApp(controller: offline));
    await tester.tap(find.text('Alice Martin'));
    await tester.pumpAndSettle();
    final button = tester.widget<IconButton>(find.byKey(const ValueKey('call-button')));
    expect(button.onPressed, isNull);
    expect(find.byTooltip('Appel audio : contact hors ligne'), findsOneWidget);
  });
}
