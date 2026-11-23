import 'package:emoji_picker_flutter/emoji_picker_flutter.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app/controller.dart';
import '../net/identity.dart';
import '../store/store.dart';
import 'format.dart';
import 'widgets.dart';

/// Intervalle maximal entre deux messages d'une même série (bulles regroupées).
const int groupIntervalMs = 5 * 60 * 1000;

bool _grouped(MessageRecord previous, MessageRecord current) =>
    previous.outgoing == current.outgoing &&
    sameDay(previous.ts, current.ts) &&
    current.ts - previous.ts < groupIntervalMs;

/// Couleurs des bulles, identiques à la version PC.
class BubbleColors {
  const BubbleColors._(this.background, this.incoming, this.outgoing, this.incomingText, this.metaIncoming);

  factory BubbleColors.of(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final text = Theme.of(context).colorScheme.onSurface;
    return BubbleColors._(
      dark ? const Color(0xFF15191E) : const Color(0xFFEEF1F5),
      dark ? const Color(0xFF262C33) : Colors.white,
      dark ? const Color(0xFF2F6FA3) : accentColor,
      text,
      text.withValues(alpha: 0.55),
    );
  }

  final Color background;
  final Color incoming;
  final Color outgoing;
  final Color incomingText;
  final Color metaIncoming;
  Color get outgoingText => Colors.white;
  Color get metaOutgoing => Colors.white.withValues(alpha: 0.75);
}

class ConversationScreen extends StatefulWidget {
  const ConversationScreen({super.key, required this.controller, required this.contactId});
  final ChatController controller;
  final String contactId;

  @override
  State<ConversationScreen> createState() => _ConversationScreenState();
}

class _ConversationScreenState extends State<ConversationScreen> {
  final TextEditingController _text = TextEditingController();
  final FocusNode _focus = FocusNode();
  bool _showEmoji = false;

  ChatController get controller => widget.controller;
  String get contactId => widget.contactId;

  @override
  void initState() {
    super.initState();
    controller.openConversation(contactId);
    _text.addListener(_onTextChanged);
    _focus.addListener(() {
      if (_focus.hasFocus && _showEmoji) setState(() => _showEmoji = false);
    });
  }

  @override
  void dispose() {
    controller.messenger.setTyping(contactId, false);
    controller.closeConversation(contactId);
    _text.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _onTextChanged() {
    controller.messenger.setTyping(contactId, _text.text.trim().isNotEmpty);
    setState(() {}); // bouton d'envoi actif ou non
  }

  void _send() {
    final text = convertShortcuts(_text.text).trim();
    if (text.isEmpty) return;
    try {
      controller.messenger.send(contactId, text);
      _text.clear();
    } on ArgumentError catch (error) {
      _snack('${error.message}');
    }
  }

  void _snack(String message) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));

  void _toggleEmoji() {
    if (_showEmoji) {
      setState(() => _showEmoji = false);
      _focus.requestFocus();
    } else {
      _focus.unfocus();
      setState(() => _showEmoji = true);
    }
  }

  void _react(MessageRecord record, String emoji) {
    final mine = record.reactionOf(me);
    controller.messenger.react(record.id, mine == emoji ? '' : emoji);
  }

  Future<void> _showActions(MessageRecord record) async {
    final mine = record.reactionOf(me);
    final file = record.file;
    final action = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  for (final emoji in quickReactions)
                    _ReactionChoice(
                      emoji: emoji,
                      selected: emoji == mine,
                      onTap: () => Navigator.pop(context, 'react:$emoji'),
                    ),
                  IconButton.filledTonal(
                    tooltip: "Plus d'emojis",
                    icon: const Icon(Icons.add_reaction_outlined),
                    onPressed: () => Navigator.pop(context, 'more'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            if (record.text.isNotEmpty)
              ListTile(
                leading: const Icon(Icons.copy),
                title: const Text('Copier le texte'),
                onTap: () => Navigator.pop(context, 'copy'),
              ),
            if (file != null && !record.outgoing && file.state == FileState.offered)
              ListTile(
                leading: const Icon(Icons.block),
                title: const Text('Refuser le fichier'),
                onTap: () => Navigator.pop(context, 'decline'),
              ),
            if (file != null && (file.state.active || (record.outgoing && file.state == FileState.offered)))
              ListTile(
                leading: const Icon(Icons.cancel_outlined),
                title: Text(record.outgoing ? "Annuler l'envoi" : 'Annuler la réception'),
                onTap: () => Navigator.pop(context, 'cancel'),
              ),
          ],
        ),
      ),
    );
    if (!mounted || action == null) return;
    switch (action) {
      case 'copy':
        await Clipboard.setData(ClipboardData(text: record.text));
        if (mounted) _snack('Texte copié');
      case 'decline':
        controller.messenger.declineFile(record.id);
      case 'cancel':
        controller.messenger.cancelFile(record.id);
      case 'more':
        final emoji = await _pickEmoji();
        if (emoji != null) _react(record, emoji);
      default:
        if (action.startsWith('react:')) _react(record, action.substring(6));
    }
  }

  Future<String?> _pickEmoji() => showModalBottomSheet<String>(
    context: context,
    builder: (context) => SafeArea(
      child: EmojiPicker(
        onEmojiSelected: (_, emoji) => Navigator.pop(context, emoji.emoji),
        config: _pickerConfig(context, showBackspace: false),
      ),
    ),
  );

  // -- Sécurité ----------------------------------------------------------
  void _showSafetyNumber(Contact contact) {
    final key = controller.messenger.contactKey(contact.id);
    final number = key == null ? null : safetyNumber(controller.messenger.identity.public, key);
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Chiffrement — ${contact.name}'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('🔒 Vos échanges avec ${contact.name} sont chiffrés.'),
            const SizedBox(height: 16),
            if (number == null)
              Text(
                "${contact.name} ne s'est pas encore connecté avec une version chiffrée de Socket Chat : "
                'le code de sécurité apparaîtra après votre premier échange.',
              )
            else ...[
              Center(
                child: SelectableText(
                  formatSafetyNumber(number),
                  key: const Key('safety-number-text'),
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 20, height: 1.5),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                "Comparez ce code avec celui qu'affiche ${contact.name} (🔒 dans votre conversation), "
                "en personne ou par téléphone. S'ils sont identiques, personne ne peut lire ni modifier "
                'vos échanges.',
              ),
            ],
          ],
        ),
        actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Fermer'))],
      ),
    );
  }

  Future<void> _acceptNewKey(Contact contact) async {
    final pending = contact.pendingKey;
    if (pending == null) return;
    final number = safetyNumber(controller.messenger.identity.public, decodeKey(pending));
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Nouvelle clé de sécurité'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Accepter la nouvelle clé de ${contact.name} ?'),
            const SizedBox(height: 12),
            const Text('Nouveau code de sécurité :'),
            const SizedBox(height: 8),
            Center(
              child: SelectableText(
                formatSafetyNumber(number),
                textAlign: TextAlign.center,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 18, height: 1.5),
              ),
            ),
            const SizedBox(height: 12),
            Text(
              "Comparez-le d'abord avec celui qu'affiche ${contact.name} (🔒 dans votre conversation). "
              "N'acceptez pas si vous n'avez pas pu vérifier.",
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Annuler')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Accepter')),
        ],
      ),
    );
    if (accepted == true) controller.messenger.trustNewKey(contact.id);
  }

  @override
  Widget build(BuildContext context) {
    final colors = BubbleColors.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final contact = controller.store.contact(contactId);
        if (contact == null) {
          return const Scaffold(body: Center(child: Text('Contact supprimé.')));
        }
        final messenger = controller.messenger;
        final online = messenger.isOnline(contactId);
        final typing = messenger.isTyping(contactId);
        final messages = controller.store.messages(contactId);
        final scheme = Theme.of(context).colorScheme;
        return PopScope(
          canPop: !_showEmoji,
          onPopInvokedWithResult: (didPop, _) {
            if (!didPop) setState(() => _showEmoji = false);
          },
          child: Scaffold(
            backgroundColor: colors.background,
            appBar: AppBar(
              titleSpacing: 0,
              title: Row(
                children: [
                  Avatar(name: contact.name, contactId: contact.id, online: online, size: 38, photo: contact.avatar),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(contact.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                        Text(
                          typing ? 'écrit…' : (online ? 'en ligne' : 'hors ligne'),
                          style: TextStyle(
                            fontSize: 13,
                            color: typing || online ? scheme.primary : scheme.onSurfaceVariant,
                            fontStyle: typing ? FontStyle.italic : FontStyle.normal,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              actions: [
                IconButton(
                  key: const Key('safety-number'),
                  icon: const Icon(Icons.lock_outline),
                  tooltip: 'Chiffrement : vérifier le code de sécurité',
                  onPressed: () => _showSafetyNumber(contact),
                ),
                IconButton(
                  key: const ValueKey('video-button'),
                  tooltip: online ? 'Appel vidéo' : 'Appel vidéo : contact hors ligne',
                  icon: const Icon(Icons.videocam_outlined),
                  onPressed: online && controller.calls.call == null
                      ? () => controller.startCall(contactId, 'video')
                      : null,
                ),
                IconButton(
                  key: const ValueKey('call-button'),
                  tooltip: online ? 'Appel audio' : 'Appel audio : contact hors ligne',
                  icon: const Icon(Icons.call),
                  onPressed: online && controller.calls.call == null ? () => controller.startCall(contactId) : null,
                ),
              ],
            ),
            body: Column(
              children: [
                if (contact.pendingKey != null)
                  _KeyBanner(
                    key: const Key('changed-key'),
                    warning: true,
                    text: changedKeyText(contact.name),
                    action: 'Accepter la nouvelle clé…',
                    onAction: () => _acceptNewKey(contact),
                  ),
                if (messenger.refusesOurKey(contactId))
                  _KeyBanner(key: const Key('refused-key'), warning: false, text: refusedKeyText(contact.name)),
                Expanded(
                  child: messages.isEmpty
                      ? _NoMessages(name: contact.name)
                      : _MessageList(
                          messages: messages,
                          colors: colors,
                          progressOf: messenger.transfers.progressOf,
                          onLongPress: _showActions,
                          onReactionTap: (record) =>
                              _react(record, record.reactionOf(me).isEmpty ? '👍' : record.reactionOf(me)),
                        ),
                ),
                if (!online)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                    child: Text(
                      'Hors ligne : vos messages partiront à son retour.',
                      style: TextStyle(fontSize: 12, color: scheme.onSurfaceVariant),
                    ),
                  ),
                _Composer(
                  text: _text,
                  focus: _focus,
                  showEmoji: _showEmoji,
                  onToggleEmoji: _toggleEmoji,
                  onSend: _send,
                ),
                if (_showEmoji)
                  EmojiPicker(textEditingController: _text, config: _pickerConfig(context, showBackspace: true)),
              ],
            ),
          ),
        );
      },
    );
  }
}

Config _pickerConfig(BuildContext context, {required bool showBackspace}) {
  final scheme = Theme.of(context).colorScheme;
  final background = scheme.surfaceContainerLow;
  return Config(
    height: 280,
    locale: const Locale('fr'),
    emojiViewConfig: EmojiViewConfig(backgroundColor: background, columns: 8, emojiSizeMax: 30),
    categoryViewConfig: CategoryViewConfig(
      backgroundColor: background,
      indicatorColor: scheme.primary,
      iconColorSelected: scheme.primary,
      iconColor: scheme.onSurfaceVariant,
      backspaceColor: scheme.primary,
    ),
    bottomActionBarConfig: BottomActionBarConfig(
      backgroundColor: background,
      buttonColor: background,
      buttonIconColor: scheme.onSurfaceVariant,
      showBackspaceButton: showBackspace,
    ),
    searchViewConfig: SearchViewConfig(backgroundColor: background, hintText: 'Rechercher un emoji'),
  );
}

class _ReactionChoice extends StatelessWidget {
  const _ReactionChoice({required this.emoji, required this.selected, required this.onTap});
  final String emoji;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkResponse(
      onTap: onTap,
      radius: 28,
      child: Container(
        width: 46,
        height: 46,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: selected ? Theme.of(context).colorScheme.primaryContainer : null,
        ),
        child: Text(emoji, style: const TextStyle(fontSize: 28)),
      ),
    );
  }
}

String changedKeyText(String name) =>
    '⚠ La clé de sécurité de $name a changé : vos échanges sont bloqués. C\'est normal après une '
    'réinstallation de Socket Chat ou un changement d\'appareil ; sinon, quelqu\'un tente peut-être '
    'd\'usurper son identité. Vérifiez avec cette personne avant d\'accepter.';

String refusedKeyText(String name) =>
    '$name ne reconnaît pas encore la clé de cet appareil (réinstallation ?) : elle doit être '
    'acceptée de son côté, dans votre conversation, pour que vos messages passent.';

/// Avertissement de sécurité en haut de la conversation (clé changée ou refusée).
class _KeyBanner extends StatelessWidget {
  const _KeyBanner({super.key, required this.warning, required this.text, this.action, this.onAction});

  final bool warning;
  final String text;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final background = warning ? scheme.errorContainer : scheme.secondaryContainer;
    final foreground = warning ? scheme.onErrorContainer : scheme.onSecondaryContainer;
    return Material(
      color: background,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 8, 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(text, style: TextStyle(color: foreground, fontSize: 13)),
            if (action != null)
              TextButton(
                onPressed: onAction,
                style: TextButton.styleFrom(foregroundColor: foreground),
                child: Text(action!),
              )
            else
              const SizedBox(height: 4),
          ],
        ),
      ),
    );
  }
}

class _NoMessages extends StatelessWidget {
  const _NoMessages({required this.name});
  final String name;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Text(
          'Écrivez votre premier message à $name 👋',
          textAlign: TextAlign.center,
          style: TextStyle(color: scheme.onSurfaceVariant),
        ),
      ),
    );
  }
}

class _MessageList extends StatelessWidget {
  const _MessageList({
    required this.messages,
    required this.colors,
    required this.progressOf,
    required this.onLongPress,
    required this.onReactionTap,
  });

  final List<MessageRecord> messages;
  final BubbleColors colors;
  final (int, int)? Function(String messageId) progressOf;
  final void Function(MessageRecord) onLongPress;
  final void Function(MessageRecord) onReactionTap;

  @override
  Widget build(BuildContext context) {
    // Liste inversée : le plus récent en bas, toujours visible sans défilement.
    final items = <Widget>[];
    for (var i = messages.length - 1; i >= 0; i--) {
      final record = messages[i];
      final previous = i > 0 ? messages[i - 1] : null;
      final next = i + 1 < messages.length ? messages[i + 1] : null;
      items.add(
        MessageBubble(
          key: ValueKey(record.id),
          record: record,
          colors: colors,
          firstInGroup: previous == null || !_grouped(previous, record),
          lastInGroup: next == null || !_grouped(record, next),
          progress: progressOf(record.id),
          onLongPress: record.call == null ? () => onLongPress(record) : null,
          onReactionTap: () => onReactionTap(record),
        ),
      );
      if (previous == null || !sameDay(previous.ts, record.ts)) {
        items.add(_DaySeparator(label: dayLabel(DateTime.fromMillisecondsSinceEpoch(record.ts))));
      }
    }
    return ListView.builder(
      reverse: true,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      itemCount: items.length,
      itemBuilder: (_, index) => items[index],
    );
  }
}

class _DaySeparator extends StatelessWidget {
  const _DaySeparator({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 12),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        decoration: BoxDecoration(
          color: dark ? const Color(0xFF262C33) : const Color(0xFFDDE3EA),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(label, style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
      ),
    );
  }
}

class MessageBubble extends StatelessWidget {
  const MessageBubble({
    super.key,
    required this.record,
    required this.colors,
    required this.firstInGroup,
    required this.lastInGroup,
    this.progress,
    this.onLongPress,
    this.onReactionTap,
  });

  final MessageRecord record;
  final BubbleColors colors;
  final bool firstInGroup;
  final bool lastInGroup;
  final (int, int)? progress;
  final VoidCallback? onLongPress;
  final VoidCallback? onReactionTap;

  @override
  Widget build(BuildContext context) {
    final outgoing = record.outgoing;
    final emojis = record.file == null && record.call == null ? emojiCount(record.text) : 0;
    final jumbo = emojis > 0 && emojis <= 3;
    final background = outgoing ? colors.outgoing : colors.incoming;
    final foreground = outgoing ? colors.outgoingText : colors.incomingText;
    final meta = outgoing ? colors.metaOutgoing : colors.metaIncoming;
    const big = Radius.circular(18);
    const small = Radius.circular(5);
    // Côté de l'expéditeur : coin bas toujours réduit (« queue » ou bulle suivante
    // de la série), coin haut réduit quand la bulle prolonge une série.
    final radius = BorderRadius.only(
      topLeft: !outgoing && !firstInGroup ? small : big,
      topRight: outgoing && !firstInGroup ? small : big,
      bottomLeft: outgoing ? big : small,
      bottomRight: outgoing ? small : big,
    );
    final metaRow = _Meta(record: record, color: jumbo ? colors.metaIncoming : meta);

    Widget content;
    if (record.file != null) {
      content = _FileContent(record: record, foreground: foreground, meta: meta, progress: progress, metaRow: metaRow);
    } else if (jumbo) {
      content = Column(
        crossAxisAlignment: outgoing ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          Text(record.text.trim(), style: TextStyle(fontSize: emojis == 1 ? 48 : 36, height: 1.15)),
          metaRow,
        ],
      );
    } else {
      content = Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 10, 6),
        child: Wrap(
          alignment: WrapAlignment.end,
          crossAxisAlignment: WrapCrossAlignment.end,
          spacing: 8,
          children: [
            Text(record.displayText, style: TextStyle(color: foreground, fontSize: 16, height: 1.3)),
            metaRow,
          ],
        ),
      );
    }

    final bubble = jumbo
        ? content
        : Material(color: background, borderRadius: radius, clipBehavior: Clip.antiAlias, child: content);

    return Padding(
      padding: EdgeInsets.only(top: firstInGroup ? 8 : 2, left: outgoing ? 48 : 0, right: outgoing ? 0 : 48),
      child: Align(
        alignment: outgoing ? Alignment.centerRight : Alignment.centerLeft,
        child: GestureDetector(
          onLongPress: onLongPress,
          behavior: HitTestBehavior.opaque,
          child: Column(
            crossAxisAlignment: outgoing ? CrossAxisAlignment.end : CrossAxisAlignment.start,
            children: [
              ConstrainedBox(constraints: const BoxConstraints(maxWidth: 520), child: bubble),
              if (record.reactions.isNotEmpty) _Reactions(record: record, onTap: onReactionTap),
            ],
          ),
        ),
      ),
    );
  }
}

class _Meta extends StatelessWidget {
  const _Meta({required this.record, required this.color});
  final MessageRecord record;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(clock(record.ts), style: TextStyle(fontSize: 11, color: color)),
          if (record.outgoing && record.call == null) ...[
            const SizedBox(width: 3),
            StatusTicks(status: record.status, color: color, readColor: readTickColor, size: 15),
          ],
        ],
      ),
    );
  }
}

class _Reactions extends StatelessWidget {
  const _Reactions({required this.record, this.onTap});
  final MessageRecord record;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final counts = <String, int>{};
    for (final (_, emoji) in record.reactions) {
      counts[emoji] = (counts[emoji] ?? 0) + 1;
    }
    final mine = record.reactionOf(me).isNotEmpty;
    return Transform.translate(
      offset: const Offset(0, -4),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Material(
          color: mine ? scheme.primaryContainer : scheme.surfaceContainerHighest,
          shape: StadiumBorder(side: BorderSide(color: scheme.surface, width: 2)),
          child: InkWell(
            customBorder: const StadiumBorder(),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              child: Text(
                [for (final MapEntry(:key, :value) in counts.entries) value > 1 ? '$key $value' : key].join(' '),
                style: const TextStyle(fontSize: 15),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

String fileStateLabel(MessageRecord record, (int, int)? progress) {
  final file = record.file!;
  String percent() {
    final p = progress;
    if (p == null || p.$2 == 0) return '';
    return ' ${(p.$1 * 100 / p.$2).floor()} %';
  }

  return switch (file.state) {
    FileState.offered => record.outgoing ? 'En attente de réponse' : 'Réception sur Android : prochaine version',
    FileState.accepted => 'Accepté',
    FileState.transferring => record.outgoing ? 'Envoi…${percent()}' : 'Réception…${percent()}',
    FileState.done => record.outgoing ? 'Envoyé' : 'Reçu',
    FileState.declined => 'Refusé',
    FileState.cancelled => 'Annulé',
    FileState.failed => 'Échec du transfert',
  };
}

class _FileContent extends StatelessWidget {
  const _FileContent({
    required this.record,
    required this.foreground,
    required this.meta,
    required this.progress,
    required this.metaRow,
  });

  final MessageRecord record;
  final Color foreground;
  final Color meta;
  final (int, int)? progress;
  final Widget metaRow;

  @override
  Widget build(BuildContext context) {
    final file = record.file!;
    final p = progress;
    final details = Padding(
      padding: const EdgeInsets.fromLTRB(12, 10, 10, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (!file.isImage) ...[
                Container(
                  width: 44,
                  height: 44,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(color: fileColor(file.extension), borderRadius: BorderRadius.circular(8)),
                  child: Text(
                    file.extension.isEmpty ? '?' : file.extension.toUpperCase(),
                    maxLines: 1,
                    style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                ),
                const SizedBox(width: 10),
              ],
              Flexible(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      file.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: foreground, fontWeight: FontWeight.w600),
                    ),
                    Text(
                      '${humanSize(file.size)} · ${fileStateLabel(record, progress)}',
                      style: TextStyle(color: meta, fontSize: 13),
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (file.state.active)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: LinearProgressIndicator(
                value: p == null || p.$2 == 0 ? null : p.$1 / p.$2,
                color: foreground,
                backgroundColor: meta.withValues(alpha: 0.3),
              ),
            ),
          Align(alignment: Alignment.centerRight, child: metaRow),
        ],
      ),
    );
    if (!file.isImage) return ConstrainedBox(constraints: const BoxConstraints(maxWidth: 300), child: details);
    return ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 260),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Image.memory(file.thumb, fit: BoxFit.cover, gaplessPlayback: true),
          details,
        ],
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({
    required this.text,
    required this.focus,
    required this.showEmoji,
    required this.onToggleEmoji,
    required this.onSend,
  });

  final TextEditingController text;
  final FocusNode focus;
  final bool showEmoji;
  final VoidCallback onToggleEmoji;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final canSend = text.text.trim().isNotEmpty;
    return Material(
      color: scheme.surface,
      child: SafeArea(
        top: false,
        bottom: !showEmoji,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(4, 6, 8, 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              IconButton(
                tooltip: showEmoji ? 'Clavier' : 'Emojis',
                icon: Icon(showEmoji ? Icons.keyboard_outlined : Icons.emoji_emotions_outlined),
                onPressed: onToggleEmoji,
              ),
              Expanded(
                child: TextField(
                  controller: text,
                  focusNode: focus,
                  minLines: 1,
                  maxLines: 5,
                  textCapitalization: TextCapitalization.sentences,
                  keyboardType: TextInputType.multiline,
                  decoration: InputDecoration(
                    hintText: 'Message',
                    filled: true,
                    fillColor: scheme.surfaceContainerHighest,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              IconButton.filled(tooltip: 'Envoyer', icon: const Icon(Icons.send), onPressed: canSend ? onSend : null),
            ],
          ),
        ),
      ),
    );
  }
}
