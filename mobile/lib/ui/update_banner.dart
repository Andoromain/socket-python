import 'package:flutter/material.dart';

import '../updates/app_updater.dart';

/// Bandeau « Socket Chat X.Y.Z est disponible » en haut de la liste des contacts.
class UpdateBanner extends StatelessWidget {
  const UpdateBanner({super.key, required this.updater});
  final AppUpdater updater;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: updater,
      builder: (context, _) {
        final manifest = updater.available;
        if (manifest == null || updater.dismissed) return const SizedBox.shrink();
        final theme = Theme.of(context);
        final progress = updater.progress;
        final String detail;
        if (progress != null) {
          detail = 'Téléchargement… ${(progress * 100).floor()} %';
        } else if (updater.needsPermission) {
          detail =
              'Autorisez Socket Chat à installer des applications (écran ouvert), '
              'puis revenez et touchez « Installer ».';
        } else {
          detail = 'Android vous demandera de confirmer l\'installation. Vos conversations sont conservées.';
        }
        return MaterialBanner(
          leading: Icon(Icons.system_update, color: theme.colorScheme.primary),
          content: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Socket Chat ${manifest.version} est disponible', style: theme.textTheme.titleSmall),
              const SizedBox(height: 4),
              Text(detail, style: theme.textTheme.bodySmall),
              if (progress != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator(value: progress),
                ),
              if (updater.error case final error?)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(error, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error)),
                ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: manifest.notes.isEmpty
                  ? null
                  : () => showReleaseNotes(context, manifest.version, manifest.notes),
              child: const Text('Nouveautés'),
            ),
            TextButton(onPressed: updater.busy ? null : updater.dismiss, child: const Text('Plus tard')),
            FilledButton(
              onPressed: updater.busy ? null : updater.install,
              child: Text(updater.needsPermission ? 'Installer' : 'Mettre à jour'),
            ),
          ],
        );
      },
    );
  }
}

Future<void> showReleaseNotes(BuildContext context, String version, String notes) => showDialog<void>(
  context: context,
  builder: (context) => AlertDialog(
    title: Text('Nouveautés de la $version'),
    content: SingleChildScrollView(child: Text(_plainNotes(notes))),
    actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Fermer'))],
  ),
);

/// Notes du journal des modifications (Markdown) rendues lisibles en texte simple.
String _plainNotes(String markdown) => markdown
    .split('\n')
    .map((line) {
      var text = line.replaceAll('**', '').replaceAll('`', '');
      if (text.startsWith('### ')) return text.substring(4).toUpperCase();
      final bullet = RegExp(r'^(\s*)- ').firstMatch(text);
      if (bullet != null) text = '${bullet[1]}• ${text.substring(bullet.end)}';
      return text;
    })
    .join('\n');
