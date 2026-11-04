# Journal des modifications

## 2.0.0

Réécriture complète de l'application de 2019.

### Modifié
- Passage de PyQt4 / Python 3.2 à **PySide6 (Qt 6)** / Python ≥ 3.10.
- Réseau piloté par la boucle d'événements Qt (`QTcpServer`/`QTcpSocket`) : plus de
  thread qui modifie l'interface (source de plantages) et plus de gel de la fenêtre
  pendant l'attente d'un correspondant.
- Nouveau protocole JSON Lines versionné avec présentation mutuelle (nom d'affichage),
  messages multi-lignes et fin de conversation explicite. **Incompatible avec la 1.x.**
- Interface refaite à la main (plus de code généré) : barre d'outils, raccourcis clavier,
  Entrée pour envoyer, historique horodaté, notification à la réception.
- Préférences enregistrées dans le profil utilisateur (`QSettings`) au lieu des fichiers
  `monIP.ip` / `sonIP.ip` du dossier courant.

### Ajouté
- Paquet Python installable (`pip`/`pipx`), installeur Windows, archive Linux avec script
  d'installation, intégration continue et tests automatisés.
- L'hôte se remet en écoute quand le correspondant part ; une seconde connexion
  simultanée est refusée poliment.
- Délais de connexion, limites de taille, journal dans le profil utilisateur.

### Corrigé
- Le nom saisi n'était jamais utilisé.
- Test erroné `hote != " "` lors de la connexion.
- Texte reçu interprété comme du HTML.

### Supprimé
- Doublons (`auter/`, `Nouveau document texte.txt`), fichiers `.pyc` versionnés,
  images d'origine inconnue (remplacées par une icône créée pour le projet).
