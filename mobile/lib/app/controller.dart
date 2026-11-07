import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../calls/webrtc_engine.dart';
import '../core/signal.dart';
import '../net/calls.dart';
import '../net/discovery.dart';
import '../net/messenger.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
import '../updates/app_updater.dart';
import 'background.dart';
import 'call_platform.dart';
import 'platform.dart';
import 'settings.dart';

/// Relie la messagerie à l'interface : démarrage, préférences, conversation ouverte.
///
/// L'interface se reconstruit à chaque [notifyListeners] et relit l'historique
/// (SQLite synchrone, rapide) : pas d'état dupliqué à tenir à jour.
class ChatController extends ChangeNotifier {
  ChatController(
    this.store,
    this.settings, {
    this.platform = const NetworkPlatform(),
    this.useDiscovery = true,
    this.useNetwork = true,
    this.updater,
    this.callPlatform = const CallPlatform(),
    EngineFactory? engineFactory,
    BackgroundPlatform? background,
  }) : messenger = Messenger(store, settings.userId, settings.name ?? 'Android', avatar: settings.avatar),
       background = background ?? BackgroundPlatform() {
    calls = CallManager(messenger, engineFactory ?? WebRtcEngine.new);
    calls.stateChanged.connect(_onCallState);
    calls.error.connect(errors.emit);
    messenger.contactsChanged.connect((_) => _changed());
    messenger.messageAdded.connect(_onMessageAdded);
    messenger.messageStatusChanged.connect((_) => _changed());
    messenger.messageChanged.connect((_) => _changed());
    messenger.fileProgress.connect((_) => _changed());
    messenger.typingChanged.connect((_) => _changed());
    messenger.presenceChanged.connect((_) => _changed());
    messenger.avatarChanged.connect((_) => _changed());
    messenger.error.connect(errors.emit);
  }

  final Store store;
  final Settings settings;
  final NetworkPlatform platform;
  final bool useDiscovery;

  /// `false` : ni écoute ni détection (tests de l'interface).
  final bool useNetwork;
  final Messenger messenger;
  Discovery? _discovery;

  /// Appels audio (un à la fois).
  late final CallManager calls;
  final CallPlatform callPlatform;
  bool _ringing = false;
  bool _proximity = false;

  /// Service au premier plan et notifications (application fermée).
  final BackgroundPlatform background;
  bool _callNotified = false;
  bool _inCall = false;
  bool _callMode = false;

  /// Conversation à ouvrir (toucher d'une notification de message).
  final Signal<String> conversationRequested = Signal();

  /// Mise à jour de l'application ; `null` hors Android (tests).
  final AppUpdater? updater;
  Timer? _firstUpdateCheck;
  Timer? _periodicUpdateCheck;

  /// Délai avant la première vérification de mise à jour après le démarrage.
  static const Duration firstUpdateCheckDelay = Duration(seconds: 15);

  /// Messages d'erreur à afficher (barre en bas de l'écran).
  final Signal<String> errors = Signal();

  /// Erreur de démarrage persistante (port occupé…), affichée en bandeau.
  String? startError;

  /// Conversation à l'écran, marquée comme lue au fil de l'eau.
  String? _openContact;
  bool _visible = true;
  bool _notifyScheduled = false;
  bool _disposed = false;

  bool get needsName => settings.name == null;

  /// L'écran de l'application est affiché (sinon : notifications).
  bool get visible => _visible;
  bool get running => messenger.running;

  // -- Cycle de vie ----------------------------------------------------
  Future<void> start() async {
    if (needsName || messenger.running) return;
    _scheduleUpdateChecks(); // indépendant de la messagerie (port occupé…)
    if (!useNetwork) {
      notifyListeners();
      return;
    }
    startError = null;
    String? failure;
    void capture(String message) => failure = message;
    messenger.error.connect(capture);
    final ok = await messenger.start(settings.port);
    messenger.error.disconnect(capture);
    if (!ok) {
      startError = failure;
      notifyListeners();
      return;
    }
    await _applyKeepRunning();
    if (useDiscovery) {
      await platform.acquireMulticastLock();
      final discovery = Discovery(
        settings.userId,
        messenger.name,
        messenger.port!,
        broadcastProvider: () async => broadcastAddresses(extra: await platform.broadcastAddresses()),
      );
      if (await discovery.start()) {
        messenger.attachDiscovery(discovery);
        _discovery = discovery;
      }
    }
    notifyListeners();
  }

  Future<void> stop() async {
    calls.stop();
    _discovery?.stop();
    _discovery = null;
    messenger.stop();
    if (useDiscovery) await platform.releaseMulticastLock();
  }

  /// Premier lancement ou changement de préférences.
  Future<void> applySettings({required String name, required int port}) async {
    final normalized = validateName(name);
    final portChanged = port != settings.port;
    settings
      ..name = normalized
      ..port = port;
    await settings.save();
    messenger.name = normalized;
    _discovery?.setName(normalized);
    if (portChanged && messenger.running) await stop();
    if (!messenger.running) await start();
    notifyListeners();
  }

  /// Rester joignable application fermée (service au premier plan et notifications).
  Future<void> setKeepRunning(bool on) async {
    settings.keepRunning = on;
    await settings.save();
    if (messenger.running) await _applyKeepRunning();
    notifyListeners();
  }

  Future<void> _applyKeepRunning() async {
    await background.setKeepRunning(settings.keepRunning);
    if (settings.keepRunning) await background.requestNotificationPermission();
  }

  /// Change (ou retire avec une liste vide) notre photo de profil et l'envoie aux contacts.
  Future<void> setAvatar(Uint8List data) async {
    messenger.setAvatar(data);
    settings.avatar = data;
    await settings.save();
    notifyListeners();
  }

  /// L'application passe au premier plan ou en arrière-plan.
  void setVisible(bool visible) {
    _visible = visible;
    // Écran quitté pendant que ça sonne : la notification d'appel prend le relais.
    if (!visible && calls.call?.state == CallState.incoming) _onCallState(calls.call);
    if (visible) {
      _markOpenRead();
      _discovery?.announce();
      if (_firstUpdateCheck != null && !_firstUpdateCheck!.isActive) updater?.check();
    }
  }

  void _scheduleUpdateChecks() {
    final updater = this.updater;
    if (updater == null || _firstUpdateCheck != null) return;
    _firstUpdateCheck = Timer(firstUpdateCheckDelay, updater.check);
    _periodicUpdateCheck = Timer.periodic(checkInterval, (_) => updater.check());
  }

  // -- Conversation ouverte --------------------------------------------
  void openConversation(String contactId) {
    _openContact = contactId;
    messenger.connectTo(contactId);
    _markOpenRead();
  }

  void closeConversation(String contactId) {
    if (_openContact == contactId) _openContact = null;
  }

  /// Ajout d'un collègue par son adresse ; `false` si la messagerie n'écoute pas.
  bool addContact(String host, int port) {
    if (!messenger.running) {
      errors.emit("La messagerie n'est pas démarrée (voir Préférences).");
      return false;
    }
    messenger.addContact(host, port);
    return true;
  }

  // -- Appels -----------------------------------------------------------
  /// Appelle un contact ; `false` (avec un message d'erreur) si c'est impossible.
  bool startCall(String contactId, [String media = 'audio']) {
    try {
      calls.startCall(contactId, media);
      return true;
    } on StateError catch (e) {
      errors.emit(e.message);
      return false;
    }
  }

  bool canCall(String contactId) => calls.call == null && messenger.isConnected(contactId);

  void _onCallState(Call? call) {
    final ringing = call?.state == CallState.incoming;
    if (ringing && !_visible && !_callNotified) {
      _callNotified = true;
      background.showIncomingCall(
        call!.id,
        call.contactId,
        store.contact(call.contactId)?.name ?? '',
        video: call.video,
      );
    } else if (!ringing && _callNotified) {
      _callNotified = false;
      background.cancelIncomingCall();
    }
    final inCall = call != null && (call.state == CallState.connecting || call.state == CallState.active);
    if (inCall != _inCall) {
      _inCall = inCall;
      background.setInCall(inCall, video: call?.video ?? false);
    }
    if ((call != null) != _callMode) {
      _callMode = call != null;
      background.setCallMode(_callMode);
    }
    if (ringing != _ringing) {
      _ringing = ringing;
      ringing ? callPlatform.startRinging() : callPlatform.stopRinging();
    }
    final proximity =
        call != null && !call.speaker && (call.state == CallState.connecting || call.state == CallState.active);
    if (proximity != _proximity) {
      _proximity = proximity;
      callPlatform.setProximity(proximity);
    }
    _changed();
  }

  /// Action venue d'une notification Android.
  void handleAction(BackgroundAction action) {
    switch (action) {
      case OpenConversation(:final contactId):
        if (store.contact(contactId) != null) conversationRequested.emit(contactId);
      case AnswerCall(:final callId):
        if (calls.call?.id == callId) calls.accept();
      case DeclineCall(:final callId):
        if (calls.call?.id == callId) calls.decline();
    }
  }

  void deleteContact(String contactId) {
    store.deleteContact(contactId);
    notifyListeners();
  }

  /// Adresses IPv4 de l'appareil (à donner à un collègue qui ajoute par adresse).
  Future<List<String>> localAddresses() async {
    try {
      final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4);
      return [
        for (final interface in interfaces)
          for (final address in interface.addresses)
            if (!address.isLoopback && !address.isLinkLocal) address.address,
      ];
    } on SocketException {
      return const [];
    }
  }

  // -- Interne ---------------------------------------------------------
  void _onMessageAdded(MessageRecord record) {
    if (!record.outgoing && record.contactId == _openContact && _visible) {
      // Affiché à l'écran : lu immédiatement (après la fin du traitement en cours).
      scheduleMicrotask(_markOpenRead);
    } else if (record.unread && !_visible) {
      _notifyMessages(record.contactId); // message ou appel manqué, application fermée
    }
    _changed();
  }

  /// Notification des messages non lus d'une conversation (les derniers, sur une ligne chacun).
  void _notifyMessages(String contactId) {
    final unread = store.messages(contactId).where((m) => m.unread).toList();
    if (unread.isEmpty) return;
    final lines = [for (final m in unread.skip(unread.length > 6 ? unread.length - 6 : 0)) m.preview];
    background.notifyMessages(contactId, store.contact(contactId)?.name ?? 'Socket Chat', lines, unread.length);
  }

  void _markOpenRead() {
    final contactId = _openContact;
    if (contactId == null || !_visible) return;
    messenger.markRead(contactId);
    background.cancelMessages(contactId);
  }

  /// Regroupe les notifications d'une même rafale d'événements réseau.
  void _changed() {
    if (_notifyScheduled) return;
    _notifyScheduled = true;
    scheduleMicrotask(() {
      _notifyScheduled = false;
      if (!_disposed) notifyListeners();
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _firstUpdateCheck?.cancel();
    _periodicUpdateCheck?.cancel();
    stop();
    super.dispose();
  }
}
