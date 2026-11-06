// Tests de l'interface : écrans rendus avec un historique préparé, sans réseau.

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socket_chat_mobile/app/controller.dart';
import 'package:socket_chat_mobile/app/settings.dart';
import 'package:socket_chat_mobile/store/store.dart';
import 'package:socket_chat_mobile/ui/app.dart';

const alice = '11111111-1111-4111-8111-111111111111';
const bob = '22222222-2222-4222-8222-222222222222';

String mid(int i) => '00000000-0000-4000-8000-${i.toString().padLeft(12, '0')}';

Future<ChatController> makeController({String? name = 'Moi'}) async {
  final prefs = MemoryPreferenceStore();
  if (name != null) prefs.values['name'] = name;
  final settings = await Settings.load(prefs);
  return ChatController(Store(), settings, useNetwork: false, useDiscovery: false);
}

void populate(Store store) {
  final now = DateTime.now().millisecondsSinceEpoch;
  final yesterday = DateTime.now().subtract(const Duration(days: 1)).millisecondsSinceEpoch;
  store.upsertContact(alice, 'Alice Martin', host: '10.0.0.2', port: 12800, lastSeen: now - 60000);
  store.upsertContact(bob, 'Bob Durand', host: '10.0.0.3', port: 12800, lastSeen: yesterday);
  store.addMessage(MessageRecord(mid(1), alice, false, 'Salut ! Réunion à 14 h ?', yesterday, Status.read));
  store.addMessage(MessageRecord(mid(2), alice, true, 'Oui, je serai là', yesterday + 1000, Status.read));
  store.addMessage(MessageRecord(mid(3), alice, false, 'Parfait, à tout à l’heure', now - 5000, Status.delivered));
  store.addMessage(MessageRecord(mid(4), alice, false, '👍', now - 4000, Status.delivered));
  store.addMessage(
    MessageRecord(
      mid(5),
      bob,
      true,
      '',
      yesterday - 1000,
      Status.delivered,
      file: FileInfo('compte rendu.pdf', 123456, FileState.offered, '/x/compte rendu.pdf', Uint8List(0)),
    ),
  );
  store.setReaction(mid(2), alice, '❤️');
}

void main() {
  testWidgets('premier lancement : choix du nom', (tester) async {
    final controller = await makeController(name: null);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    expect(find.text('Bienvenue dans Socket Chat'), findsOneWidget);

    await tester.tap(find.text('Commencer'));
    await tester.pump();
    expect(find.text('Le nom ne peut pas être vide.'), findsOneWidget);

    await tester.enterText(find.byType(TextFormField), '  Jean   Dupont ');
    await tester.tap(find.text('Commencer'));
    await tester.pumpAndSettle();
    expect(controller.settings.name, 'Jean Dupont');
    expect(controller.messenger.name, 'Jean Dupont');
    expect(find.text('Aucun collègue pour le moment'), findsOneWidget);
  });

  testWidgets('liste des contacts : aperçus, non-lus, recherche', (tester) async {
    final controller = await makeController();
    populate(controller.store);
    await tester.pumpWidget(SocketChatApp(controller: controller));

    expect(find.text('Alice Martin'), findsOneWidget);
    expect(find.text('Bob Durand'), findsOneWidget);
    expect(find.text('👍'), findsOneWidget); // aperçu du dernier message d'Alice
    expect(find.text('2'), findsOneWidget); // deux messages non lus
    expect(find.text('📎 compte rendu.pdf'), findsOneWidget);
    // Alice, plus récente, apparaît en premier.
    expect(tester.getTopLeft(find.text('Alice Martin')).dy, lessThan(tester.getTopLeft(find.text('Bob Durand')).dy));

    await tester.tap(find.byTooltip('Rechercher'));
    await tester.pump();
    await tester.enterText(find.byType(TextField), 'bob');
    await tester.pump();
    expect(find.text('Alice Martin'), findsNothing);
    expect(find.text('Bob Durand'), findsOneWidget);
  });

  testWidgets('conversation : lecture, envoi, émoticônes, séparateurs', (tester) async {
    final controller = await makeController();
    populate(controller.store);
    await tester.pumpWidget(SocketChatApp(controller: controller));

    await tester.tap(find.text('Alice Martin'));
    await tester.pumpAndSettle();
    expect(controller.store.totalUnread(), 0); // ouverte = lue
    expect(find.text('Hier'), findsOneWidget);
    expect(find.text("Aujourd'hui"), findsOneWidget);
    expect(find.text('Salut ! Réunion à 14 h ?'), findsOneWidget);
    expect(find.text('hors ligne'), findsOneWidget);

    await tester.enterText(find.byType(TextField), 'Merci :)');
    await tester.pump();
    await tester.tap(find.byTooltip('Envoyer'));
    await tester.pumpAndSettle();
    final sent = controller.store.messages(alice).last;
    expect([sent.text, sent.outgoing, sent.status], ['Merci 🙂', true, Status.pending]);
    expect(find.text('Merci 🙂'), findsOneWidget);
    expect(find.byIcon(Icons.schedule), findsOneWidget); // en attente d'envoi
    expect(find.text('Hors ligne : vos messages partiront à son retour.'), findsOneWidget);
  });

  testWidgets('réaction par appui long et copie', (tester) async {
    final controller = await makeController();
    populate(controller.store);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    await tester.tap(find.text('Alice Martin'));
    await tester.pumpAndSettle();

    await tester.longPress(find.text('Salut ! Réunion à 14 h ?'));
    await tester.pumpAndSettle();
    expect(find.text('Copier le texte'), findsOneWidget);
    await tester.tap(find.text('😂'));
    await tester.pumpAndSettle();
    expect(controller.store.message(mid(1))!.reactionOf(me), '😂');
    expect(find.text('😂'), findsOneWidget); // pastille sous la bulle

    // Même réaction : retirée.
    await tester.longPress(find.text('Salut ! Réunion à 14 h ?'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('😂').last);
    await tester.pumpAndSettle();
    expect(controller.store.message(mid(1))!.reactionOf(me), '');

    // Réaction reçue affichée.
    expect(find.text('❤️'), findsOneWidget);
  });

  testWidgets('bulle de fichier', (tester) async {
    final controller = await makeController();
    populate(controller.store);
    await tester.pumpWidget(SocketChatApp(controller: controller));
    await tester.tap(find.text('Bob Durand'));
    await tester.pumpAndSettle();
    expect(find.text('compte rendu.pdf'), findsOneWidget);
    expect(find.text('121 Ko · En attente de réponse'), findsOneWidget);
    expect(find.text('PDF'), findsOneWidget);

    await tester.longPress(find.text('compte rendu.pdf'));
    await tester.pumpAndSettle();
    await tester.tap(find.text("Annuler l'envoi"));
    await tester.pumpAndSettle();
    expect(controller.store.message(mid(5))!.file!.state, FileState.cancelled);
    expect(find.text('121 Ko · Annulé'), findsOneWidget);
  });

  testWidgets('préférences : validation et enregistrement', (tester) async {
    final controller = await makeController();
    await tester.pumpWidget(SocketChatApp(controller: controller));
    await tester.tap(find.byTooltip('Préférences'));
    await tester.pumpAndSettle();

    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), 'x' * 40);
    await tester.enterText(fields.at(1), '70000');
    await tester.tap(find.text('Enregistrer'));
    await tester.pump();
    expect(find.text('Le nom ne doit pas dépasser 32 caractères.'), findsOneWidget);
    expect(find.text('Port entre 1 et 65535.'), findsOneWidget);

    await tester.enterText(fields.at(0), 'Nouveau Nom');
    await tester.enterText(fields.at(1), '12900');
    await tester.tap(find.text('Enregistrer'));
    await tester.pumpAndSettle();
    expect(controller.settings.name, 'Nouveau Nom');
    expect(controller.settings.port, 12900);
    expect(find.text('Socket Chat'), findsOneWidget); // retour à la liste
  });

  testWidgets('ajout par adresse : adresse:port reconnue', (tester) async {
    final controller = await makeController();
    await tester.pumpWidget(SocketChatApp(controller: controller));
    await tester.tap(find.text('Ajouter'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Se connecter'));
    await tester.pump();
    expect(find.text('Indiquez une adresse.'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).first, '192.168.1.20:12850');
    await tester.tap(find.text('Se connecter'));
    await tester.pumpAndSettle();
    // Sans réseau dans ce test, la messagerie n'écoute pas : l'ajout est refusé.
    expect(find.text("La messagerie n'est pas démarrée (voir Préférences)."), findsOneWidget);
  });
}
