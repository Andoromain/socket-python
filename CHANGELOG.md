# Journal des modifications

## 3.1.0

**Protocole version 3** : incompatible avec la 3.0 et la 2.x, mettre à jour tous les
postes. Les trames d'un type inconnu sont désormais ignorées, ce qui évitera ce genre
de mise à jour simultanée à l'avenir.

### Ajouté
- **Emojis** : sélecteur (catégories, recherche en français, récents), conversion des
  émoticônes (`:)` → 🙂, `<3` → ❤️…) à l'envoi, affichage agrandi des messages
  composés de 1 à 3 emojis.
- **Réactions** sur les messages (👍 ❤️ 😂 😮 😢 🙏 ou n'importe quel emoji) au survol
  ou par clic droit, visibles des deux côtés ; envoyées au retour du contact s'il est
  hors ligne.
- **Envoi de fichiers** (jusqu'à 2 Go) par le bouton 📎, glisser-déposer ou `Ctrl+V` :
  le destinataire accepte (en choisissant l'emplacement) ou refuse ; progression,
  annulation des deux côtés, vérification SHA-256, reprise après coupure.
- **Images** : aperçu dans la bulle avant même l'acceptation ; une capture d'écran
  collée est envoyée directement.

### Modifié
- Historique : schéma SQLite version 2 (fichiers, réactions), migration automatique
  des bases de la 3.0.

## 3.0.0

Socket Chat devient une vraie messagerie. **Incompatible avec la 2.x** (protocole
version 2) : mettre à jour tous les postes.

### Ajouté
- **Détection automatique** des collègues sur le réseau local (annonces UDP, port
  12801) : plus besoin de saisir d'adresse. L'ajout par adresse reste possible.
- **Plusieurs conversations** simultanées : liste des contacts avec avatar, présence,
  aperçu du dernier message, heure, compteur de non-lus et recherche.
- **Bulles de messages** : regroupement des messages successifs, séparateurs par jour,
  heure dans la bulle, menu « Copier », thème clair ou sombre automatique.
- **Accusés de réception et de lecture** (🕓 en attente, ✓ envoyé, ✓✓ remis,
  ✓✓ bleu lu) et indication **« écrit… »**.
- **Historique conservé** (SQLite dans le profil utilisateur).
- **File d'envoi** : un message écrit hors ligne est remis au retour du collègue,
  sans doublon en cas de retransmission.
- **Zone de notification** : notifications, compteur de non-lus, l'application reste
  active à la fermeture de la fenêtre (réglable).
- **Instance unique** : relancer l'application réaffiche la fenêtre existante.

### Modifié
- Plus de modes « Héberger » / « Rejoindre » : chaque poste écoute en permanence.
- Chaque utilisateur a un identifiant stable ; une seule connexion par collègue, même
  si deux postes se connectent l'un à l'autre en même temps.

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
