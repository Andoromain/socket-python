// Pair Socket Chat en ligne de commande, sans interface, pour le test
// d'interopérabilité avec la version PC (tests/test_interop.py).
//
// Usage : dart run tool/peer.dart --name Téléphone
//
// Commandes (une ligne JSON par commande sur l'entrée standard) :
//   {"cmd": "connect", "host": "127.0.0.1", "port": 12800}
//   {"cmd": "send", "to": "<id>", "text": "..."}
//   {"cmd": "send_file", "to": "<id>", "path": "..."}
//   {"cmd": "accept", "id": "<message>", "path": "..."}
//   {"cmd": "read", "contact": "<id>"}
//   {"cmd": "react", "id": "<message>", "emoji": "👍"}
//   {"cmd": "typing", "to": "<id>", "active": true}
//   {"cmd": "avatar", "data": "<base64>"}          (vide : photo retirée)
//   {"cmd": "discover", "target_port": 45678}
//   {"cmd": "safety", "contact": "<id>"}
//   {"cmd": "call", "to": "<id>", "media": "video"} · {"cmd": "accept_call"} · {"cmd": "decline_call"}
//   {"cmd": "hangup"}                (média simulé : signalisation seulement)
//   {"cmd": "quit"}
// Événements (une ligne JSON par événement sur la sortie standard) : started,
// presence, message, status, changed, typing, avatar, error, result, discovery, safety.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:socket_chat_mobile/net/calls.dart';
import 'package:socket_chat_mobile/net/discovery.dart';
import 'package:socket_chat_mobile/net/identity.dart';
import 'package:socket_chat_mobile/net/messenger.dart';
import 'package:socket_chat_mobile/protocol/protocol.dart';
import 'package:socket_chat_mobile/store/store.dart';

void emit(String event, [Map<String, Object?> fields = const {}]) {
  stdout.writeln(jsonEncode({'event': event, ...fields}));
}

Map<String, Object?> describe(MessageRecord record) => {
  'id': record.id,
  'contact': record.contactId,
  'outgoing': record.outgoing,
  'text': record.text,
  'status': record.status.value,
  'file': record.file == null
      ? null
      : {'name': record.file!.name, 'size': record.file!.size, 'state': record.file!.state.value},
  'reactions': {for (final (author, emoji) in record.reactions) author: emoji},
};

/// Média simulé : la signalisation est réelle, la liaison « se connecte » après l'échange
/// de descriptions (flutter_webrtc n'existe pas hors d'un appareil).
class SimulatedEngine extends MediaEngine {
  @override
  Future<void> start({required bool caller, required String media}) async {
    if (caller) Timer.run(() => localDescription.emit(('offer', 'v=0\r\ns=pair Dart\r\n')));
    Timer.run(() => localIce.emit(('candidate:1 1 udp 2122260223 127.0.0.1 9 typ host', '0', 0)));
  }

  @override
  Future<void> setRemoteDescription(String kind, String sdp) async {
    if (kind == 'offer') Timer.run(() => localDescription.emit(('answer', 'v=0\r\ns=pair Dart\r\n')));
    Timer(const Duration(milliseconds: 20), () => connected.emit(null));
  }

  @override
  Future<void> addRemoteIce(String candidate, String mid, int index) async {}

  @override
  void setMuted(bool muted) {}

  @override
  Future<void> stop() async {}
}

Future<void> main(List<String> args) async {
  final nameIndex = args.indexOf('--name');
  final name = nameIndex >= 0 && nameIndex + 1 < args.length ? args[nameIndex + 1] : 'Téléphone';
  final store = Store();
  final messenger = Messenger(store, newId(), name);
  Discovery? discovery;

  messenger.presenceChanged.connect((p) => emit('presence', {'contact': p.$1, 'online': p.$2}));
  messenger.messageAdded.connect((record) => emit('message', describe(record)));
  messenger.messageStatusChanged.connect((s) => emit('status', {'id': s.$1, 'status': s.$2.value}));
  messenger.messageChanged.connect((id) => emit('changed', describe(store.message(id)!)));
  messenger.typingChanged.connect((t) => emit('typing', {'contact': t.$1, 'active': t.$2}));
  messenger.avatarChanged.connect(
    (id) => emit('avatar', {'contact': id, 'data': base64Encode(store.contact(id)!.avatar)}),
  );
  messenger.error.connect((message) => emit('error', {'message': message}));
  final calls = CallManager(messenger, SimulatedEngine.new);
  calls.incomingCall.connect((c) => emit('incoming_call', {'contact': c.$1, 'media': c.$2}));
  calls.stateChanged.connect((c) => emit('call_state', {'state': c?.state.name, 'id': c?.id}));
  calls.callEnded.connect((e) => emit('call_ended', {'contact': e.$1, 'outcome': e.$2.value}));

  if (!await messenger.start(0)) exit(1);
  emit('started', {'id': messenger.userId, 'port': messenger.port, 'key': encodeKey(messenger.identity.public)});

  final done = Completer<void>();
  final lines = stdin.transform(utf8.decoder).transform(const LineSplitter());
  final subscription = lines.listen(
    (line) async {
      if (line.trim().isEmpty) return;
      final command = jsonDecode(line) as Map<String, Object?>;
      try {
        switch (command['cmd']) {
          case 'connect':
            messenger.addContact(command['host']! as String, command['port']! as int);
          case 'send':
            final record = messenger.send(command['to']! as String, command['text']! as String);
            emit('result', {'id': record.id});
          case 'send_file':
            final record = messenger.sendFile(command['to']! as String, command['path']! as String);
            emit('result', {'id': record.id});
          case 'accept':
            messenger.acceptFile(command['id']! as String, command['path']! as String);
          case 'read':
            messenger.markRead(command['contact']! as String);
          case 'react':
            messenger.react(command['id']! as String, command['emoji']! as String);
          case 'typing':
            messenger.setTyping(command['to']! as String, command['active']! as bool);
          case 'avatar':
            messenger.setAvatar(base64Decode(command['data']! as String));
          case 'discover':
            final d = Discovery(
              messenger.userId,
              messenger.name,
              messenger.port!,
              udpPort: 0,
              targets: [('127.0.0.1', command['target_port']! as int)],
            );
            await d.start();
            messenger.attachDiscovery(d);
            discovery = d;
            emit('discovery', {'port': d.localPort});
            d.announce();
          case 'safety':
            final contact = command['contact']! as String;
            final key = messenger.contactKey(contact);
            emit('safety', {
              'contact': contact,
              'key': key == null ? null : encodeKey(key),
              'number': key == null ? null : safetyNumber(messenger.identity.public, key),
            });
          case 'call':
            final media = command['media'] as String? ?? 'audio';
            emit('result', {'id': calls.startCall(command['to']! as String, media).id});
          case 'accept_call':
            calls.accept();
          case 'decline_call':
            calls.decline();
          case 'hangup':
            calls.hangup();
          case 'quit':
            if (!done.isCompleted) done.complete();
          default:
            emit('error', {'message': 'Commande inconnue : ${command['cmd']}'});
        }
      } on ArgumentError catch (exc) {
        emit('error', {'message': '${exc.message}'});
      } on StateError catch (exc) {
        emit('error', {'message': exc.message});
      }
    },
    onDone: () {
      if (!done.isCompleted) done.complete();
    },
  );

  await done.future;
  await subscription.cancel();
  discovery?.stop();
  messenger.stop();
  await Future<void>.delayed(const Duration(milliseconds: 300)); // laisse partir les « bye »
  emit('stopped');
  store.close();
  exit(0);
}
