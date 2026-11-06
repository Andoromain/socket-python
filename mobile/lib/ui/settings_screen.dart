import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../app/controller.dart';
import '../protocol/protocol.dart';
import '../version.dart';

/// Nom affiché, port d'écoute et informations utiles pour être ajouté par adresse.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});
  final ChatController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _name = TextEditingController(text: widget.controller.settings.name);
  late final TextEditingController _port = TextEditingController(text: '${widget.controller.settings.port}');
  late final Future<List<String>> _addresses = widget.controller.localAddresses();
  bool _saving = false;

  @override
  void dispose() {
    _name.dispose();
    _port.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() => _saving = true);
    await widget.controller.applySettings(name: _name.text, port: int.parse(_port.text.trim()));
    if (!mounted) return;
    setState(() => _saving = false);
    final error = widget.controller.startError;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error ?? 'Préférences enregistrées')));
    if (error == null) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final controller = widget.controller;
    return Scaffold(
      appBar: AppBar(title: const Text('Préférences')),
      body: Form(
        key: _form,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextFormField(
              controller: _name,
              textCapitalization: TextCapitalization.words,
              decoration: const InputDecoration(
                labelText: 'Votre nom',
                helperText: 'Affiché chez vos collègues.',
                border: OutlineInputBorder(),
              ),
              validator: nameError,
            ),
            const SizedBox(height: 16),
            TextFormField(
              controller: _port,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: "Port d'écoute",
                helperText: 'À ne changer que sur demande de votre service informatique.',
                border: OutlineInputBorder(),
              ),
              validator: (value) {
                final port = int.tryParse((value ?? '').trim());
                return port == null || port < 1 || port > 65535 ? 'Port entre 1 et 65535.' : null;
              },
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Enregistrement…' : 'Enregistrer')),
            const SizedBox(height: 32),
            Text('Cet appareil', style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.primary)),
            FutureBuilder<List<String>>(
              future: _addresses,
              builder: (context, snapshot) {
                final addresses = snapshot.data ?? const [];
                final port = controller.messenger.port ?? controller.settings.port;
                final text = addresses.isEmpty
                    ? (snapshot.connectionState == ConnectionState.done ? 'Aucun réseau' : '…')
                    : addresses.map((a) => '$a:$port').join('\n');
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.wifi),
                  title: const Text('Adresse'),
                  subtitle: Text("$text\nÀ indiquer à un collègue qui vous ajoute par adresse."),
                  isThreeLine: true,
                  trailing: addresses.isEmpty
                      ? null
                      : IconButton(
                          tooltip: "Copier l'adresse",
                          icon: const Icon(Icons.copy),
                          onPressed: () => Clipboard.setData(ClipboardData(text: '${addresses.first}:$port')),
                        ),
                );
              },
            ),
            const ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.dark_mode_outlined),
              title: Text('Thème'),
              subtitle: Text("Clair ou sombre, selon le réglage d'Android."),
            ),
            const ListTile(
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.info_outline),
              title: Text('Version'),
              subtitle: Text('Socket Chat $appVersion · protocole v$protocolVersion'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Message d'erreur pour un nom invalide, ou `null`.
String? nameError(String? value) {
  try {
    validateName(value ?? '');
    return null;
  } on ArgumentError catch (error) {
    return '${error.message}';
  }
}
