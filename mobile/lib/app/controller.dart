import 'dart:async';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../core/signal.dart';
import '../net/discovery.dart';
import '../net/messenger.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
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
  }) : messenger = Messenger(store, settings.userId, settings.name ?? 'Android', identity: settings.identity) {
    messenger.contactsChanged.connect((_) => _changed());
    messenger.messageAdded.connect(_onMessageAdded);
    messenger.messageStatusChanged.connect((_) => _changed());
    messenger.messageChanged.connect((_) => _changed());
    messenger.fileProgress.connect((_) => _changed());
    messenger.typingChanged.connect((_) => _changed());
    messenger.presenceChanged.connect((_) => _changed());
    messenger.keyChanged.connect((_) => _changed());
    messenger.keyRefused.connect((_) => _changed());
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

  /// Messages d'erreur à afficher (barre en bas de l'écran).
  final Signal<String> errors = Signal();

  /// Erreur de démarrage persistante (port occupé…), affichée en bandeau.
  String? startError;

  /// Information importante à afficher en bandeau jusqu'à ce que l'utilisateur la ferme
  /// (historique illisible mis de côté…).
  String? notice;

  void dismissNotice() {
    notice = null;
    notifyListeners();
  }

  /// Conversation à l'écran, marquée comme lue au fil de l'eau.
  String? _openContact;
  bool _visible = true;
  bool _notifyScheduled = false;
  bool _disposed = false;

  bool get needsName => settings.name == null;
  bool get running => messenger.running;

  // -- Cycle de vie ----------------------------------------------------
  Future<void> start() async {
    if (needsName || messenger.running) return;
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

  /// L'application passe au premier plan ou en arrière-plan.
  void setVisible(bool visible) {
    _visible = visible;
    if (visible) {
      _markOpenRead();
      _discovery?.announce();
    }
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
    }
    _changed();
  }

  void _markOpenRead() {
    final contactId = _openContact;
    if (contactId != null && _visible) messenger.markRead(contactId);
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
    stop();
    super.dispose();
  }
}
