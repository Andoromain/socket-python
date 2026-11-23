// Chiffrement des connexions : Noise_XX_25519_ChaChaPoly_SHA256.
//
// Transposé pas à pas de src/socket_chat/noise.py (même spécification, mêmes noms) et
// vérifié avec le même vecteur officiel (test/fixtures/noise_xx_vector.json).
//
//     -> e
//     <- e, ee, s, es
//     -> s, se

import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as hashes;
import 'package:cryptography/cryptography.dart' as c;
import 'package:cryptography/dart.dart' as cd;

const List<int> protocolName = [
  // "Noise_XX_25519_ChaChaPoly_SHA256" (32 octets)
  78, 111, 105, 115, 101, 95, 88, 88, 95, 50, 53, 53, 49, 57, 95, 67, //
  104, 97, 67, 104, 97, 80, 111, 108, 121, 95, 83, 72, 65, 50, 53, 54,
];
const int dhLen = 32;
const int hashLen = 32;
const int tagLen = 16;

/// Taille maximale d'un message Noise (octets, étiquette d'authentification comprise).
const int maxMessageSize = 65535;

/// Données utiles maximales par message de transport.
const int maxPayloadSize = maxMessageSize - tagLen;

class NoiseException implements Exception {
  NoiseException(this.message);
  final String message;

  @override
  String toString() => message;
}

// -- Primitives ---------------------------------------------------------------------
const cd.DartX25519 _x25519 = cd.DartX25519();
const cd.DartChacha20 _aead = cd.DartChacha20.poly1305Aead();
final Uint8List _basePoint = Uint8List(32)..[0] = 9;

Uint8List _scalarMult(Uint8List private, List<int> public) {
  final keyPair = c.SimpleKeyPairData(
    private,
    publicKey: c.SimplePublicKey(_basePoint, type: c.KeyPairType.x25519),
    type: c.KeyPairType.x25519,
  );
  final secret = _x25519.sharedSecretSync(
    keyPairData: keyPair,
    remotePublicKey: c.SimplePublicKey(List<int>.of(public), type: c.KeyPairType.x25519),
  ) as c.SecretKeyData;
  return Uint8List.fromList(secret.bytes);
}

/// Paire de clés X25519 (32 octets bruts chacune).
class KeyPair {
  KeyPair(List<int> private)
    : private = Uint8List.fromList(private),
      public = _scalarMult(Uint8List.fromList(private), _basePoint) {
    if (private.length != dhLen) throw NoiseException('Clé privée invalide.');
  }

  factory KeyPair.generate([Random? random]) {
    final rng = random ?? Random.secure();
    return KeyPair(List<int>.generate(dhLen, (_) => rng.nextInt(256)));
  }

  final Uint8List private;
  final Uint8List public;

  Uint8List dh(List<int> remotePublic) {
    if (remotePublic.length != dhLen) throw NoiseException('Clé publique du correspondant invalide.');
    final shared = _scalarMult(private, remotePublic);
    // Clé publique de petit ordre : secret nul (refusé aussi côté PC).
    if (shared.every((b) => b == 0)) throw NoiseException('Clé publique du correspondant invalide.');
    return shared;
  }
}

Uint8List _hash(List<int> data) => Uint8List.fromList(hashes.sha256.convert(data).bytes);

Uint8List _hmac(List<int> key, List<int> data) =>
    Uint8List.fromList(hashes.Hmac(hashes.sha256, key).convert(data).bytes);

(Uint8List, Uint8List) _hkdf(List<int> chainingKey, List<int> ikm) {
  final temp = _hmac(chainingKey, ikm);
  final out1 = _hmac(temp, const [1]);
  return (out1, _hmac(temp, [...out1, 2]));
}

Uint8List _concat(List<int> a, List<int> b) => Uint8List(a.length + b.length)
  ..setAll(0, a)
  ..setAll(a.length, b);

// -- États de la spécification ----------------------------------------------------------
class CipherState {
  CipherState([List<int>? key]) : _key = key == null ? null : c.SecretKeyData(List<int>.of(key));

  final c.SecretKeyData? _key;
  int n = 0;

  bool get hasKey => _key != null;

  List<int> _nonce() {
    // 2^64 - 1 est réservé ; un int Dart (64 bits signés) ne l'atteint pas en pratique.
    if (n < 0) throw NoiseException('Trop de messages sur cette connexion.');
    final nonce = Uint8List(12);
    ByteData.sublistView(nonce).setUint64(4, n, Endian.little);
    return nonce;
  }

  Uint8List encrypt(List<int> ad, List<int> plaintext) {
    final key = _key;
    if (key == null) return Uint8List.fromList(plaintext);
    final box = _aead.encryptSync(plaintext, secretKey: key, nonce: _nonce(), aad: ad);
    n++;
    return _concat(box.cipherText, box.mac.bytes);
  }

  Uint8List decrypt(List<int> ad, List<int> ciphertext) {
    final key = _key;
    if (key == null) return Uint8List.fromList(ciphertext);
    if (ciphertext.length < tagLen) throw NoiseException('Message chiffré altéré ou clé incorrecte.');
    final split = ciphertext.length - tagLen;
    final box = c.SecretBox(ciphertext.sublist(0, split), nonce: _nonce(), mac: c.Mac(ciphertext.sublist(split)));
    List<int> plaintext;
    try {
      plaintext = _aead.decryptSync(box, secretKey: key, aad: ad);
    } on c.SecretBoxAuthenticationError {
      throw NoiseException('Message chiffré altéré ou clé incorrecte.');
    }
    n++;
    return Uint8List.fromList(plaintext);
  }
}

class SymmetricState {
  SymmetricState(List<int> name) : h = name.length <= hashLen ? (Uint8List(hashLen)..setAll(0, name)) : _hash(name) {
    ck = h;
  }

  Uint8List h;
  late Uint8List ck;
  CipherState cipher = CipherState();

  void mixKey(List<int> ikm) {
    final (newCk, tempK) = _hkdf(ck, ikm);
    ck = newCk;
    cipher = CipherState(tempK);
  }

  void mixHash(List<int> data) => h = _hash(_concat(h, data));

  Uint8List encryptAndHash(List<int> plaintext) {
    final ciphertext = cipher.encrypt(h, plaintext);
    mixHash(ciphertext);
    return ciphertext;
  }

  Uint8List decryptAndHash(List<int> ciphertext) {
    final plaintext = cipher.decrypt(h, ciphertext);
    mixHash(ciphertext);
    return plaintext;
  }

  (CipherState, CipherState) split() {
    final (k1, k2) = _hkdf(ck, const []);
    return (CipherState(k1), CipherState(k2));
  }
}

/// Poignée de main XX ; [writeMessage] / [readMessage] à tour de rôle.
///
/// L'initiateur écrit les messages 1 et 3, le répondeur le message 2. Une fois
/// [complete], [remoteStatic] est la clé publique prouvée du correspondant et
/// [send] / [receive] chiffrent le transport.
class HandshakeState {
  HandshakeState(
    this.initiator,
    this._s, {
    List<int> prologue = const [],
    KeyPair? ephemeral, // imposée par les vecteurs de test
  }) : _fixedE = ephemeral {
    _ss.mixHash(prologue);
  }

  final bool initiator;
  final KeyPair _s;
  final KeyPair? _fixedE;
  final SymmetricState _ss = SymmetricState(protocolName);
  KeyPair? _e;
  Uint8List? _re;
  Uint8List? remoteStatic;
  int _step = 0;
  CipherState? send;
  CipherState? receive;

  bool get complete => send != null;
  Uint8List get handshakeHash => _ss.h;
  bool get myTurn => !complete && (_step % 2 == 0) == initiator;

  KeyPair _ephemeral() => _e = _fixedE ?? KeyPair.generate();

  Uint8List writeMessage([List<int> payload = const []]) {
    if (!myTurn) throw NoiseException("Ce n'est pas à ce poste d'écrire.");
    final out = BytesBuilder(copy: false);
    if (_step == 0) {
      // -> e
      final e = _ephemeral();
      out.add(e.public);
      _ss.mixHash(e.public);
    } else if (_step == 1) {
      // <- e, ee, s, es
      final e = _ephemeral();
      out.add(e.public);
      _ss.mixHash(e.public);
      _ss.mixKey(e.dh(_re!));
      out.add(_ss.encryptAndHash(_s.public));
      _ss.mixKey(_s.dh(_re!));
    } else {
      // -> s, se
      out.add(_ss.encryptAndHash(_s.public));
      _ss.mixKey(_s.dh(_re!));
    }
    out.add(_ss.encryptAndHash(payload));
    _advance();
    final message = out.takeBytes();
    if (message.length > maxMessageSize) throw NoiseException('Message de poignée de main trop long.');
    return message;
  }

  Uint8List readMessage(List<int> message) {
    if (complete || myTurn) throw NoiseException('Message de poignée de main inattendu.');
    if (message.length > maxMessageSize) throw NoiseException('Message de poignée de main trop long.');
    var rest = message;
    List<int> take(int size) {
      if (rest.length < size) throw NoiseException('Message de poignée de main tronqué.');
      final head = rest.sublist(0, size);
      rest = rest.sublist(size);
      return head;
    }

    if (_step == 0) {
      // -> e
      _re = Uint8List.fromList(take(dhLen));
      _ss.mixHash(_re!);
    } else if (_step == 1) {
      // <- e, ee, s, es
      _re = Uint8List.fromList(take(dhLen));
      _ss.mixHash(_re!);
      _ss.mixKey(_e!.dh(_re!));
      remoteStatic = _ss.decryptAndHash(take(dhLen + tagLen));
      _ss.mixKey(_e!.dh(remoteStatic!));
    } else {
      // -> s, se
      remoteStatic = _ss.decryptAndHash(take(dhLen + tagLen));
      _ss.mixKey(_e!.dh(remoteStatic!));
    }
    final payload = _ss.decryptAndHash(rest);
    _advance();
    return payload;
  }

  void _advance() {
    _step++;
    if (_step == 3) {
      final (first, second) = _ss.split();
      if (initiator) {
        send = first;
        receive = second;
      } else {
        send = second;
        receive = first;
      }
    }
  }
}

// -- Flux chiffré ----------------------------------------------------------------------------
/// Découpe un flux TCP en messages Noise préfixés par leur longueur (2 octets).
class FrameReader {
  final BytesBuilder _buffer = BytesBuilder(copy: false);
  Uint8List _pending = Uint8List(0);

  List<Uint8List> feed(List<int> data) {
    _buffer
      ..add(_pending)
      ..add(data);
    var bytes = _buffer.takeBytes();
    final messages = <Uint8List>[];
    var offset = 0;
    while (bytes.length - offset >= 2) {
      final size = (bytes[offset] << 8) | bytes[offset + 1];
      if (bytes.length - offset - 2 < size) break;
      messages.add(Uint8List.sublistView(bytes, offset + 2, offset + 2 + size));
      offset += 2 + size;
    }
    _pending = Uint8List.sublistView(bytes, offset);
    return messages;
  }
}

Uint8List frame(List<int> message) {
  if (message.length > maxMessageSize) throw NoiseException('Message trop long.');
  return Uint8List(message.length + 2)
    ..[0] = message.length >> 8
    ..[1] = message.length & 0xff
    ..setAll(2, message);
}

/// Chiffre [data] en un ou plusieurs messages de transport prêts à envoyer.
Uint8List encryptStream(CipherState cipher, List<int> data) {
  final out = BytesBuilder(copy: false);
  for (var start = 0; start < data.length; start += maxPayloadSize) {
    final end = min(start + maxPayloadSize, data.length);
    out.add(frame(cipher.encrypt(const [], data.sublist(start, end))));
  }
  return out.takeBytes();
}

/// Préambule des poignées de main (identique côté PC : identity.PROLOGUE).
final Uint8List prologue = Uint8List.fromList(utf8.encode('SocketChat/4'));
