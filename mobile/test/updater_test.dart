// Mise à jour Android : manifeste (contre le vrai make_latest.py) et téléchargement vérifié.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
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
      'https://github.com/andoajpr2/socket-chat-releases/releases/download/v9.8.7/socket-chat-9.8.7-android.apk',
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
      client = UpdateClient(allowInsecureForTests: true);
    });
    tearDown(() async {
      client.close();
      await server.close(force: true);
      tmp.deleteSync(recursive: true);
    });

    String base() => 'http://127.0.0.1:${server.port}';

    Asset assetFor(List<int> content, {List<int>? announced, int? size}) => Asset(
      'socket-chat-9.9.9-android.apk',
      size ?? content.length,
      sha256.convert(announced ?? content).toString(),
      Uri.parse('${base()}/maj/socket-chat-9.9.9-android.apk'),
    );

    test('manifeste lu puis APK téléchargé et vérifié', () async {
      final apk = Uint8List.fromList(List.generate(300000, (i) => i % 253));
      routes['/maj/latest.json'] = (
        200,
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
      routes['/maj/latest.json'] = (200, encoded(manifestJson()));
      expect((await client.fetchManifest('${base()}/redirige')).version, '9.9.9');
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
