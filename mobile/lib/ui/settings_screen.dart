import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../app/avatar.dart';
import '../app/background.dart';
import '../app/controller.dart';
import '../net/netpolicy.dart';
import '../updates/app_updater.dart';
import '../protocol/protocol.dart';
import '../version.dart';
import 'widgets.dart';

/// Choisit une image (galerie ou appareil photo) ; `null` si l'utilisateur renonce.
typedef ImagePickerCallback = Future<Uint8List?> Function(ImageSource source);

Future<Uint8List?> _pickWithImagePicker(ImageSource source) async {
  // Réduite dès la sélection : le recadrage reste rapide même pour une photo de 12 Mpx.
  final file = await ImagePicker().pickImage(source: source, maxWidth: 1024, maxHeight: 1024);
  return file?.readAsBytes();
}

/// Nom affiché, port d'écoute et informations utiles pour être ajouté par adresse.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller, this.pickImage = _pickWithImagePicker});
  final ChatController controller;

  /// Remplaçable dans les tests (pas de galerie hors d'un appareil).
  final ImagePickerCallback pickImage;

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
  BackgroundStatus? _status;
  late final AppLifecycleListener _lifecycle;

  @override
  void initState() {
    super.initState();
    _refreshStatus();
    // Retour des réglages d'Android : l'autorisation vient peut-être d'être accordée.
    _lifecycle = AppLifecycleListener(onResume: _refreshStatus);
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    _name.dispose();
    _port.dispose();
    _networks.dispose();
    super.dispose();
  }

  Future<void> _refreshStatus() async {
    final status = await widget.controller.background.status();
    if (mounted) setState(() => _status = status);
  }

  Future<void> _setKeepRunning(bool on) async {
    await widget.controller.setKeepRunning(on);
    await _refreshStatus();
  }

  /// Joignable application fermée : interrupteur et autorisations encore manquantes.
  List<Widget> _backgroundSection(ThemeData theme) {
    final controller = widget.controller;
    final background = controller.background;
    final status = _status;
    final on = controller.settings.keepRunning;
    return [
      Text('Application fermée', style: theme.textTheme.titleSmall?.copyWith(color: theme.colorScheme.primary)),
      SwitchListTile(
        key: const ValueKey('keep-running'),
        contentPadding: EdgeInsets.zero,
        secondary: const Icon(Icons.notifications_active_outlined),
        title: const Text('Rester joignable'),
        subtitle: const Text(
          'Recevoir messages et appels même application fermée. Une notification discrète '
          'indique que Socket Chat reste actif.',
        ),
        value: on,
        onChanged: _setKeepRunning,
      ),
      if (on && status != null && !status.notifications)
        _Permission(
          key: const ValueKey('allow-notifications'),
          icon: Icons.notifications_off_outlined,
          title: 'Notifications désactivées',
          text: 'Sans elles, vous ne verrez ni les nouveaux messages ni les appels.',
          action: 'Autoriser',
          onPressed: background.openNotificationSettings,
        ),
      if (on && status != null && !status.fullScreen)
        _Permission(
          key: const ValueKey('allow-full-screen'),
          icon: Icons.phone_locked_outlined,
          title: "Appels sur l'écran verrouillé",
          text: 'Autorisez Socket Chat à afficher les appels en plein écran pour les voir téléphone verrouillé.',
          action: 'Autoriser',
          onPressed: background.openFullScreenSettings,
        ),
      if (on && status != null && !status.battery)
        _Permission(
          key: const ValueKey('allow-battery'),
          icon: Icons.battery_alert_outlined,
          title: 'Optimisation de la batterie',
          text:
              'Certains téléphones arrêtent les applications en arrière-plan : excluez Socket Chat '
              "de l'optimisation pour rester joignable (consommation très faible).",
          action: 'Ne pas restreindre',
          onPressed: background.requestBatteryUnrestricted,
        ),
      const SizedBox(height: 16),
    ];
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

  Future<void> _checkForUpdate(AppUpdater updater) async {
    final available = await updater.check(manual: true);
    if (!mounted) return;
    final String message;
    if (updater.error case final error?) {
      message = error;
    } else if (available) {
      message = 'Socket Chat ${updater.available!.version} est disponible : voir le bandeau de la liste des contacts.';
    } else {
      message = 'Vous avez la dernière version ($appVersion).';
    }
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _changePhoto() async {
    final hasPhoto = widget.controller.settings.avatar.isNotEmpty;
    final choice = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('Choisir dans la galerie'),
              onTap: () => Navigator.pop(context, 'gallery'),
            ),
            ListTile(
              leading: const Icon(Icons.photo_camera_outlined),
              title: const Text('Prendre une photo'),
              onTap: () => Navigator.pop(context, 'camera'),
            ),
            if (hasPhoto)
              ListTile(
                leading: const Icon(Icons.delete_outline),
                title: const Text('Retirer la photo'),
                onTap: () => Navigator.pop(context, 'remove'),
              ),
          ],
        ),
      ),
    );
    if (choice == null || !mounted) return;
    if (choice == 'remove') {
      await widget.controller.setAvatar(Uint8List(0));
    } else {
      final source = await widget.pickImage(choice == 'camera' ? ImageSource.camera : ImageSource.gallery);
      if (source == null || !mounted) return;
      try {
        await widget.controller.setAvatar(makeAvatar(source));
      } on ArgumentError catch (error) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${error.message}')));
        }
        return;
      }
    }
    if (mounted) setState(() {});
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
            Center(
              child: Semantics(
                button: true,
                label: 'Changer la photo de profil',
                child: InkWell(
                  customBorder: const CircleBorder(),
                  onTap: _changePhoto,
                  child: Stack(
                    children: [
                      ListenableBuilder(
                        listenable: _name,
                        builder: (context, _) => Avatar(
                          name: _name.text.trim().isEmpty ? '?' : _name.text,
                          contactId: controller.settings.userId,
                          size: 96,
                          photo: controller.settings.avatar,
                        ),
                      ),
                      Positioned(
                        right: 0,
                        bottom: 0,
                        child: CircleAvatar(
                          radius: 16,
                          backgroundColor: theme.colorScheme.primary,
                          child: Icon(Icons.photo_camera, size: 18, color: theme.colorScheme.onPrimary),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Center(
              child: Text(
                'Photo de profil, visible par vos collègues',
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
              ),
            ),
            const SizedBox(height: 24),
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
            ..._backgroundSection(theme),
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
            if (controller.updater case final updater?)
              ListenableBuilder(
                listenable: updater,
                builder: (context, _) => Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                    onPressed: updater.busy ? null : () => _checkForUpdate(updater),
                    icon: updater.checking
                        ? const SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.system_update),
                    label: const Text('Rechercher une mise à jour'),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Autorisation manquante : explication et bouton vers le réglage d'Android.
class _Permission extends StatelessWidget {
  const _Permission({
    super.key,
    required this.icon,
    required this.title,
    required this.text,
    required this.action,
    required this.onPressed,
  });

  final IconData icon;
  final String title;
  final String text;
  final String action;
  final Future<void> Function() onPressed;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Card(
      elevation: 0,
      color: scheme.errorContainer,
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: scheme.onErrorContainer),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleSmall?.copyWith(color: scheme.onErrorContainer),
                  ),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(left: 36, top: 4, right: 8),
              child: Text(text, style: TextStyle(color: scheme.onErrorContainer)),
            ),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(onPressed: onPressed, child: Text(action)),
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
