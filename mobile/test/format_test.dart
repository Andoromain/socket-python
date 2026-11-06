import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/ui/format.dart';
import 'package:socket_chat_mobile/version.dart';

void main() {
  test('initiales comme sur PC', () {
    expect(initials('Jean Dupont'), 'JD');
    expect(initials('Jean-Pierre Martin'), 'JM');
    expect(initials('Élodie'), 'ÉL');
    expect(initials('x'), 'X');
    expect(initials('  '), '?');
    expect(initials('🎉 Fête'), 'FÊ');
  });

  test("couleur d'avatar identique à la version PC (md5)", () {
    // hashlib.md5(b"abc").digest()[0] == 0x90 → 144 % 10 == 4.
    expect(avatarColor('abc'), avatarColors[4]);
  });

  test('tailles à la française', () {
    expect(humanSize(532), '532 o');
    expect(humanSize(12 * 1024), '12 Ko');
    expect(humanSize((1.4 * 1024 * 1024).round()), '1,4 Mo');
    expect(humanSize(150 * 1024 * 1024), '150 Mo');
    expect(humanSize(3 * 1024 * 1024 * 1024), '3 Go');
  });

  test('dates', () {
    final today = DateTime(2026, 11, 5, 15);
    expect(dayLabel(DateTime(2026, 11, 5, 8), today: today), "Aujourd'hui");
    expect(dayLabel(DateTime(2026, 11, 4, 23), today: today), 'Hier');
    expect(dayLabel(DateTime(2026, 11, 2), today: today), 'Lundi 2 novembre');
    expect(dayLabel(DateTime(2025, 8, 15), today: today), '15 août 2025');
    final ts = DateTime(2026, 11, 5, 9, 7).millisecondsSinceEpoch;
    expect(shortTime(ts, now: today), '09:07');
    expect(shortTime(DateTime(2026, 11, 4, 9).millisecondsSinceEpoch, now: today), 'Hier');
    expect(shortTime(DateTime(2026, 11, 2, 9).millisecondsSinceEpoch, now: today), 'lun.');
    expect(shortTime(DateTime(2026, 10, 2, 9).millisecondsSinceEpoch, now: today), '02/10/2026');
  });

  test('emojis seuls', () {
    expect(emojiCount('👍'), 1);
    expect(emojiCount(' 🎉 🎉 '), 2);
    expect(emojiCount('🧑‍💻'), 1);
    expect(emojiCount('🇫🇷❤️'), 2);
    expect(emojiCount('ok 👍'), 0);
    expect(emojiCount(''), 0);
  });

  test('émoticônes converties', () {
    expect(convertShortcuts('Merci :) <3'), 'Merci 🙂 ❤️');
    expect(convertShortcuts('http://a.b/:)x'), 'http://a.b/:)x');
    expect(convertShortcuts('XD (y)'), '😆 👍');
  });

  test('couleur par type de fichier', () {
    expect(fileColor('pdf'), const Color(0xFFD93025));
    expect(fileColor('inconnu'), const Color(0xFF78909C));
  });

  test("version identique à pubspec.yaml et à l'application PC", () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final version = RegExp(r'^version:\s*(\S+)$', multiLine: true).firstMatch(pubspec)!.group(1)!;
    final [name, code] = version.split('+');
    expect(name, appVersion);
    final [major, minor, patch] = name.split('.').map(int.parse).toList();
    expect(int.parse(code), major * 10000 + minor * 100 + patch, reason: 'versionCode Android');
    final python = File('../src/socket_chat/__init__.py').readAsStringSync();
    expect(RegExp(r'__version__\s*=\s*"([^"]+)"').firstMatch(python)!.group(1), appVersion);
  });
}
