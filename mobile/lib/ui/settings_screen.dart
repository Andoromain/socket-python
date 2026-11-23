import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../app/avatar.dart';
import '../app/controller.dart';
import '../net/netpolicy.dart';
import '../protocol/protocol.dart';
import '../version.dart';
import 'widgets.dart';

/// Choix d'une image dans la galerie (octets bruts), ou `null` si annulé.
typedef PhotoPicker = Future<List<int>?> Function();

Future<List<int>?> pickFromGallery() async {
  final file = await ImagePicker().pickImage(source: ImageSource.gallery, maxWidth: 1024, maxHeight: 1024);
  return file?.readAsBytes();
}

/// Nom affiché, port d'écoute et informations utiles pour être ajouté par adresse.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller, this.pickPhoto = pickFromGallery});
  final ChatController controller;

  /// Choix de la photo de profil (remplaçable dans les tests).
  final PhotoPicker pickPhoto;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _name = TextEditingController(text: widget.controller.settings.name);
  late final TextEditingController _port = TextEditingController(text: '${widget.controller.settings.port}');
  late final TextEditingController _networks = TextEditingController(text: widget.controller.allowedNetworks);
  late final Future<List<String>> _addresses = widget.controller.localAddresses();
  bool _saving = false;

  @override
  void dispose() {
    _name.dispose();
    _port.dispose();
    _networks.dispose();
    super.dispose();
  }

  Future<void> _choosePhoto() async {
    final messenger = ScaffoldMessenger.of(context);
    final bytes = await widget.pickPhoto();
    if (bytes == null) return;
    final avatar = makeAvatar(bytes);
    if (avatar.isEmpty) {
      messenger.showSnackBar(const SnackBar(content: Text('Image illisible.')));
      return;
    }
    await widget.controller.setAvatar(avatar);
    if (mounted) setState(() {});
  }

  Future<void> _removePhoto() async {
    await widget.controller.setAvatar(const []);
    if (mounted) setState(() {});
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() => _saving = true);
    await widget.controller.applySettings(
      name: _name.text,
      port: int.parse(_port.text.trim()),
      allowedNetworks: widget.controller.managedNetworks == null ? _networks.text : null,
    );
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
            Row(
              children: [
                Avatar(
                  key: const Key('my-avatar'),
                  name: controller.settings.name ?? controller.messenger.name,
                  contactId: controller.settings.userId,
                  size: 64,
                  photo: controller.settings.avatar,
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      OutlinedButton.icon(
                        onPressed: _choosePhoto,
                        icon: const Icon(Icons.photo_camera_outlined),
                        label: const Text('Choisir une photo'),
                      ),
                      if (controller.settings.avatar.isNotEmpty)
                        TextButton(onPressed: _removePhoto, child: const Text('Retirer')),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 20),
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
            const SizedBox(height: 16),
            TextFormField(
              key: const Key('allowed-networks'),
              controller: _networks,
              enabled: controller.managedNetworks == null,
              decoration: InputDecoration(
                labelText: 'Réseaux autorisés',
                hintText: 'ex. 10.20.0.0/16, 192.168.1.0/24',
                helperText: controller.managedNetworks != null
                    ? 'Fixé par votre administrateur.'
                    : "Facultatif : l'application ignore les autres réseaux (Wi-Fi public, domicile…).",
                helperMaxLines: 2,
                border: const OutlineInputBorder(),
              ),
              validator: networksError,
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

/// Message d'erreur pour une liste de réseaux invalide, ou `null`.
String? networksError(String? value) {
  final entries = (value ?? '').replaceAll(';', ',').split(',').where((e) => e.trim().isNotEmpty).toList();
  final parsed = parseNetworks(value ?? '');
  return parsed.length == entries.length ? null : 'Format attendu : 10.20.0.0/16, 192.168.1.0/24';
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
