import 'package:flutter/services.dart';

/// Action venue d'une notification Android.
sealed class BackgroundAction {
  const BackgroundAction();

  static BackgroundAction? fromMap(Map<Object?, Object?>? map) {
    if (map == null) return null;
    final contact = map['contact'];
    final call = map['call'];
    return switch (map['type']) {
      'open' when contact is String => OpenConversation(contact),
      'answer' when call is String => AnswerCall(call),
      'decline' when call is String => DeclineCall(call),
      _ => null,
    };
  }
}

/// Toucher une notification de message : ouvrir la conversation.
class OpenConversation extends BackgroundAction {
  const OpenConversation(this.contactId);
  final String contactId;
}

/// « Répondre » sur la notification d'appel entrant.
class AnswerCall extends BackgroundAction {
  const AnswerCall(this.callId);
  final String callId;
}

/// « Refuser » sur la notification d'appel entrant.
class DeclineCall extends BackgroundAction {
  const DeclineCall(this.callId);
  final String callId;
}

/// État des autorisations utiles application fermée.
class BackgroundStatus {
  const BackgroundStatus({this.notifications = true, this.fullScreen = true, this.battery = true});

  /// Notifications autorisées.
  final bool notifications;

  /// Appels affichés en plein écran sur l'écran verrouillé (à autoriser sur Android 14).
  final bool fullScreen;

  /// Socket Chat exclue de l'optimisation de batterie.
  final bool battery;
}

/// Fonctions Android pour rester joignable application fermée (voir Platform.kt, canal
/// « socket_chat/background ») : service au premier plan, notifications, autorisations.
/// Hors Android (tests), les appels sont sans effet.
class BackgroundPlatform {
  BackgroundPlatform();

  static const MethodChannel _channel = MethodChannel('socket_chat/background');

  /// Reçoit les actions des notifications (y compris celle qui a ouvert l'application).
  void listen(void Function(BackgroundAction action) onAction) {
    Future<void> take() async {
      try {
        final raw = await _channel.invokeMapMethod<Object?, Object?>('takePendingAction');
        final action = BackgroundAction.fromMap(raw);
        if (action != null) onAction(action);
      } on MissingPluginException {
        // Pas d'Android (tests).
      } on PlatformException {
        // Rien à lire.
      }
    }

    try {
      _channel.setMethodCallHandler((call) async {
        if (call.method == 'action') await take();
      });
    } on MissingPluginException {
      return;
    }
    take();
  }

  /// Garde Socket Chat joignable application fermée (service au premier plan) ou non.
  Future<void> setKeepRunning(bool on) => _call('setKeepRunning', {'on': on});

  /// Appel en cours : le service déclare l'usage du micro, et de la caméra en vidéo
  /// (Android 14).
  Future<void> setInCall(bool on, {bool video = false}) => _call('setInCall', {'on': on, 'video': video});

  /// Pendant un appel, l'écran s'affiche par-dessus le verrouillage.
  Future<void> setCallMode(bool on) => _call('setCallMode', {'on': on});

  /// Notification des derniers messages non lus d'une conversation.
  Future<void> notifyMessages(String contactId, String title, List<String> lines, int count) =>
      _call('notifyMessages', {'contact': contactId, 'title': title, 'lines': lines, 'count': count});

  Future<void> cancelMessages(String contactId) => _call('cancelMessages', {'contact': contactId});

  /// Appel entrant : notification plein écran avec Répondre et Refuser.
  Future<void> showIncomingCall(String callId, String contactId, String name, {bool video = false}) =>
      _call('showIncomingCall', {'call': callId, 'contact': contactId, 'name': name, 'video': video});

  Future<void> cancelIncomingCall() => _call('cancelIncomingCall');

  Future<BackgroundStatus> status() async {
    try {
      final raw = await _channel.invokeMapMethod<String, Object?>('status');
      if (raw == null) return const BackgroundStatus();
      return BackgroundStatus(
        notifications: raw['notifications'] != false,
        fullScreen: raw['fullScreen'] != false,
        battery: raw['battery'] != false,
      );
    } on MissingPluginException {
      return const BackgroundStatus();
    } on PlatformException {
      return const BackgroundStatus();
    }
  }

  /// Android 13+ : demande l'autorisation d'afficher des notifications.
  Future<void> requestNotificationPermission() => _call('requestNotificationPermission');

  Future<void> openNotificationSettings() => _call('openNotificationSettings');

  Future<void> openFullScreenSettings() => _call('openFullScreenSettings');

  Future<void> requestBatteryUnrestricted() => _call('requestBatteryUnrestricted');

  Future<void> _call(String method, [Map<String, Object?>? arguments]) async {
    try {
      await _channel.invokeMethod<void>(method, arguments);
    } on MissingPluginException {
      // Pas d'Android (tests).
    } on PlatformException {
      // Fonction indisponible : Socket Chat reste utilisable application ouverte.
    }
  }
}
