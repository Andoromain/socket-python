"""Emojis : catalogue (mots-clés français), raccourcis, sélecteur et barre de réactions."""

from __future__ import annotations

import re
import unicodedata

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

#: Réactions proposées au survol d'un message.
QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏"]

# Chaque entrée : emoji puis mots-clés de recherche (en français).
_CATALOG: dict[str, list[tuple[str, str]]] = {
    "Smileys": [
        ("😀", "sourire content heureux"),
        ("😃", "sourire joie grand"),
        ("😄", "rire sourire yeux"),
        ("😁", "sourire dents"),
        ("😆", "rire mdr xd"),
        ("😅", "rire gene sueur ouf"),
        ("🤣", "mort de rire mdr"),
        ("😂", "larmes de rire mdr"),
        ("🙂", "sourire leger"),
        ("🙃", "envers ironie"),
        ("😉", "clin d oeil"),
        ("😊", "rougir sourire timide"),
        ("😇", "ange innocent"),
        ("🥰", "amoureux coeurs"),
        ("😍", "yeux coeur adore"),
        ("🤩", "etoiles waouh"),
        ("😘", "bisou"),
        ("😋", "miam delicieux"),
        ("😛", "langue"),
        ("😜", "langue clin d oeil blague"),
        ("🤪", "fou loufoque"),
        ("🤗", "calin accolade"),
        ("🤭", "oups main bouche"),
        ("🤫", "chut secret"),
        ("🤔", "reflexion hmm pensif"),
        ("🤐", "bouche cousue"),
        ("🤨", "sourcil doute"),
        ("😐", "neutre"),
        ("😑", "inexpressif blase"),
        ("😶", "sans voix"),
        ("😏", "malicieux en coin"),
        ("😒", "pas content agace"),
        ("🙄", "yeux au ciel exaspere"),
        ("😬", "grimace gene"),
        ("😌", "soulage serein"),
        ("😔", "pensif triste"),
        ("😪", "somnolent"),
        ("😴", "dormir sommeil zzz"),
        ("😷", "masque malade"),
        ("🤒", "fievre malade thermometre"),
        ("🤧", "eternuer rhume"),
        ("🥵", "chaud canicule"),
        ("🥶", "froid gele"),
        ("😵", "etourdi"),
        ("🤯", "explosion tete choque"),
        ("🥳", "fete anniversaire"),
        ("😎", "cool lunettes"),
        ("🤓", "intello geek"),
        ("🧐", "monocle examiner"),
        ("😕", "confus"),
        ("😟", "inquiet"),
        ("🙁", "triste leger"),
        ("😮", "surpris oh"),
        ("😲", "stupefait"),
        ("😳", "rougir gene"),
        ("🥺", "supplier yeux"),
        ("😢", "triste larme pleurer"),
        ("😭", "pleurer sanglots"),
        ("😱", "peur cri horreur"),
        ("😖", "confondu"),
        ("😞", "decu"),
        ("😓", "sueur froide"),
        ("😩", "epuise las"),
        ("😫", "fatigue"),
        ("🥱", "baille ennui"),
        ("😤", "triomphe vapeur"),
        ("😡", "colere rouge"),
        ("😠", "colere fache"),
        ("🤬", "jurons insultes"),
        ("😈", "diable malin"),
        ("💀", "crane mort"),
        ("💩", "caca"),
        ("🤡", "clown"),
        ("👻", "fantome"),
        ("👽", "extraterrestre alien"),
        ("🤖", "robot"),
    ],
    "Gestes": [
        ("👍", "pouce ok d accord oui bien"),
        ("👎", "pouce bas non pas bien"),
        ("👌", "ok parfait"),
        ("✌️", "victoire paix"),
        ("🤞", "doigts croises chance"),
        ("🤟", "je t aime"),
        ("🤘", "rock cornes"),
        ("🤙", "appelle moi"),
        ("👈", "gauche"),
        ("👉", "droite"),
        ("👆", "haut"),
        ("👇", "bas"),
        ("☝️", "index attention"),
        ("✋", "main stop"),
        ("🤚", "dos de la main"),
        ("🖐️", "main ouverte"),
        ("🖖", "vulcain spock"),
        ("👋", "salut coucou bonjour au revoir"),
        ("🤝", "poignee de main accord"),
        ("🙏", "merci priere svp"),
        ("👏", "applaudir bravo"),
        ("🙌", "hourra bravo"),
        ("👐", "mains ouvertes"),
        ("🤲", "paumes"),
        ("💪", "muscle force courage"),
        ("✍️", "ecrire"),
        ("🫶", "coeur mains"),
        ("🫡", "salut militaire"),
        ("🤷", "je ne sais pas haussement"),
        ("🤦", "facepalm consternation"),
        ("🙋", "main levee moi"),
        ("🙅", "non interdit"),
        ("🙆", "ok d accord"),
        ("💁", "information"),
        ("🧑‍💻", "informaticien ordinateur developpeur"),
        ("👀", "yeux regarder"),
        ("🧠", "cerveau idee"),
    ],
    "Cœurs": [
        ("❤️", "coeur rouge amour"),
        ("🧡", "coeur orange"),
        ("💛", "coeur jaune"),
        ("💚", "coeur vert"),
        ("💙", "coeur bleu"),
        ("💜", "coeur violet"),
        ("🖤", "coeur noir"),
        ("🤍", "coeur blanc"),
        ("💔", "coeur brise"),
        ("💕", "deux coeurs"),
        ("💖", "coeur etincelant"),
        ("💯", "cent parfait"),
        ("✅", "valide fait coche"),
        ("✔️", "coche ok"),
        ("❌", "croix non erreur"),
        ("❗", "exclamation important"),
        ("❓", "question"),
        ("⚠️", "attention avertissement"),
        ("🚫", "interdit"),
        ("⭐", "etoile favori"),
        ("✨", "etincelles nouveau"),
        ("🔥", "feu top chaud"),
        ("💥", "boum explosion"),
        ("💤", "dodo sommeil"),
        ("🎉", "fete bravo confettis"),
        ("🎊", "confettis"),
        ("🔔", "cloche notification"),
        ("📌", "epingle important"),
        ("🆗", "ok"),
        ("🆕", "nouveau"),
        ("➡️", "fleche droite"),
        ("⬅️", "fleche gauche"),
        ("🔄", "actualiser recommencer"),
    ],
    "Nature": [
        ("☀️", "soleil beau temps"),
        ("🌤️", "eclaircie"),
        ("⛅", "nuageux"),
        ("🌧️", "pluie"),
        ("⛈️", "orage"),
        ("❄️", "neige flocon froid"),
        ("🌈", "arc en ciel"),
        ("🌙", "lune nuit"),
        ("🌍", "terre monde"),
        ("🌸", "fleur cerisier"),
        ("🌹", "rose fleur"),
        ("🌻", "tournesol"),
        ("🌳", "arbre"),
        ("🍀", "trefle chance"),
        ("🍁", "feuille automne"),
        ("🐶", "chien"),
        ("🐱", "chat"),
        ("🐭", "souris"),
        ("🐰", "lapin"),
        ("🦊", "renard"),
        ("🐻", "ours"),
        ("🐼", "panda"),
        ("🐨", "koala"),
        ("🦁", "lion"),
        ("🐮", "vache"),
        ("🐷", "cochon"),
        ("🐸", "grenouille"),
        ("🐵", "singe"),
        ("🐔", "poule"),
        ("🐧", "pingouin manchot"),
        ("🐦", "oiseau"),
        ("🦄", "licorne"),
        ("🐝", "abeille"),
        ("🦋", "papillon"),
        ("🐢", "tortue lent"),
        ("🐟", "poisson"),
        ("🐬", "dauphin"),
    ],
    "Nourriture": [
        ("☕", "cafe pause"),
        ("🍵", "the"),
        ("🥐", "croissant"),
        ("🥖", "baguette pain"),
        ("🧀", "fromage"),
        ("🍷", "vin"),
        ("🍺", "biere"),
        ("🥂", "trinquer champagne"),
        ("🍾", "champagne bouteille"),
        ("🍕", "pizza"),
        ("🍔", "burger"),
        ("🍟", "frites"),
        ("🥗", "salade"),
        ("🍝", "pates"),
        ("🍣", "sushi"),
        ("🥪", "sandwich"),
        ("🍰", "gateau"),
        ("🎂", "gateau anniversaire"),
        ("🍫", "chocolat"),
        ("🍪", "cookie biscuit"),
        ("🍩", "donut"),
        ("🍦", "glace"),
        ("🍎", "pomme"),
        ("🍌", "banane"),
        ("🍓", "fraise"),
        ("🍇", "raisin"),
        ("🍉", "pasteque"),
        ("🍋", "citron"),
        ("🥑", "avocat"),
        ("🍿", "popcorn"),
    ],
    "Activités": [
        ("⚽", "foot football"),
        ("🏀", "basket"),
        ("🏉", "rugby"),
        ("🎾", "tennis"),
        ("🚴", "velo"),
        ("🏃", "courir course"),
        ("🏆", "trophee victoire"),
        ("🥇", "medaille or premier"),
        ("🎯", "cible objectif"),
        ("🎮", "jeu video"),
        ("🎲", "de jeu"),
        ("🎵", "musique note"),
        ("🎧", "casque musique"),
        ("🎬", "film cinema"),
        ("📸", "photo"),
        ("🎁", "cadeau"),
        ("🎈", "ballon fete"),
        ("🚗", "voiture"),
        ("🚌", "bus"),
        ("🚆", "train"),
        ("🚇", "metro"),
        ("✈️", "avion voyage"),
        ("🚀", "fusee lancement"),
        ("🏖️", "plage vacances"),
        ("🏔️", "montagne"),
        ("🏠", "maison"),
        ("🏢", "bureau immeuble"),
        ("⏰", "reveil heure"),
        ("⌛", "sablier attente"),
    ],
    "Bureau": [
        ("💻", "ordinateur portable"),
        ("🖥️", "ecran ordinateur"),
        ("⌨️", "clavier"),
        ("🖱️", "souris"),
        ("🖨️", "imprimante"),
        ("📱", "telephone portable"),
        ("☎️", "telephone"),
        ("📞", "appel combine"),
        ("📧", "email courriel"),
        ("📨", "message recu"),
        ("📎", "trombone piece jointe"),
        ("📁", "dossier"),
        ("📂", "dossier ouvert"),
        ("📄", "document page"),
        ("📝", "note memo"),
        ("📋", "presse papier liste"),
        ("📅", "calendrier date"),
        ("📆", "agenda"),
        ("📊", "graphique statistiques"),
        ("📈", "hausse croissance"),
        ("📉", "baisse"),
        ("🗂️", "classeur"),
        ("🗑️", "corbeille poubelle"),
        ("🔒", "verrou securite"),
        ("🔑", "cle"),
        ("🔍", "loupe recherche"),
        ("💡", "idee ampoule"),
        ("🔧", "cle outil reparer"),
        ("⚙️", "engrenage parametres"),
        ("🛠️", "outils"),
        ("🐛", "bug"),
        ("💾", "disquette sauvegarder"),
        ("☁️", "nuage cloud"),
        ("📦", "colis paquet"),
        ("💰", "argent"),
        ("💶", "euro billet"),
        ("🧾", "facture recu"),
        ("✏️", "crayon"),
        ("🖊️", "stylo"),
        ("📚", "livres"),
        ("🎓", "diplome formation"),
        ("☕", "cafe pause"),
    ],
}

CATEGORIES = list(_CATALOG)

_SHORTCUTS = {
    ":)": "🙂",
    ":-)": "🙂",
    ":D": "😃",
    ":-D": "😃",
    "xD": "😆",
    "XD": "😆",
    ";)": "😉",
    ";-)": "😉",
    ":(": "🙁",
    ":-(": "🙁",
    ":'(": "😢",
    ":P": "😛",
    ":p": "😛",
    ":-P": "😛",
    ":o": "😮",
    ":O": "😮",
    ":|": "😐",
    "<3": "❤️",
    "^^": "😊",
    "(y)": "👍",
}
_SHORTCUT_RE = re.compile(
    r"(?<!\S)("
    + "|".join(re.escape(k) for k in sorted(_SHORTCUTS, key=len, reverse=True))
    + r")(?!\S)"
)


def convert_shortcuts(text: str) -> str:
    """Remplace les émoticônes isolées (« :) », « <3 »…) par l'emoji correspondant."""
    return _SHORTCUT_RE.sub(lambda m: _SHORTCUTS[m.group(1)], text)


_EMOJI_UNIT = (
    r"(?:[\U0001F1E6-\U0001F1FF]{2}"  # drapeaux
    r"|[0-9#*]️?⃣"  # touches 1️⃣
    r"|[©®‼⁉™ℹ↔-↪⌚-⏿Ⓜ▪-◾"
    r"☀-➿⤴⤵⬅-⭕〰〽㊗㊙\U0001F000-\U0001FAFF]"
    r"️?[\U0001F3FB-\U0001F3FF]?"
    r"(?:‍[☀-➿\U0001F000-\U0001FAFF]️?[\U0001F3FB-\U0001F3FF]?)*)"
)
_EMOJI_ONLY_RE = re.compile(rf"\s*(?:{_EMOJI_UNIT}\s*)+")
_EMOJI_UNIT_RE = re.compile(_EMOJI_UNIT)


def emoji_count(text: str) -> int:
    """Nombre d'emojis si le texte n'est composé que d'emojis, sinon 0."""
    if not text.strip() or not _EMOJI_ONLY_RE.fullmatch(text):
        return 0
    return len(_EMOJI_UNIT_RE.findall(text))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def search(query: str) -> list[str]:
    """Emojis dont les mots-clés contiennent tous les mots de la recherche."""
    words = _normalize(query).split()
    results: list[str] = []
    for entries in _CATALOG.values():
        for emoji, keywords in entries:
            haystack = _normalize(keywords)
            if all(word in haystack for word in words) and emoji not in results:
                results.append(emoji)
    return results


def category(name: str) -> list[str]:
    return [emoji for emoji, _ in _CATALOG[name]]


def emoji_font(base: QFont, scale: float = 1.0) -> QFont:
    """Police agrandie pour les emojis (Qt bascule seul sur la police emoji du système)."""
    font = QFont(base)
    if scale != 1.0:
        font.setPointSizeF(max(8.0, base.pointSizeF() * scale))
    return font


class EmojiPicker(QFrame):
    """Fenêtre surgissante : recherche, catégories, emojis récents."""

    picked = Signal(str)

    def __init__(self, recent: list[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._recent = list(recent or [])

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Rechercher un emoji (ex. « merci », « café »)…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)

        tabs = QHBoxLayout()
        tabs.setSpacing(2)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        names = (["Récents"] if self._recent else []) + CATEGORIES
        for name in names:
            button = QToolButton(self)
            button.setText("🕘" if name == "Récents" else category(name)[0])
            button.setToolTip(name)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFont(emoji_font(self.font(), 1.2))
            button.clicked.connect(lambda _=False, n=name: self.show_category(n))
            self._tab_group.addButton(button)
            tabs.addWidget(button)
        tabs.addStretch(1)

        self.title = QLabel(self)
        self.title.setForegroundRole(self.title.foregroundRole().PlaceholderText)

        self.grid = QListWidget(self)
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(38, 38))
        self.grid.setFont(emoji_font(self.font(), 1.6))
        self.grid.setFrameShape(QFrame.Shape.NoFrame)
        self.grid.setMouseTracking(True)
        self.grid.itemClicked.connect(self._on_item_clicked)
        self.grid.itemActivated.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.search_edit)
        layout.addLayout(tabs)
        layout.addWidget(self.title)
        layout.addWidget(self.grid, 1)
        self.resize(370, 360)
        self.show_category(names[0])

    def show_category(self, name: str) -> None:
        for button in self._tab_group.buttons():
            button.setChecked(button.toolTip() == name)
        self.title.setText(name)
        self._fill(self._recent if name == "Récents" else category(name))

    def popup_at(self, anchor: QPoint) -> None:
        """Affiche le sélecteur au-dessus du point donné (coordonnées globales)."""
        self.move(anchor.x(), max(0, anchor.y() - self.height()))
        self.show()
        self.search_edit.setFocus()

    def _fill(self, emojis: list[str]) -> None:
        self.grid.clear()
        for emoji in emojis:
            item = QListWidgetItem(emoji)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setSizeHint(QSize(36, 36))
            self.grid.addItem(item)

    def _on_search(self, text: str) -> None:
        if text.strip():
            results = search(text)
            self.title.setText(f"{len(results)} résultat(s)" if results else "Aucun résultat")
            self._fill(results)
        else:
            self.show_category("Récents" if self._recent else CATEGORIES[0])

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.picked.emit(item.text())
        self.close()


class ReactionBar(QFrame):
    """Petite barre surgissante des réactions rapides (+ accès au sélecteur complet)."""

    picked = Signal(str)
    more_requested = Signal()

    def __init__(self, current: str = "", parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        for emoji in QUICK_REACTIONS:
            button = QToolButton(self)
            button.setText(emoji)
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setChecked(emoji == current)
            button.setToolTip("Retirer la réaction" if emoji == current else "Réagir")
            button.setFont(emoji_font(self.font(), 1.5))
            # Recliquer sur sa réaction actuelle la retire.
            button.clicked.connect(lambda _=False, e=emoji: self._pick("" if e == current else e))
            layout.addWidget(button)
        more = QToolButton(self)
        more.setText("+")
        more.setToolTip("Plus d'emojis")
        more.setAutoRaise(True)
        more.clicked.connect(self._more)
        layout.addWidget(more)

    def _pick(self, emoji: str) -> None:
        self.picked.emit(emoji)
        self.close()

    def _more(self) -> None:
        self.close()
        self.more_requested.emit()
