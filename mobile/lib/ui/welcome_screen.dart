import 'package:flutter/material.dart';

import '../app/controller.dart';
import 'settings_screen.dart';

/// Premier lancement : choix du nom affiché chez les collègues.
class WelcomeScreen extends StatefulWidget {
  const WelcomeScreen({super.key, required this.controller});
  final ChatController controller;

  @override
  State<WelcomeScreen> createState() => _WelcomeScreenState();
}

class _WelcomeScreenState extends State<WelcomeScreen> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    if (!_form.currentState!.validate()) return;
    setState(() => _busy = true);
    final controller = widget.controller;
    await controller.applySettings(name: _name.text, port: controller.settings.port);
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Icon(Icons.forum_rounded, size: 72, color: theme.colorScheme.primary),
                  const SizedBox(height: 16),
                  Text('Bienvenue dans Socket Chat', style: theme.textTheme.headlineSmall, textAlign: TextAlign.center),
                  const SizedBox(height: 8),
                  Text(
                    'Discutez avec vos collègues connectés au même réseau, sans compte ni serveur.',
                    style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 32),
                  TextFormField(
                    controller: _name,
                    autofocus: true,
                    textCapitalization: TextCapitalization.words,
                    decoration: const InputDecoration(
                      labelText: 'Votre nom',
                      hintText: 'Prénom Nom',
                      helperText: 'Affiché chez vos collègues, modifiable plus tard.',
                      border: OutlineInputBorder(),
                    ),
                    validator: nameError,
                    onFieldSubmitted: (_) => _start(),
                  ),
                  const SizedBox(height: 24),
                  FilledButton(onPressed: _busy ? null : _start, child: const Text('Commencer')),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
