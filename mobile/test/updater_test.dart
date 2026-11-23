// Mise à jour Android : manifeste (contre le vrai make_latest.py) et téléchargement vérifié.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:cryptography/cryptography.dart' as c;
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/updates/updater.dart';

Map<String, Object?> loadVectors() =>
    jsonDecode(File('test/fixtures/protocol_vectors.json').readAsStringSync()) as Map<String, Object?>;

Map<String, Object?> manifestJson({
  String version = '9.9.9',
  Map<String, Object?>? android,
  Object? notes = 'Nouveautés',
}) => {
  'version': version,
  'published': '2026-11-06',
  'notes': notes,
  'assets': {
    'android': android ?? {'name': 'socket-chat-$version-android.apk', 'size': 10, 'sha256': 'a' * 64},
  },
};

List<int> encoded(Map<String, Object?> json) => utf8.encode(jsonEncode(json));

/// Clé de signature des tests (la vraie clé privée n'est que dans les secrets GitHub).
final Future<c.SimpleKeyPair> testKey = c.Ed25519().newKeyPairFromSeed(List<int>.generate(32, (i) => i + 7));

Future<String> testPublicKey() async => base64.encode((await (await testKey).extractPublicKey()).bytes);

Future<List<int>> signed(List<int> data) async {
  final signature = await c.Ed25519().sign(data, keyPair: await testKey);
  return utf8.encode(base64.encode(signature.bytes));
}

void main() {
  test('versions', () {
    expect(parseVersion('3.10.2'), (3, 10, 2));
    expect(isNewer('3.5.0', '3.4.0'), isTrue);
    expect(isNewer('3.10.0', '3.9.9'), isTrue); // comparaison numérique, pas textuelle
    expect(isNewer('3.4.0', '3.4.0'), isFalse);
    expect(isNewer('3.3.1', '3.4.0'), isFalse);
    expect(() => parseVersion('3.4'), throwsFormatException);
    expect(() => parseVersion('v3.4.0'), throwsFormatException);
  });

  test('manifeste produit par make_latest.py (version PC) accepté', () {
    final vector = loadVectors()['update_manifest']! as Map<String, Object?>;
    final url = vector['url']! as String;
    final raw = vector['manifest']! as Map<String, Object?>;
    final manifest = parseManifest(encoded(raw), url);
    expect(manifest.version, '9.8.7');
    expect(manifest.notes, '### Ajouté\n- Une nouveauté « accentuée ».');
    final apk = manifest.android!;
    final expected = ((raw['assets']! as Map)['android']! as Map).cast<String, Object?>();
    expect(apk.name, 'socket-chat-9.8.7-android.apk');
    expect(apk.size, expected['size']);
    expect(apk.sha256, expected['sha256']);
    // Version exacte, pas « latest » : pas de mélange si une version sort entre-temps.
    expect(
      apk.url.toString(),
      'https://github.com/Andoromain/socket-python/releases/download/v9.8.7/socket-chat-9.8.7-android.apk',
    );
    expect(manifest.assets.keys, containsAll(['windows-x64', 'linux-x86_64', 'android']));
    // Pas d'APK par processeur dans ce manifeste : l'universel convient à tous.
    expect(manifest.androidFor(['arm64-v8a'])!.name, 'socket-chat-9.8.7-android.apk');
  });

  test('manifestes refusés', () {
    const url = 'https://exemple.fr/maj/latest.json';
    void refused(Object payload) =>
        expect(() => parseManifest(utf8.encode(jsonEncode(payload)), url), throwsA(isA<UpdateException>()));
    refused(manifestJson(version: '3.4'));
    refused(manifestJson(android: {'name': '../evil.apk', 'size': 10, 'sha256': 'a' * 64}));
    refused(manifestJson(android: {'name': 'a.apk', 'size': 0, 'sha256': 'a' * 64}));
    refused(manifestJson(android: {'name': 'a.apk', 'size': 10, 'sha256': 'A' * 64}));
    refused(manifestJson(notes: 5));
    refused({'version': '1.2.3', 'assets': {}});
    refused([1, 2]);
    expect(() => parseManifest(utf8.encode('pas du json'), url), throwsA(isA<UpdateException>()));
    expect(() => parseManifest(List.filled(maxManifestSize + 1, 32), url), throwsA(isA<UpdateException>()));
    // Sans APK : le manifeste reste valide, mais rien à proposer au téléphone.
    final pcOnly = {
      'version': '9.9.9',
      'assets': {
        'windows-x64': {'name': 'x.exe', 'size': 1, 'sha256': 'b' * 64},
      },
    };
    expect(parseManifest(encoded(pcOnly), url).android, isNull);
  });

  group('signature du manifeste', () {
    test('signée par la version PC (signing.py) : acceptée', () async {
      final vector = loadVectors()['update_signature']! as Map<String, Object?>;
      final data = base64.decode(vector['manifest']! as String);
      final signature = utf8.encode(vector['signature']! as String);
      final keys = [vector['public_key']! as String];
      await verifyManifest(data, signature, keys: keys);
      await verifyManifest(data, utf8.encode('${vector['signature']}\n'), keys: keys); // fin de ligne
      final tampered = List<int>.of(data)..[10] ^= 1;
      await expectLater(verifyManifest(tampered, signature, keys: keys), throwsA(isA<UpdateException>()));
      await expectLater(verifyManifest(data, signature), throwsA(isA<UpdateException>())); // autre clé
      for (final bad in ['', 'pas du base64 !', 'AAAA', 'A' * 2000]) {
        await expectLater(verifyManifest(data, utf8.encode(bad), keys: keys), throwsA(isA<UpdateException>()));
      }
    });

    test('adresse de la signature', () {
      expect(
        signatureUrl('https://github.com/o/r/releases/latest/download/latest.json', '9.9.9'),
        'https://github.com/o/r/releases/download/v9.9.9/latest.json.sig',
      );
      expect(signatureUrl('https://intranet/maj/latest.json', '1.0.0'), 'https://intranet/maj/latest.json.sig');
    });

    test('clé publique identique à la version PC', () {
      final python = File('../src/socket_chat/signing.py').readAsStringSync();
      final keys = RegExp(r'TRUSTED_KEYS[^=]*= \(([^)]*)\)').firstMatch(python)![1]!;
      expect(RegExp(r'"([^"]+)"').allMatches(keys).map((m) => m[1]).toList(), trustedUpdateKeys);
    });
  });

  group('téléchargement', () {
    late HttpServer server;
    late Directory tmp;
    late UpdateClient client;
    final routes = <String, (int, List<int>)>{};

    setUp(() async {
      tmp = Directory.systemTemp.createTempSync('socket-chat-maj-');
      routes.clear();
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        final route = routes[request.uri.path];
        if (request.uri.path == '/redirige') {
          request.response.redirect(Uri.parse('http://127.0.0.1:${server.port}/maj/latest.json'));
          return;
        }
        request.response.statusCode = route?.$1 ?? 404;
        if (route != null) request.response.add(route.$2);
        await request.response.close();
      });
      client = UpdateClient(allowInsecureForTests: true, trustedKeys: [await testPublicKey()]);
    });
    tearDown(() async {
      client.close();
      await server.close(force: true);
      tmp.deleteSync(recursive: true);
    });

    String base() => 'http://127.0.0.1:${server.port}';

    /// Publie un manifeste et sa signature.
    Future<void> publish(String path, List<int> data, {bool sign = true}) async {
      routes[path] = (200, data);
      if (sign) routes['$path.sig'] = (200, await signed(data));
    }

    Asset assetFor(List<int> content, {List<int>? announced, int? size}) => Asset(
      'socket-chat-9.9.9-android.apk',
      size ?? content.length,
      sha256.convert(announced ?? content).toString(),
      Uri.parse('${base()}/maj/socket-chat-9.9.9-android.apk'),
    );

    test('manifeste lu puis APK téléchargé et vérifié', () async {
      final apk = Uint8List.fromList(List.generate(300000, (i) => i % 253));
      await publish(
        '/maj/latest.json',
        encoded(
          manifestJson(
            android: {
              'name': 'socket-chat-9.9.9-android.apk',
              'size': apk.length,
              'sha256': sha256.convert(apk).toString(),
            },
          ),
        ),
      );
      routes['/maj/socket-chat-9.9.9-android.apk'] = (200, apk);
      final manifest = await client.fetchManifest('${base()}/maj/latest.json');
      final progress = <int>[];
      final file = await client.download(manifest.android!, tmp, onProgress: (done, _) => progress.add(done));
      expect(file.readAsBytesSync(), apk);
      expect(progress.last, apk.length);
      expect(tmp.listSync().map((e) => e.path.split('/').last), ['socket-chat-9.9.9-android.apk']);
    });

    test('redirection suivie (comme GitHub vers son stockage)', () async {
      final data = encoded(manifestJson());
      await publish('/maj/latest.json', data);
      routes['/redirige.sig'] = (200, await signed(data));
      expect((await client.fetchManifest('${base()}/redirige')).version, '9.9.9');
    });

    test('manifeste sans signature ou modifié : refusé', () async {
      final data = encoded(manifestJson());
      await publish('/maj/latest.json', data, sign: false);
      await expectLater(
        client.fetchManifest('${base()}/maj/latest.json'),
        throwsA(isA<UpdateException>().having((e) => e.message, 'message', contains('404'))),
      );
      routes['/maj/latest.json.sig'] = (200, await signed(encoded(manifestJson(version: '9.9.8'))));
      await expectLater(
        client.fetchManifest('${base()}/maj/latest.json'),
        throwsA(isA<UpdateException>().having((e) => e.message, 'message', contains('signature invalide'))),
      );
    });

    test('empreinte fausse : refusé et supprimé', () async {
      final apk = List.filled(1000, 7);
      routes['/maj/socket-chat-9.9.9-android.apk'] = (200, apk);
      await expectLater(
        client.download(assetFor(apk, announced: List.filled(1000, 8)), tmp),
        throwsA(isA<UpdateException>().having((e) => e.message, 'message', contains('Empreinte'))),
      );
      expect(tmp.listSync(), isEmpty);
    });

    test('fichier plus gros qu\'annoncé : coupé et refusé', () async {
      final apk = List.filled(5000, 1);
      routes['/maj/socket-chat-9.9.9-android.apk'] = (200, apk);
      await expectLater(client.download(assetFor(apk, size: 100), tmp), throwsA(isA<UpdateException>()));
      expect(tmp.listSync(), isEmpty);
    });

    test('fichier absent (404)', () async {
      await expectLater(
        client.download(assetFor([1, 2, 3]), tmp),
        throwsA(isA<UpdateException>().having((e) => e.message, 'message', contains('404'))),
      );
      expect(tmp.listSync(), isEmpty);
    });

    test('HTTP refusé hors tests', () async {
      final strict = UpdateClient();
      addTearDown(strict.close);
      await expectLater(strict.fetchManifest('${base()}/maj/latest.json'), throwsA(isA<UpdateException>()));
    });
  });
}
