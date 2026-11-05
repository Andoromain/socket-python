# Journal des modifications

## 3.3.0

Compatible avec la 3.2 (même protocole) : rien ne change sur les postes Windows et Linux.

### Ajouté
- **Application Android** (dossier `mobile/`, Flutter) : un téléphone ou une tablette
  sur le Wi-Fi de l'entreprise converse avec les postes Windows et Linux, sans serveur.
  Même protocole, mêmes règles que la version PC :
  - détection automatique des collègues sur le réseau, ajout par adresse ;
  - bulles de conversation, coches ✓ ✓✓ ✓✓ bleues, « écrit… », séparateurs par jour,
    gros emojis ;
  - sélecteur d'emojis, émoticônes converties (`:)` → 🙂), réactions par appui long ;
  - messages écrits hors ligne envoyés au retour du contact ;
  - fichiers proposés par un PC affichés (nom, taille, aperçu des images), avec
    refus possible ; la réception et l'envoi de fichiers arrivent dans la version
    suivante, avec les notifications en arrière-plan et la mise à jour intégrée.
- APK de test construit à chaque modification (onglet « Artifacts » de GitHub Actions).
- Compatibilité garantie par des vecteurs de test partagés (encodage octet pour octet
  des trames) et par un test d'interopérabilité où la version PC converse avec le cœur
  de l'application Android : messages, accusés, réactions, fichiers de plusieurs Mo dans
  les deux sens, détection UDP.

## 3.2.1

Compatible avec la 3.2 (même protocole).

### Corrigé
- Connexion parfois coupée juste après l'arrivée d'un collègue (plus fréquent sous
  Windows) : sur une connexion entrante, des données (messages en attente, réponse à
  une offre de fichier, réactions) pouvaient partir avant notre présentation, et le pair
  coupait alors la connexion ; les messages n'étaient renvoyés que 30 s plus tard. Dans
  le même cas, un délai de présentation relancé à tort coupait la connexion au bout de
  10 s. La présentation part désormais toujours en premier.

## 3.2.0

Compatible avec la 3.1 (même protocole). Dernière mise à jour à installer à la main :
les suivantes se feront depuis l'application.

### Ajouté
- **Mise à jour automatique** : l'application vérifie au démarrage puis toutes les
  6 heures si une nouvelle version existe et l'annonce dans un bandeau (et une fois dans
  la zone de notification). Un clic télécharge l'installeur, vérifie taille et SHA-256,
  l'installe silencieusement et relance l'application. Nouveautés, « Plus tard »,
  « Ignorer cette version », et menu Aide › Rechercher des mises à jour.
- Publication des installeurs et du manifeste `latest.json` dans un dépôt public dédié
  (`socket-chat-releases`) : le code reste privé.
- Source de mise à jour réglable par l'administrateur (`update/url` : HTTPS ou dossier
  réseau) ou désactivable (`update/enabled`).
- `socket-chat --update` : vérifie et installe la mise à jour sans rien demander
  (scripts d'administration).
- Installeur Windows : `/RELAUNCH=1` relance l'application après une installation
  silencieuse ; `install.sh --relaunch` fait de même sous Linux, après avoir laissé
  l'application se fermer d'elle-même. Les téléchargements précédents sont supprimés
  au démarrage suivant.
- Le workflow Release construit aussi les installeurs sur les pull requests qui touchent
  au packaging, pour vérifier l'installeur avant publication.

## 3.1.1

Compatible avec la 3.1.0 (même protocole) : pas besoin de mettre tous les postes à
jour en même temps.

### Amélioré
- **Mise à jour par-dessus une version installée** : l'installeur Windows ferme
  lui-même Socket Chat s'il est ouvert (même caché dans la zone de notification, et
  aussi en installation silencieuse) ; le script Linux arrête l'instance ouverte et
  indique « mis à jour ». Préférences et historique sont conservés.
- README : section « Mettre à jour ».

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
