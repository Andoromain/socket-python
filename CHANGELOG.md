# Journal des modifications

## 2.0.1

### Corrigé
- Erreur « Internal C++ object (QTcpSocket) already deleted » environ 3 secondes
  après chaque fin de conversation : le délai de fermeture visait une socket déjà
  détruite (visible surtout sous Windows).

### Modifié
- **Python 3.12 minimum** (au lieu de 3.10). Sous Python 3.10 et 3.11, PySide6 6.12
  fausse le compteur de références de `True` à chaque émission de signal, ce qui
  fait planter l'interpréteur (« free(): invalid size » / « munmap_chunk(): invalid
  pointer »). Python 3.12 rend `True` immortel et supprime le problème. Les
  exécutables Windows et Linux, construits avec Python 3.12, n'étaient pas concernés.
- Les tests détruisent proprement les objets Qt qu'ils créent.

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
