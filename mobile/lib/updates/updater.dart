// Mise à jour de l'application Android, transposée de src/socket_chat/updater.py.
//
// Chaque version publiée accompagne ses installeurs d'un manifeste `latest.json`
// (dépôt public des installeurs). L'application le lit, compare la version, télécharge
// l'APK (entrée « android »), vérifie taille et SHA-256, puis demande à Android de
// l'installer : la confirmation de l'utilisateur est toujours exigée par le système.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

import '../protocol/protocol.dart';

const String releasesRepo = 'andoajpr2/socket-chat-releases';
const String defaultUpdateUrl = 'https://github.com/$releasesRepo/releases/latest/download/latest.json';

/// Entrée du manifeste qui désigne l'APK universel (tous les téléphones).
const String androidAssetKey = 'android';

/// Entrée de l'APK propre à un type de processeur (« android-arm64-v8a »…), trois fois
/// plus léger que l'universel.
String androidAbiAssetKey(String abi) => '$androidAssetKey-$abi';

/// Taille maximale du manifeste et d'un fichier publié (octets).
const int maxManifestSize = 256 * 1024;
const int maxAssetSize = 1024 * 1024 * 1024;

/// Délai maximal sans données reçues.
const Duration networkTimeout = Duration(seconds: 60);

/// Manifeste absent, illisible ou invalide, ou téléchargement refusé.
class UpdateException implements Exception {
  UpdateException(this.message);
  final String message;
  @override
  String toString() => message;
}

final RegExp _version = RegExp(r'^(\d+)\.(\d+)\.(\d+)$');
final RegExp _sha256 = RegExp(r'^[0-9a-f]{64}$');

(int, int, int) parseVersion(String text) {
  final match = _version.firstMatch(text.trim());
  if (match == null) throw FormatException('Version invalide : $text');
  return (int.parse(match[1]!), int.parse(match[2]!), int.parse(match[3]!));
}

/// Vrai si [candidate] est strictement plus récente que [current].
bool isNewer(String candidate, String current) {
  final (a1, b1, c1) = parseVersion(candidate);
  final (a2, b2, c2) = parseVersion(current);
  if (a1 != a2) return a1 > a2;
  if (b1 != b2) return b1 > b2;
  return c1 > c2;
}

class Asset {
  const Asset(this.name, this.size, this.sha256, this.url);
  final String name;
  final int size;
  final String sha256;
  final Uri url;
}

class Manifest {
  const Manifest(this.version, this.published, this.notes, this.assets);
  final String version;
  final String published;
  final String notes;
  final Map<String, Asset> assets;

  /// L'APK universel de cette version, s'il est publié.
  Asset? get android => assets[androidAssetKey];

  /// L'APK le plus léger qui convient au téléphone : celui du premier type de processeur
  /// de [abis] (ordre de préférence d'Android) qui est publié, sinon l'universel.
  Asset? androidFor(List<String> abis) {
    for (final abi in abis) {
      final asset = assets[androidAbiAssetKey(abi)];
      if (asset != null) return asset;
    }
    return android;
  }
}

/// Adresse à laquelle chercher les fichiers d'une version.
///
/// Sur GitHub, « releases/latest/download/ » suit toujours la dernière publication : on
/// vise la version exacte annoncée, pour ne pas mélanger deux versions si une nouvelle
/// sort entre la lecture du manifeste et le téléchargement.
String assetBase(String manifestUrl, String version) {
  const marker = '/releases/latest/download/';
  return manifestUrl.contains(marker) ? manifestUrl.replaceFirst(marker, '/releases/download/v$version/') : manifestUrl;
}

/// Valide un `latest.json` ; les fichiers sont cherchés à côté du manifeste.
Manifest parseManifest(List<int> data, String manifestUrl) {
  if (data.length > maxManifestSize) throw UpdateException('Manifeste trop volumineux.');
  Object? payload;
  try {
    payload = jsonDecode(utf8.decode(data));
  } on FormatException {
    throw UpdateException('Manifeste illisible.');
  }
  if (payload is! Map<String, Object?>) throw UpdateException('Manifeste invalide.');

  final version = payload['version'];
  if (version is! String) throw UpdateException('Version absente.');
  try {
    parseVersion(version);
  } on FormatException catch (error) {
    throw UpdateException(error.message);
  }
  final published = payload['published'] ?? '';
  final notes = payload['notes'] ?? '';
  if (published is! String || published.length > 40) throw UpdateException('Date de publication invalide.');
  if (notes is! String || notes.length > 20000) throw UpdateException('Notes de version invalides.');

  final base = Uri.parse(assetBase(manifestUrl, version));
  final rawAssets = payload['assets'];
  if (rawAssets is! Map<String, Object?> || rawAssets.isEmpty) {
    throw UpdateException('Aucun fichier dans le manifeste.');
  }
  final assets = <String, Asset>{};
  for (final MapEntry(:key, :value) in rawAssets.entries) {
    if (value is! Map<String, Object?>) throw UpdateException('Fichier invalide dans le manifeste.');
    final name = value['name'];
    final size = value['size'];
    final digest = value['sha256'];
    if (name is! String || name.isEmpty || safeFileName(name) != name) {
      throw UpdateException('Nom de fichier refusé : $name');
    }
    if (size is! int || size <= 0 || size > maxAssetSize) throw UpdateException('Taille invalide pour $name.');
    if (digest is! String || !_sha256.hasMatch(digest)) throw UpdateException('Empreinte invalide pour $name.');
    assets[key] = Asset(name, size, digest, base.resolve(Uri.encodeComponent(name)));
  }
  return Manifest(version, published, notes, assets);
}

/// Lecture du manifeste et téléchargement vérifié, en HTTPS uniquement.
class UpdateClient {
  UpdateClient({HttpClient? client, this.allowInsecureForTests = false})
    : _client = client ?? (HttpClient()..connectionTimeout = const Duration(seconds: 30)) {
    _client.userAgent = 'SocketChat-Updater';
  }

  final HttpClient _client;

  /// Tests uniquement : autorise HTTP (serveur local).
  final bool allowInsecureForTests;

  void close() => _client.close(force: true);

  Future<Manifest> fetchManifest([String url = defaultUpdateUrl]) async {
    final response = await _get(Uri.parse(url));
    final data = <int>[];
    await for (final chunk in response.timeout(networkTimeout)) {
      data.addAll(chunk);
      if (data.length > maxManifestSize) throw UpdateException('Manifeste trop volumineux.');
    }
    return parseManifest(data, url);
  }

  /// Télécharge [asset] dans [directory] et vérifie taille et empreinte ; le fichier est
  /// supprimé au moindre problème.
  Future<File> download(Asset asset, Directory directory, {void Function(int done, int total)? onProgress}) async {
    directory.createSync(recursive: true);
    final part = File('${directory.path}/${asset.name}.part');
    final target = File('${directory.path}/${asset.name}');
    final sink = part.openWrite();
    final digest = _DigestSink();
    final hasher = sha256.startChunkedConversion(digest);
    var received = 0;
    try {
      final response = await _get(asset.url);
      await for (final chunk in response.timeout(networkTimeout)) {
        received += chunk.length;
        if (received > asset.size) throw UpdateException('Fichier plus gros qu\'annoncé : téléchargement refusé.');
        hasher.add(chunk);
        sink.add(chunk);
        onProgress?.call(received, asset.size);
      }
      await sink.close();
      hasher.close();
      if (received != asset.size) throw UpdateException('Téléchargement incomplet.');
      if (digest.value.toString() != asset.sha256) {
        throw UpdateException('Empreinte du fichier incorrecte : téléchargement refusé.');
      }
      if (target.existsSync()) target.deleteSync();
      return part.renameSync(target.path);
    } catch (error) {
      await sink.close().catchError((_) {});
      if (part.existsSync()) part.deleteSync();
      if (error is UpdateException) rethrow;
      if (error is TimeoutException) throw UpdateException('Délai dépassé pendant le téléchargement.');
      if (error is IOException) throw UpdateException('Téléchargement impossible : vérifiez la connexion à Internet.');
      rethrow;
    }
  }

  Future<HttpClientResponse> _get(Uri url) async {
    _checkScheme(url);
    HttpClientResponse response;
    try {
      final request = await _client.getUrl(url).timeout(networkTimeout);
      response = await request.close().timeout(networkTimeout);
    } on TimeoutException {
      throw UpdateException('Délai dépassé : serveur de mise à jour injoignable.');
    } on IOException {
      throw UpdateException('Serveur de mise à jour injoignable : vérifiez la connexion à Internet.');
    }
    for (final redirect in response.redirects) {
      _checkScheme(redirect.location.hasScheme ? redirect.location : url.resolveUri(redirect.location));
    }
    if (response.statusCode != HttpStatus.ok) {
      await response.drain<void>().catchError((_) {});
      throw UpdateException('Réponse inattendue du serveur (${response.statusCode}).');
    }
    return response;
  }

  void _checkScheme(Uri url) {
    if (url.scheme == 'https') return;
    if (allowInsecureForTests && url.scheme == 'http') return;
    throw UpdateException('Adresse de mise à jour refusée (« ${url.scheme} ») : HTTPS uniquement.');
  }
}

class _DigestSink implements Sink<Digest> {
  late Digest value;
  @override
  void add(Digest data) => value = data;
  @override
  void close() {}
}
