import 'package:flutter/material.dart';

import '../app/controller.dart';
import '../net/calls.dart';
import 'call_screen.dart';
import 'conversation_screen.dart';
import 'contacts_screen.dart';
import 'format.dart';
import 'welcome_screen.dart';

ThemeData buildTheme(Brightness brightness) {
  final scheme = ColorScheme.fromSeed(
    seedColor: accentColor,
    brightness: brightness,
    primary: brightness == Brightness.light ? accentColor : const Color(0xFF5AA9E6),
  );
  return ThemeData(colorScheme: scheme, useMaterial3: true);
}

class SocketChatApp extends StatefulWidget {
  const SocketChatApp({
    super.key,
    required this.controller,
    this.theme = buildTheme,
    this.themeMode = ThemeMode.system,
  });
  final ChatController controller;

  /// Thème clair ou sombre (remplaçable pour les captures d'écran).
  final ThemeData Function(Brightness) theme;
  final ThemeMode themeMode;

  @override
  State<SocketChatApp> createState() => _SocketChatAppState();
}

class _SocketChatAppState extends State<SocketChatApp> {
  final GlobalKey<ScaffoldMessengerState> _messenger = GlobalKey();
  final GlobalKey<NavigatorState> _navigator = GlobalKey();
  Route<void>? _callRoute;
  late final AppLifecycleListener _lifecycle;

  ChatController get controller => widget.controller;

  @override
  void initState() {
    super.initState();
    controller.errors.connect(_showError);
    controller.calls.stateChanged.connect(_onCall);
    controller.conversationRequested.connect(_openConversation);
    // La messagerie tourne aussi sans écran (application fermée) : elle n'est « vue »
    // que lorsque l'écran est affiché, sinon les nouveaux messages sont notifiés.
    _lifecycle = AppLifecycleListener(onStateChange: _onLifecycle);
    final state = WidgetsBinding.instance.lifecycleState;
    if (state != null) _onLifecycle(state);
    controller.background.listen(controller.handleAction);
  }

  void _onLifecycle(AppLifecycleState state) {
    final visible = state == AppLifecycleState.resumed || state == AppLifecycleState.inactive;
    if (visible != controller.visible || state == AppLifecycleState.resumed) controller.setVisible(visible);
  }

  /// Toucher d'une notification de message : la conversation s'ouvre par-dessus la liste.
  void _openConversation(String contactId) {
    final navigator = _navigator.currentState;
    if (navigator == null || controller.needsName) return;
    if (controller.calls.call == null) navigator.popUntil((route) => route.isFirst);
    navigator.push(
      MaterialPageRoute<void>(
        builder: (_) => ConversationScreen(controller: controller, contactId: contactId),
      ),
    );
  }

  @override
  void dispose() {
    controller.errors.disconnect(_showError);
    controller.calls.stateChanged.disconnect(_onCall);
    controller.conversationRequested.disconnect(_openConversation);
    _lifecycle.dispose();
    super.dispose();
  }

  void _showError(String message) {
    _messenger.currentState?.showSnackBar(SnackBar(content: Text(message), duration: const Duration(seconds: 6)));
  }

  /// Un appel commence (entrant ou sortant) : écran d'appel par-dessus le reste.
  Future<void> _onCall(Call? call) async {
    final navigator = _navigator.currentState;
    if (call == null || navigator == null || (_callRoute?.isActive ?? false)) return;
    final route = MaterialPageRoute<void>(
      settings: const RouteSettings(name: 'call'),
      fullscreenDialog: true,
      builder: (_) => CallScreen(controller: controller),
    );
    _callRoute = route;
    await navigator.push(route);
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      navigatorKey: _navigator,
      title: 'Socket Chat',
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: _messenger,
      theme: widget.theme(Brightness.light),
      darkTheme: widget.theme(Brightness.dark),
      themeMode: widget.themeMode,
      home: ListenableBuilder(
        listenable: controller,
        builder: (context, _) =>
            controller.needsName ? WelcomeScreen(controller: controller) : ContactsScreen(controller: controller),
      ),
    );
  }
}
