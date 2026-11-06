// Détection des collègues sur le réseau local par annonces UDP, transposée de
// src/socket_chat/discovery.py.
//
// Chaque instance diffuse régulièrement une annonce (identifiant, nom, port TCP)
// et écoute celles des autres. Un pair silencieux depuis [peerTimeoutMs] est
// considéré comme parti. Le broadcast ne franchit pas les routeurs : pour un autre
// sous-réseau ou un VPN, on ajoute le contact par son adresse.
//
// Sur Android, la réception du broadcast exige un verrou multicast Wi-Fi (pris par
// l'application, voir android/…/MainActivity.kt).

import 'dart:async';
import 'dart:io';

import '../core/signal.dart';
import '../protocol/protocol.dart';

/// Intervalle entre deux annonces (ms).
int announceIntervalMs = 5000;

/// Silence au-delà duquel un pair est considéré comme parti (ms).
int peerTimeoutMs = 15000;

/// Intervalle de vérification des pairs silencieux (ms).
int expireCheckMs = 1000;

final Stopwatch _clock = Stopwatch()..start();

/// Horloge monotone en millisecondes.
int monotonicMs() => _clock.elapsedMilliseconds;

/// Adresses de diffusion : globale, plus celle supposée de chaque réseau IPv4 local.
///
/// Dart n'expose pas les masques de sous-réseau : on suppose un /24, le cas de la
/// quasi-totalité des réseaux Wi-Fi et de bureau. Le broadcast global suffit
/// d'ailleurs dans la plupart des cas. [extra] complète la liste (adresses réelles
/// fournies par Android).
Future<List<String>> broadcastAddresses({List<String> extra = const []}) async {
  final targets = <String>{'255.255.255.255', ...extra};
  try {
    final interfaces = await NetworkInterface.list(type: InternetAddressType.IPv4);
    for (final interface in interfaces) {
      for (final address in interface.addresses) {
        if (address.isLoopback || address.isLinkLocal) continue;
        final parts = address.address.split('.');
        if (parts.length == 4) targets.add('${parts[0]}.${parts[1]}.${parts[2]}.255');
      }
    }
  } on SocketException {
    // Pas d'interface lisible : diffusion globale seulement.
  }
  return targets.toList()..sort();
}

class Discovery {
  Discovery(
    this.userId,
    String name,
    this.tcpPort, {
    this.udpPort = discoveryPort,
    this.targets,
    this.broadcastProvider = broadcastAddresses,
  }) : _name = validateName(name);

  final String userId;
  String _name;
  int tcpPort;
  final int udpPort;

  /// Destinataires des annonces (adresse, port) ; `null` = diffusion sur le réseau local.
  List<(String, int)>? targets;

  /// Calcule les adresses de diffusion (quand [targets] vaut `null`).
  final Future<List<String>> Function() broadcastProvider;

  /// Identifiant, nom, adresse, port TCP.
  final Signal<(String, String, String, int)> peerSeen = Signal();
  final Signal<String> peerLost = Signal();

  RawDatagramSocket? _socket;
  List<String> _broadcast = const ['255.255.255.255'];
  final Map<String, int> _peers = {}; // identifiant -> dernière annonce (monotonicMs)
  Timer? _announceTimer;
  Timer? _expireTimer;

  int get localPort => _socket?.port ?? 0;
  List<String> get peers => _peers.keys.toList();

  void setName(String name) {
    _name = validateName(name);
    announce();
  }

  Future<bool> start() async {
    if (_socket != null) return true;
    RawDatagramSocket socket;
    try {
      socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, udpPort, reuseAddress: true);
    } on SocketException {
      return false;
    }
    socket.broadcastEnabled = true;
    socket.listen((event) {
      if (event == RawSocketEvent.read) _onReadyRead();
    });
    _socket = socket;
    if (targets == null) await _refreshBroadcast();
    _announceTimer = Timer.periodic(Duration(milliseconds: announceIntervalMs), (_) {
      announce();
      if (targets == null) _refreshBroadcast();
    });
    _expireTimer = Timer.periodic(Duration(milliseconds: expireCheckMs), (_) => _expire());
    announce();
    return true;
  }

  void stop() {
    final socket = _socket;
    if (socket == null) return;
    _announceTimer?.cancel();
    _expireTimer?.cancel();
    _send(encodeLeave(userId));
    socket.close();
    _socket = null;
    for (final peerId in _peers.keys.toList()) {
      _peers.remove(peerId);
      peerLost.emit(peerId);
    }
  }

  void announce() {
    if (_socket != null) _send(encodeAnnounce(userId, _name, tcpPort));
  }

  /// Oublie immédiatement les pairs silencieux (sans attendre le minuteur).
  void expireNow() => _expire();

  /// Arrête les annonces périodiques sans prévenir les autres (pour les tests).
  void silence() {
    _announceTimer?.cancel();
    targets = const [];
  }

  // -- Interne ---------------------------------------------------------
  Future<void> _refreshBroadcast() async {
    try {
      final addresses = await broadcastProvider();
      if (addresses.isNotEmpty) _broadcast = addresses;
    } on Exception {
      // On garde la liste précédente.
    }
  }

  void _send(List<int> datagram) {
    final socket = _socket;
    if (socket == null) return;
    final destinations = targets ?? [for (final host in _broadcast) (host, udpPort)];
    for (final (host, port) in destinations) {
      try {
        socket.send(datagram, InternetAddress(host), port);
      } on Object {
        // Réseau absent ou adresse injoignable : l'annonce suivante réessaiera.
      }
    }
  }

  void _onReadyRead() {
    final socket = _socket;
    if (socket == null) return;
    Datagram? datagram;
    while ((datagram = socket.receive()) != null) {
      Announcement announcement;
      try {
        announcement = decodeAnnounce(datagram!.data);
      } on ProtocolException {
        continue;
      }
      if (announcement.id == userId) continue;
      if (announcement.leaving) {
        if (_peers.remove(announcement.id) != null) peerLost.emit(announcement.id);
        continue;
      }
      final isNew = !_peers.containsKey(announcement.id);
      _peers[announcement.id] = monotonicMs();
      var host = datagram.address.address;
      if (host.startsWith('::ffff:')) host = host.substring(7);
      peerSeen.emit((announcement.id, announcement.name, host, announcement.port));
      if (isNew) announce(); // le nouveau venu nous découvre sans attendre
    }
  }

  void _expire() {
    final limit = monotonicMs() - peerTimeoutMs;
    for (final entry in _peers.entries.toList()) {
      if (entry.value < limit) {
        _peers.remove(entry.key);
        peerLost.emit(entry.key);
      }
    }
  }
}
