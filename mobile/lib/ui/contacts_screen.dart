import 'package:flutter/material.dart';

import '../app/controller.dart';
import '../protocol/protocol.dart';
import '../store/store.dart';
import 'conversation_screen.dart';
import 'format.dart';
import 'settings_screen.dart';
import 'widgets.dart';

class ContactsScreen extends StatefulWidget {
  const ContactsScreen({super.key, required this.controller});
  final ChatController controller;

  @override
  State<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends State<ContactsScreen> {
  final TextEditingController _search = TextEditingController();
  bool _searching = false;

  ChatController get controller => widget.controller;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _open(String contactId) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ConversationScreen(controller: controller, contactId: contactId),
      ),
    );
  }

  Future<void> _addByAddress() async {
    final result = await showDialog<(String, int)>(context: context, builder: (_) => const AddContactDialog());
    if (result == null) return;
    if (controller.addContact(result.$1, result.$2) && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Connexion à ${result.$1}…')));
    }
  }

  Future<void> _confirmDelete(Contact contact) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Supprimer ${contact.name} ?'),
        content: const Text(
          'La conversation et son historique seront effacés de cet appareil. '
          "Le contact réapparaîtra s'il est de nouveau détecté.",
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Annuler')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Supprimer')),
        ],
      ),
    );
    if (confirmed == true) controller.deleteContact(contact.id);
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final query = _search.text.trim().toLowerCase();
        final summaries = controller.store
            .contacts()
            .where((s) => query.isEmpty || s.contact.name.toLowerCase().contains(query))
            .toList();
        return Scaffold(
          appBar: AppBar(
            title: _searching
                ? TextField(
                    controller: _search,
                    autofocus: true,
                    decoration: const InputDecoration(hintText: 'Rechercher un contact', border: InputBorder.none),
                    onChanged: (_) => setState(() {}),
                  )
                : const Text('Socket Chat'),
            actions: [
              IconButton(
                tooltip: _searching ? 'Fermer la recherche' : 'Rechercher',
                icon: Icon(_searching ? Icons.close : Icons.search),
                onPressed: () => setState(() {
                  _searching = !_searching;
                  _search.clear();
                }),
              ),
              IconButton(
                tooltip: 'Préférences',
                icon: const Icon(Icons.settings_outlined),
                onPressed: () =>
                    Navigator.of(context)
                        .push(MaterialPageRoute<void>(builder: (_) => SettingsScreen(controller: controller))),
              ),
            ],
          ),
          body: Column(
            children: [
              if (controller.notice case final notice?)
                MaterialBanner(
                  key: const Key('notice'),
                  leading: const Icon(Icons.lock_outline),
                  content: Text(notice),
                  actions: [TextButton(onPressed: controller.dismissNotice, child: const Text('OK'))],
                ),
              if (controller.startError case final error?)
                MaterialBanner(
                  leading: const Icon(Icons.wifi_off),
                  content: Text(error),
                  actions: [TextButton(onPressed: controller.start, child: const Text('Réessayer'))],
                ),
              Expanded(
                child: summaries.isEmpty
                    ? _EmptyState(searching: query.isNotEmpty)
                    : ListView.builder(
                        padding: const EdgeInsets.only(bottom: 88),
                        itemCount: summaries.length,
                        itemBuilder: (context, index) {
                          final summary = summaries[index];
                          return ContactTile(
                            summary: summary,
                            online: controller.messenger.isOnline(summary.contact.id),
                            typing: controller.messenger.isTyping(summary.contact.id),
                            onTap: () => _open(summary.contact.id),
                            onLongPress: () => _confirmDelete(summary.contact),
                          );
                        },
                      ),
              ),
            ],
          ),
          floatingActionButton: FloatingActionButton.extended(
            onPressed: _addByAddress,
            icon: const Icon(Icons.person_add_alt_1),
            label: const Text('Ajouter'),
          ),
        );
      },
    );
  }
}

class ContactTile extends StatelessWidget {
  const ContactTile({
    super.key,
    required this.summary,
    required this.online,
    required this.typing,
    required this.onTap,
    this.onLongPress,
  });

  final ContactSummary summary;
  final bool online;
  final bool typing;
  final VoidCallback onTap;
  final VoidCallback? onLongPress;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final last = summary.lastMessage;
    final unread = summary.unread > 0;
    final muted = scheme.onSurfaceVariant;
    Widget subtitle;
    if (typing) {
      subtitle = Text(
        'écrit…',
        style: TextStyle(color: scheme.primary, fontStyle: FontStyle.italic),
      );
    } else if (last != null) {
      subtitle = Row(
        children: [
          if (last.outgoing) ...[
            StatusTicks(status: last.status, color: muted, readColor: scheme.primary),
            const SizedBox(width: 4),
          ],
          Expanded(
            child: Text(
              last.preview,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: unread ? scheme.onSurface : muted),
            ),
          ),
        ],
      );
    } else {
      subtitle = Text(online ? 'En ligne' : 'Aucun message', style: TextStyle(color: muted));
    }
    return ListTile(
      onTap: onTap,
      onLongPress: onLongPress,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: Avatar(name: summary.contact.name, contactId: summary.contact.id, online: online),
      title: Text(
        summary.contact.name,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontWeight: unread ? FontWeight.bold : FontWeight.w500),
      ),
      subtitle: subtitle,
      trailing: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (summary.lastActivity > 0)
            Text(
              shortTime(summary.lastActivity),
              style: TextStyle(fontSize: 12, color: unread ? scheme.primary : muted),
            ),
          const SizedBox(height: 4),
          if (unread)
            Badge(
              label: Text(summary.unread > 99 ? '99+' : '${summary.unread}'),
              backgroundColor: scheme.primary,
              textColor: scheme.onPrimary,
            )
          else
            const SizedBox(height: 16),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.searching});
  final bool searching;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(searching ? Icons.search_off : Icons.wifi_find, size: 64, color: theme.colorScheme.primary),
            const SizedBox(height: 16),
            Text(
              searching ? 'Aucun contact ne correspond' : 'Aucun collègue pour le moment',
              style: theme.textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            if (!searching) ...[
              const SizedBox(height: 8),
              Text(
                'Les postes Socket Chat connectés au même réseau Wi-Fi apparaissent ici '
                'automatiquement. Sinon, ajoutez un collègue par son adresse.',
                style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                textAlign: TextAlign.center,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Saisie d'une adresse (« 192.168.1.20 » ou « 192.168.1.20:12800 ») et d'un port.
class AddContactDialog extends StatefulWidget {
  const AddContactDialog({super.key});

  @override
  State<AddContactDialog> createState() => _AddContactDialogState();
}

class _AddContactDialogState extends State<AddContactDialog> {
  final _form = GlobalKey<FormState>();
  final _host = TextEditingController();
  final _port = TextEditingController(text: '$defaultPort');

  @override
  void dispose() {
    _host.dispose();
    _port.dispose();
    super.dispose();
  }

  void _submit() {
    if (!_form.currentState!.validate()) return;
    var host = _host.text.trim();
    var port = int.parse(_port.text.trim());
    final colon = host.lastIndexOf(':');
    if (colon > 0 && !host.contains('::') && host.indexOf(':') == colon) {
      port = int.tryParse(host.substring(colon + 1)) ?? port;
      host = host.substring(0, colon);
    }
    Navigator.pop(context, (host, port));
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Ajouter par adresse'),
      content: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text("Adresse affichée dans les préférences de l'autre poste."),
            const SizedBox(height: 16),
            TextFormField(
              controller: _host,
              autofocus: true,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'Adresse', hintText: '192.168.1.20'),
              validator: (value) => (value ?? '').trim().isEmpty ? 'Indiquez une adresse.' : null,
            ),
            const SizedBox(height: 8),
            TextFormField(
              controller: _port,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: 'Port'),
              validator: (value) {
                final port = int.tryParse((value ?? '').trim());
                return port == null || port < 1 || port > 65535 ? 'Port entre 1 et 65535.' : null;
              },
              onFieldSubmitted: (_) => _submit(),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Annuler')),
        FilledButton(onPressed: _submit, child: const Text('Se connecter')),
      ],
    );
  }
}
