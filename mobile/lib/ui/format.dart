// Mises en forme partagées, transposées de src/socket_chat/ui/theme.py et emoji.py :
// mêmes initiales et mêmes couleurs d'avatar que sur PC, dates et tailles en français.

import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';

const List<Color> avatarColors = [
  Color(0xFF1E88E5),
  Color(0xFF43A047),
  Color(0xFFE53935),
  Color(0xFF8E24AA),
  Color(0xFFFB8C00),
  Color(0xFF00897B),
  Color(0xFF3949AB),
  Color(0xFFD81B60),
  Color(0xFF6D4C41),
  Color(0xFF546E7A),
];

const Color onlineColor = Color(0xFF2BB673);
const Color accentColor = Color(0xFF2A7AB8);
const Color readTickColor = Color(0xFF8FE3FF);

/// Réactions proposées par un appui long sur une bulle.
const List<String> quickReactions = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

/// Couleur stable pour un contact (la même que sur PC).
Color avatarColor(String key) => avatarColors[md5.convert(utf8.encode(key)).bytes[0] % avatarColors.length];

String initials(String name) {
  final words = name
      .replaceAll('-', ' ')
      .split(RegExp(r'\s+'))
      .where((w) => w.isNotEmpty && RegExp(r'^[\p{L}\p{N}]', unicode: true).hasMatch(w))
      .toList();
  if (words.isEmpty) return '?';
  String first(String word, [int count = 1]) => String.fromCharCodes(word.runes.take(count));
  if (words.length == 1) return first(words[0], words[0].runes.length > 1 ? 2 : 1).toUpperCase();
  return (first(words.first) + first(words.last)).toUpperCase();
}

const List<String> _days = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];
const List<String> _months = [
  'janvier',
  'février',
  'mars',
  'avril',
  'mai',
  'juin',
  'juillet',
  'août',
  'septembre',
  'octobre',
  'novembre',
  'décembre',
];

DateTime _date(DateTime moment) => DateTime(moment.year, moment.month, moment.day);

int _daysBetween(DateTime from, DateTime to) =>
    _date(to).difference(_date(from)).inHours ~/ 24; // ~/ 24 : robuste aux changements d'heure

String _two(int value) => value.toString().padLeft(2, '0');

/// Séparateur de jour : « Aujourd'hui », « Hier », « Lundi 3 novembre », « 3 novembre 2025 ».
String dayLabel(DateTime day, {DateTime? today}) {
  final delta = _daysBetween(day, today ?? DateTime.now());
  if (delta == 0) return "Aujourd'hui";
  if (delta == 1) return 'Hier';
  final now = today ?? DateTime.now();
  final text = day.year == now.year
      ? '${_days[day.weekday - 1]} ${day.day} ${_months[day.month - 1]}'
      : '${day.day} ${_months[day.month - 1]} ${day.year}';
  return text[0].toUpperCase() + text.substring(1);
}

/// Heure pour la liste des contacts : « 14:05 », « Hier », « lun. » ou « 03/11/2026 ».
String shortTime(int tsMs, {DateTime? now}) {
  final moment = DateTime.fromMillisecondsSinceEpoch(tsMs);
  final delta = _daysBetween(moment, now ?? DateTime.now());
  if (delta == 0) return clock(tsMs);
  if (delta == 1) return 'Hier';
  if (delta > 1 && delta < 7) return '${_days[moment.weekday - 1].substring(0, 3)}.';
  return '${_two(moment.day)}/${_two(moment.month)}/${moment.year}';
}

String clock(int tsMs) {
  final moment = DateTime.fromMillisecondsSinceEpoch(tsMs);
  return '${_two(moment.hour)}:${_two(moment.minute)}';
}

bool sameDay(int a, int b) =>
    _date(DateTime.fromMillisecondsSinceEpoch(a)) == _date(DateTime.fromMillisecondsSinceEpoch(b));

/// Taille lisible à la française : « 532 o », « 12 Ko », « 1,4 Mo ».
String humanSize(int size) {
  if (size < 1024) return '$size o';
  var value = size.toDouble();
  for (final unit in const ['Ko', 'Mo', 'Go']) {
    value /= 1024;
    if (value < 1024 || unit == 'Go') {
      var text = value >= 100 ? value.toStringAsFixed(0) : value.toStringAsFixed(1);
      if (text.endsWith('.0')) text = text.substring(0, text.length - 2);
      return '${text.replaceAll('.', ',')} $unit';
    }
  }
  throw StateError('inatteignable');
}

const Map<List<String>, Color> _fileColors = {
  ['pdf']: Color(0xFFD93025),
  ['doc', 'docx', 'odt', 'rtf', 'txt', 'md']: Color(0xFF1A73E8),
  ['xls', 'xlsx', 'ods', 'csv']: Color(0xFF188038),
  ['ppt', 'pptx', 'odp']: Color(0xFFE8710A),
  ['zip', '7z', 'rar', 'tar', 'gz']: Color(0xFF9C6B00),
  ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg']: Color(0xFF8E24AA),
  ['mp3', 'wav', 'ogg', 'flac', 'm4a']: Color(0xFF00897B),
  ['mp4', 'avi', 'mkv', 'mov', 'webm']: Color(0xFFC2185B),
  ['py', 'js', 'ts', 'java', 'c', 'cpp', 'cs', 'html', 'css', 'json', 'xml', 'sql']: Color(0xFF455A64),
};

Color fileColor(String extension) {
  for (final MapEntry(key: extensions, value: color) in _fileColors.entries) {
    if (extensions.contains(extension)) return color;
  }
  return const Color(0xFF78909C);
}

// -- Emojis ---------------------------------------------------------------
const String _emojiUnit =
    r'(?:[\u{1F1E6}-\u{1F1FF}]{2}'
    r'|[0-9#*]\u{FE0F}?\u{20E3}'
    r'|[\u{A9}\u{AE}\u{203C}\u{2049}\u{2122}\u{2139}\u{2194}-\u{21AA}\u{231A}-\u{23FF}\u{24C2}\u{25AA}-\u{25FE}'
    r'\u{2600}-\u{27BF}\u{2934}\u{2935}\u{2B05}-\u{2B55}\u{3030}\u{303D}\u{3297}\u{3299}\u{1F000}-\u{1FAFF}]'
    r'\u{FE0F}?[\u{1F3FB}-\u{1F3FF}]?'
    r'(?:\u{200D}[\u{2600}-\u{27BF}\u{1F000}-\u{1FAFF}]\u{FE0F}?[\u{1F3FB}-\u{1F3FF}]?)*)';
final RegExp _emojiOnly = RegExp('^\\s*(?:$_emojiUnit\\s*)+\$', unicode: true);
final RegExp _emojiUnitRe = RegExp(_emojiUnit, unicode: true);

/// Nombre d'emojis si le texte n'est composé que d'emojis, sinon 0.
int emojiCount(String text) {
  if (text.trim().isEmpty || !_emojiOnly.hasMatch(text)) return 0;
  return _emojiUnitRe.allMatches(text).length;
}

const Map<String, String> _shortcuts = {
  ':)': '🙂',
  ':-)': '🙂',
  ':D': '😃',
  ':-D': '😃',
  'xD': '😆',
  'XD': '😆',
  ';)': '😉',
  ';-)': '😉',
  ':(': '🙁',
  ':-(': '🙁',
  ":'(": '😢',
  ':P': '😛',
  ':p': '😛',
  ':-P': '😛',
  ':o': '😮',
  ':O': '😮',
  ':|': '😐',
  '<3': '❤️',
  '^^': '😊',
  '(y)': '👍',
};

final RegExp _shortcutRe = RegExp(
  r'(?<!\S)(' +
      (_shortcuts.keys.toList()..sort((a, b) => b.length.compareTo(a.length))).map(RegExp.escape).join('|') +
      r')(?!\S)',
);

/// Remplace les émoticônes isolées (« :) », « <3 »…) par l'emoji correspondant.
String convertShortcuts(String text) => text.replaceAllMapped(_shortcutRe, (m) => _shortcuts[m[1]]!);
