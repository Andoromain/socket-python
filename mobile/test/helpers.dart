import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/core/signal.dart';
import 'package:socket_chat_mobile/net/calls.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';

const Duration timeout = Duration(seconds: 10);

/// Attend qu'une condition soit vraie (échoue au bout de [limit]).
Future<void> waitUntil(bool Function() condition, {Duration limit = timeout}) async {
  final deadline = DateTime.now().add(limit);
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) fail('Condition non remplie en ${limit.inSeconds} s');
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

/// Prochaine émission de [signal] (à préparer avant l'action qui la déclenche).
Future<T> next<T>(Signal<T> signal, {bool Function(T value)? where, Duration limit = timeout}) {
  final completer = Completer<T>();
  late void Function(T) listener;
  listener = (value) {
    if (completer.isCompleted || (where != null && !where(value))) return;
    signal.disconnect(listener);
    completer.complete(value);
  };
  signal.connect(listener);
  return completer.future.timeout(
    limit,
    onTimeout: () {
      signal.disconnect(listener);
      throw TimeoutException('Signal non reçu en ${limit.inSeconds} s');
    },
  );
}

Future<void> pause([int ms = 200]) => Future<void>.delayed(Duration(milliseconds: ms));

/// Crée des Messenger démarrés (port libre), arrêtés en fin de test.
class Messengers {
  final List<Messenger> _created = [];

  Future<Messenger> make(String name, {String? userId, Store? store, bool start = true}) async {
    final messenger = Messenger(store ?? Store(), userId ?? newId(), name);
    _created.add(messenger);
    if (start) expect(await messenger.start(0), isTrue);
    return messenger;
  }

  Future<void> dispose() async {
    for (final messenger in _created) {
      messenger.stop();
    }
    await pause(50);
  }
}

/// [a] ajoute [b] par son adresse ; attend que chacun voie l'autre en ligne.
Future<void> link(Messenger a, Messenger b) async {
  final seenByA = next(a.presenceChanged);
  final seenByB = next(b.presenceChanged);
  a.addContact('127.0.0.1', b.port!);
  await Future.wait([seenByA, seenByB]);
  expect(a.isOnline(b.userId) && b.isOnline(a.userId), isTrue);
}

/// Média d'appel simulé : connecté dès que l'offre et la réponse sont passées.
class FakeEngine extends MediaEngine {
  static final instances = <FakeEngine>[];
  FakeEngine() {
    instances.add(this);
  }
  final remoteIce = <(String, String, int)>[];
  bool muted = false;
  bool speaker = false;
  bool stopped = false;

  @override
  Future<void> start({required bool caller, required String media}) async {
    if (caller) Timer.run(() => localDescription.emit(('offer', 'v=0 offre')));
    Timer.run(() => localIce.emit(('candidate:1 1 udp 1 10.0.0.1 1 typ host', '0', 0)));
  }

  @override
  Future<void> setRemoteDescription(String kind, String sdp) async {
    if (kind == 'offer') Timer.run(() => localDescription.emit(('answer', 'v=0 réponse')));
    Timer(const Duration(milliseconds: 10), () => connected.emit(null));
  }

  @override
  Future<void> addRemoteIce(String candidate, String mid, int index) async => remoteIce.add((candidate, mid, index));

  @override
  void setMuted(bool value) => muted = value;

  @override
  void setSpeaker(bool on) => speaker = on;

  @override
  Future<void> stop() async => stopped = true;
}
