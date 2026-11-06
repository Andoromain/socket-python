// Transposition de tests/test_calls.py : signalisation entre deux appareils, média simulé.

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/net/calls.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/store/store.dart';

import 'helpers.dart';

/// Connecté dès que l'offre et la réponse sont passées.
class FakeEngine extends MediaEngine {
  static final instances = <FakeEngine>[];
  FakeEngine() {
    instances.add(this);
  }
  final remoteIce = <(String, String, int)>[];
  bool muted = false;
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
  Future<void> stop() async => stopped = true;
}

void main() {
  late Messengers messengers;
  late Messenger a;
  late Messenger b;
  late CallManager alice;
  late CallManager bob;

  setUp(() async {
    FakeEngine.instances.clear();
    messengers = Messengers();
    a = await messengers.make('Alice');
    b = await messengers.make('Bob');
    alice = CallManager(a, FakeEngine.new);
    bob = CallManager(b, FakeEngine.new);
    await link(a, b);
  });
  tearDown(() async {
    ringTimeoutMs = 30000;
    ringingAckTimeoutMs = 6000;
    await messengers.dispose();
  });

  MessageRecord lastCall(Messenger m, String contactId) => m.store.messages(contactId).lastWhere((r) => r.call != null);

  test('appel complet', () async {
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    expect(await incoming, (a.userId, 'audio'));
    await waitUntil(() => alice.call?.state == CallState.ringingOut);

    bob.accept();
    await waitUntil(() => alice.call?.state == CallState.active && bob.call?.state == CallState.active);
    expect(FakeEngine.instances, hasLength(2));
    expect(FakeEngine.instances.every((e) => e.remoteIce.isNotEmpty), isTrue);

    alice.setMuted(true);
    expect((alice.call!.engine! as FakeEngine).muted, isTrue);
    final ended = next(bob.callEnded);
    alice.hangup();
    expect(await ended, (a.userId, CallOutcome.completed));
    expect(alice.call, isNull);
    expect(bob.call, isNull);
    expect(FakeEngine.instances.every((e) => e.stopped), isTrue);
    final mine = lastCall(a, b.userId);
    expect(mine.outgoing, isTrue);
    expect(mine.call!.outcome, CallOutcome.completed);
    expect(mine.unread, isFalse);
    expect(lastCall(b, a.userId).preview, startsWith('📞 Appel audio · '));
  });

  test('refusé', () async {
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    await incoming;
    final ended = next(alice.callEnded);
    bob.decline();
    expect((await ended).$2, CallOutcome.declined);
    expect(lastCall(b, a.userId).call!.outcome, CallOutcome.declined);
    expect(FakeEngine.instances, isEmpty);
  });

  test("l'appelant raccroche pendant la sonnerie : appel manqué", () async {
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    await incoming;
    final ended = next(bob.callEnded);
    alice.hangup();
    expect((await ended).$2, CallOutcome.missed);
    expect(lastCall(a, b.userId).call!.outcome, CallOutcome.cancelled);
    final missed = lastCall(b, a.userId);
    expect(missed.unread, isTrue);
    expect(missed.preview, '📞 Appel audio manqué');
  });

  test('sonnerie sans réponse', () async {
    ringTimeoutMs = 200;
    final incoming = next(bob.incomingCall);
    final ends = Future.wait([next(alice.callEnded), next(bob.callEnded)]);
    alice.startCall(b.userId);
    await incoming;
    await ends;
    expect(lastCall(a, b.userId).call!.outcome, CallOutcome.noAnswer);
    expect(lastCall(b, a.userId).call!.outcome, CallOutcome.missed);
  });

  test('occupé', () async {
    final c = await messengers.make('Carol');
    final carol = CallManager(c, FakeEngine.new);
    await link(c, b);
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    await incoming;
    final ended = next(carol.callEnded);
    carol.startCall(b.userId);
    expect((await ended).$2, CallOutcome.busy);
    expect(bob.call!.contactId, a.userId);
  });

  test('correspondant sans les appels (version antérieure)', () async {
    ringingAckTimeoutMs = 200;
    final older = await messengers.make('Ancien'); // aucun CallManager : trames ignorées
    await link(a, older);
    final ended = next(alice.callEnded);
    alice.startCall(older.userId);
    expect((await ended).$2, CallOutcome.unsupported);
    expect(lastCall(a, older.userId).preview, contains('mettre Socket Chat à jour'));
  });

  test('coupure de connexion pendant un appel', () async {
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    await incoming;
    bob.accept();
    await waitUntil(() => alice.call?.state == CallState.active);
    final ended = next(alice.callEnded);
    b.stop();
    expect((await ended).$2, CallOutcome.completed);
  });

  test('échec du média', () async {
    final incoming = next(bob.incomingCall);
    alice.startCall(b.userId);
    await incoming;
    bob.accept();
    await waitUntil(() => alice.call?.engine != null);
    final errors = <String>[];
    alice.error.connect(errors.add);
    final ended = next(bob.callEnded);
    alice.call!.engine!.failed.emit('micro introuvable');
    await ended;
    expect(errors, ['micro introuvable']);
    expect(alice.call, isNull);
    expect(bob.call, isNull);
  });

  test('appels croisés : un seul appel subsiste', () async {
    alice.startCall(b.userId);
    bob.startCall(a.userId);
    await waitUntil(
      () =>
          alice.call != null &&
          bob.call != null &&
          alice.call!.id == bob.call!.id &&
          {alice.call!.state, bob.call!.state}.containsAll([CallState.ringingOut, CallState.incoming]),
    );
    final keeper = a.userId.compareTo(b.userId) < 0 ? alice : bob;
    expect(keeper.call!.outgoing, isTrue);
  });

  test('erreurs au lancement', () async {
    expect(() => alice.startCall('11111111-1111-4111-8111-111111111111'), throwsStateError);
    alice.startCall(b.userId);
    expect(() => alice.startCall(b.userId), throwsStateError);
  });
}
