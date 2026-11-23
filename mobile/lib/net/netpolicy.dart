// Réseaux autorisés, transposé de src/socket_chat/netpolicy.py.
//
// Liste au format CIDR (« 10.20.0.0/16, 192.168.1.0/24 ») : une connexion ou une
// annonce venant d'une autre adresse est ignorée. Liste vide : aucune restriction. La
// boucle locale reste toujours autorisée.
//
// Sur Android, la liste vient de la configuration gérée par l'administrateur (MDM,
// clé « allowed_networks ») ou, à défaut, des Préférences de l'application.

import 'dart:io';
import 'dart:typed_data';

class Network {
  Network._(this._bytes, this.prefix);

  final Uint8List _bytes;
  final int prefix;

  int get version => _bytes.length == 4 ? 4 : 6;

  bool contains(Uint8List address) {
    if (address.length != _bytes.length) return false;
    var bits = prefix;
    for (var i = 0; i < address.length && bits > 0; i++) {
      final mask = bits >= 8 ? 0xff : (0xff << (8 - bits)) & 0xff;
      if ((address[i] & mask) != (_bytes[i] & mask)) return false;
      bits -= 8;
    }
    return true;
  }

  @override
  String toString() => '${InternetAddress.fromRawAddress(_masked()).address}/$prefix';

  Uint8List _masked() {
    final result = Uint8List.fromList(_bytes);
    var bits = prefix;
    for (var i = 0; i < result.length; i++) {
      final mask = bits >= 8 ? 0xff : (bits <= 0 ? 0 : (0xff << (8 - bits)) & 0xff);
      result[i] &= mask;
      bits -= 8;
    }
    return result;
  }
}

InternetAddress? _parseAddress(String text) {
  final plain = text.split('%').first; // « fe80::1%wlan0 »
  return InternetAddress.tryParse(plain);
}

/// Réseaux au format CIDR ou adresses seules ; entrées invalides ignorées.
List<Network> parseNetworks(String entries) {
  final networks = <Network>[];
  for (final raw in entries.replaceAll(';', ',').split(',')) {
    final entry = raw.trim();
    if (entry.isEmpty) continue;
    final slash = entry.indexOf('/');
    final address = _parseAddress(slash < 0 ? entry : entry.substring(0, slash));
    if (address == null) continue;
    final maximum = address.rawAddress.length * 8;
    final prefix = slash < 0 ? maximum : int.tryParse(entry.substring(slash + 1));
    if (prefix == null || prefix < 0 || prefix > maximum) continue;
    networks.add(Network._(address.rawAddress, prefix));
  }
  return networks;
}

/// Vrai si [address] appartient à l'un des réseaux (ou si la liste est vide).
bool isAllowed(String address, List<Network> networks) {
  if (networks.isEmpty) return true;
  var ip = _parseAddress(address);
  if (ip == null) return false;
  var raw = ip.rawAddress;
  // « ::ffff:10.0.0.5 » (sockets double pile) : comparé comme une adresse IPv4.
  if (raw.length == 16 && raw.sublist(0, 10).every((b) => b == 0) && raw[10] == 0xff && raw[11] == 0xff) {
    raw = Uint8List.fromList(raw.sublist(12));
    ip = InternetAddress.fromRawAddress(raw);
  }
  if (ip.isLoopback) return true;
  return networks.any((network) => network.contains(raw));
}
